// Puente servidor-a-servidor hacia el motor Python.
// Lee variables en cada llamada para funcionar también con dotenv en local.

function config() {
  const url = String(process.env.MOTOR_URL || "").replace(/\/$/, "");
  const key = process.env.MOTOR_KEY;
  if (!url || !key) throw new Error("Motor no configurado: faltan MOTOR_URL/MOTOR_KEY");
  const timeoutMs = Math.min(Math.max(Number(process.env.MOTOR_TIMEOUT_MS || 45000), 1000), 90000);
  const retries = Math.min(Math.max(Number(process.env.MOTOR_RETRIES || 1), 0), 3);
  return { url, key, timeoutMs, retries };
}

function headers(key, requestId) {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${key}`,
    ...(requestId ? { "X-Request-Id": requestId } : {}),
  };
}

async function motorRequest(path, options = {}) {
  const { url, key, timeoutMs, retries } = config();
  const method = options.method || "GET";
  const attempts = method === "GET" ? retries + 1 : 1;
  let lastError;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      const response = await fetch(`${url}${path}`, {
        ...options,
        headers: { ...headers(key, options.requestId), ...(options.headers || {}) },
        signal: AbortSignal.timeout(timeoutMs),
      });
      if (!response.ok) {
        const retryable = [429, 502, 503, 504].includes(response.status);
        if (retryable && attempt < attempts) {
          await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
          continue;
        }
        const detail = (await response.text()).slice(0, 300);
        throw new Error(`Motor respondió ${response.status}${detail ? `: ${detail}` : ""}`);
      }
      return response.json();
    } catch (error) {
      lastError = error;
      if (attempt < attempts && (error?.name === "TimeoutError" || error?.name === "TypeError")) {
        await new Promise((resolve) => setTimeout(resolve, 250 * attempt));
        continue;
      }
      break;
    }
  }
  throw lastError || new Error("Motor no disponible");
}

export function analizarPartido(local, visitante, liga = "ligamx", profundo = false, forzar = false, requestId) {
  return motorRequest("/pickazo/analizar", {
    method: "POST",
    requestId,
    body: JSON.stringify({ local, visitante, liga, profundo, forzar }),
  });
}

export function picksDelDia(liga = "ligamx", limite = 8, requestId) {
  return motorRequest(`/pickazo/picks_dia?liga=${encodeURIComponent(liga)}&limite=${limite}`, { requestId });
}

export function partidosLiga(liga = "ligamx", limite = 60, requestId) {
  return motorRequest(`/pickazo/partidos?liga=${encodeURIComponent(liga)}&limite=${limite}`, { requestId });
}

export function tablaLiga(liga = "ligamx", requestId) {
  return motorRequest(`/pickazo/tabla?liga=${encodeURIComponent(liga)}`, { requestId });
}

export function jugadoresLiga(liga = "ligamx", top = 50, requestId) {
  return motorRequest(`/pickazo/jugadores?liga=${encodeURIComponent(liga)}&top=${top}`, { requestId });
}

export async function motorVivo() {
  try {
    const { url, timeoutMs } = config();
    const response = await fetch(`${url}/pickazo/estado`, { signal: AbortSignal.timeout(Math.min(timeoutMs, 3000)) });
    return response.ok;
  } catch {
    return false;
  }
}

export function jugadoresEquipo(liga, equipo, top = 5, requestId) {
  return motorRequest(`/pickazo/jugadores_equipo?liga=${encodeURIComponent(liga)}&equipo=${encodeURIComponent(equipo)}&top=${top}`, { requestId });
}

export function alineacionesPartido(liga, local, visitante, requestId) {
  return motorRequest(`/pickazo/alineaciones?liga=${encodeURIComponent(liga)}&local=${encodeURIComponent(local)}&visitante=${encodeURIComponent(visitante)}`, { requestId });
}

export function jornadasLiga(liga = "ligamx", requestId) {
  return motorRequest(`/pickazo/jornadas?liga=${encodeURIComponent(liga)}`, { requestId });
}

export function todasLasLigas(limite = 300, requestId) {
  return motorRequest(`/pickazo/todas_ligas?limite=${limite}`, { requestId });
}

export function traerAlineacion(liga, local, visitante, requestId) {
  return motorRequest("/pickazo/traer_alineacion", {
    method: "POST",
    requestId,
    body: JSON.stringify({ liga, local, visitante }),
  });
}

// Parlays del día armados por el motor (Camino B: fuente única).
// El motor los combina de los próximos partidos y rankea por probabilidad
// conjunta estimada. El edge llega como N/D hasta que haya momios reales.
// forzar=true regenera ignorando la cache del motor.
export function parlaysDelDia(liga = "ligamx", forzar = false, requestId) {
  const q = `/pickazo/parlays?liga=${encodeURIComponent(liga)}${forzar ? "&forzar=true" : ""}`;
  return motorRequest(q, { requestId });
}

// Stats por partido: goleadores, amonestados y córners/tiros recientes
// de ambos equipos. Alimenta el detalle del partido en pickazoapp.
export function statsPartido(liga, local, visitante, top = 3, requestId) {
  const q = `/pickazo/stats_partido?liga=${encodeURIComponent(liga)}` +
    `&local=${encodeURIComponent(local)}&visitante=${encodeURIComponent(visitante)}&top=${top}`;
  return motorRequest(q, { requestId });
}

// Track record: resumen de aciertos/fallos + historial. Alimenta el
// registro público de credibilidad y el detalle PRO.
export function trackRecord(liga = "", limite = 50, requestId) {
  const q = `/pickazo/trackrecord?liga=${encodeURIComponent(liga)}&limite=${limite}`;
  return motorRequest(q, { requestId });
}

// Fuerza la resolución de picks pendientes (normalmente el motor lo hace
// solo tras cada partido, pero se puede disparar manualmente).
export function resolverTrack(requestId) {
  return motorRequest(`/pickazo/resolver`, { method: "POST", requestId });
}

// Parlay Maestro: picks de varias ligas a la vez (el usuario elige cuáles).
export function parlayMaestro(ligas = "ligamx", limitePorLiga = 4, requestId) {
  const q = `/pickazo/maestro?ligas=${encodeURIComponent(ligas)}&limite_por_liga=${limitePorLiga}`;
  return motorRequest(q, { requestId });
}

// Comunidad: parlays más gustados (boletos guardados por la banda).
export function parlayComunidad(top = 8, requestId) {
  return motorRequest(`/pickazo/comunidad?top=${top}`, { requestId });
}

// Guardar un boleto en la comunidad (con nombre + autor).
export function guardarBoletoComunidad(payload, requestId) {
  return motorRequest(`/pickazo/guardar_boleto`, {
    method: "POST", body: JSON.stringify(payload),
    headers: { "Content-Type": "application/json" }, requestId,
  });
}

// Dar like a un boleto.
export function likeBoleto(id, requestId) {
  return motorRequest(`/pickazo/like_boleto`, {
    method: "POST", body: JSON.stringify({ id }),
    headers: { "Content-Type": "application/json" }, requestId,
  });
}

