import express from "express";
import UserPick from "../models/UserPick.js";
import Pick from "../models/Pick.js";
import Parlay from "../models/Parlay.js";
import { requireAuth, requireAdmin } from "../middleware/auth.js";
import { invalidarRecord } from "./record.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();
const hoy = () => new Date().toISOString().slice(0, 10);

// --- REGISTRAR que metí este pick en mi casa de apuestas ---
router.post("/registrar", requireAuth, ah(async (req, res) => {
  const { pickId, parlayId, snapshotParlay, monto } = req.body;
  if (!pickId && !parlayId) {
    return res.status(400).json({ error: "Falta el pick o el parlay" });
  }

  // ObjectId de Mongo = 24 hex. Los parlays del motor traen ids tipo
  // "ligamx-0": esos NO viven en la Mongo local del Node.
  const esObjectId = (v) => typeof v === "string" && /^[a-f\d]{24}$/i.test(v);
  const hoyFecha = () => new Date().toISOString().slice(0, 10);

  let snapshot, fecha;
  let parlayObjId = null, parlayMotor = false;

  if (pickId) {
    const p = await Pick.findById(pickId).lean();
    if (!p) return res.status(404).json({ error: "Pick no encontrado" });
    // Un pick PRO solo lo registra un PRO activo
    if (p.tier === "pro" && !req.user.isProActive()) {
      return res.status(403).json({ error: "Requiere PRO" });
    }
    fecha = p.fecha;
    snapshot = { partido: p.partido, mercado: p.mercado, momio: p.momio,
                 probEstimada: p.probEstimada, edge: p.edge, tipo: "pick" };
  } else if (esObjectId(parlayId)) {
    // Parlay heredado que sí vive en la Mongo local
    const pl = await Parlay.findById(parlayId).lean();
    if (!pl) return res.status(404).json({ error: "Parlay no encontrado" });
    if (pl.tier === "pro" && !req.user.isProActive()) {
      return res.status(403).json({ error: "Requiere PRO" });
    }
    fecha = pl.fecha;
    parlayObjId = parlayId;
    snapshot = {
      partido: pl.patas.map(x => x.partido).join(" + "),
      mercado: `${pl.patas.length} patas`,
      momio: pl.momioComb, probEstimada: pl.probConjunta,
      edge: pl.edge, tipo: "parlay"
    };
  } else {
    // Parlay del motor (Camino B): congelamos lo que el usuario vio.
    // El front manda el snapshot; validamos lo mínimo.
    const sp = snapshotParlay || {};
    if (!Array.isArray(sp.patas) || !sp.patas.length) {
      return res.status(400).json({ error: "Falta el detalle del parlay" });
    }
    fecha = sp.fecha || hoyFecha();
    parlayMotor = true;
    snapshot = {
      partido: sp.patas.map(x => x.partido).join(" + "),
      mercado: `${sp.patas.length} patas`,
      momio: sp.momioComb, probEstimada: sp.probConjunta,
      edge: sp.edge ?? null, tipo: "parlay"   // edge puede ser N/D
    };
  }

  try {
    // Dedupe manual para parlays del motor (sin índice ObjectId que aplique)
    if (parlayMotor) {
      const yaExiste = await UserPick.findOne({
        userId: req.user._id, fecha, "snapshot.tipo": "parlay",
        "snapshot.partido": snapshot.partido
      }).lean();
      if (yaExiste) return res.status(409).json({ error: "Ya registraste este parlay" });
    }

    const doc = await UserPick.create({
      userId: req.user._id,
      pickId: pickId || null,
      parlayId: parlayObjId,       // solo ObjectId; null para parlays del motor
      snapshot,
      monto: monto ?? null,
      fecha
    });
    res.json({ ok: true, registro: doc });
  } catch (err) {
    if (err.code === 11000) {
      return res.status(409).json({ error: "Ya registraste este pick" });
    }
    throw err;
  }
}));

// --- QUITAR un registro ---
router.delete("/:id", requireAuth, ah(async (req, res) => {
  const r = await UserPick.deleteOne({ _id: req.params.id, userId: req.user._id });
  if (!r.deletedCount) return res.status(404).json({ error: "Registro no encontrado" });
  res.json({ ok: true });
}));

// --- MI HISTORIAL + resumen de rendimiento ---
router.get("/", requireAuth, ah(async (req, res) => {
  const registros = await UserPick.find({ userId: req.user._id })
    .sort({ createdAt: -1 }).limit(200).lean();

  const cerrados = registros.filter(r => r.resultado === "acierto" || r.resultado === "fallo");
  const aciertos = cerrados.filter(r => r.resultado === "acierto").length;

  // Rendimiento a unidad constante: cada pick = 1 unidad.
  // Acierto devuelve (momio - 1), fallo pierde 1.
  let unidades = 0;
  for (const r of cerrados) {
    unidades += r.resultado === "acierto" ? (r.snapshot.momio - 1) : -1;
  }

  const resumen = {
    total: registros.length,
    pendientes: registros.filter(r => r.resultado === "pendiente").length,
    cerrados: cerrados.length,
    aciertos,
    fallos: cerrados.length - aciertos,
    tasaAcierto: cerrados.length ? Number((aciertos / cerrados.length).toFixed(3)) : null,
    unidades: Number(unidades.toFixed(2)),
    // ROI sobre unidades apostadas
    roi: cerrados.length ? Number((unidades / cerrados.length).toFixed(3)) : null
  };

  res.json({ resumen, registros });
}));

// --- ADMIN: cerrar resultado de un pick publicado ---
// Marca el pick y propaga a todos los registros de usuarios.
router.post("/cerrar", requireAdmin, ah(async (req, res) => {
  const { pickId, parlayId, resultado } = req.body;
  if (!["acierto", "fallo", "nulo"].includes(resultado)) {
    return res.status(400).json({ error: "Resultado inválido" });
  }
  const q = pickId ? { pickId } : { parlayId };
  if (!pickId && !parlayId) return res.status(400).json({ error: "Falta pickId o parlayId" });

  const r = await UserPick.updateMany(q, {
    $set: { resultado, cerradoAt: new Date() }
  });

  if (pickId) await Pick.updateOne({ _id: pickId }, { $set: { resultado } });

  // El track record publico cambio: tirar cache para que se recalcule
  invalidarRecord();

  res.json({ ok: true, actualizados: r.modifiedCount });
}));

// --- PENDIENTES DEL MOTOR (Camino B): parlays/picks que el usuario marcó
// "Lo jugué" desde Parlays/Pick Lock/Soñadores. Esos vienen sin pickId ni
// parlayId (el motor no vive en esta Mongo, solo guardamos el snapshot que
// el usuario vio) — por eso NUNCA aparecían en "Cerrar resultados": esa
// pantalla solo listaba picks publicados a mano. Agrupamos por lo que
// identifica la jugada (fecha + partido + mercado) para poder cerrarlos
// todos de un jalón, aunque los haya marcado mucha gente.
router.get("/pendientes-motor", requireAdmin, ah(async (req, res) => {
  const grupos = await UserPick.aggregate([
    { $match: { pickId: null, parlayId: null, resultado: "pendiente" } },
    {
      $group: {
        _id: { fecha: "$fecha", partido: "$snapshot.partido", mercado: "$snapshot.mercado" },
        personas: { $sum: 1 },
        momio: { $first: "$snapshot.momio" },
        probEstimada: { $first: "$snapshot.probEstimada" },
        creado: { $min: "$createdAt" },
      },
    },
    { $sort: { creado: -1 } },
    { $limit: 150 },
  ]);
  res.json(
    grupos.map((g) => ({
      fecha: g._id.fecha,
      partido: g._id.partido,
      mercado: g._id.mercado,
      personas: g.personas,
      momio: g.momio,
      probEstimada: g.probEstimada,
    }))
  );
}));

// --- CERRAR un grupo de pendientes del motor por fecha+partido+mercado ---
router.post("/cerrar-manual", requireAdmin, ah(async (req, res) => {
  const { fecha, partido, mercado, resultado } = req.body;
  if (!["acierto", "fallo", "nulo"].includes(resultado)) {
    return res.status(400).json({ error: "Resultado inválido" });
  }
  if (!fecha || !partido || !mercado) {
    return res.status(400).json({ error: "Falta fecha, partido o mercado" });
  }

  const r = await UserPick.updateMany(
    {
      pickId: null,
      parlayId: null,
      resultado: "pendiente",
      fecha,
      "snapshot.partido": partido,
      "snapshot.mercado": mercado,
    },
    { $set: { resultado, cerradoAt: new Date() } }
  );

  invalidarRecord();

  res.json({ ok: true, actualizados: r.modifiedCount });
}));

export default router;
