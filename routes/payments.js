import express from "express";
import Stripe from "stripe";
import User from "../models/User.js";
import StripeEvent from "../models/StripeEvent.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

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

async function syncSubscription(stripe, subscriptionId, fallbackUserId = null) {
  if (!subscriptionId) return;
  const subscription = typeof subscriptionId === "string"
    ? await stripe.subscriptions.retrieve(subscriptionId)
    : subscriptionId;

  const customerId = typeof subscription.customer === "string"
    ? subscription.customer
    : subscription.customer?.id;
  const periodEnd = subscription.current_period_end
    ? new Date(subscription.current_period_end * 1000)
    : null;
  const activeStatuses = new Set(["active", "trialing"]);
  const update = {
    stripeCustomerId: customerId || null,
    stripeSubscriptionId: subscription.id,
    stripeSubscriptionStatus: subscription.status,
    subscriptionExpiresAt: periodEnd,
    isPro: activeStatuses.has(subscription.status) && Boolean(periodEnd && periodEnd.getTime() > Date.now()),
  };

  if (fallbackUserId) {
    await User.findByIdAndUpdate(fallbackUserId, update);
  } else if (customerId) {
    await User.findOneAndUpdate({ stripeCustomerId: customerId }, update);
  }
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

  try {
    const obj = event.data.object;
    switch (event.type) {
      case "checkout.session.completed": {
        if (obj.mode === "subscription" && obj.subscription) {
          await syncSubscription(stripe, obj.subscription, obj.metadata?.uid || obj.client_reference_id);
        }
        break;
      }
      case "customer.subscription.created":
      case "customer.subscription.updated":
      case "customer.subscription.deleted":
        await syncSubscription(stripe, obj);
        break;
      case "invoice.paid":
      case "invoice.payment_failed": {
        const subscriptionId = obj.subscription || obj.parent?.subscription_details?.subscription;
        if (subscriptionId) await syncSubscription(stripe, subscriptionId);
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
}));

export default router;
