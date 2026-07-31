// lib/motor.js — Puente al motor de análisis Pickazo MX (Python + RAG)
// Camino B: pickazoapp le pide TODO al motor (partidos, tabla, jugadores, análisis).

const MOTOR_URL = process.env.MOTOR_URL;
const MOTOR_KEY = process.env.MOTOR_KEY;

function headers() {
  return { "Content-Type": "application/json", Authorization: `Bearer ${MOTOR_KEY}` };
}

/** Análisis completo de un partido (PRO = profundo). */
export async function analizarPartido(local, visitante, liga = "ligamx", profundo = false) {
  const r = await fetch(`${MOTOR_URL}/pickazo/analizar`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ local, visitante, liga, profundo }),
  });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Picks sugeridos del día (gratis). */
export async function picksDelDia(liga = "ligamx", limite = 8) {
  const r = await fetch(`${MOTOR_URL}/pickazo/picks_dia?liga=${encodeURIComponent(liga)}&limite=${limite}`,
    { headers: headers() });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Lista de partidos de una liga (para Jornadas). */
export async function partidosLiga(liga = "ligamx", limite = 60) {
  const r = await fetch(`${MOTOR_URL}/pickazo/partidos?liga=${encodeURIComponent(liga)}&limite=${limite}`,
    { headers: headers() });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Tabla de posiciones de una liga. */
export async function tablaLiga(liga = "ligamx") {
  const r = await fetch(`${MOTOR_URL}/pickazo/tabla?liga=${encodeURIComponent(liga)}`,
    { headers: headers() });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Jugadores de una liga. */
export async function jugadoresLiga(liga = "ligamx", top = 50) {
  const r = await fetch(`${MOTOR_URL}/pickazo/jugadores?liga=${encodeURIComponent(liga)}&top=${top}`,
    { headers: headers() });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Health check. */
export async function motorVivo() {
  try { const r = await fetch(`${MOTOR_URL}/pickazo/estado`); return r.ok; }
  catch { return false; }
}
