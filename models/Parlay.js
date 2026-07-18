import mongoose from "mongoose";

const parlaySchema = new mongoose.Schema({
  fecha: { type: String, required: true, index: true },
  tier: { type: String, enum: ["public", "pro"], required: true, index: true },
  patas: [{
    partido: String,
    mercado: String,
    momio: Number,
    probEstimada: Number
  }],
  momioComb: Number,
  probConjunta: Number,
  edge: Number,
  analisisIA: { type: String, default: "" },
  veredicto: { type: String, default: "" },
  createdAt: { type: Date, default: Date.now }
});

parlaySchema.index({ fecha: 1, tier: 1 });

export default mongoose.model("Parlay", parlaySchema);
