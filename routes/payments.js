import express from "express";
import Stripe from "stripe";
import User from "../models/User.js";
import StripeEvent from "../models/StripeEvent.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";
import {
  enviarCorreo,
  correoBienvenidaPro,
  correoPagoFallido,
  correoCancelacionProgramada,
  correoProTerminado,
} from "../mailer.js";

const router = express.Router();
let _stripe = null;

function getStripe() {
  if (!process.env.STRIPE_SECRET_KEY) return null;
  if (!_stripe) _stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
  return _stripe;
}

function appUrl() {
  const value = String(process.env.APP_URL || "").split(",")[0].trim().replace(/\/$/, "");
  if (!value) throw new Error("Falta APP_URL");
  return value;
}

async function syncSubscription(stripe, subscriptionRef, fallbackUserId = null) {
  if (!subscriptionRef) return;
  // Siempre se consulta el estado actual en Stripe (con la versión de API del SDK).
  // Así no dependemos de la versión de API configurada en el webhook (desde
  // 2025-03-31.basil, current_period_end ya no viene en la raíz de la suscripción)
  // y un evento viejo que llegue tarde no pisa un estado más nuevo.
  const subscriptionId = typeof subscriptionRef === "string" ? subscriptionRef : subscriptionRef.id;
  const subscription = await stripe.subscriptions.retrieve(subscriptionId);

  const customerId = typeof subscription.customer === "string"
    ? subscription.customer
    : subscription.customer?.id;
  const periodEndUnix = subscription.current_period_end
    ?? subscription.items?.data?.[0]?.current_period_end;
  const periodEnd = periodEndUnix ? new Date(periodEndUnix * 1000) : null;
  const activeStatuses = new Set(["active", "trialing"]);
  const update = {
    stripeCustomerId: customerId || null,
    stripeSubscriptionId: subscription.id,
    stripeSubscriptionStatus: subscription.status,
    subscriptionExpiresAt: periodEnd,
    isPro: activeStatuses.has(subscription.status) && Boolean(periodEnd && periodEnd.getTime() > Date.now()),
  };

  // Si el evento no trae el usuario (customer.subscription.*, invoice.*), lo
  // sacamos de la metadata que el checkout guarda en la suscripción. Así el PRO
  // se activa aunque checkout.session.completed no llegue o llegue después.
  if (!fallbackUserId) fallbackUserId = subscription.metadata?.uid || null;

  let user = null;
  if (fallbackUserId) {
    user = await User.findByIdAndUpdate(fallbackUserId, update, { new: true });
  } else if (customerId) {
    user = await User.findOneAndUpdate({ stripeCustomerId: customerId }, update, { new: true });
  }
  return { user, subscription, periodEnd };
}

function precioRecurrente(subscription) {
  const price = subscription?.items?.data?.[0]?.price;
  return { monto: price?.unit_amount ?? 0, moneda: price?.currency || "mxn" };
}

// Fecha en que termina una suscripción con cancelación programada.
function finProgramado(subscription, periodEnd) {
  if (subscription?.cancel_at) return new Date(subscription.cancel_at * 1000);
  return periodEnd;
}

router.post("/checkout", requireAuth, ah(async (req, res) => {
  const stripe = getStripe();
  if (!stripe) return res.status(503).json({ error: "Pagos no configurados todavía" });

  if (req.user.isProActive() || (req.user.stripeSubscriptionId && !["canceled", "incomplete_expired"].includes(req.user.stripeSubscriptionStatus))) {
    return res.status(409).json({ error: "Ya existe una suscripción. Usa el portal para administrarla." });
  }

  const params = {
    mode: "subscription",
    payment_method_types: ["card"],
    line_items: [{ price: process.env.STRIPE_PRICE_ID, quantity: 1 }],
    success_url: `${appUrl()}/?pro=ok&session_id={CHECKOUT_SESSION_ID}`,
    cancel_url: `${appUrl()}/?pro=cancel`,
    allow_promotion_codes: true,
    client_reference_id: req.user._id.toString(),
    metadata: { uid: req.user._id.toString() },
    subscription_data: { metadata: { uid: req.user._id.toString() } },
  };
  if (req.user.stripeCustomerId) params.customer = req.user.stripeCustomerId;
  else params.customer_email = req.user.email;

  const session = await stripe.checkout.sessions.create(params, {
    idempotencyKey: `checkout:${req.user._id}:${process.env.STRIPE_PRICE_ID}:${new Date().toISOString().slice(0, 13)}`,
  });
  res.json({ url: session.url });
}));

router.post("/portal", requireAuth, ah(async (req, res) => {
  const stripe = getStripe();
  if (!stripe) return res.status(503).json({ error: "Pagos no configurados todavía" });
  if (!req.user.stripeCustomerId) {
    return res.status(400).json({ error: "La cuenta todavía no tiene una suscripción en Stripe" });
  }
  const session = await stripe.billingPortal.sessions.create({
    customer: req.user.stripeCustomerId,
    return_url: appUrl(),
  });
  res.json({ url: session.url });
}));

router.post("/webhook", ah(async (req, res) => {
  const stripe = getStripe();
  if (!stripe || !process.env.STRIPE_WEBHOOK_SECRET) {
    return res.status(503).json({ error: "Webhook no configurado" });
  }

  let event;
  try {
    event = stripe.webhooks.constructEvent(
      req.body,
      req.headers["stripe-signature"],
      process.env.STRIPE_WEBHOOK_SECRET
    );
  } catch (err) {
    return res.status(400).send(`Webhook error: ${err.message}`);
  }

  try {
    await StripeEvent.create({ eventId: event.id, type: event.type, status: "processing" });
  } catch (error) {
    if (error?.code === 11000) return res.json({ received: true, duplicate: true });
    throw error;
  }

  // Los correos se mandan hasta que el evento quedó procesado, para que un
  // reintento de Stripe no los duplique, y nunca hacen fallar el webhook.
  const correos = [];
  let base = null;
  try { base = appUrl(); } catch { base = null; }

  try {
    const obj = event.data.object;
    switch (event.type) {
      case "checkout.session.completed": {
        if (obj.mode === "subscription" && obj.subscription) {
          const { user, subscription, periodEnd } =
            await syncSubscription(stripe, obj.subscription, obj.metadata?.uid || obj.client_reference_id);
          if (user?.email && base) {
            const { monto, moneda } = precioRecurrente(subscription);
            correos.push({ para: user.email, ...correoBienvenidaPro({
              appUrl: base,
              montoHoy: obj.amount_total ?? 0,
              montoRecurrente: monto,
              moneda: obj.currency || moneda,
              proximoCobro: periodEnd,
            }) });
          }
        }
        break;
      }
      case "customer.subscription.created":
        await syncSubscription(stripe, obj);
        break;
      case "customer.subscription.updated": {
        const { user, subscription, periodEnd } = await syncSubscription(stripe, obj);
        const prev = event.data.previous_attributes || {};
        const cambioCancelacion = "cancel_at_period_end" in prev || "cancel_at" in prev;
        const antesProgramada = Boolean(prev.cancel_at_period_end || prev.cancel_at);
        const ahoraProgramada = Boolean(subscription.cancel_at_period_end || subscription.cancel_at);
        if (cambioCancelacion && !antesProgramada && ahoraProgramada
            && subscription.status !== "canceled" && user?.email && base) {
          correos.push({ para: user.email, ...correoCancelacionProgramada({
            appUrl: base, terminaEl: finProgramado(subscription, periodEnd),
          }) });
        }
        break;
      }
      case "customer.subscription.deleted": {
        const { user } = await syncSubscription(stripe, obj);
        if (user?.email && base) {
          const motivo = obj.cancellation_details?.reason;
          correos.push({ para: user.email, ...correoProTerminado({
            appUrl: base, porPagoFallido: motivo === "payment_failed",
          }) });
        }
        break;
      }
      case "invoice.paid": {
        const subscriptionId = obj.subscription || obj.parent?.subscription_details?.subscription;
        if (subscriptionId) await syncSubscription(stripe, subscriptionId);
        break;
      }
      case "invoice.payment_failed": {
        const subscriptionId = obj.subscription || obj.parent?.subscription_details?.subscription;
        if (subscriptionId) {
          const { user } = await syncSubscription(stripe, subscriptionId);
          // Solo en el primer rechazo y en el último intento, para no saturar.
          const primero = Number(obj.attempt_count) <= 1;
          const ultimo = !obj.next_payment_attempt;
          if ((primero || ultimo) && user?.email && base) {
            correos.push({ para: user.email, ...correoPagoFallido({
              appUrl: base,
              monto: obj.amount_due,
              moneda: obj.currency,
              siguienteIntento: obj.next_payment_attempt ? new Date(obj.next_payment_attempt * 1000) : null,
            }) });
          }
        }
        break;
      }
      default:
        break;
    }
    await StripeEvent.updateOne({ eventId: event.id }, { $set: { status: "done" } });
  } catch (error) {
    // Permite que Stripe reintente un evento que no se procesó por completo.
    await StripeEvent.deleteOne({ eventId: event.id });
    throw error;
  }

  res.json({ received: true });

  for (const c of correos) {
    enviarCorreo(c).then((r) => {
      if (!r.ok) console.warn(`Correo "${c.asunto}" no enviado (${r.motivo}) a ${c.para}`);
    }).catch((err) => console.error("Error de correo:", err?.message));
  }
}));

export default router;
