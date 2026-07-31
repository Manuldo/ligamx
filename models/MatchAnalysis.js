import mongoose from "mongoose";
// Analisis generado por el motor para un partido, a peticion de un PRO.
// Se cachea por partido+fecha: si otro PRO pide el mismo el mismo dia, se reusa.
const matchAnalysisSchema = new mongoose.Schema({
  matchId: { type: mongoose.Schema.Types.ObjectId, ref: "Match", default: null },
  fecha: { type: String, required: true, index: true },
  partido: { type: String, required: true, index: true },  // "liga:local:visitante"
  sugerencias: [{
    mercado: String,
    momioSugerido: Number,
    probEstimada: Number,
    razon: String
  }],
  resumen: { type: String, default: "" },
  analisisMarkdown: { type: String, default: "" },  // el análisis completo del motor
  nivel: { type: String, default: "premium" },       // basico | premium
  aviso: { type: String, default: "" },
  generadoPor: { type: mongoose.Schema.Types.ObjectId, ref: "User" },
  createdAt: { type: Date, default: Date.now }
});
// Cache por partido + fecha (ya no por matchId, porque los datos vienen del motor)
matchAnalysisSchema.index({ partido: 1, fecha: 1 }, { unique: true });
export default mongoose.model("MatchAnalysis", matchAnalysisSchema);
