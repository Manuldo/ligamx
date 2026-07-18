import mongoose from "mongoose";

const voteSchema = new mongoose.Schema({
  userId: { type: mongoose.Schema.Types.ObjectId, ref: "User", required: true },
  pickId: { type: mongoose.Schema.Types.ObjectId, ref: "Pick", required: true },
  createdAt: { type: Date, default: Date.now }
});

// La garantia a nivel de base de datos: un usuario, un voto por pick.
// Aunque el frontend haga trampa, Mongo rechaza el duplicado.
voteSchema.index({ userId: 1, pickId: 1 }, { unique: true });

export default mongoose.model("Vote", voteSchema);
