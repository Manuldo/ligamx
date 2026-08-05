import express from "express";
import { parlaysDelDia } from "../lib/motor.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Camino B: los parlays los arma el MOTOR (fuente única). Este router solo
// hace de puente + aplica el muro freemium. Ya no se generan desde la Mongo
// local del Node; eso quedó atrás cuando los picks migraron al motor.

function opcionalAuth(req, _res, next) {
  const h = req.headers.authorization || "";
  if (!h.startsWith("Bearer ")) return next();
  requireAuth(req, _res, () => next());
}

// Censura las patas de un parlay PRO para un no-suscriptor.
// Igual criterio que los picks: mostramos que existe y su ventaja,
// pero lo que se paga (las patas) no sale del server.
function censurar(pl) {
  return {
    _id: pl._id,
    numPatas: (pl.patas || []).length,
    momioComb: pl.momioComb,
    edge: pl.edge,
    bloqueado: true,
  };
}

// --- PARLAYS DEL DÍA ---
// El motor devuelve cada parlay con tier "public" | "pro".
// Al no-PRO le liberamos los public completos y le censuramos los pro.
router.get("/hoy", opcionalAuth, ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";

  let data;
  try {
    data = await parlaysDelDia(liga, false);
  } catch (err) {
    // Si el motor no responde, no reventamos la vista: parlays vacío.
    return res.json({ parlays: [], esPro: false, motorCaido: true });
  }

  const parlays = data.parlays || [];
  // el motor no persiste _id propio por parlay; damos uno estable por índice
  parlays.forEach((pl, i) => { pl._id = pl._id || `${liga}-${i}`; });

  const esPro = !!(req.user && req.user.isProActive());
  if (esPro) return res.json({ parlays, esPro: true });

  const salida = parlays.map((pl) =>
    pl.tier === "public" ? { ...pl, bloqueado: false } : censurar(pl)
  );
  res.json({ parlays: salida, esPro: false });
}));

// --- PÚBLICO (compat): solo el/los parlay(s) public del día ---
router.get("/public", ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, false);
    const pub = (data.parlays || []).filter((p) => p.tier === "public");
    res.json(pub);
  } catch {
    res.json([]);
  }
}));

// --- PRO: parlays exclusivos. Muro server-side igual que los picks ---
router.get("/pro", requireAuth, requirePro, ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, false);
    const pro = (data.parlays || []).filter((p) => p.tier === "pro");
    res.json(pro);
  } catch {
    res.json([]);
  }
}));

// --- ADMIN: forzar regeneración de los parlays del día ---
// Antes esto los generaba localmente; ahora le pide al motor que regenere
// (forzar=true ignora su cache y arma con los datos frescos).
router.post("/generate", requireAdmin, ah(async (req, res) => {
  const liga = req.body.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, true);
    res.json({ ok: true, liga, generados: (data.parlays || []).length });
  } catch (err) {
    res.status(502).json({ error: "El motor no pudo generar los parlays", detalle: err.message });
  }
}));

export default router;
