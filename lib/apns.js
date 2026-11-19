import http2 from "node:http2";
import jwt from "jsonwebtoken";
import { createPrivateKey } from "node:crypto";

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

// Railway a veces guarda la llave en una sola línea, con espacios o con \n literales:
// se reconstruye el PEM a partir del contenido base64 y se valida con un mensaje claro.
function pemNormalizado(raw) {
  const t = String(raw || "").trim().replace(/^["']|["']$/g, "").replace(/\\n/g, "\n");
  const m = t.match(/-----BEGIN PRIVATE KEY-----([\s\S]*?)-----END PRIVATE KEY-----/);
  const b64 = (m ? m[1] : t.replace(/-----[A-Z ]+-----/g, "")).replace(/[^A-Za-z0-9+/=]/g, "");
  const pem = `-----BEGIN PRIVATE KEY-----\n${(b64.match(/.{1,64}/g) || []).join("\n")}\n-----END PRIVATE KEY-----\n`;
  try {
    createPrivateKey(pem);
  } catch {
    throw new Error(`APNS_KEY no es una llave .p8 válida (llegaron ${b64.length} caracteres; debe traer todo el contenido del archivo, ~184)`);
  }
  return pem;
}

let jwtCache = { ts: 0, val: "" };
function providerToken() {
  // Apple pide renovar entre 20 y 60 min.
  if (Date.now() - jwtCache.ts < 40 * 60 * 1000 && jwtCache.val) return jwtCache.val;
  const key = pemNormalizado(process.env.APNS_KEY);
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
