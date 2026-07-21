import mongoose from "mongoose";

const standingSchema = new mongoose.Schema({
  torneo: { type: String, default: "Apertura 2026", index: true },
  equipo: { type: String, required: true },
  abrev: { type: String, default: "" },        // CRU, AME, TIG
  color: { type: String, default: "#605d5d" }, // color del escudo simulado
  pos: { type: Number, required: true },
  jugados: { type: Number, default: 0 },
  ganados: { type: Number, default: 0 },
  empatados: { type: Number, default: 0 },
  perdidos: { type: Number, default: 0 },
  golesFavor: { type: Number, default: 0 },
  golesContra: { type: Number, default: 0 },
  dif: { type: Number, default: 0 },
  puntos: { type: Number, default: 0 },
  forma: [{ type: String }],                    // ["G","P","E",...] mas reciente primero
  actualizado: { type: Date, default: Date.now }
});

standingSchema.index({ torneo: 1, equipo: 1 }, { unique: true });
standingSchema.index({ torneo: 1, pos: 1 });

export default mongoose.model("Standing", standingSchema);
