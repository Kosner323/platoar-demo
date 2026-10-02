# Protocolo de escaneo: de un plato real a un modelo 3D para AR

Objetivo: un archivo `.glb` de **menos de 5 MB**, con **medidas reales** (1 unidad = 1 metro) y una textura que no se vea de plástico. Este archivo reemplaza a los modelos de muestra de la carpeta `web/modelos/`.

## 1. Qué método usar

| Método | Para qué sirve | Funciona en AR del celular |
|---|---|---|
| **Fotogrametría** (malla con textura) | El modelo que va a la app y a la AR | Sí: Android (Scene Viewer) y iPhone (Quick Look) |
| **Gaussian Splatting** | Visor 3D muy realista dentro de la app, con brillos y salsas creíbles | Todavía no de forma nativa: los visores AR del sistema solo aceptan mallas |

Recomendación para el MVP: **fotogrametría para todo lo que va a AR**. Gaussian Splatting queda como mejora futura para el visor 3D dentro de la app.

Apps para capturar con el celular (revisa qué formatos exporta tu plan de cada una): Polycam, RealityScan, Scaniverse. En computador: RealityCapture o Meshroom (gratuito).

## 2. Montaje (10 minutos por plato)

1. **Luz pareja y suave.** Junto a una ventana sin sol directo, o dos lámparas con difusor a los lados. Sin flash. Las sombras duras quedan "pintadas" en la textura.
2. **Fondo mate y liso** (mantel gris o verde oscuro). Nada brillante ni con patrones.
3. **Plato sobre una base giratoria** (sirve una para pasteles). Así la cámara queda quieta y el plato gira.
4. **Objeto de escala en la toma:** una regla, o mejor una tarjeta impresa con un cuadrado de 10 cm. Sin esto no hay "tamaño real".
5. **Comida recién servida.** Las salsas pierden brillo en 5 minutos: escanea primero y emplata de nuevo si hace falta.

## 3. Captura

- **60 a 120 fotos** (o un video de 60–90 s si la app lo admite) en tres alturas: casi a ras de la mesa (15°), a media altura (45°) y casi cenital (75°).
- Cada foto debe compartir **al menos 60 %** con la anterior. Gira la base unos 10–15° entre fotos.
- Enfoque fijo, exposición fija (bloquea AE/AF en el celular), sin zoom digital.
- Nada de movimiento: vapor visible y hojas de cilantro que se mueven generan ruido.

## 4. Limpieza y escala

1. Recorta todo lo que no sea el plato (mantel, base giratoria).
2. **Calibra la escala** con el objeto de referencia: mide la distancia entre dos puntos conocidos y escala el modelo para que coincida. Verifica que el plato mida lo que mide en la vida real (ej. 30 cm de diámetro).
3. Centra el modelo y pon la base del plato en `y = 0` (si no, en AR flota o se hunde en la mesa).
4. Reduce la malla a **20.000–60.000 triángulos** (en Blender: modificador *Decimate*).

## 5. Optimización a menos de 5 MB

Con la herramienta gratuita `gltf-transform` (Node.js):

```bash
npm install -g @gltf-transform/cli
gltf-transform resize plato.glb plato-2k.glb --width 2048 --height 2048
gltf-transform optimize plato-2k.glb plato-final.glb --texture-compress webp
gltf-transform inspect plato-final.glb
```

- Textura a 2048 px como máximo (1024 px si el plato es pequeño).
- Para que salsas y quesos se vean húmedos, ajusta en Blender el material: *Roughness* bajo (0,2–0,35) en esas zonas, o usa un mapa de rugosidad pintado. Un toque de *Clearcoat* ayuda en glaseados.
- Prueba siempre el resultado en un celular Android y en un iPhone antes de publicarlo.

## 6. Control de calidad (checklist)

- [ ] Pesa menos de 5 MB
- [ ] Las medidas coinciden con el plato real (±1 cm)
- [ ] La base está en el piso (`y = 0`) y centrada
- [ ] Sin huecos visibles ni restos del mantel
- [ ] Se ve bien en AR con luz de interior
- [ ] El restaurante aprobó cómo se ve su plato

## 7. Publicarlo

1. Copia el `.glb` en `web/modelos/`.
2. Agrega el plato en `api/data/catalog.json` con sus medidas (`dimensions_cm`) y el método (`photogrammetry`).
3. Agrégalo a la lista `DISHES` de `web/index.html`.
