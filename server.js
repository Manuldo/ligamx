import express from "express";
import helmet from "helmet";
import cors from "cors";
import rateLimit from "express-rate-limit";
import dotenv from "dotenv";
import { fileURLToPath } from "url";
import path from "path";

import { connectDB } from "./db.js";
import authRoutes from "./routes/auth.js";
import pickRoutes from "./routes/picks.js";
import adminRoutes from "./routes/admin.js";
import paymentRoutes from "./routes/payments.js";
import parlayRoutes from "./routes/parlays.js";

dotenv.config();
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();

app.use(helmet({ contentSecurityPolicy: false })); // CSP off para HTML inline
app.use(cors({ origin: process.env.APP_URL || true }));

// El webhook de Stripe necesita el body crudo, ANTES de express.json
app.use("/api/payments/webhook", express.raw({ type: "application/json" }));
app.use(express.json());

// Rate limit global
app.use(rateLimit({ windowMs: 60 * 1000, max: 100 }));

app.use("/api/auth", authRoutes);
app.use("/api/picks", pickRoutes);
app.use("/api/admin", adminRoutes);
app.use("/api/payments", paymentRoutes);
app.use("/api/parlays", parlayRoutes);

app.use(express.static(path.join(__dirname, "public"), { setHeaders: (r) => r.setHeader("Cache-Control", "no-store") }));

// Manejo de errores async
app.use((err, req, res, next) => {
  console.error(err);
  res.status(500).json({ error: "Error interno" });
});

const PORT = process.env.PORT || 3000;
connectDB().then(() => {
  app.listen(PORT, () => console.log(`Servidor en puerto ${PORT}`));
});
