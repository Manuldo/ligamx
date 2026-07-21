import mongoose from "mongoose";

const pickSchema = new mongoose.Schema({
  fecha: { type: String, required: true, index: true }, // YYYY-MM-DD
  partido: { type: String, required: true },             // "America vs Chivas"
  mercado: { type: String, required: true },             // "Over 2.5", "1X2: Local", etc.
  momio: { type: Number, required: true },               // decimal, piso 1.70
  probEstimada: { type: Number, required: true },        // 0-1, tu Poisson
  probImplicita: { type: Number, required: true },       // 1/momio
  edge: { type: Number, required: true },                // probEstimada - probImplicita
  verdicto: { type: String, enum: ["MANDAR", "RESERVAS", "EVITAR"], default: "MANDAR" },
  analisis: { type: String, default: "" },               // texto del analisis
  tier: { type: String, enum: ["public", "pro"], required: true, index: true },
  votos: { type: Number, default: 0 },                   // contador denormalizado
  activo: { type: Boolean, default: true },
  resultado: { type: String, enum: ["pendiente","acierto","fallo","nulo"], default: "pendiente" },
  createdAt: { type: Date, default: Date.now }
});

// Para rankear People's Choice rapido
pickSchema.index({ fecha: 1, tier: 1, votos: -1 });

export default mongoose.model("Pick", pickSchema);
