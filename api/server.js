// PlatoAR API — servidor REST sin dependencias externas (solo Node.js 18+).
//
//   node server.js            → arranca en http://localhost:3000
//   PORT=8080 API_KEYS=k1,k2 PUBLIC_BASE_URL=https://api.midominio.co node server.js
//
// Endpoints principales (ver openapi.yaml para el contrato completo):
//   GET  /v1/health
//   GET  /v1/restaurants/:restaurant_id/items
//   GET  /v1/restaurants/:restaurant_id/items/:item_id/model   ← lo que llama la app de delivery
//   POST /v1/events                                           ← vistas 3D, AR abierta, agregado al carrito
//   GET  /v1/analytics/items/:item_id                         ← conversión por plato
//   POST /v1/captures   /   GET /v1/captures/:job_id          ← subir un video del plato y seguir su procesamiento
//
// Además sirve la demo web (carpeta ../web) en "/", con el tipo MIME correcto para .glb.

"use strict";
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

const PORT = Number(process.env.PORT || 3000);
const API_KEYS = new Set((process.env.API_KEYS || "demo_key_123").split(",").map(s => s.trim()).filter(Boolean));
const WEB_DIR = path.resolve(process.env.WEB_DIR || path.join(__dirname, "..", "web"));
const DATA_DIR = path.resolve(process.env.DATA_DIR || path.join(__dirname, "data"));
const EVENTS_FILE = path.join(DATA_DIR, "events.ndjson");
const RATE_LIMIT_PER_MIN = Number(process.env.RATE_LIMIT_PER_MIN || 600);

const EVENT_TYPES = new Set(["view_3d", "view_end", "ar_tap", "ar_open", "ar_failed", "add_to_cart", "order_placed", "refund_requested"]);

// ------------------------------------------------------------------ datos
const catalog = JSON.parse(fs.readFileSync(path.join(DATA_DIR, "catalog.json"), "utf8"));
const restaurants = new Map(catalog.restaurants.map(r => [r.restaurant_id, r]));

const events = [];
if (fs.existsSync(EVENTS_FILE)) {
  for (const line of fs.readFileSync(EVENTS_FILE, "utf8").split("\n")) {
    if (line.trim()) { try { events.push(JSON.parse(line)); } catch { /* línea dañada: se ignora */ } }
  }
}
const jobs = new Map();

// ------------------------------------------------------------------ utilidades
function baseUrl(req) {
  if (process.env.PUBLIC_BASE_URL) return process.env.PUBLIC_BASE_URL.replace(/\/$/, "");
  const proto = req.headers["x-forwarded-proto"] || "http";
  return `${proto}://${req.headers.host}`;
}

function send(res, status, body, extra = {}) {
  const json = JSON.stringify(body, null, 2);
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(json),
    ...extra,
  });
  res.end(json);
}

function apiError(res, status, code, message) {
  send(res, status, { error: { code, message } });
}

function readJson(req, limit = 1e6) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", c => {
      size += c.length;
      if (size > limit) { reject(Object.assign(new Error("payload_too_large"), { status: 413 })); req.destroy(); }
      else chunks.push(c);
    });
    req.on("end", () => {
      if (!chunks.length) return resolve({});
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { reject(Object.assign(new Error("invalid_json"), { status: 400 })); }
    });
    req.on("error", reject);
  });
}

function findItem(restaurantId, itemId) {
  const r = restaurants.get(restaurantId);
  if (!r) return { error: ["restaurant_not_found", `No existe el restaurante ${restaurantId}.`] };
  const item = r.items.find(i => i.item_id === itemId);
  if (!item) return { error: ["item_not_found", `El restaurante ${restaurantId} no tiene el plato ${itemId}.`] };
  return { restaurant: r, item };
}

function findItemAnywhere(itemId) {
  for (const r of restaurants.values()) {
    const item = r.items.find(i => i.item_id === itemId);
    if (item) return { restaurant: r, item };
  }
  return null;
}

// Límite de peticiones por API key (ventana de 1 minuto, en memoria).
const buckets = new Map();
function rateLimited(key) {
  const now = Date.now();
  const b = buckets.get(key) || { start: now, count: 0 };
  if (now - b.start > 60_000) { b.start = now; b.count = 0; }
  b.count++;
  buckets.set(key, b);
  return b.count > RATE_LIMIT_PER_MIN;
}

// ------------------------------------------------------------------ respuestas
function modelPayload(req, restaurant, item) {
  const base = baseUrl(req);
  const m = item.model;
  const glbUrl = `${base}/${m.glb}`;
  return {
    restaurant_id: restaurant.restaurant_id,
    item_id: item.item_id,
    name: item.name,
    status: "ready",
    model: {
      format: "glb",
      glb_url: glbUrl,
      usdz_url: m.usdz ? `${base}/${m.usdz}#allowsContentScaling=0` : null,
      dimensions_cm: m.dimensions_cm,
      real_scale: true,
      file_size_bytes: m.file_size_bytes,
      triangles: m.triangles,
      capture_method: m.capture_method,
      version: m.version,
      updated_at: m.updated_at,
    },
    ar: {
      scale: "fixed",
      placement: "floor",
      modes: ["webxr", "scene-viewer", "quick-look"],
    },
    viewer: {
      web_url: `${base}/?item=${encodeURIComponent(item.item_id)}`,
      embed_html:
        `<script type="module" src="https://cdn.jsdelivr.net/npm/@google/model-viewer@4.0.0/dist/model-viewer.min.js"></script>\n` +
        `<model-viewer src="${glbUrl}" ar ar-modes="webxr scene-viewer quick-look" ar-scale="fixed" ` +
        `camera-controls shadow-intensity="1" alt="${item.name.replace(/"/g, "&quot;")}"></model-viewer>`,
    },
    size_reference: item.size_reference,
    tracking: {
      events_endpoint: `${base}/v1/events`,
      event_types: [...EVENT_TYPES],
    },
  };
}

function analyticsFor(itemId, from, to) {
  const list = events.filter(e => e.item_id === itemId && (!from || e.ts >= from) && (!to || e.ts <= to));
  const count = t => list.filter(e => e.type === t).length;
  const sessions = t => new Set(list.filter(e => e.type === t).map(e => e.session_id)).size;
  const views = sessions("view_3d");
  const arOpens = sessions("ar_open");
  const carts = sessions("add_to_cart");
  const dwell = list.filter(e => (e.type === "view_end" || e.type === "view_3d") && Number.isFinite(e.duration_ms)).map(e => e.duration_ms);
  const pct = (a, b) => (b ? Math.round((a / b) * 1000) / 10 : 0);
  return {
    item_id: itemId,
    period: { from: from || null, to: to || null },
    totals: {
      events: list.length,
      view_3d: count("view_3d"),
      ar_open: count("ar_open"),
      add_to_cart: count("add_to_cart"),
      order_placed: count("order_placed"),
      refund_requested: count("refund_requested"),
    },
    unique_sessions: { viewed_3d: views, opened_ar: arOpens, added_to_cart: carts },
    rates_pct: {
      ar_open_rate: pct(arOpens, views),
      view_to_cart: pct(carts, views),
      ar_to_cart: pct(new Set(list.filter(e => e.type === "add_to_cart" && list.some(x => x.type === "ar_open" && x.session_id === e.session_id)).map(e => e.session_id)).size, arOpens),
    },
    avg_view_seconds: dwell.length ? Math.round(dwell.reduce((a, b) => a + b, 0) / dwell.length / 100) / 10 : null,
  };
}

// Simula el pipeline de captura: en producción cada etapa la ejecuta un worker
// (reconstrucción por fotogrametría o Gaussian Splatting, limpieza, compresión).
const STAGES = [
  { status: "queued", until: 0 },
  { status: "reconstructing", until: 5_000 },
  { status: "optimizing", until: 12_000 },
  { status: "ready", until: 20_000 },
];
function jobView(req, job) {
  const elapsed = Date.now() - job.created;
  let stage = STAGES[0];
  for (const s of STAGES) if (elapsed >= s.until) stage = s;
  const out = {
    job_id: job.job_id,
    restaurant_id: job.restaurant_id,
    item_id: job.item_id,
    method: job.method,
    source_video_url: job.source_video_url,
    status: stage.status,
    progress_pct: Math.min(100, Math.round((elapsed / STAGES.at(-1).until) * 100)),
    created_at: new Date(job.created).toISOString(),
    simulated: true,
  };
  if (stage.status === "ready") {
    const found = findItem(job.restaurant_id, job.item_id);
    out.result = found.item ? modelPayload(req, found.restaurant, found.item).model : null;
  }
  return out;
}

// ------------------------------------------------------------------ archivos estáticos
const MIME = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8", ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
  ".usdz": "model/vnd.usdz+zip", ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp",
  ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8",
};
function serveStatic(req, res, pathname) {
  let rel = decodeURIComponent(pathname);
  if (rel.endsWith("/")) rel += "index.html";
  const file = path.resolve(WEB_DIR, "." + rel);
  if (!file.startsWith(WEB_DIR + path.sep)) return apiError(res, 403, "forbidden", "Ruta no permitida.");
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) return apiError(res, 404, "not_found", "No existe ese archivo.");
    res.writeHead(200, {
      "Content-Type": MIME[path.extname(file).toLowerCase()] || "application/octet-stream",
      "Content-Length": st.size,
      "Cache-Control": file.endsWith(".glb") ? "public, max-age=86400" : "no-cache",
      "Access-Control-Allow-Origin": "*",
    });
    fs.createReadStream(file).pipe(res);
  });
}

// ------------------------------------------------------------------ enrutador
async function handle(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, X-API-Key");
  res.setHeader("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  if (req.method === "OPTIONS") { res.writeHead(204); return res.end(); }

  const url = new URL(req.url, "http://localhost");
  const p = url.pathname;

  if (!p.startsWith("/v1/")) return serveStatic(req, res, p);
  if (p === "/v1/health") return send(res, 200, { status: "ok", restaurants: restaurants.size, events: events.length, time: new Date().toISOString() });

  const key = req.headers["x-api-key"];
  if (!key || !API_KEYS.has(key)) return apiError(res, 401, "unauthorized", "Falta la cabecera X-API-Key o no es válida.");
  if (rateLimited(key)) return apiError(res, 429, "rate_limited", `Máximo ${RATE_LIMIT_PER_MIN} peticiones por minuto.`);

  let m;
  if (req.method === "GET" && (m = p.match(/^\/v1\/restaurants\/([^/]+)\/items$/))) {
    const r = restaurants.get(m[1]);
    if (!r) return apiError(res, 404, "restaurant_not_found", `No existe el restaurante ${m[1]}.`);
    return send(res, 200, {
      restaurant_id: r.restaurant_id, name: r.name, city: r.city,
      items: r.items.map(i => ({ item_id: i.item_id, name: i.name, price_cop: i.price_cop, has_3d: true, dimensions_cm: i.model.dimensions_cm })),
    });
  }

  if (req.method === "GET" && (m = p.match(/^\/v1\/restaurants\/([^/]+)\/items\/([^/]+)\/model$/))) {
    const found = findItem(m[1], m[2]);
    if (found.error) return apiError(res, 404, ...found.error);
    const etag = `"${found.item.item_id}-v${found.item.model.version}"`;
    if (req.headers["if-none-match"] === etag) { res.writeHead(304, { ETag: etag }); return res.end(); }
    return send(res, 200, modelPayload(req, found.restaurant, found.item), { ETag: etag, "Cache-Control": "public, max-age=300" });
  }

  if (req.method === "POST" && p === "/v1/events") {
    const body = await readJson(req);
    const list = Array.isArray(body.events) ? body.events : [body];
    if (list.length > 100) return apiError(res, 400, "too_many_events", "Envía como máximo 100 eventos por petición.");
    const accepted = [];
    for (const [i, e] of list.entries()) {
      if (!EVENT_TYPES.has(e.type)) return apiError(res, 400, "invalid_event", `Evento ${i}: type debe ser uno de ${[...EVENT_TYPES].join(", ")}.`);
      if (!e.item_id || !findItemAnywhere(e.item_id)) return apiError(res, 400, "invalid_event", `Evento ${i}: item_id desconocido.`);
      if (!e.session_id) return apiError(res, 400, "invalid_event", `Evento ${i}: falta session_id.`);
      accepted.push({
        event_id: "evt_" + crypto.randomUUID(),
        type: e.type, item_id: e.item_id, session_id: String(e.session_id).slice(0, 100),
        ts: e.ts && !Number.isNaN(Date.parse(e.ts)) ? new Date(e.ts).toISOString() : new Date().toISOString(),
        duration_ms: Number.isFinite(e.duration_ms) ? e.duration_ms : undefined,
        platform: e.platform ? String(e.platform).slice(0, 40) : undefined,
        api_key_hash: crypto.createHash("sha256").update(key).digest("hex").slice(0, 12),
      });
    }
    events.push(...accepted);
    fs.appendFileSync(EVENTS_FILE, accepted.map(e => JSON.stringify(e)).join("\n") + "\n");
    return send(res, 202, { accepted: accepted.length, event_ids: accepted.map(e => e.event_id) });
  }

  if (req.method === "GET" && (m = p.match(/^\/v1\/analytics\/items\/([^/]+)$/))) {
    if (!findItemAnywhere(m[1])) return apiError(res, 404, "item_not_found", `No existe el plato ${m[1]}.`);
    return send(res, 200, analyticsFor(m[1], url.searchParams.get("from"), url.searchParams.get("to")));
  }

  if (req.method === "POST" && p === "/v1/captures") {
    const b = await readJson(req);
    const found = findItem(b.restaurant_id, b.item_id);
    if (found.error) return apiError(res, 404, ...found.error);
    if (!b.source_video_url || !/^https:\/\//.test(b.source_video_url))
      return apiError(res, 400, "invalid_source", "source_video_url debe ser un enlace https al video del plato.");
    const method = ["photogrammetry", "gaussian_splatting"].includes(b.method) ? b.method : "photogrammetry";
    const job = { job_id: "job_" + crypto.randomUUID(), restaurant_id: b.restaurant_id, item_id: b.item_id, method, source_video_url: b.source_video_url, created: Date.now() };
    jobs.set(job.job_id, job);
    return send(res, 202, jobView(req, job), { Location: `/v1/captures/${job.job_id}` });
  }

  if (req.method === "GET" && (m = p.match(/^\/v1\/captures\/([^/]+)$/))) {
    const job = jobs.get(m[1]);
    if (!job) return apiError(res, 404, "job_not_found", `No existe el trabajo ${m[1]}.`);
    return send(res, 200, jobView(req, job));
  }

  return apiError(res, 404, "not_found", `No existe la ruta ${req.method} ${p}.`);
}

const server = http.createServer((req, res) => {
  handle(req, res).catch(err => {
    if (res.headersSent) return res.destroy();
    apiError(res, err.status || 500, err.message === "invalid_json" ? "invalid_json" : err.status === 413 ? "payload_too_large" : "internal_error",
      err.message === "invalid_json" ? "El cuerpo no es JSON válido." : err.status === 413 ? "El cuerpo es demasiado grande." : "Error interno.");
  });
});

if (require.main === module) {
  server.listen(PORT, () => {
    console.log(`PlatoAR API escuchando en http://localhost:${PORT}`);
    console.log(`Demo web: http://localhost:${PORT}/   ·   Salud: http://localhost:${PORT}/v1/health`);
  });
}
module.exports = { server };
