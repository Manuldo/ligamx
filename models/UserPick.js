import mongoose from "mongoose";

// Registro personal: el usuario marca que metio este pick en SU casa
// de apuestas. Nosotros no vemos ni tocamos la apuesta real, solo el registro.
const userPickSchema = new mongoose.Schema({
  userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
  pickId: { type: mongoose.Schema.Types.ObjectId, ref: "Pick", default: null },
  parlayId: { type: mongoose.Schema.Types.ObjectId, ref: "Parlay", default: null },

  // Copia congelada al momento de registrar: si el pick cambia despues,
  // el historial del usuario conserva lo que el vio.
  snapshot: {
    partido: String,
    mercado: String,
    momio: Number,
    probEstimada: Number,
    edge: Number,
    tipo: { type: String, enum: ["pick", "parlay"], default: "pick" }
  },

  monto: { type: Number, default: null },   // opcional, lo pone el usuario
  fecha: { type: String, required: true, index: true },
  resultado: { type: String, enum: ["pendiente", "acierto", "fallo", "nulo"], default: "pendiente", index: true },
  cerradoAt: { type: Date, default: null },
  createdAt: { type: Date, default: Date.now }
});

// Un usuario no registra dos veces el mismo pick
userPickSchema.index({ userId: 1, pickId: 1 }, { unique: true, sparse: true });
userPickSchema.index({ userId: 1, parlayId: 1 }, { unique: true, sparse: true });

export default mongoose.model("UserPick", userPickSchema);
