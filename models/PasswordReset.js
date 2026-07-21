import mongoose from "mongoose";
import crypto from "crypto";

// Token de recuperacion de contraseña.
//
// Guardamos SOLO el hash, nunca el token en claro. Si alguien lee la base
// de datos, no puede reconstruir el enlace y tomar cuentas ajenas.
// Es el mismo criterio que usamos con las contraseñas.
const resetSchema = new mongoose.Schema({
  userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
  tokenHash: { type: String, required: true, index: true },
  expiraEn: { type: Date, required: true },
  usadoEn: { type: Date, default: null },
  createdAt: { type: Date, default: Date.now }
});

// Mongo borra solo los documentos vencidos: no acumulamos tokens muertos.
resetSchema.index({ expiraEn: 1 }, { expireAfterSeconds: 0 });

// Genera el par (token en claro para el enlace, hash para guardar).
resetSchema.statics.generar = function () {
  const token = crypto.randomBytes(32).toString("hex");
  const tokenHash = crypto.createHash("sha256").update(token).digest("hex");
  return { token, tokenHash };
};

resetSchema.statics.hashDe = function (token) {
  return crypto.createHash("sha256").update(token).digest("hex");
};

resetSchema.methods.esValido = function () {
  return !this.usadoEn && this.expiraEn.getTime() > Date.now();
};

export default mongoose.model("PasswordReset", resetSchema);
