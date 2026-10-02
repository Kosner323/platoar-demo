"""Lee un .glb, valida su estructura y genera una vista previa PNG (rasterizador simple)."""
import json
import struct
import sys

import numpy as np
from PIL import Image

CT = {5126: np.float32, 5125: np.uint32}
NC = {"VEC3": 3, "SCALAR": 1}


def load(path):
    data = open(path, "rb").read()
    magic, ver, total = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67 and ver == 2 and total == len(data), "cabecera GLB inválida"
    jl, jt = struct.unpack_from("<II", data, 12)
    assert jt == 0x4E4F534A
    gl = json.loads(data[20:20 + jl])
    bl, bt = struct.unpack_from("<II", data, 20 + jl)
    assert bt == 0x004E4942
    binb = data[28 + jl:28 + jl + bl]

    def acc(i):
        a = gl["accessors"][i]
        v = gl["bufferViews"][a["bufferView"]]
        arr = np.frombuffer(binb, CT[a["componentType"]], a["count"] * NC[a["type"]], v["byteOffset"])
        return arr.reshape(a["count"], -1) if NC[a["type"]] > 1 else arr

    parts = []
    for m in gl["meshes"]:
        pr = m["primitives"][0]
        P, N, C = acc(pr["attributes"]["POSITION"]), acc(pr["attributes"]["NORMAL"]), acc(pr["attributes"]["COLOR_0"])
        I = acc(pr["indices"]).reshape(-1, 3)
        assert not np.isnan(P).any() and not np.isnan(N).any(), f"NaN en {m['name']}"
        assert I.max() < len(P), f"índice fuera de rango en {m['name']}"
        nl = np.linalg.norm(N, axis=1)
        assert np.all(np.abs(nl - 1) < 1e-3), f"normales no unitarias en {m['name']}"
        parts.append((m["name"], P, N, C, I))
    return gl, parts


def render(parts, out, W=900, H=650, elev=32, azim=-35):
    e, a = np.radians(elev), np.radians(azim)
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(e), -np.sin(e)], [0, np.sin(e), np.cos(e)]])
    R = Rx @ Ry
    allP = np.vstack([p[1] for p in parts])
    c = (allP.min(0) + allP.max(0)) / 2
    span = np.ptp(allP @ R.T, axis=0)
    s = 0.85 * min(W / span[0], H / span[1])
    img = np.ones((H, W, 3)) * np.array([0.93, 0.92, 0.9])
    zb = np.full((H, W), np.inf)
    L = np.array([-0.4, 0.8, 0.45]); L /= np.linalg.norm(L)
    for _, P, N, C, I in parts:
        V = (P - c) @ R.T
        X = V[:, 0] * s + W / 2
        Y = -V[:, 1] * s + H / 2
        Z = -V[:, 2]
        lin = np.clip(C, 0, 1)
        srgb = np.where(lin <= 0.0031308, lin * 12.92, 1.055 * lin ** (1 / 2.4) - 0.055)
        shade = 0.35 + 0.65 * np.clip(N @ L, 0, 1)
        col = srgb * shade[:, None]
        for t in I:
            xs, ys = X[t], Y[t]
            x0, x1 = int(max(xs.min(), 0)), int(min(xs.max() + 1, W))
            y0, y1 = int(max(ys.min(), 0)), int(min(ys.max() + 1, H))
            if x0 >= x1 or y0 >= y1:
                continue
            gx, gy = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
            d = (ys[1] - ys[2]) * (xs[0] - xs[2]) + (xs[2] - xs[1]) * (ys[0] - ys[2])
            if abs(d) < 1e-9:
                continue
            w0 = ((ys[1] - ys[2]) * (gx - xs[2]) + (xs[2] - xs[1]) * (gy - ys[2])) / d
            w1 = ((ys[2] - ys[0]) * (gx - xs[2]) + (xs[0] - xs[2]) * (gy - ys[2])) / d
            w2 = 1 - w0 - w1
            m = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
            if not m.any():
                continue
            z = w0 * Z[t[0]] + w1 * Z[t[1]] + w2 * Z[t[2]]
            sub = zb[y0:y1, x0:x1]
            m &= z < sub
            sub[m] = z[m]
            cc = w0[..., None] * col[t[0]] + w1[..., None] * col[t[1]] + w2[..., None] * col[t[2]]
            img[y0:y1, x0:x1][m] = cc[m]
    Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(out)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        gl, parts = load(path)
        P = np.vstack([p[1] for p in parts])
        dim = (P.max(0) - P.min(0)) * 100
        tris = sum(len(p[4]) for p in parts)
        print(f"{path}: OK  {len(parts)} piezas, {tris} triángulos, "
              f"ancho {dim[0]:.1f} cm x alto {dim[1]:.1f} cm x fondo {dim[2]:.1f} cm")
        render(parts, path.replace(".glb", ".png"))
