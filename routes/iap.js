import express from "express";
import crypto from "crypto";
import User from "../models/User.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

// Compras dentro de la app (Apple In-App Purchase) vía RevenueCat.
// Guideline 3.1.1: en la app iOS el PRO solo se vende con IAP.
// El cobro lo hace Apple; RevenueCat nos avisa por webhook y aquí activamos el PRO.
const router = express.Router();

function igual(recibido, esperado) {
  if (!recibido || !esperado) return false;
  const a = crypto.createHash("sha256").update(String(recibido)).digest();
  const b = crypto.createHash("sha256").update(String(esperado)).digest();
  return crypto.timingSafeEqual(a, b);
}

// La llave pública del SDK de RevenueCat NO es secreta (va en la app), pero la
// leemos de variables para no hornearla en el HTML.
router.get("/config", requireAuth, (req, res) => {
  const apiKey = process.env.REVENUECAT_IOS_KEY || "";
  res.json({
    activo: Boolean(apiKey),
    apiKey,
    appUserId: req.user._id.toString(),
    entitlement: process.env.REVENUECAT_ENTITLEMENT || "pro",
  });
});

// El front llama aquí justo después de comprar o de "Restaurar compras".
// Verificamos con RevenueCat (llave secreta, solo en el servidor) en vez de
// confiar en lo que diga el teléfono, y así no dependemos de que el webhook llegue primero.
router.post("/sincronizar", requireAuth, ah(async (req, res) => {
  const llave = process.env.REVENUECAT_SECRET_KEY;
  if (!llave) return res.status(503).json({ error: "Compras no configuradas todavía" });
  const uid = req.user._id.toString();
  let r;
  try {
    r = await fetch(`https://api.revenuecat.com/v1/subscribers/${encodeURIComponent(uid)}`, {
      headers: { Authorization: `Bearer ${llave}`, "Content-Type": "application/json" },
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    return res.status(502).json({ error: "No se pudo verificar la compra, intenta de nuevo" });
  }
  if (!r.ok) return res.status(502).json({ error: "No se pudo verificar la compra, intenta de nuevo" });
  const data = await r.json().catch(() => ({}));
  const ent = data?.subscriber?.entitlements?.[process.env.REVENUECAT_ENTITLEMENT || "pro"];
  const vence = ent?.expires_date ? new Date(ent.expires_date) : null;
  if (ent && vence && vence.getTime() > Date.now()) {
    await User.updateOne({ _id: uid }, {
      isPro: true,
      subscriptionExpiresAt: vence,
      stripeSubscriptionStatus: "active",
      proOrigen: "apple",
      iapProducto: ent.product_identifier || null,
    });
    return res.json({ ok: true, isPro: true });
  }
  res.json({ ok: true, isPro: req.user.isProActive() });
}));

const ACTIVAR = new Set(["INITIAL_PURCHASE", "RENEWAL", "PRODUCT_CHANGE", "UNCANCELLATION", "NON_RENEWING_PURCHASE", "SUBSCRIPTION_EXTENDED", "TEMPORARY_ENTITLEMENT_GRANT"]);
const TERMINAR = new Set(["EXPIRATION"]);
// CANCELLATION = el usuario apagó la renovación: conserva PRO hasta que venza.
// BILLING_ISSUE = Apple reintenta el cobro (periodo de gracia): no se corta aún.

router.post("/webhook", ah(async (req, res) => {
  const secreto = process.env.REVENUECAT_WEBHOOK_SECRET;
  if (!secreto) return res.status(503).json({ error: "Webhook no configurado" });
  const auth = String(req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  if (!igual(auth, secreto)) return res.status(401).json({ error: "No autorizado" });

  const ev = req.body?.event;
  if (!ev || !ev.type) return res.status(400).json({ error: "Evento inválido" });
  if (ev.environment === "SANDBOX" && process.env.REVENUECAT_ACEPTAR_SANDBOX === "false") {
    return res.json({ ok: true, ignorado: "sandbox" });
  }

  // app_user_id es el _id de Mongo (lo fijamos al configurar el SDK).
  const ids = [ev.app_user_id, ev.original_app_user_id, ...(ev.aliases || [])].filter(Boolean);
  const uid = ids.find((x) => /^[a-f\d]{24}$/i.test(String(x)));
  if (!uid) return res.json({ ok: true, ignorado: "sin usuario" });

  const vence = ev.expiration_at_ms ? new Date(Number(ev.expiration_at_ms)) : null;
  let update = null;
  if (ACTIVAR.has(ev.type) && vence) {
    update = {
      isPro: vence.getTime() > Date.now(),
      subscriptionExpiresAt: vence,
      stripeSubscriptionStatus: "active",
      proOrigen: "apple",
      iapProducto: ev.product_id || null,
    };
  } else if (TERMINAR.has(ev.type)) {
    update = { isPro: false, stripeSubscriptionStatus: "canceled", subscriptionExpiresAt: vence || new Date() };
  } else if (ev.type === "CANCELLATION" && vence) {
    update = { stripeSubscriptionStatus: "canceled", subscriptionExpiresAt: vence };
  } else if (ev.type === "BILLING_ISSUE" && vence) {
    update = { stripeSubscriptionStatus: "past_due" };
  }
  if (update) await User.updateOne({ _id: uid, $or: [{ proOrigen: "apple" }, { isPro: { $ne: true } }, { proOrigen: null }] }, update);
  res.json({ ok: true });
}));

export default router;
