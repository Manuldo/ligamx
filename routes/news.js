import express from "express";
import { ah } from "../asyncHandler.js";

const router = express.Router();

// ─────────────────────────────────────────────────────────────────────────
// Noticias: agregador de RSS públicos, al estilo Google News / Flipboard.
// Solo tomamos lo que el feed YA publica para esto (título + resumen corto +
// link al artículo original), nunca el cuerpo completo ni las imágenes
// principales del sitio. El link manda tráfico de vuelta a la fuente — por
// eso es la forma estándar y segura de hacer esto, sin pisar copyright.
//
// Si algún día quieres quitar o agregar una fuente, es solo tocar este
// arreglo — no hay nada más que depender de ello.
// ─────────────────────────────────────────────────────────────────────────
const FUENTES = [
  { nombre: "ESPN Fútbol", url: "https://www.espn.com/espn/rss/soccer/news", liga: "futbol" },
  { nombre: "ESPN NFL", url: "https://www.espn.com/espn/rss/nfl/news", liga: "nfl" },
  { nombre: "ESPN MLB", url: "https://www.espn.com/espn/rss/mlb/news", liga: "mlb" },
  { nombre: "BBC Sport Football", url: "https://feeds.bbci.co.uk/sport/football/rss.xml", liga: "futbol" },
  { nombre: "Marca Fútbol", url: "https://www.marca.com/rss/futbol.xml", liga: "futbol" },
];

const CACHE_MS = 20 * 60 * 1000; // 20 min — no les pegamos en cada visita de cada usuario
let cache = { ts: 0, items: [] };
let refrescando = null; // evita refrescos duplicados en paralelo

function limpiarHTML(s) {
  return String(s || "")
    .replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1")
    .replace(/<[^>]+>/g, " ")
    .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"').replace(/&#0?39;/g, "'").replace(/&nbsp;/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function extraerTag(bloque, tag) {
  const re = new RegExp(`<${tag}[^>]*>([\\s\\S]*?)</${tag}>`, "i");
  const m = bloque.match(re);
  return m ? m[1].trim() : "";
}

function extraerImagen(bloque) {
  const m =
    bloque.match(/<media:thumbnail[^>]*url="([^"]+)"/i) ||
    bloque.match(/<media:content[^>]*url="([^"]+)"/i) ||
    bloque.match(/<enclosure[^>]*url="([^"]+)"[^>]*type="image/i) ||
    bloque.match(/<enclosure[^>]*type="image[^"]*"[^>]*url="([^"]+)"/i);
  return m ? m[1] : null;
}

async function leerFuente(fuente) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  try {
    const r = await fetch(fuente.url, {
      signal: ctrl.signal,
      headers: { "User-Agent": "Mozilla/5.0 (compatible; PickazoMXNewsBot/1.0)" },
    });
    if (!r.ok) return [];
    const xml = await r.text();
    const items = xml.match(/<item[\s\S]*?<\/item>/gi) || [];
    return items
      .slice(0, 15)
      .map((bloque) => {
        const titulo = limpiarHTML(extraerTag(bloque, "title"));
        const link = limpiarHTML(extraerTag(bloque, "link"));
        const descripcion = limpiarHTML(extraerTag(bloque, "description")).slice(0, 220);
        const pubDateRaw = extraerTag(bloque, "pubDate") || extraerTag(bloque, "dc:date");
        const fecha = pubDateRaw ? new Date(pubDateRaw) : null;
        return {
          titulo,
          link,
          descripcion,
          imagen: extraerImagen(bloque),
          fuente: fuente.nombre,
          liga: fuente.liga,
          fecha: fecha && !isNaN(fecha.getTime()) ? fecha.toISOString() : null,
        };
      })
      .filter((n) => n.titulo && n.link);
  } catch {
    return []; // una fuente caída/lenta/bloqueada nunca debe tumbar a las demás
  } finally {
    clearTimeout(timer);
  }
}

async function refrescarCache() {
  const resultados = await Promise.all(FUENTES.map(leerFuente));
  const todas = resultados.flat();
  todas.sort((a, b) => (b.fecha || "").localeCompare(a.fecha || ""));
  cache = { ts: Date.now(), items: todas.slice(0, 80) };
  return cache.items;
}

router.get(
  "/",
  ah(async (req, res) => {
    const vencido = Date.now() - cache.ts > CACHE_MS;
    if (vencido || !cache.items.length) {
      // si ya hay alguien refrescando, súbete a ese mismo refresh en vez de
      // disparar fetches duplicados a las mismas fuentes
      if (!refrescando) refrescando = refrescarCache().finally(() => { refrescando = null; });
      try {
        await refrescando;
      } catch {
        /* si falla, servimos lo que haya en cache aunque esté viejo */
      }
    }
    const liga = req.query.liga;
    const items = liga ? cache.items.filter((n) => n.liga === liga) : cache.items;
    res.json({ ok: true, actualizado: cache.ts ? new Date(cache.ts).toISOString() : null, noticias: items });
  })
);

export default router;
