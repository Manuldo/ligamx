// Motor de parlays. Logica pura, sin IA ni DB, para poder testearla.

// Coherencia: dos patas NO son compatibles si se contradicen o
// estan tan correlacionadas que juntarlas es trampa estadistica.
export function compatible(a, b) {
  // Misma pata exacta: no
  if (a._id && b._id && a._id.toString() === b._id.toString()) return false;

  // Dos mercados del MISMO partido: alto riesgo de correlacion.
  // Los bloqueamos salvo que sean claramente independientes.
  if (a.partido === b.partido) {
    // Over/Under del mismo partido = contradiccion directa
    const m1 = (a.mercado || "").toLowerCase();
    const m2 = (b.mercado || "").toLowerCase();
    const esOverUnder = (m) => m.includes("over") || m.includes("under") ||
                                m.includes("mas de") || m.includes("menos de");
    if (esOverUnder(m1) && esOverUnder(m2)) return false;
    // 1X2 contradictorio (local vs visitante mismo partido)
    if (m1.includes("local") && m2.includes("visit")) return false;
    if (m1.includes("visit") && m2.includes("local")) return false;
    // Por defecto, mismo partido = evitar (correlacion)
    return false;
  }
  return true;
}

// Genera combinaciones coherentes de tamano k a partir de los picks.
export function generarCombinaciones(picks, k) {
  const res = [];
  const n = picks.length;

  function backtrack(start, combo) {
    if (combo.length === k) {
      // Verifica coherencia de todas las parejas
      for (let i = 0; i < combo.length; i++)
        for (let j = i + 1; j < combo.length; j++)
          if (!compatible(combo[i], combo[j])) return;
      res.push([...combo]);
      return;
    }
    for (let i = start; i < n; i++) {
      combo.push(picks[i]);
      backtrack(i + 1, combo);
      combo.pop();
    }
  }
  backtrack(0, []);
  return res;
}

// Metricas de un parlay: momio combinado y probabilidad conjunta.
export function calcularParlay(combo) {
  const momioComb = combo.reduce((acc, p) => acc * p.momio, 1);
  const probConjunta = combo.reduce((acc, p) => acc * p.probEstimada, 1);
  const probImplicita = 1 / momioComb;
  return {
    momioComb: Number(momioComb.toFixed(2)),
    probConjunta: Number(probConjunta.toFixed(4)),
    probImplicita: Number(probImplicita.toFixed(4)),
    edge: Number((probConjunta - probImplicita).toFixed(4))
  };
}

// Rankea parlays por SEGURIDAD: priorizamos probabilidad conjunta alta
// que ademas tenga edge positivo (valor). No el momio mas alto.
export function mejoresParlays(picks, { minK = 2, maxK = 3, top = 5 } = {}) {
  // Solo patas con verdicto MANDAR entran al armado
  const elegibles = picks.filter((p) => p.verdicto === "MANDAR");
  const parlays = [];

  for (let k = minK; k <= maxK; k++) {
    for (const combo of generarCombinaciones(elegibles, k)) {
      const m = calcularParlay(combo);
      if (m.edge <= 0) continue; // sin valor, fuera
      parlays.push({ patas: combo, ...m });
    }
  }

  // Orden: mayor probabilidad conjunta primero (mas seguro),
  // desempate por edge.
  parlays.sort((a, b) =>
    b.probConjunta - a.probConjunta || b.edge - a.edge
  );
  return parlays.slice(0, top);
}
