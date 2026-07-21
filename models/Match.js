import mongoose from "mongoose";

const matchSchema = new mongoose.Schema({
  extId: { type: String, index: true },        // id de la fuente (FotMob, etc)
  fecha: { type: String, required: true, index: true }, // YYYY-MM-DD
  kickoff: { type: Date },
  local: { type: String, required: true },
  visitante: { type: String, required: true },
  golesLocal: { type: Number, default: null },
  golesVisitante: { type: Number, default: null },
  estado: { type: String, enum: ["programado", "en_juego", "finalizado"], default: "programado", index: true },
  jornada: { type: String, default: "" },
  torneo: { type: String, default: "Liga MX" },
  actualizado: { type: Date, default: Date.now }
});

matchSchema.index({ fecha: 1, estado: 1 });
matchSchema.index({ local: 1, visitante: 1, fecha: 1 }, { unique: true });

export default mongoose.model("Match", matchSchema);
