import express from "express";
import Stripe from "stripe";
import User from "../models/User.js";
import { requireAuth } from "../middleware/auth.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Inicializacion perezosa: si no hay llave, el server arranca igual
// y solo fallan los endpoints de pago. No tumba el proceso.
let _stripe = null;
function getStripe() {
  if (!process.env.STRIPE_SECRET_KEY) return null;
  if (!_stripe) _stripe = new Stripe(process.env.STRIPE_SECRET_KEY);
  return _stripe;
}

// Crea sesion de checkout. Stripe MX soporta tarjeta + OXXO + SPEI
// habilitandolos en el dashboard de Stripe (payment_method_types).
router.post("/checkout", requireAuth, ah(async (req, res) => {
  const stripe = getStripe();
  if (!stripe) {
    return res.status(503).json({ error: "Pagos no configurados todavia" });
  }
  const session = await stripe.checkout.sessions.create({
    mode: "subscription",
    payment_method_types: ["card", "oxxo"], // SPEI/OXXO se habilitan en dashboard
    line_items: [{ price: process.env.STRIPE_PRICE_ID, quantity: 1 }],
    customer_email: req.user.email,
    success_url: `${process.env.APP_URL}/?pro=ok`,
    cancel_url: `${process.env.APP_URL}/?pro=cancel`,
    metadata: { uid: req.user._id.toString() }
  });
  res.json({ url: session.url });
}));

// Webhook: la unica fuente de verdad del estado de pago.
// Sincroniza isPro + vigencia con lo que dice Stripe.
// NOTA: este endpoint usa raw body (ver server.js).
router.post("/webhook", ah(async (req, res) => {
  const stripe = getStripe();
  if (!stripe || !process.env.STRIPE_WEBHOOK_SECRET) {
    return res.status(503).json({ error: "Webhook no configurado" });
  }
  const sig = req.headers["stripe-signature"];
  let event;
  try {
    event = stripe.webhooks.constructEvent(
      req.body,
      sig,
      process.env.STRIPE_WEBHOOK_SECRET
    );
  } catch (err) {
    return res.status(400).send(`Webhook error: ${err.message}`);
  }

  switch (event.type) {
    case "checkout.session.completed": {
      const s = event.data.object;
      const uid = s.metadata?.uid;
      if (uid) {
        await User.findByIdAndUpdate(uid, {
          isPro: true,
          stripeCustomerId: s.customer,
          subscriptionExpiresAt: new Date(Date.now() + 31 * 24 * 60 * 60 * 1000)
        });
      }
      break;
    }
    case "invoice.paid": {
      // Renovacion: extiende vigencia
      const inv = event.data.object;
      await User.findOneAndUpdate(
        { stripeCustomerId: inv.customer },
        {
          isPro: true,
          subscriptionExpiresAt: new Date(Date.now() + 31 * 24 * 60 * 60 * 1000)
        }
      );
      break;
    }
    case "customer.subscription.deleted":
    case "invoice.payment_failed": {
      // Dejo de pagar -> deja de ser PRO. Acceso cortado automatico.
      const obj = event.data.object;
      await User.findOneAndUpdate(
        { stripeCustomerId: obj.customer },
        { isPro: false, subscriptionExpiresAt: null }
      );
      break;
    }
  }

  res.json({ received: true });
}));

export default router;
