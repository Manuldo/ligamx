import PartidoSeguido from "../models/PartidoSeguido.js";
import PushToken from "../models/PushToken.js";
import { enviarPush, apnsConfigurado } from "./apns.js";

// Cada minuto busca partidos seguidos que empiezan en la próxima hora y manda
// el push una sola vez. "Reclamar" el aviso (notificado=true) con una
// operación atómica evita duplicados aunque haya más de una instancia.
const VENTANA_MIN = 60;
let corriendo = false;

async function tick() {
  if (corriendo || !apnsConfigurado()) return;
  corriendo = true;
  try {
    const ahora = new Date();
    const limite = new Date(ahora.getTime() + VENTANA_MIN * 60000);
    const candidatos = await PartidoSeguido.find({
      notificado: false, kickoff: { $gt: ahora, $lte: limite }
    }).limit(500);

    for (const c of candidatos) {
      const reclamado = await PartidoSeguido.findOneAndUpdate(
        { _id: c._id, notificado: false }, { notificado: true }
      );
      if (!reclamado) continue; // otra instancia ya lo mandó
      const tokens = (await PushToken.find({ user: c.user })).map(t => t.token);
      if (!tokens.length) continue;
      const min = Math.max(1, Math.round((c.kickoff.getTime() - Date.now()) / 60000));
      const cuando = min >= 50 ? "en 1 hora" : `en ${min} min`;
      const r = await enviarPush(tokens, {
        titulo: "Pickazo MX",
        cuerpo: `${c.local} vs ${c.visitante} empieza ${cuando}. Ya puedes ver el análisis.`,
        data: { view: "hoy", partido: c.matchId },
      });
      if (r.invalidos.length) await PushToken.deleteMany({ token: { $in: r.invalidos } });
    }
    // Limpieza: seguimientos de partidos que ya pasaron hace más de 2 días.
    await PartidoSeguido.deleteMany({ kickoff: { $lt: new Date(Date.now() - 2 * 86400000) } });
  } catch (e) {
    console.error("recordatorios:", e.message);
  } finally {
    corriendo = false;
  }
}

export function iniciarRecordatorios() {
  setInterval(tick, 60 * 1000).unref();
}
