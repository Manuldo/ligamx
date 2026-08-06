import mongoose from "mongoose";

const stripeEventSchema = new mongoose.Schema({
  eventId: { type: String, required: true, unique: true, index: true },
  type: { type: String, required: true },
  status: { type: String, enum: ["processing", "done"], default: "processing" },
  createdAt: { type: Date, default: Date.now, expires: 60 * 60 * 24 * 30 },
});

export default mongoose.model("StripeEvent", stripeEventSchema);
