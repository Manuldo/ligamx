import express from "express";
import PushToken from "../models/PushToken.js";
import { requireAuth, requireAdmin } from "../middleware/auth.js";
import { enviarPush, apnsConfigurado } from "../lib/apns.js";
import PartidoSeguido from "../models/PartidoSeguido.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// La app registra el token de su dispositivo tras iniciar sesión.
router.post("/register", requireAuth, ah(async (req, res) => {
  const token = String(req.body?.token || "").trim();
  if (!/^[0-9a-fA-F]{32,200}$/.test(token)) return res.status(400).json({ error: "Token inválido" });
  await PushToken.findOneAndUpdate(
    { token },
    { user: req.user._id, plataforma: "ios", ultimoUso: new Date() },
    { upsert: true, new: true }
  );
  res.json({ ok: true });
}));

// El usuario puede quitar su dispositivo (al salir de la cuenta).
router.post("/unregister", requireAuth, ah(async (req, res) => {
  const token = String(req.body?.token || "").trim();
  await PushToken.deleteOne({ token, user: req.user._id });
  res.json({ ok: true });
}));

// Prueba manual (solo admin): manda una notificación a todos los dispositivos
// de un correo. Body: { email, titulo, cuerpo }
router.post("/test", requireAdmin, ah(async (req, res) => {
  if (!apnsConfigurado()) return res.status(503).json({ error: "APNs no configurado (faltan variables APNS_*)" });
  const { default: User } = await import("../models/User.js");
  const user = await User.findOne({ email: String(req.body?.email || "").toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  const docs = await PushToken.find({ user: user._id });
  const r = await enviarPush(docs.map(d => d.token), {
    titulo: req.body?.titulo || "Pickazo MX",
    cuerpo: req.body?.cuerpo || "Notificación de prueba",
  });
  if (r.invalidos.length) await PushToken.deleteMany({ token: { $in: r.invalidos } });
  res.json({ dispositivos: docs.length, ...r });
}));

// ── Partidos seguidos: aviso una hora antes ─────────────────────────────
router.get("/follows", requireAuth, ah(async (req, res) => {
  const docs = await PartidoSeguido.find({ user: req.user._id, kickoff: { $gt: new Date() } }).select("matchId -_id");
  res.json(docs.map(d => d.matchId));
}));

router.post("/follow", requireAuth, ah(async (req, res) => {
  const { matchId, liga, local, visitante, kickoff } = req.body || {};
  const k = new Date(kickoff);
  if (!matchId || String(matchId).length > 200 || isNaN(k.getTime())) return res.status(400).json({ error: "Partido inválido" });
  if (k.getTime() <= Date.now()) return res.status(400).json({ error: "Ese partido ya empezó" });
  const total = await PartidoSeguido.countDocuments({ user: req.user._id, kickoff: { $gt: new Date() } });
  if (total >= 100) return res.status(400).json({ error: "Máximo 100 partidos seguidos" });
  await PartidoSeguido.findOneAndUpdate(
    { user: req.user._id, matchId: String(matchId) },
    { liga: String(liga || "").slice(0, 60), local: String(local || "").slice(0, 80), visitante: String(visitante || "").slice(0, 80), kickoff: k, notificado: false },
    { upsert: true, new: true }
  );
  res.json({ ok: true });
}));

router.post("/unfollow", requireAuth, ah(async (req, res) => {
  await PartidoSeguido.deleteOne({ user: req.user._id, matchId: String(req.body?.matchId || "") });
  res.json({ ok: true });
}));

export default router;
