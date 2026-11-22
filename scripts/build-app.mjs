// Arma la carpeta www-app/ que Capacitor empaqueta DENTRO de la app de iPhone.
// La interfaz (HTML/imágenes) viaja en la app; los datos, login, análisis y push
// siguen viniendo de tu servidor en internet (APP_API_BASE).
//
//   APP_API_BASE=https://pickazoapp.com node scripts/build-app.mjs      (producción)
//   APP_API_BASE=https://<tu-preview>.up.railway.app node scripts/build-app.mjs   (pruebas)
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const raiz = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const origen = path.join(raiz, "public");
const destino = path.join(raiz, "www-app");
const base = String(process.env.APP_API_BASE || "https://pickazoapp.com").trim().replace(/\/+$/, "");
if (!/^https:\/\/[a-z0-9.-]+(:\d+)?$/i.test(base)) {
  console.error(`APP_API_BASE inválida: "${base}" (debe ser https://dominio, sin ruta)`);
  process.exit(1);
}
// Páginas internas que no deben ir dentro de la app.
const EXCLUIR = new Set(["admin.html", "metricas.html", "reset.html"]);

fs.rmSync(destino, { recursive: true, force: true });
fs.cpSync(origen, destino, { recursive: true, filter: (src) => !EXCLUIR.has(path.basename(src)) });

const idx = path.join(destino, "index.html");
let html = fs.readFileSync(idx, "utf8");
const marca = `<script>window.PZ_API=${JSON.stringify(base)};</script>`;
if (!html.includes("<head>")) { console.error("index.html sin <head>"); process.exit(1); }
html = html.replace("<head>", `<head>\n${marca}`);
fs.writeFileSync(idx, html);
console.log(`www-app listo · API = ${base}/api · ${fs.readdirSync(destino).length} archivos en la raíz`);
