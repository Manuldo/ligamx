// lib/motor.js — Puente al motor de análisis Pickazo MX (Python + RAG)
// Coloca este archivo en la raíz de pickazoapp o en una carpeta lib/

const MOTOR_URL = process.env.MOTOR_URL;
const MOTOR_KEY = process.env.MOTOR_KEY;

/**
 * Pide un análisis completo de un partido al motor Python.
 * @param {string} local - equipo local
 * @param {string} visitante - equipo visitante
 * @param {string} liga - slug de liga (ligamx, premier, laliga...)
 * @param {boolean} profundo - true = análisis avanzado (para PRO)
 * @returns {Promise<{ok, partido, liga, analisis_markdown, nivel, desde_cache}>}
 */
export async function analizarPartido(local, visitante, liga = "ligamx", profundo = false) {
  const r = await fetch(`${MOTOR_URL}/pickazo/analizar`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${MOTOR_KEY}`,
    },
    body: JSON.stringify({ local, visitante, liga, profundo }),
  });
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/**
 * Trae los picks sugeridos de una liga (para "Picks de hoy", gratis).
 * @param {string} liga - slug de liga
 * @param {number} limite - cuántos partidos
 */
export async function picksDelDia(liga = "ligamx", limite = 8) {
  const r = await fetch(
    `${MOTOR_URL}/pickazo/picks_dia?liga=${encodeURIComponent(liga)}&limite=${limite}`,
    { headers: { Authorization: `Bearer ${MOTOR_KEY}` } }
  );
  if (!r.ok) throw new Error(`Motor respondió ${r.status}`);
  return await r.json();
}

/** Verifica que el motor esté vivo (health check). */
export async function motorVivo() {
  try {
    const r = await fetch(`${MOTOR_URL}/pickazo/estado`);
    return r.ok;
  } catch {
    return false;
  }
}
