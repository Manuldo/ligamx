import http2 from "node:http2";
import jwt from "jsonwebtoken";

// Envío de notificaciones push a iOS directo con APNs (sin Firebase).
// Variables (todas opcionales; si faltan, el push queda apagado sin romper nada):
//   APNS_KEY_ID     ID de la llave .p8 (Apple Developer → Keys)
//   APNS_TEAM_ID    Team ID de tu cuenta developer
//   APNS_KEY        contenido de la llave .p8 (con saltos de línea o como \n)
//   APNS_BUNDLE_ID  Bundle ID de la app (apns-topic)
//   APNS_SANDBOX    "true" para builds de desarrollo/TestFlight local (default: producción)
export function apnsConfigurado() {
  return !!(process.env.APNS_KEY_ID && process.env.APNS_TEAM_ID && process.env.APNS_KEY && process.env.APNS_BUNDLE_ID);
}

let jwtCache = { ts: 0, val: "" };
function providerToken() {
  // Apple pide renovar entre 20 y 60 min.
  if (Date.now() - jwtCache.ts < 40 * 60 * 1000 && jwtCache.val) return jwtCache.val;
  const key = process.env.APNS_KEY.replace(/\\n/g, "\n");
  const val = jwt.sign({}, key, {
    algorithm: "ES256",
    keyid: process.env.APNS_KEY_ID,
    issuer: process.env.APNS_TEAM_ID,
  });
  jwtCache = { ts: Date.now(), val };
  return val;
}

function host() {
  return process.env.APNS_SANDBOX === "true" ? "https://api.sandbox.push.apple.com" : "https://api.push.apple.com";
}

// Envía a una lista de tokens. Devuelve { enviados, invalidos:[tokens] }.
export async function enviarPush(tokens, { titulo, cuerpo, data = {} }) {
  if (!apnsConfigurado()) return { enviados: 0, invalidos: [], error: "APNs no configurado" };
  const client = http2.connect(host());
  client.on("error", () => {});
  const bearer = providerToken();
  const payload = JSON.stringify({ aps: { alert: { title: titulo, body: cuerpo }, sound: "default" }, ...data });
  let enviados = 0;
  const invalidos = [];
  await Promise.all(tokens.map(token => new Promise(resolve => {
    const req = client.request({
      ":method": "POST",
      ":path": `/3/device/${token}`,
      authorization: `bearer ${bearer}`,
      "apns-topic": process.env.APNS_BUNDLE_ID,
      "apns-push-type": "alert",
      "content-type": "application/json",
    });
    let status = 0, body = "";
    req.on("response", h => { status = h[":status"]; });
    req.on("data", c => { body += c; });
    req.on("end", () => {
      if (status === 200) enviados++;
      else if (status === 410 || /BadDeviceToken|Unregistered|DeviceTokenNotForTopic/.test(body)) invalidos.push(token);
      resolve();
    });
    req.on("error", () => resolve());
    req.setTimeout(10000, () => { req.close(); resolve(); });
    req.end(payload);
  })));
  client.close();
  return { enviados, invalidos };
}
