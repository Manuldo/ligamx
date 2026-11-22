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
// iOS agranda el área de maquetado si algo se desborda, y los elementos fijos (barra
// inferior) la siguen y quedan cortados. En la app el ancho se fija al de la pantalla.
const vp = /<meta name="viewport"[^>]*>/;
if (!vp.test(html)) { console.error("index.html sin meta viewport"); process.exit(1); }
html = html.replace(vp, '<meta name="viewport" content="width=device-width, initial-scale=1, minimum-scale=1, maximum-scale=1, user-scalable=no, viewport-fit=cover">');
html = html.replace("<head>", `<head>\n${marca}`);
// APP_DEBUG=1: muestra una franja con las medidas reales del WebView (solo para diagnosticar).
if (process.env.APP_DEBUG === "1") {
  const dbg = `<script>addEventListener("load",()=>{const d=document.createElement("div");d.style.cssText="position:fixed;top:calc(env(safe-area-inset-top) + 2px);left:0;z-index:99999;background:#fff;color:#000;font:10px monospace;padding:2px 4px;pointer-events:none";const f=()=>{d.textContent="iw "+innerWidth+" vv "+Math.round(visualViewport.width)+" x"+visualViewport.scale.toFixed(2)+" sw "+document.documentElement.scrollWidth+" scr "+screen.width+" dpr "+devicePixelRatio};f();document.body.appendChild(d);setInterval(f,1500)});</script>`;
  html = html.replace("</body>", `${dbg}\n</body>`);
}
fs.writeFileSync(idx, html);
console.log(`www-app listo · API = ${base}/api · ${fs.readdirSync(destino).length} archivos en la raíz`);
