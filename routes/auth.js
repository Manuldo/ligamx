import express from "express";
import jwt from "jsonwebtoken";
import rateLimit from "express-rate-limit";
import User from "../models/User.js";
import PasswordReset from "../models/PasswordReset.js";
import { enviarCorreo, plantillaReset } from "../mailer.js";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// Rate limit agresivo en auth para frenar fuerza bruta
const authLimiter = rateLimit({
  windowMs: 15 * 60 * 1000,
  max: 10,
  message: { error: "Demasiados intentos, espera unos minutos" }
});

function signToken(user) {
  return jwt.sign({ uid: user._id.toString() }, process.env.JWT_SECRET, {
    expiresIn: "7d"
  });
}

router.post("/register", authLimiter, ah(async (req, res) => {
  const { email, password } = req.body;
  if (!email || !password || password.length < 8) {
    return res.status(400).json({ error: "Email y password (min 8) requeridos" });
  }
  const existe = await User.findOne({ email: email.toLowerCase() });
  if (existe) return res.status(409).json({ error: "Ese email ya esta registrado" });

  const { salt, hash } = User.hashPassword(password);
  const user = await User.create({ email, salt, passwordHash: hash });
  res.json({ token: signToken(user), user: { email: user.email, isPro: false } });
}));

router.post("/login", authLimiter, ah(async (req, res) => {
  const { email, password } = req.body;
  const user = await User.findOne({ email: (email || "").toLowerCase() });
  // Mensaje generico: no revelar si el email existe
  if (!user || !user.verifyPassword(password)) {
    return res.status(401).json({ error: "Credenciales invalidas" });
  }
  res.json({
    token: signToken(user),
    user: { email: user.email, isPro: user.isProActive() }
  });
}));

// ===================== RECUPERACION DE CONTRASEÑA =====================

// Limite propio, mas estricto: pedir resets es barato para un atacante
// y caro para nosotros (cada uno manda un correo).
const resetLimiter = rateLimit({
  windowMs: 60 * 60 * 1000,
  max: 5,
  message: { error: "Demasiadas solicitudes. Intenta en una hora." }
});

const MINUTOS_VIGENCIA = 30;

// --- PEDIR el enlace ---
// La respuesta es SIEMPRE la misma, exista o no la cuenta.
// Si dijeramos "ese correo no existe" estariamos regalando una lista
// de quien tiene cuenta aqui, util para spam o ataques dirigidos.
router.post("/forgot", resetLimiter, ah(async (req, res) => {
  const email = String(req.body.email || "").toLowerCase().trim();
  const respuestaNeutra = {
    ok: true,
    mensaje: "Si ese correo tiene cuenta, te enviamos el enlace para recuperarla."
  };
  if (!email || !email.includes("@")) return res.json(respuestaNeutra);

  const user = await User.findOne({ email });
  if (!user) return res.json(respuestaNeutra);

  // Un solo enlace vivo por usuario: invalidamos los anteriores.
  await PasswordReset.updateMany(
    { userId: user._id, usadoEn: null },
    { $set: { usadoEn: new Date() } }
  );

  const { token, tokenHash } = PasswordReset.generar();
  await PasswordReset.create({
    userId: user._id,
    tokenHash,
    expiraEn: new Date(Date.now() + MINUTOS_VIGENCIA * 60 * 1000)
  });

  const base = (process.env.APP_URL || "").replace(/\/$/, "");
  const enlace = `${base}/reset.html?token=${token}`;
  const { html, texto } = plantillaReset({ enlace, minutos: MINUTOS_VIGENCIA });

  const envio = await enviarCorreo({
    para: user.email,
    asunto: "Recupera tu contraseña de Pickazo",
    html, texto
  });

  if (!envio.ok) {
    // El correo fallo, pero el token existe: queda recuperable a mano
    // desde el panel admin. No se lo decimos al usuario para no revelar
    // si su cuenta existe.
    console.warn(`Reset generado sin correo (${envio.motivo}) para ${user.email}`);
  }
  res.json(respuestaNeutra);
}));

// --- VERIFICAR el token (antes de mostrar el formulario) ---
router.get("/reset/check", ah(async (req, res) => {
  const token = String(req.query.token || "");
  if (!token) return res.status(400).json({ valido: false, error: "Falta el token" });

  const doc = await PasswordReset.findOne({ tokenHash: PasswordReset.hashDe(token) });
  if (!doc || !doc.esValido()) {
    return res.status(400).json({ valido: false, error: "El enlace venció o ya se usó" });
  }
  res.json({ valido: true, minutosRestantes: Math.max(0,
    Math.round((doc.expiraEn.getTime() - Date.now()) / 60000)) });
}));

// --- CAMBIAR la contraseña ---
router.post("/reset", resetLimiter, ah(async (req, res) => {
  const { token, password } = req.body;
  if (!token || !password) return res.status(400).json({ error: "Faltan datos" });
  if (String(password).length < 8) {
    return res.status(400).json({ error: "La contraseña necesita al menos 8 caracteres" });
  }

  const doc = await PasswordReset.findOne({ tokenHash: PasswordReset.hashDe(String(token)) });
  if (!doc || !doc.esValido()) {
    return res.status(400).json({ error: "El enlace venció o ya se usó" });
  }

  const user = await User.findById(doc.userId);
  if (!user) return res.status(400).json({ error: "Cuenta no encontrada" });

  const { salt, hash } = User.hashPassword(password);
  user.salt = salt;
  user.passwordHash = hash;
  await user.save();

  // Un token, un uso.
  doc.usadoEn = new Date();
  await doc.save();

  // Damos sesion de una vez: ya probo que controla el correo.
  res.json({ ok: true, token: signToken(user),
    user: { email: user.email, isPro: user.isProActive() } });
}));

export default router;
