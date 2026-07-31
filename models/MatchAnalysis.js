import mongoose from "mongoose";
// Analisis generado por IA para un partido concreto, a peticion de un PRO.
// Se cachea por partido: si otro PRO pide el mismo partido el mismo dia,
// se reusa y no se gasta otra llamada a la API.
const matchAnalysisSchema = new mongoose.Schema({
  matchId: { type: mongoose.Schema.Types.ObjectId, ref: "Match", required: true, index: true },
  fecha: { type: String, required: true, index: true },
  partido: { type: String, required: true },
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
matchAnalysisSchema.index({ matchId: 1, fecha: 1 }, { unique: true });
export default mongoose.model("MatchAnalysis", matchAnalysisSchema);
 
