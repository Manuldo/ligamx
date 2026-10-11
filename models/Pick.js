import mongoose from "mongoose";

const pickSchema = new mongoose.Schema({
  fecha: { type: String, required: true, index: true }, // YYYY-MM-DD
  partido: { type: String, required: true },             // "America vs Chivas"
  mercado: { type: String, required: true },             // "Over 2.5", "1X2: Local", etc.
  momio: { type: Number, required: true },               // decimal
  probEstimada: { type: Number, default: null },         // 0-1, opcional (pick manual puede no traerla)
  probImplicita: { type: Number, default: null },        // 1/momio
  edge: { type: Number, default: null },                 // probEstimada - probImplicita (null si no hay probEstimada)
  verdicto: { type: String, enum: ["MANDAR", "RESERVAS", "EVITAR"], default: "MANDAR" },
  analisis: { type: String, default: "" },               // texto del analisis
  tier: { type: String, enum: ["public", "pro"], required: true, index: true },
  pickLock: { type: Boolean, default: false, index: true }, // la jugada mas viable del dia (mayor edge, ya con momio real/estimado)
  votos: { type: Number, default: 0 },                   // contador denormalizado
  activo: { type: Boolean, default: true },
  resultado: { type: String, enum: ["pendiente","acierto","fallo","nulo"], default: "pendiente" },
  createdAt: { type: Date, default: Date.now }
});

// Para rankear People's Choice rapido
pickSchema.index({ fecha: 1, tier: 1, votos: -1 });

export default mongoose.model("Pick", pickSchema);
