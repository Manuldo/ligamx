import mongoose from "mongoose";

// Ajustes simples clave/valor que el admin cambia sin redeploy
// (p. ej. el link del grupo de WhatsApp de cada mes).
const settingSchema = new mongoose.Schema({
  key: { type: String, required: true, unique: true, index: true },
  value: { type: mongoose.Schema.Types.Mixed, default: null },
  updatedAt: { type: Date, default: Date.now },
});

settingSchema.statics.get = async function (key, fallback = null) {
  const doc = await this.findOne({ key }).lean();
  return doc ? doc.value : fallback;
};

settingSchema.statics.set = async function (key, value) {
  await this.updateOne({ key }, { $set: { value, updatedAt: new Date() } }, { upsert: true });
};

export default mongoose.model("Setting", settingSchema);
