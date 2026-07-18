import express from "express";
import jwt from "jsonwebtoken";
import rateLimit from "express-rate-limit";
import User from "../models/User.js";
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

export default router;
