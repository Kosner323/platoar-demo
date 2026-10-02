// Pruebas de la API:  node --test
"use strict";
const { test, before, after } = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "platoar-"));
fs.copyFileSync(path.join(__dirname, "data", "catalog.json"), path.join(tmp, "catalog.json"));
process.env.DATA_DIR = tmp;
process.env.API_KEYS = "test_key";
const { server } = require("./server");

let base;
const KEY = { "X-API-Key": "test_key" };
before(() => new Promise(r => server.listen(0, () => { base = `http://127.0.0.1:${server.address().port}`; r(); })));
after(() => { server.close(); fs.rmSync(tmp, { recursive: true, force: true }); });

const post = (p, body, headers = KEY) =>
  fetch(base + p, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });

test("health responde sin API key", async () => {
  const r = await fetch(base + "/v1/health");
  assert.equal(r.status, 200);
  assert.equal((await r.json()).status, "ok");
});

test("sin API key devuelve 401", async () => {
  const r = await fetch(base + "/v1/restaurants/rst_cali_001/items");
  assert.equal(r.status, 401);
  assert.equal((await r.json()).error.code, "unauthorized");
});

test("lista los platos de un restaurante", async () => {
  const r = await fetch(base + "/v1/restaurants/rst_cali_001/items", { headers: KEY });
  const j = await r.json();
  assert.equal(r.status, 200);
  assert.equal(j.items[0].item_id, "itm_hamburguesa_artesanal");
  assert.equal(j.items[0].has_3d, true);
});

test("devuelve el modelo 3D con medidas reales y ETag", async () => {
  const r = await fetch(base + "/v1/restaurants/rst_cali_003/items/itm_chuleta_valluna/model", { headers: KEY });
  const j = await r.json();
  assert.equal(r.status, 200);
  assert.match(j.model.glb_url, /modelos\/chuleta-valluna\.glb$/);
  assert.deepEqual(j.model.dimensions_cm, { width: 30, depth: 30, height: 4.2 });
  assert.equal(j.ar.scale, "fixed");
  const etag = r.headers.get("etag");
  const again = await fetch(base + "/v1/restaurants/rst_cali_003/items/itm_chuleta_valluna/model", { headers: { ...KEY, "If-None-Match": etag } });
  assert.equal(again.status, 304);
});

test("plato inexistente devuelve 404 con código claro", async () => {
  const r = await fetch(base + "/v1/restaurants/rst_cali_001/items/no_existe/model", { headers: KEY });
  assert.equal(r.status, 404);
  assert.equal((await r.json()).error.code, "item_not_found");
});

test("registra eventos y calcula la conversión", async () => {
  const item_id = "itm_empanadas_x3";
  const evs = [];
  for (let s = 1; s <= 10; s++) evs.push({ type: "view_3d", item_id, session_id: "s" + s }, { type: "view_end", item_id, session_id: "s" + s, duration_ms: 8000 });
  for (let s = 1; s <= 4; s++) evs.push({ type: "ar_open", item_id, session_id: "s" + s });
  for (const s of [1, 2, 3, 7]) evs.push({ type: "add_to_cart", item_id, session_id: "s" + s });
  const r = await post("/v1/events", { events: evs });
  assert.equal(r.status, 202);
  assert.equal((await r.json()).accepted, evs.length);

  const a = await (await fetch(base + "/v1/analytics/items/" + item_id, { headers: KEY })).json();
  assert.equal(a.unique_sessions.viewed_3d, 10);
  assert.equal(a.rates_pct.ar_open_rate, 40);
  assert.equal(a.rates_pct.view_to_cart, 40);
  assert.equal(a.rates_pct.ar_to_cart, 75);
  assert.equal(a.avg_view_seconds, 8);
  assert.ok(fs.readFileSync(path.join(tmp, "events.ndjson"), "utf8").includes("ar_open"));
});

test("rechaza eventos inválidos", async () => {
  const r = await post("/v1/events", { type: "comprar", item_id: "itm_empanadas_x3", session_id: "x" });
  assert.equal(r.status, 400);
  const bad = await fetch(base + "/v1/events", { method: "POST", headers: { ...KEY, "Content-Type": "application/json" }, body: "{no es json" });
  assert.equal(bad.status, 400);
  assert.equal((await bad.json()).error.code, "invalid_json");
});

test("crea un trabajo de captura y lo consulta", async () => {
  const r = await post("/v1/captures", { restaurant_id: "rst_cali_001", item_id: "itm_hamburguesa_artesanal", source_video_url: "https://ejemplo.co/video.mp4", method: "gaussian_splatting" });
  assert.equal(r.status, 202);
  const job = await r.json();
  assert.equal(job.status, "queued");
  assert.equal(job.method, "gaussian_splatting");
  const g = await (await fetch(base + "/v1/captures/" + job.job_id, { headers: KEY })).json();
  assert.equal(g.job_id, job.job_id);
});

test("sirve la demo web y los .glb con el tipo correcto", async () => {
  const r = await fetch(base + "/modelos/hamburguesa.glb");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("content-type"), "model/gltf-binary");
  const buf = Buffer.from(await r.arrayBuffer());
  assert.equal(buf.readUInt32LE(0), 0x46546c67); // "glTF"
  const t = await fetch(base + "/../server.js");
  assert.notEqual(t.status, 200);
});
