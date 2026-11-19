import mongoose from "mongoose";

// Token de dispositivo (APNs) por usuario. Un usuario puede tener varios
// dispositivos; el token es único.
const pushTokenSchema = new mongoose.Schema({
  user: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
  token: { type: String, required: true, unique: true },
  plataforma: { type: String, enum: ["ios"], default: "ios" },
  creado: { type: Date, default: Date.now },
  ultimoUso: { type: Date, default: Date.now }
});

export default mongoose.model("PushToken", pushTokenSchema);
