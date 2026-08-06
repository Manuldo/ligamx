import crypto from "crypto";
import jwt from "jsonwebtoken";
import User from "../models/User.js";

export async function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: "No autenticado" });

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET, {
      issuer: "pickazoapp",
      audience: "pickazoapp-web",
    });
    const user = await User.findById(payload.uid);
    if (!user || Number(payload.ver || 0) !== Number(user.tokenVersion || 0)) {
      return res.status(401).json({ error: "Sesión inválida" });
    }
    req.user = user;
    next();
  } catch {
    return res.status(401).json({ error: "Token inválido o expirado" });
  }
}

export function requirePro(req, res, next) {
  if (!req.user || !req.user.isProActive()) {
    return res.status(403).json({ error: "Requiere suscripción PRO activa" });
  }
  next();
}

function safeEqualSecret(received, expected) {
  if (!received || !expected) return false;
  const a = crypto.createHash("sha256").update(String(received)).digest();
  const b = crypto.createHash("sha256").update(String(expected)).digest();
  return crypto.timingSafeEqual(a, b);
}

export function requireAdmin(req, res, next) {
  if (!safeEqualSecret(req.headers["x-admin-key"], process.env.ADMIN_KEY)) {
    return res.status(403).json({ error: "No autorizado" });
  }
  next();
}
