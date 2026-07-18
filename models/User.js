import mongoose from "mongoose";
import crypto from "crypto";

const userSchema = new mongoose.Schema({
  email: { type: String, required: true, unique: true, lowercase: true, trim: true },
  passwordHash: { type: String, required: true },
  salt: { type: String, required: true },
  isPro: { type: Boolean, default: false },
  subscriptionExpiresAt: { type: Date, default: null },
  stripeCustomerId: { type: String, default: null },
  createdAt: { type: Date, default: Date.now }
});

// --- PBKDF2 (mismo patron que usas en LingoPal) ---
userSchema.statics.hashPassword = function (password) {
  const salt = crypto.randomBytes(16).toString("hex");
  const hash = crypto.pbkdf2Sync(password, salt, 120000, 64, "sha512").toString("hex");
  return { salt, hash };
};

userSchema.methods.verifyPassword = function (password) {
  const hash = crypto
    .pbkdf2Sync(password, this.salt, 120000, 64, "sha512")
    .toString("hex");
  // comparacion en tiempo constante contra timing attacks
  const a = Buffer.from(hash, "hex");
  const b = Buffer.from(this.passwordHash, "hex");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
};

// Fuente de verdad del estado PRO: flag + vigencia.
// Un PRO vencido deja de serlo automaticamente sin correr nada.
userSchema.methods.isProActive = function () {
  if (!this.isPro) return false;
  if (!this.subscriptionExpiresAt) return false;
  return this.subscriptionExpiresAt.getTime() > Date.now();
};

export default mongoose.model("User", userSchema);
