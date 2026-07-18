import mongoose from "mongoose";

export async function connectDB() {
  const uri = process.env.MONGODB_URI;
  if (!uri) {
    console.error("Falta MONGODB_URI en variables de entorno");
    process.exit(1);
  }
  try {
    await mongoose.connect(uri);
    console.log("MongoDB Atlas conectado");
  } catch (err) {
    console.error("Error conectando a MongoDB:", err.message);
    process.exit(1);
  }
}
