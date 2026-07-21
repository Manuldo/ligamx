import mongoose from "mongoose";

const playerStatSchema = new mongoose.Schema({
  torneo: { type: String, default: "Apertura 2026", index: true },
  nombre: { type: String, required: true },
  equipo: { type: String, required: true, index: true },
  abrevEquipo: { type: String, default: "" },
  posicion: { type: String, enum: ["POR", "DEF", "MED", "DEL"], default: "MED", index: true },
  dorsal: { type: Number, default: null },

  partidos: { type: Number, default: 0 },
  minutos: { type: Number, default: 0 },
  goles: { type: Number, default: 0 },
  asistencias: { type: Number, default: 0 },
  xG: { type: Number, default: 0 },
  rating: { type: Number, default: 0 },

  actualizado: { type: Date, default: Date.now }
});

playerStatSchema.index({ torneo: 1, nombre: 1, equipo: 1 }, { unique: true });
playerStatSchema.index({ torneo: 1, goles: -1 });
playerStatSchema.index({ torneo: 1, rating: -1 });

export default mongoose.model("PlayerStat", playerStatSchema);
