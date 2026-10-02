# PlatoAR — MVP

Muestra un plato de comida en 3D y **en tamaño real sobre la mesa del cliente** (realidad aumentada) antes de pedirlo. Pensado para integrarse en apps de domicilios como Rappi, DiDi Food o Uber Eats.

## Qué hay en este paquete

```
platoar-demo/
├── index.html               Redirige a la demo (web/)
├── web/                     Demo web (la que llevas en el celular o iPad)
│   ├── index.html
│   └── modelos/             3 platos de muestra en .glb a escala real
├── api/                     Servidor de la API (Node.js, sin dependencias)
│   ├── server.js
│   ├── server.test.js       9 pruebas automáticas
│   ├── openapi.yaml         Contrato de la API para los desarrolladores de la app
│   ├── data/catalog.json    Restaurantes y platos
│   └── ejemplos/            Respuestas JSON reales del servidor
├── docs/
│   └── PROTOCOLO_ESCANEO.md Cómo convertir un plato real en modelo 3D
└── herramientas/            Generador de los modelos de muestra (Python + numpy)
```

## Arquitectura

```
App de domicilios (Rappi, DiDi…)
   │  1. El usuario toca "Ver en mi mesa"
   ▼
GET /v1/restaurants/{restaurant_id}/items/{item_id}/model   ── PlatoAR API
   │  2. Responde con la URL del .glb, medidas en cm y modo AR
   ▼
Visor <model-viewer> (WebView o web)  ── abre la cámara del celular
   │  Android → Scene Viewer · iPhone → Quick Look · navegador → WebXR
   ▼
POST /v1/events  (vista 3D, AR abierta, agregado al carrito)
   ▼
GET /v1/analytics/items/{item_id}  → conversión por plato
```

**Stack:** `<model-viewer>` 4.0 de Google (WebAR sin instalar nada), modelos glTF binario (`.glb`), API REST en Node.js 18+ sin librerías externas, datos en JSON (en producción: PostgreSQL + almacenamiento S3/Cloud Storage + CDN para los `.glb`).

## 1. Probar la demo con AR real en tu celular

Demo publicada con GitHub Pages: **https://kosner323.github.io/platoar-demo/**

Ábrela en el celular y toca **Ver en mi mesa**. La página de la raíz redirige a la carpeta `web/`, donde vive la demo. Cada cambio que se suba a la rama `main` se publica solo en 1–2 minutos.

Requisitos de AR: Android con ARCore (la mayoría de gama media en adelante) o iPhone/iPad con iOS 12 o superior.

## 2. Correr la API

Necesitas Node.js 18 o superior (nodejs.org).

```bash
cd api
npm start          # API en http://localhost:3000 y demo en http://localhost:3000/
npm test           # corre las 9 pruebas
```

Probar una petición:

```bash
curl -H "X-API-Key: demo_key_123" \
  http://localhost:3000/v1/restaurants/rst_cali_001/items/itm_hamburguesa_artesanal/model
```

Variables de entorno para producción:

| Variable | Para qué | Por defecto |
|---|---|---|
| `PORT` | Puerto | `3000` |
| `API_KEYS` | Llaves válidas separadas por coma | `demo_key_123` |
| `PUBLIC_BASE_URL` | Dominio público que aparece en las URLs | el del request |
| `RATE_LIMIT_PER_MIN` | Peticiones por minuto por llave | `600` |

Para publicarla: Render.com o Railway.app → nuevo servicio web → carpeta `api`, comando `npm start`. **Cambia `API_KEYS`** antes de compartirla.

Para que la demo web envíe sus métricas a la API, edita en `web/index.html` la línea `const API = { base: "", key: "" };` con tu dominio y llave.

## 3. Agregar un plato real

Sigue `docs/PROTOCOLO_ESCANEO.md`. Resumen: escanear con fotogrametría → calibrar la escala → bajar a menos de 5 MB → copiar el `.glb` a `web/modelos/` → registrarlo en `api/data/catalog.json` y en `DISHES` de `web/index.html`.

## Estado del MVP

| Parte | Estado |
|---|---|
| Visor 3D con medidas reales y AR | Funciona |
| API de modelos, eventos y analítica | Funciona, con pruebas |
| Modelos 3D | Muestras generadas por computador; faltan escaneos reales |
| Procesamiento de videos (`/v1/captures`) | Simulado: crea el trabajo y avanza de estado, pero no reconstruye todavía |
| Base de datos | Archivos JSON; migrar a PostgreSQL antes de producción |
| Panel para restaurantes | Pendiente |
