import express from "express";
import User from "../models/User.js";
import Setting from "../models/Setting.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

export const LINK_GRUPO = "premium.grupoLink";

// Deja solo dígitos y "+" inicial; números de 10 dígitos se toman como México.
export function normalizarWhatsapp(valor) {
  const limpio = String(valor || "").trim().replace(/[^\d+]/g, "");
  let digitos = limpio.replace(/\D/g, "");
  if (!limpio.startsWith("+") && digitos.length === 10) digitos = "52" + digitos;
  if (digitos.length < 10 || digitos.length > 15) return null;
  return "+" + digitos;
}

function requirePremium(req, res, next) {
  if (!req.user?.isPremiumActive()) {
    return res.status(403).json({ error: "Requiere Premium activo" });
  }
  next();
}

/**
 * POST /api/premium/whatsapp  { whatsapp }
 * El Premium registra el número con el que va a pedir entrar al grupo.
 */
router.post("/whatsapp", requireAuth, requirePremium, ah(async (req, res) => {
  const whatsapp = normalizarWhatsapp(req.body?.whatsapp);
  if (!whatsapp) return res.status(400).json({ error: "Escribe un número válido (10 dígitos)" });
  await User.updateOne({ _id: req.user._id }, { $set: { whatsapp, whatsappRegistradoEn: new Date() } });
  res.json({ ok: true, whatsapp });
}));

/**
 * GET /api/premium/grupo
 * Link del grupo del mes. Solo para Premium activo con WhatsApp registrado.
 */
router.get("/grupo", requireAuth, requirePremium, ah(async (req, res) => {
  if (!req.user.whatsapp) {
    return res.status(409).json({ error: "Registra tu WhatsApp para ver el acceso", faltaWhatsapp: true });
  }
  const link = await Setting.get(LINK_GRUPO);
  if (!link) return res.status(503).json({ error: "El acceso al grupo estará disponible en un momento" });
  res.json({ link, whatsapp: req.user.whatsapp });
}));

export default router;
