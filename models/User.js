import mongoose from "mongoose";
import crypto from "crypto";
import { promisify } from "util";

const scryptAsync = promisify(crypto.scrypt);

const userSchema = new mongoose.Schema({
  email: { type: String, required: true, unique: true, lowercase: true, trim: true, index: true },
  passwordHash: { type: String, required: true },
  salt: { type: String, required: true },
  passwordAlgo: { type: String, enum: ["pbkdf2", "scrypt"], default: "pbkdf2" },
  tokenVersion: { type: Number, default: 0 },
  isPro: { type: Boolean, default: false },
  subscriptionExpiresAt: { type: Date, default: null },
  stripeCustomerId: { type: String, default: null, index: true },
  stripeSubscriptionId: { type: String, default: null, index: true },
  stripeSubscriptionStatus: { type: String, default: null },
  analisisFecha: { type: String, default: "" },
  analisisUsados: { type: Number, default: 0 },
  createdAt: { type: Date, default: Date.now }
});

// Scrypt es asíncrono y evita bloquear el event loop. Los hashes PBKDF2
// existentes se siguen verificando y se migran al iniciar sesión.
userSchema.statics.hashPassword = async function (password) {
  const salt = crypto.randomBytes(16).toString("hex");
  const derived = await scryptAsync(password, salt, 64);
  return { salt, hash: Buffer.from(derived).toString("hex"), algo: "scrypt" };
};

userSchema.methods.verifyPassword = async function (password) {
  let candidate;
  if ((this.passwordAlgo || "pbkdf2") === "scrypt") {
    candidate = Buffer.from(await scryptAsync(password, this.salt, 64));
  } else {
    candidate = crypto.pbkdf2Sync(password, this.salt, 120000, 64, "sha512");
  }
  const stored = Buffer.from(this.passwordHash, "hex");
  return candidate.length === stored.length && crypto.timingSafeEqual(candidate, stored);
};

userSchema.methods.setPassword = async function (password) {
  const { salt, hash, algo } = await this.constructor.hashPassword(password);
  this.salt = salt;
  this.passwordHash = hash;
  this.passwordAlgo = algo;
};

userSchema.methods.isProActive = function () {
  return Boolean(this.isPro && this.subscriptionExpiresAt && this.subscriptionExpiresAt.getTime() > Date.now());
};

export default mongoose.model("User", userSchema);
