// Activar/quitar PRO manualmente (admin). Ajustado a tu models/User.js real.
// Puedes pegar estas rutas dentro de tu routes/admin.js existente,
// o montar este archivo como ruta nueva en server.js:
//   import adminProRoutes from "./routes/admin-pro.js";
//   app.use("/api/admin", adminProRoutes);

import express from "express";
import User from "../models/User.js";
import Setting from "../models/Setting.js";
import { LINK_GRUPO, normalizarWhatsapp } from "./premium.js";
import { requireAdmin } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

/**
 * POST /api/admin/pro
 * Header: x-admin-key: TU_ADMIN_KEY
 * Body: { email: "usuario@correo.com", meses: 1 }
 * Activa PRO por N meses (default 1). Pone isPro=true y la vigencia.
 */
router.post("/pro", requireAdmin, ah(async (req, res) => {
  const { email, meses = 1 } = req.body;
  if (!email) return res.status(400).json({ error: "Falta email" });

  const user = await User.findOne({ email: email.toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });

  const hasta = new Date();
  hasta.setMonth(hasta.getMonth() + Number(meses));
  user.isPro = true;
  user.plan = "pro";
  user.subscriptionExpiresAt = hasta;
  await user.save();

  res.json({ ok: true, email: user.email, pro: true, vence: user.subscriptionExpiresAt });
}));

/**
 * POST /api/admin/quitar-pro
 * Header: x-admin-key: TU_ADMIN_KEY
 * Body: { email }
 */
router.post("/quitar-pro", requireAdmin, ah(async (req, res) => {
  const { email } = req.body;
  const user = await User.findOne({ email: (email || "").toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  user.isPro = false;
  user.subscriptionExpiresAt = null;
  await user.save();
  res.json({ ok: true, email: user.email, pro: false });
}));

/**
 * GET /api/admin/usuario?email=...
 * Header: x-admin-key: TU_ADMIN_KEY
 * Para checar el estado PRO de alguien.
 */
router.get("/usuario", requireAdmin, ah(async (req, res) => {
  const user = await User.findOne({ email: (req.query.email || "").toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  res.json({
    email: user.email,
    isPro: user.isPro,
    plan: user.plan,
    whatsapp: user.whatsapp,
    vence: user.subscriptionExpiresAt,
    activo: user.isProActive(),
    premiumActivo: user.isPremiumActive(),
  });
}));

/**
 * GET /api/admin/metricas
 * Header: x-admin-key: TU_ADMIN_KEY
 * Panel de negocio: usuarios, PRO, ingresos estimados, conversión.
 * Los números REALES para tus reportes mensuales y el inversionista.
 */
router.get("/metricas", requireAdmin, ah(async (req, res) => {
  const PRECIO = 299;           // precio mensual PRO en MXN
  const PRECIO_PREMIUM = 1000;  // precio mensual Premium en MXN
  const ahora = new Date();
  const hoy0 = new Date(ahora); hoy0.setHours(0, 0, 0, 0);
  const hace7 = new Date(ahora.getTime() - 7 * 864e5);
  const hace30 = new Date(ahora.getTime() - 30 * 864e5);

  const [total, proActivos, nuevosHoy, nuevos7, nuevos30, proMes, premiumActivos] = await Promise.all([
    User.countDocuments({}),
    User.countDocuments({ isPro: true, subscriptionExpiresAt: { $gt: ahora } }),
    User.countDocuments({ createdAt: { $gte: hoy0 } }),
    User.countDocuments({ createdAt: { $gte: hace7 } }),
    User.countDocuments({ createdAt: { $gte: hace30 } }),
    User.countDocuments({ isPro: true, subscriptionExpiresAt: { $gt: ahora }, createdAt: { $gte: hace30 } }),
    User.countDocuments({ plan: "premium", isPro: true, subscriptionExpiresAt: { $gt: ahora } }),
  ]);

  const conversion = total > 0 ? proActivos / total : 0;
  const soloPro = proActivos - premiumActivos;
  const mrr = soloPro * PRECIO + premiumActivos * PRECIO_PREMIUM; // ingreso recurrente mensual estimado
  const arr = mrr * 12;                       // anualizado

  // serie de registros por día (últimos 14 días) para una mini gráfica
  const serie = [];
  for (let i = 13; i >= 0; i--) {
    const d0 = new Date(ahora.getTime() - i * 864e5); d0.setHours(0, 0, 0, 0);
    const d1 = new Date(d0.getTime() + 864e5);
    const n = await User.countDocuments({ createdAt: { $gte: d0, $lt: d1 } });
    serie.push({ dia: d0.toISOString().slice(5, 10), registros: n });
  }

  res.json({
    usuarios: { total, nuevosHoy, nuevos7, nuevos30 },
    pro: { activos: proActivos, soloPro, premium: premiumActivos, nuevos30: proMes },
    conversion: Math.round(conversion * 1000) / 10, // % con 1 decimal
    ingresos: { precio: PRECIO, precioPremium: PRECIO_PREMIUM, mrr, arr, moneda: "MXN" },
    serieRegistros: serie,
    generado: ahora.toISOString(),
  });
}));

/* ===================== PREMIUM (grupo de WhatsApp) ===================== */

const DIA = 864e5;
function filaPremium(u, ahora) {
  const vence = u.subscriptionExpiresAt ? new Date(u.subscriptionExpiresAt) : null;
  return {
    email: u.email,
    whatsapp: u.whatsapp || null,
    vence,
    dias: vence ? Math.ceil((vence.getTime() - ahora) / DIA) : null,
    estado: u.stripeSubscriptionStatus || (u.stripeSubscriptionId ? null : "manual"),
    premiumDesde: u.premiumDesde,
  };
}

/**
 * GET /api/admin/premium
 * Listas para controlar el grupo: falta WhatsApp, vencidos (sacar), por vencer, activos.
 */
router.get("/premium", requireAdmin, ah(async (req, res) => {
  const ahora = Date.now();
  const usuarios = await User.find({ premiumDesde: { $ne: null } })
    .select("email whatsapp plan isPro subscriptionExpiresAt stripeSubscriptionId stripeSubscriptionStatus premiumDesde grupoSacadoEn")
    .sort({ subscriptionExpiresAt: 1 })
    .lean();

  const activo = (u) => u.plan === "premium" && u.isPro && u.subscriptionExpiresAt && new Date(u.subscriptionExpiresAt).getTime() > ahora;
  const faltaWhatsapp = [], vencidos = [], porVencer = [], activos = [];
  for (const u of usuarios) {
    const f = filaPremium(u, ahora);
    if (activo(u)) {
      if (!u.whatsapp) faltaWhatsapp.push(f);
      else if (f.dias != null && f.dias <= 5) porVencer.push(f);
      else activos.push(f);
    } else if (!u.grupoSacadoEn && u.whatsapp) {
      vencidos.push(f); // ya no paga y sigue (o pudo seguir) en el grupo
    }
  }
  const totalActivos = faltaWhatsapp.length + porVencer.length + activos.length;
  res.json({
    link: await Setting.get(LINK_GRUPO),
    kpis: { activos: totalActivos, ingresoMes: totalActivos * 1000, porVencer: porVencer.length, vencidos: vencidos.length },
    faltaWhatsapp, vencidos, porVencer, activos,
  });
}));

/** POST /api/admin/premium/link  { link }  — link del grupo del mes */
router.post("/premium/link", requireAdmin, ah(async (req, res) => {
  const link = String(req.body?.link || "").trim();
  if (!/^https:\/\/chat\.whatsapp\.com\/[A-Za-z0-9]{10,40}$/.test(link)) {
    return res.status(400).json({ error: "Pega un link de invitación de WhatsApp (https://chat.whatsapp.com/...)" });
  }
  await Setting.set(LINK_GRUPO, link);
  res.json({ ok: true, link });
}));

/** POST /api/admin/premium/sacado  { email }  — ya lo sacaste del grupo */
router.post("/premium/sacado", requireAdmin, ah(async (req, res) => {
  const user = await User.findOneAndUpdate(
    { email: String(req.body?.email || "").toLowerCase().trim() },
    { $set: { grupoSacadoEn: new Date() } },
    { new: true }
  );
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  res.json({ ok: true, email: user.email });
}));

/**
 * POST /api/admin/premium/activar  { email, meses = 1, whatsapp? }
 * Premium manual (cortesía o ajuste). No toca Stripe.
 */
router.post("/premium/activar", requireAdmin, ah(async (req, res) => {
  const { email, meses = 1, whatsapp } = req.body || {};
  if (!email) return res.status(400).json({ error: "Falta email" });
  const n = Math.min(Math.max(parseInt(meses, 10) || 1, 1), 12);
  const user = await User.findOne({ email: String(email).toLowerCase().trim() });
  if (!user) return res.status(404).json({ error: "Usuario no encontrado" });
  if (user.stripeSubscriptionId && !["canceled", "incomplete_expired"].includes(user.stripeSubscriptionStatus)) {
    return res.status(409).json({ error: "Tiene suscripción activa en Stripe; cámbiala desde Stripe" });
  }
  const base = user.isProActive() ? new Date(user.subscriptionExpiresAt) : new Date();
  base.setMonth(base.getMonth() + n);
  user.isPro = true;
  user.plan = "premium";
  user.subscriptionExpiresAt = base;
  user.grupoSacadoEn = null;
  if (!user.premiumDesde) user.premiumDesde = new Date();
  if (whatsapp) {
    const w = normalizarWhatsapp(whatsapp);
    if (!w) return res.status(400).json({ error: "WhatsApp inválido" });
    user.whatsapp = w;
    user.whatsappRegistradoEn = new Date();
  }
  await user.save();
  res.json({ ok: true, email: user.email, plan: "premium", vence: user.subscriptionExpiresAt });
}));

export default router;
