import express from "express";
import { parlaysDelDia } from "../lib/motor.js";
import { requireAuth, requirePro, requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Camino B: los parlays los arma el MOTOR (fuente única). Este router solo
// hace de puente + aplica el muro freemium. Node ya no genera parlays desde
// su Mongo local (los picks migraron al motor, esa colección ya no se llena).
//
// Honestidad: el motor entrega edge = N/D (null) mientras no haya momios
// reales ni probabilidades calibradas. Aquí solo lo transportamos.

function opcionalAuth(req, _res, next) {
  const h = req.headers.authorization || "";
  if (!h.startsWith("Bearer ")) return next();
  requireAuth(req, _res, () => next());
}

// Censura las patas de un parlay PRO para un no-suscriptor.
// Mostramos que existe y su momio combinado, pero las patas (lo que se
// paga) no salen del server sin PRO.
function censurar(pl) {
  return {
    _id: pl._id,
    numPatas: (pl.patas || []).length,
    momioComb: pl.momioComb,
    edge: pl.edge,               // N/D igual
    edgeValido: pl.edgeValido,
    bloqueado: true,
  };
}

// --- PARLAYS DEL DÍA ---
router.get("/hoy", opcionalAuth, ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";

  let data;
  try {
    data = await parlaysDelDia(liga, false, req.id);
  } catch (err) {
    // Si el motor no responde, no reventamos la vista.
    return res.json({ parlays: [], esPro: false, edgeDisponible: false, motorCaido: true });
  }

  const parlays = data.parlays || [];
  parlays.forEach((pl, i) => { pl._id = pl._id || `${liga}-${i}`; });
  // soñadores: siempre públicos (gancho gratis), con _id estable
  const sonadores = (data.sonadores || []);
  sonadores.forEach((s) => { s._id = s._id || `${liga}-${s.tipo}`; });

  const esPro = !!(req.user && req.user.isProActive());
  if (esPro) {
    return res.json({ parlays, sonadores, esPro: true, edgeDisponible: !!data.edge_disponible });
  }

  const salida = parlays.map((pl) =>
    pl.tier === "public" ? { ...pl, bloqueado: false } : censurar(pl)
  );
  res.json({ parlays: salida, sonadores, esPro: false, edgeDisponible: !!data.edge_disponible });
}));

// --- PÚBLICO (compat): solo el/los parlay(s) public del día ---
router.get("/public", ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, false, req.id);
    res.json((data.parlays || []).filter((p) => p.tier === "public"));
  } catch {
    res.json([]);
  }
}));

// --- PRO: parlays exclusivos. Muro server-side igual que los picks ---
router.get("/pro", requireAuth, requirePro, ah(async (req, res) => {
  const liga = req.query.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, false, req.id);
    res.json((data.parlays || []).filter((p) => p.tier === "pro"));
  } catch {
    res.json([]);
  }
}));

// --- ADMIN: forzar regeneración de los parlays del día en el motor ---
router.post("/generate", requireAdmin, ah(async (req, res) => {
  const liga = req.body.liga || "ligamx";
  try {
    const data = await parlaysDelDia(liga, true, req.id);
    res.json({ ok: true, liga, generados: (data.parlays || []).length });
  } catch (err) {
    res.status(502).json({ error: "El motor no pudo generar los parlays", detalle: err.message });
  }
}));

export default router;
