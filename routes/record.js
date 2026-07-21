import express from "express";
import Pick from "../models/Pick.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Track record publico: la evidencia de venta.
// Se calcula sobre picks YA cerrados, a unidad constante.
// Cacheado 10 min para no pegarle a Mongo en cada visita.
let cache = null, cacheAt = 0;
const TTL = 10 * 60 * 1000;

router.get("/", ah(async (req, res) => {
  if (cache && Date.now() - cacheAt < TTL) return res.json(cache);

  const cerrados = await Pick.find({
    resultado: { $in: ["acierto", "fallo"] }
  }).sort({ fecha: -1 }).limit(500).lean();

  let unidades = 0, aciertos = 0;
  for (const p of cerrados) {
    if (p.resultado === "acierto") { unidades += p.momio - 1; aciertos++; }
    else unidades -= 1;
  }

  // Racha actual (desde el mas reciente hacia atras)
  let racha = 0, tipoRacha = null;
  for (const p of cerrados) {
    if (tipoRacha === null) { tipoRacha = p.resultado; racha = 1; }
    else if (p.resultado === tipoRacha) racha++;
    else break;
  }

  // Ultimos 10 para la tira visual
  const ultimos = cerrados.slice(0, 10).map(p => ({
    resultado: p.resultado, momio: p.momio, partido: p.partido, fecha: p.fecha
  }));

  cache = {
    total: cerrados.length,
    aciertos,
    fallos: cerrados.length - aciertos,
    tasaAcierto: cerrados.length ? Number((aciertos / cerrados.length).toFixed(3)) : null,
    unidades: Number(unidades.toFixed(2)),
    roi: cerrados.length ? Number((unidades / cerrados.length).toFixed(3)) : null,
    racha, tipoRacha,
    ultimos
  };
  cacheAt = Date.now();
  res.json(cache);
}));

// Invalida el cache cuando se cierra un pick (lo llama history.js)
export function invalidarRecord() { cache = null; }

export default router;
