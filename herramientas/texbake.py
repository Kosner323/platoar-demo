"""Convierte los platos procedurales a modelos con textura:
   - .glb con baseColorTexture (Android / navegador)
   - .usdz con UsdPreviewSurface (iPhone / Quick Look)

Los colores por vértice se "hornean" en una textura por pieza usando un
atlas de 6 proyecciones planas (una por eje dominante de la normal).
"""
import io
import json
import struct
import sys
import zipfile

import numpy as np
from PIL import Image

import glb as glbmod

TEX = 1024
COLS, ROWS = 3, 2
PAD = 6


def linear_to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def bake_part(mesh, colors_lin):
    """Devuelve (pos, nrm, uv, idx, imagen PIL) con UV en atlas y textura horneada."""
    pos, nrm, idx = mesh.pos, mesh.nrm, mesh.idx
    col = linear_to_srgb(np.asarray(colors_lin))
    tri = pos[idx]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    ax = np.argmax(np.abs(fn), axis=1)
    sg = (np.take_along_axis(fn, ax[:, None], 1)[:, 0] >= 0).astype(int)
    chart = ax * 2 + sg

    img = np.zeros((TEX, TEX, 3))
    filled = np.zeros((TEX, TEX), bool)
    cw, ch = TEX // COLS, TEX // ROWS
    out_pos, out_nrm, out_uv, out_idx = [], [], [], []
    base = 0
    for c in range(6):
        T = np.where(chart == c)[0]
        if len(T) == 0:
            continue
        a = c // 2
        uaxis, vaxis = [k for k in range(3) if k != a]
        verts, inv = np.unique(idx[T].ravel(), return_inverse=True)
        P = pos[verts]
        uv = P[:, [uaxis, vaxis]]
        if c % 2 == 0:  # mirar desde el lado negativo: espejo para no invertir la textura
            uv[:, 0] = -uv[:, 0]
        mn, mx = uv.min(0), uv.max(0)
        span = np.maximum(mx - mn, 1e-9)
        n = (uv - mn) / span
        col_i, row_i = c % COLS, c // COLS
        px = col_i * cw + PAD + n[:, 0] * (cw - 2 * PAD - 1)
        py = row_i * ch + PAD + n[:, 1] * (ch - 2 * PAD - 1)
        # rasterizar triángulos en espacio UV
        lidx = inv.reshape(-1, 3)
        C = col[verts]
        for t in lidx:
            xs, ys = px[t], py[t]
            x0, x1 = int(np.floor(xs.min())), int(np.ceil(xs.max())) + 1
            y0, y1 = int(np.floor(ys.min())), int(np.ceil(ys.max())) + 1
            gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-12:
                continue
            w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
            w2 = 1 - w0 - w1
            m = (w0 >= -0.02) & (w1 >= -0.02) & (w2 >= -0.02)
            if not m.any():
                continue
            cc = w0[..., None] * C[t[0]] + w1[..., None] * C[t[1]] + w2[..., None] * C[t[2]]
            ys_i, xs_i = np.nonzero(m)
            yy, xx = ys_i + y0, xs_i + x0
            ok = (yy >= 0) & (yy < TEX) & (xx >= 0) & (xx < TEX)
            img[yy[ok], xx[ok]] = cc[ys_i[ok], xs_i[ok]]
            filled[yy[ok], xx[ok]] = True
        out_pos.append(P)
        out_nrm.append(nrm[verts])
        out_uv.append(np.stack([px / TEX, py / TEX], axis=1))  # origen arriba-izquierda (glTF)
        out_idx.append(lidx + base)
        base += len(verts)

    # dilatar para que los bordes de cada isla no muestren negro
    for _ in range(PAD + 2):
        if filled.all():
            break
        acc = np.zeros_like(img)
        cnt = np.zeros(filled.shape)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            sf = np.roll(filled, (dy, dx), (0, 1))
            si = np.roll(img, (dy, dx), (0, 1))
            acc += si * sf[..., None]
            cnt += sf
        grow = (~filled) & (cnt > 0)
        img[grow] = acc[grow] / cnt[grow][:, None]
        filled |= grow
    im = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    return (np.vstack(out_pos), np.vstack(out_nrm), np.vstack(out_uv), np.vstack(out_idx), im)


def jpeg_bytes(im, q=86):
    b = io.BytesIO()
    im.save(b, "JPEG", quality=q, optimize=True)
    return b.getvalue()


# ---------------------------------------------------------------- GLB texturizado
def save_glb(parts, path, title):
    buf = bytearray()
    views, accs, meshes, nodes, mats, images, textures = [], [], [], [], [], [], []
    uses_cc = False

    def add_view(data, target=None):
        while len(buf) % 4:
            buf.append(0)
        off = len(buf)
        buf.extend(data)
        v = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        return len(views) - 1

    for i, (name, P, N, UV, I, img, mat) in enumerate(parts):
        P, N, UV = P.astype(np.float32), N.astype(np.float32), UV.astype(np.float32)
        I = I.astype(np.uint32).ravel()
        a = len(accs)
        accs.append({"bufferView": add_view(P.tobytes(), 34962), "componentType": 5126, "count": len(P),
                     "type": "VEC3", "min": P.min(0).tolist(), "max": P.max(0).tolist()})
        accs.append({"bufferView": add_view(N.tobytes(), 34962), "componentType": 5126, "count": len(N), "type": "VEC3"})
        accs.append({"bufferView": add_view(UV.tobytes(), 34962), "componentType": 5126, "count": len(UV), "type": "VEC2"})
        accs.append({"bufferView": add_view(I.tobytes(), 34963), "componentType": 5125, "count": len(I), "type": "SCALAR"})
        images.append({"bufferView": add_view(jpeg_bytes(img)), "mimeType": "image/jpeg", "name": name})
        textures.append({"source": i, "sampler": 0})
        m = {"name": name, "pbrMetallicRoughness": {"baseColorTexture": {"index": i},
                                                     "metallicFactor": mat["metal"], "roughnessFactor": mat["rough"]}}
        if mat["clearcoat"]:
            uses_cc = True
            m["extensions"] = {"KHR_materials_clearcoat": {"clearcoatFactor": mat["clearcoat"], "clearcoatRoughnessFactor": 0.15}}
        mats.append(m)
        meshes.append({"name": name, "primitives": [{"attributes": {"POSITION": a, "NORMAL": a + 1, "TEXCOORD_0": a + 2},
                                                     "indices": a + 3, "material": i}]})
        nodes.append({"name": name, "mesh": i})
    nodes.append({"name": title, "children": list(range(len(parts)))})
    gltf = {"asset": {"version": "2.0", "generator": "PlatoAR texbake v1"}, "scene": 0,
            "scenes": [{"name": title, "nodes": [len(nodes) - 1]}], "nodes": nodes, "meshes": meshes,
            "materials": mats, "textures": textures, "images": images,
            "samplers": [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}],
            "accessors": accs, "bufferViews": views, "buffers": [{"byteLength": len(buf)}]}
    if uses_cc:
        gltf["extensionsUsed"] = ["KHR_materials_clearcoat"]
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    while len(buf) % 4:
        buf.append(0)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, 28 + len(js) + len(buf)))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(buf), 0x004E4942) + bytes(buf))


# ---------------------------------------------------------------- USDZ
def fmt3(a):
    return "[" + ", ".join(f"({x:.5f}, {y:.5f}, {z:.5f})" for x, y, z in a) + "]"


def fmt2(a):
    return "[" + ", ".join(f"({x:.5f}, {y:.5f})" for x, y in a) + "]"


def save_usdz(parts, path, title):
    safe = lambda s: "".join(ch if ch.isalnum() else "_" for ch in s)
    L = ['#usda 1.0', '(', '    defaultPrim = "Root"', '    metersPerUnit = 1', '    upAxis = "Y"', ')', '',
         'def Xform "Root" (', '    kind = "component"', ')', '{', '    def Scope "Materials"', '    {']
    files = {}
    for i, (name, P, N, UV, I, img, mat) in enumerate(parts):
        mn = f"m{i}_{safe(name)}"
        tex = f"textures/{mn}.jpg"
        files[tex] = jpeg_bytes(img)
        base = f"/Root/Materials/{mn}"
        L += [f'        def Material "{mn}"', '        {',
              f'            token outputs:surface.connect = <{base}/Surface.outputs:surface>',
              '            def Shader "Surface"', '            {',
              '                uniform token info:id = "UsdPreviewSurface"',
              f'                color3f inputs:diffuseColor.connect = <{base}/Tex.outputs:rgb>',
              f'                float inputs:roughness = {mat["rough"]}',
              f'                float inputs:metallic = {mat["metal"]}']
        if mat["clearcoat"]:
            L += [f'                float inputs:clearcoat = {mat["clearcoat"]}', '                float inputs:clearcoatRoughness = 0.15']
        L += ['                token outputs:surface', '            }',
              '            def Shader "UV"', '            {',
              '                uniform token info:id = "UsdPrimvarReader_float2"',
              '                token inputs:varname = "st"', '                float2 outputs:result', '            }',
              '            def Shader "Tex"', '            {',
              '                uniform token info:id = "UsdUVTexture"',
              f'                asset inputs:file = @{tex}@',
              f'                float2 inputs:st.connect = <{base}/UV.outputs:result>',
              '                token inputs:sourceColorSpace = "sRGB"',
              '                token inputs:wrapS = "clamp"', '                token inputs:wrapT = "clamp"',
              '                float3 outputs:rgb', '            }', '        }']
    L += ['    }']
    for i, (name, P, N, UV, I, img, mat) in enumerate(parts):
        mn = f"m{i}_{safe(name)}"
        st = np.stack([UV[:, 0], 1.0 - UV[:, 1]], axis=1)  # USD: origen abajo-izquierda
        L += [f'    def Mesh "{safe(name)}" (', '        prepend apiSchemas = ["MaterialBindingAPI"]', '    )', '    {',
              '        uniform token subdivisionScheme = "none"',
              '        uniform token orientation = "rightHanded"',
              f'        float3[] extent = [({P[:,0].min():.5f}, {P[:,1].min():.5f}, {P[:,2].min():.5f}), ({P[:,0].max():.5f}, {P[:,1].max():.5f}, {P[:,2].max():.5f})]',
              '        int[] faceVertexCounts = [' + ", ".join(["3"] * len(I)) + ']',
              '        int[] faceVertexIndices = [' + ", ".join(map(str, I.ravel().tolist())) + ']',
              f'        point3f[] points = {fmt3(P)}',
              f'        normal3f[] normals = {fmt3(N)} (', '            interpolation = "vertex"', '        )',
              f'        texCoord2f[] primvars:st = {fmt2(st)} (', '            interpolation = "vertex"', '        )',
              f'        rel material:binding = </Root/Materials/{mn}>', '    }']
    L += ['}', '']
    usda = "\n".join(L).encode()

    # USDZ = zip sin compresión, con cada archivo alineado a 64 bytes
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        offset = 0
        for fname, data in [("model.usda", usda)] + list(files.items()):
            zi = zipfile.ZipInfo(fname, date_time=(2026, 10, 2, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_STORED
            header = 30 + len(fname.encode())
            pad = (64 - (offset + header + 4) % 64) % 64
            zi.extra = struct.pack("<HH", 0x1986, pad) + b"\0" * pad
            z.writestr(zi, data)
            offset = z.fp.tell()


# ---------------------------------------------------------------- ejecución
def run(fn, name, outdir):
    captured = {}
    orig = glbmod.GLB.save

    def capture(self, path, title):
        captured["parts"], captured["title"] = list(self.parts), title
        return 0

    glbmod.GLB.save = capture
    try:
        fn("/dev/null")
    finally:
        glbmod.GLB.save = orig
    parts = []
    for pname, mesh, colors, mat in captured["parts"]:
        P, N, UV, I, img = bake_part(mesh, colors)
        parts.append((pname, P, N, UV, I, img, mat))
    save_glb(parts, f"{outdir}/{name}.glb", captured["title"])
    save_usdz(parts, f"{outdir}/{name}.usdz", captured["title"])


if __name__ == "__main__":
    import os
    import dishes
    out = sys.argv[1] if len(sys.argv) > 1 else "out_tex"
    os.makedirs(out, exist_ok=True)
    for fn, name in [(dishes.hamburguesa, "hamburguesa"), (dishes.empanadas, "empanadas"),
                     (dishes.chuleta_valluna, "chuleta-valluna")]:
        run(fn, name, out)
        print(name, os.path.getsize(f"{out}/{name}.glb") // 1024, "KB glb,",
              os.path.getsize(f"{out}/{name}.usdz") // 1024, "KB usdz")
