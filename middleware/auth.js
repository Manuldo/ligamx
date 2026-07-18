import jwt from "jsonwebtoken";
import User from "../models/User.js";

// Verifica el JWT y carga el usuario fresco desde la DB.
// NO confiamos en el token para saber si es PRO: el token puede
// estar desactualizado (suscripcion vencida). Siempre leemos la DB.
export async function requireAuth(req, res, next) {
  const header = req.headers.authorization || "";
  const token = header.startsWith("Bearer ") ? header.slice(7) : null;
  if (!token) return res.status(401).json({ error: "No autenticado" });

  try {
    const payload = jwt.verify(token, process.env.JWT_SECRET);
    const user = await User.findById(payload.uid);
    if (!user) return res.status(401).json({ error: "Sesion invalida" });
    req.user = user;
    next();
  } catch {
    return res.status(401).json({ error: "Token invalido o expirado" });
  }
}

// Muro PRO real: si el usuario no tiene suscripcion activa, corta aqui.
// El pick PRO NUNCA llega a la respuesta si no pasa este filtro.
export function requirePro(req, res, next) {
  if (!req.user || !req.user.isProActive()) {
    return res.status(403).json({ error: "Requiere suscripcion PRO activa" });
  }
  next();
}

// Solo admin (tu) para disparar el analisis diario.
export function requireAdmin(req, res, next) {
  const key = req.headers["x-admin-key"];
  if (!key || key !== process.env.ADMIN_KEY) {
    return res.status(403).json({ error: "No autorizado" });
  }
  next();
}
