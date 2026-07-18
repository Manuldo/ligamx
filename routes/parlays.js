import express from "express";
import Pick from "../models/Pick.js";
import Parlay from "../models/Parlay.js";
import { mejoresParlays } from "../parlay-engine.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);

// Llama a Anthropic para redactar el analisis del parlay.
// La API key vive SOLO en el server (env var), nunca llega al frontend.
async function analizarConIA(parlay) {
  const patasTxt = parlay.patas
    .map((p, i) => `${i + 1}. ${p.partido} — ${p.mercado} (momio ${p.momio})`)
    .join("\n");

  const prompt = `Eres un analista de apuestas deportivas de Liga MX, directo y honesto, sin humo.
Analiza este parlay de ${parlay.patas.length} patas:

${patasTxt}

Momio combinado: ${parlay.momioComb}
Probabilidad conjunta estimada: ${(parlay.probConjunta * 100).toFixed(1)}%
Edge: ${(parlay.edge * 100).toFixed(1)}%

Responde SOLO con un JSON valido, sin markdown ni backticks:
{"analisis": "2-3 frases en espanol de Mexico sobre por que estas patas juntas y el riesgo real", "veredicto": "una de: MANDAR / CON RESERVAS / SOLO SI TE GUSTA EL RIESGO"}`;

  try {
    const r = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": process.env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01"
      },
      body: JSON.stringify({
        model: "claude-haiku-4-5-20251001",
        max_tokens: 400,
        messages: [{ role: "user", content: prompt }]
      })
    });
    const data = await r.json();
    const txt = (data.content?.[0]?.text || "").replace(/```json|```/g, "").trim();
    const parsed = JSON.parse(txt);
    return { analisisIA: parsed.analisis || "", veredicto: parsed.veredicto || "" };
  } catch (err) {
    console.error("Error IA parlay:", err.message);
    return { analisisIA: "", veredicto: "" };
  }
}

// --- ADMIN: genera los parlays del dia ---
// Se dispara despues de publicar los picks. Arma:
//   - 1 parlay publico (SOLO con picks publicos, para no filtrar PRO)
//   - N parlays PRO (con todos los picks del dia)
router.post("/generate", requireAdmin, ah(async (req, res) => {
  const fecha = req.body.fecha || hoy();
  const cuantosPro = req.body.cuantosPro || 3;

  const publicos = await Pick.find({ fecha, tier: "public", activo: true }).lean();
  const todos = await Pick.find({ fecha, activo: true }).lean();

  // Limpia parlays previos de la fecha
  await Parlay.deleteMany({ fecha });

  // Parlay publico: SOLO picks publicos (muro intacto)
  const pubList = mejoresParlays(publicos, { minK: 2, maxK: 3, top: 1 });
  // Parlays PRO: todos los picks
  const proList = mejoresParlays(todos, { minK: 2, maxK: 3, top: cuantosPro });

  const guardados = [];

  if (pubList[0]) {
    const ia = await analizarConIA(pubList[0]);
    guardados.push(await Parlay.create({
      fecha, tier: "public",
      patas: pubList[0].patas.map((p) => ({
        partido: p.partido, mercado: p.mercado, momio: p.momio, probEstimada: p.probEstimada
      })),
      momioComb: pubList[0].momioComb,
      probConjunta: pubList[0].probConjunta,
      edge: pubList[0].edge,
      ...ia
    }));
  }

  for (const pl of proList) {
    const ia = await analizarConIA(pl);
    guardados.push(await Parlay.create({
      fecha, tier: "pro",
      patas: pl.patas.map((p) => ({
        partido: p.partido, mercado: p.mercado, momio: p.momio, probEstimada: p.probEstimada
      })),
      momioComb: pl.momioComb,
      probConjunta: pl.probConjunta,
      edge: pl.edge,
      ...ia
    }));
  }

  res.json({ ok: true, fecha, generados: guardados.length });
}));

// --- PUBLICO: el parlay del dia ---
router.get("/public", ah(async (req, res) => {
  const fecha = req.query.fecha || hoy();
  const parlays = await Parlay.find({ fecha, tier: "public" }).lean();
  res.json(parlays);
}));

// --- PRO: parlays exclusivos. Muro server-side igual que los picks ---
router.get("/pro", requireAuth, requirePro, ah(async (req, res) => {
  const fecha = req.query.fecha || hoy();
  const parlays = await Parlay.find({ fecha, tier: "pro" }).lean();
  res.json(parlays);
}));

export default router;
