import PushToken from "../models/PushToken.js";
import { enviarPush, apnsConfigurado } from "./apns.js";

// Avisos "tu análisis está listo": la app los pide cuando se minimiza mientras
// espera un análisis. Viven en memoria (10 min): si el servidor se reinicia en
// medio, ese aviso se pierde, y no pasa nada más.
const pendientes = new Map();
const clave = (user, id) => `${user}:${id}`;

export function pedirAviso(user, id) {
  const ahora = Date.now();
  for (const [k, exp] of pendientes) if (exp < ahora) pendientes.delete(k);
  pendientes.set(clave(user, id), ahora + 10 * 60000);
}
export function cancelarAviso(user, id) {
  pendientes.delete(clave(user, id));
}
export async function avisarSiPedido(user, id, local, visitante) {
  const k = clave(user, id);
  const exp = pendientes.get(k);
  if (!exp) return;
  pendientes.delete(k);
  if (exp < Date.now() || !apnsConfigurado()) return;
  const tokens = (await PushToken.find({ user })).map(t => t.token);
  if (!tokens.length) return;
  const r = await enviarPush(tokens, {
    titulo: "Pickazo MX",
    cuerpo: `Ya está tu análisis de ${local} vs ${visitante}. Revísalo en la app.`,
    data: { view: "hoy", partido: id },
  });
  if (r.invalidos.length) await PushToken.deleteMany({ token: { $in: r.invalidos } });
}
