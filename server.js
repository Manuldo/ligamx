import express from "express";
import helmet from "helmet";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { randomUUID } from "crypto";
import { fileURLToPath } from "url";
import path from "path";
import mongoose from "mongoose";

// Carga .env antes de leer variables dentro de cualquier función.
dotenv.config();

import { connectDB } from "./db.js";
import authRoutes from "./routes/auth.js";
import pickRoutes from "./routes/picks.js";
import adminRoutes from "./routes/admin.js";
import adminProRoutes from "./routes/admin-pro.js";
import paymentRoutes from "./routes/payments.js";
import parlayRoutes from "./routes/parlays.js";
import matchRoutes from "./routes/matches.js";
import historyRoutes from "./routes/history.js";
import recordRoutes from "./routes/record.js";
import leagueRoutes from "./routes/league.js";
import motorRoutes from "./routes/motor.js";
import { motorVivo } from "./lib/motor.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const isProduction = process.env.NODE_ENV === "production";

const CRITICAS = ["MONGODB_URI", "JWT_SECRET", "ADMIN_KEY"];
if (isProduction) CRITICAS.push("APP_URL", "MOTOR_URL", "MOTOR_KEY");
const faltantes = CRITICAS.filter((k) => !process.env[k]);
if (faltantes.length) {
  console.error("Faltan variables de entorno críticas:", faltantes.join(", "));
  process.exit(1);
}
if (isProduction) {
  const secretosDebiles = ["JWT_SECRET", "ADMIN_KEY", "MOTOR_KEY"]
    .filter((k) => String(process.env[k] || "").length < 32);
  if (secretosDebiles.length) {
    console.error("Secretos demasiado cortos (mínimo 32 caracteres):", secretosDebiles.join(", "));
    process.exit(1);
  }
}
if (process.env.STRIPE_SECRET_KEY && !process.env.STRIPE_PRICE_ID) {
  console.error("STRIPE_SECRET_KEY está definida, pero falta STRIPE_PRICE_ID");
  process.exit(1);
}

// Railway/Cloudflare colocan un proxy delante de Express.
if (isProduction) app.set("trust proxy", Number(process.env.TRUST_PROXY_HOPS || 1));
app.disable("x-powered-by");

// ID para relacionar logs del navegador, Node y el motor Python.
app.use((req, res, next) => {
  req.id = req.headers["x-request-id"] || randomUUID();
  res.setHeader("X-Request-Id", req.id);
  next();
});

// La app todavía usa CSS/JS inline. Se activa CSP sin romperla; la siguiente
// etapa debe moverlos a archivos y retirar 'unsafe-inline'.
app.use(helmet({
  contentSecurityPolicy: {
    directives: {
      defaultSrc: ["'self'"],
      scriptSrc: ["'self'", "'unsafe-inline'"],
      styleSrc: ["'self'", "'unsafe-inline'", "https://fonts.googleapis.com"],
      fontSrc: ["'self'", "https://fonts.gstatic.com", "data:"],
      imgSrc: ["'self'", "data:", "https:"],
      mediaSrc: ["'self'"],
      connectSrc: ["'self'"],
      frameAncestors: ["'none'"],
      objectSrc: ["'none'"],
      upgradeInsecureRequests: isProduction ? [] : null,
    },
  },
  crossOriginResourcePolicy: { policy: "same-site" },
}));

const allowedOrigins = String(process.env.APP_URL || "")
  .split(",")
  .map((s) => s.trim().replace(/\/$/, ""))
  .filter(Boolean);

app.use(cors({
  origin(origin, callback) {
    // Requests same-origin y llamadas servidor-a-servidor normalmente no traen Origin.
    if (!origin) return callback(null, true);
    const normalized = origin.replace(/\/$/, "");
    if (!isProduction && /^http:\/\/localhost:\d+$/.test(normalized)) {
      return callback(null, true);
    }
    return callback(null, allowedOrigins.includes(normalized));
  },
  methods: ["GET", "POST", "OPTIONS"],
  allowedHeaders: ["Content-Type", "Authorization", "X-Admin-Key", "X-Request-Id"],
  maxAge: 86400,
}));

// Stripe necesita el body crudo antes de express.json().
app.use("/api/payments/webhook", express.raw({ type: "application/json", limit: "256kb" }));
app.use(express.json({ limit: "128kb" }));

app.use(rateLimit({
  windowMs: 60 * 1000,
  max: Number(process.env.RATE_LIMIT_PER_MINUTE || 120),
  standardHeaders: "draft-7",
  legacyHeaders: false,
  message: { error: "Demasiadas solicitudes. Intenta de nuevo en un momento." },
}));

app.get("/health/live", (req, res) => res.json({ ok: true, requestId: req.id }));
app.get("/health/ready", async (req, res) => {
  const mongo = mongoose.connection.readyState === 1;
  const motor = await motorVivo();
  const ok = mongo && motor;
  res.status(ok ? 200 : 503).json({ ok, mongo, motor, requestId: req.id });
});

app.use("/api/auth", authRoutes);
app.use("/api/picks", pickRoutes);
app.use("/api/admin", adminRoutes);
app.use("/api/admin", adminProRoutes);
app.use("/api/payments", paymentRoutes);
app.use("/api/parlays", parlayRoutes);
app.use("/api/matches", matchRoutes);
app.use("/api/history", historyRoutes);
app.use("/api/record", recordRoutes);
app.use("/api/league", leagueRoutes);
app.use("/api/motor", motorRoutes);

app.use(express.static(path.join(__dirname, "public"), {
  etag: true,
  lastModified: true,
  setHeaders(res, filePath) {
    if (/\.html$/i.test(filePath)) {
      res.setHeader("Cache-Control", "no-cache");
    } else if (/\.(?:png|jpg|jpeg|webp|svg|ico|mp4|webm)$/i.test(filePath)) {
      res.setHeader("Cache-Control", "public, max-age=604800, immutable");
    } else {
      res.setHeader("Cache-Control", "public, max-age=3600");
    }
  },
}));

app.use((req, res) => res.status(404).json({ error: "Ruta no encontrada", requestId: req.id }));

app.use((err, req, res, next) => {
  console.error(JSON.stringify({
    level: "error",
    requestId: req.id,
    method: req.method,
    path: req.originalUrl,
    message: err?.message || String(err),
    stack: isProduction ? undefined : err?.stack,
  }));
  if (res.headersSent) return next(err);
  res.status(err?.status || 500).json({ error: "Error interno", requestId: req.id });
});

for (const [k, msg] of Object.entries({
  STRIPE_SECRET_KEY: "pagos deshabilitados",
  STRIPE_WEBHOOK_SECRET: "webhook de Stripe deshabilitado",
  ANTHROPIC_API_KEY: "análisis IA opcional deshabilitado",
})) {
  if (!process.env[k]) console.warn(`Aviso: falta ${k} -> ${msg}`);
}

process.on("unhandledRejection", (err) => console.error("unhandledRejection:", err));
process.on("uncaughtException", (err) => {
  console.error("uncaughtException:", err);
  process.exit(1);
});

const PORT = Number(process.env.PORT || 3000);
connectDB().then(() => {
  app.listen(PORT, () => console.log(`Servidor en puerto ${PORT}`));
});
