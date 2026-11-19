import mongoose from "mongoose";

// Partido que un usuario eligió seguir para recibir un aviso push antes
// de que empiece (para ir a hacer el análisis).
const schema = new mongoose.Schema({
  user: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true, index: true },
  matchId: { type: String, required: true },      // id del motor: "liga:local:visitante"
  liga: { type: String, default: "" },
  local: { type: String, default: "" },
  visitante: { type: String, default: "" },
  kickoff: { type: Date, required: true, index: true },
  notificado: { type: Boolean, default: false, index: true },
  creado: { type: Date, default: Date.now }
});
schema.index({ user: 1, matchId: 1 }, { unique: true });

export default mongoose.model("PartidoSeguido", schema);
