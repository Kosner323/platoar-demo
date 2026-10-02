"""Generador de modelos 3D de platos a escala real (metros) en formato .glb.

Sin dependencias externas salvo numpy. Cada plato se construye con piezas
procedurales (revolución, elipsoides, rejillas deformadas) y se exporta como
glTF binario con colores por vértice y materiales PBR.
"""
import json
import math
import struct

import numpy as np

RNG = np.random.default_rng(7)


# ---------------------------------------------------------------- utilidades
def srgb_to_linear(c):
    c = np.asarray(c, dtype=np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def hexrgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)])


class Noise:
    """Ruido suave barato: suma de senos con fases aleatorias."""

    def __init__(self, octaves=4, freq=40.0, seed=0):
        r = np.random.default_rng(seed)
        self.terms = []
        f = freq
        amp = 1.0
        for _ in range(octaves):
            for _ in range(3):
                d = r.normal(size=3)
                d /= np.linalg.norm(d)
                self.terms.append((d * f, r.uniform(0, 2 * math.pi), amp))
            f *= 2.1
            amp *= 0.5
        self.norm = sum(t[2] for t in self.terms)

    def __call__(self, p):
        p = np.atleast_2d(p)
        v = np.zeros(len(p))
        for d, ph, a in self.terms:
            v += a * np.sin(p @ d + ph)
        return v / self.norm  # aprox. en [-1, 1]


def smooth_normals(pos, idx):
    n = np.zeros_like(pos)
    tri = pos[idx]
    fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    for k in range(3):
        np.add.at(n, idx[:, k], fn)
    ln = np.linalg.norm(n, axis=1, keepdims=True)
    bad = ln[:, 0] < 1e-12
    ln[bad] = 1
    n = n / ln
    n[bad] = (0, 1, 0)  # vértices aislados o polos degenerados
    return n


class Mesh:
    def __init__(self, pos, idx, nrm=None):
        self.pos = np.asarray(pos, dtype=np.float64)
        self.idx = np.asarray(idx, dtype=np.int64).reshape(-1, 3)
        self.nrm = smooth_normals(self.pos, self.idx) if nrm is None else np.asarray(nrm, dtype=np.float64)

    def merge(self, other):
        off = len(self.pos)
        return Mesh(np.vstack([self.pos, other.pos]),
                    np.vstack([self.idx, other.idx + off]),
                    np.vstack([self.nrm, other.nrm]))

    def transform(self, scale=(1, 1, 1), rot=None, trans=(0, 0, 0)):
        s = np.asarray(scale, dtype=np.float64)
        R = np.eye(3) if rot is None else np.asarray(rot)
        pos = (self.pos * s) @ R.T + np.asarray(trans)
        nrm = (self.nrm / s) @ R.T
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        bad = ln[:, 0] < 1e-12
        ln[bad] = 1
        nrm = nrm / ln
        nrm[bad] = (0, 1, 0)
        return Mesh(pos, self.idx.copy(), nrm)

    def recompute_normals(self):
        self.nrm = smooth_normals(self.pos, self.idx)
        return self

    def fix_winding(self):
        tri = self.pos[self.idx]
        fn = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        vn = self.nrm[self.idx].sum(axis=1)
        flip = (fn * vn).sum(axis=1) < 0
        self.idx[flip] = self.idx[flip][:, [0, 2, 1]]
        return self


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0], [0, c, -s], [0, s, c]])


def rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def align_y_to(n):
    """Rotación que lleva el eje +Y a la dirección n."""
    n = n / np.linalg.norm(n)
    y = np.array([0.0, 1.0, 0.0])
    v = np.cross(y, n)
    c = float(y @ n)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else rot_x(math.pi)
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * (1 / (1 + c))


# ---------------------------------------------------------------- primitivas
def lathe(profile, seg=64, sharp=()):
    """Superficie de revolución sobre el eje Y.

    profile: lista de (r, y) recorrida de abajo/eje hacia afuera, arriba y de
    vuelta al eje (sentido antihorario visto con r a la derecha). Los índices
    en `sharp` duplican vértices para dejar aristas marcadas.
    """
    pts = [tuple(p) for p in profile]
    cuts = sorted(set(sharp))
    pieces, start = [], 0
    for c in cuts:
        pieces.append(pts[start:c + 1])
        start = c
    pieces.append(pts[start:])
    out = None
    for poly in pieces:
        if len(poly) < 2:
            continue
        P = np.array(poly)
        m = len(P)
        tang = np.zeros_like(P)
        for i in range(m):
            a = P[max(i - 1, 0)]
            b = P[min(i + 1, m - 1)]
            tang[i] = b - a
        n2 = np.stack([tang[:, 1], -tang[:, 0]], axis=1)
        n2 /= np.linalg.norm(n2, axis=1, keepdims=True) + 1e-12
        th = np.linspace(0, 2 * math.pi, seg + 1)
        pos, nrm = [], []
        for t in th:
            c, s = math.cos(t), math.sin(t)
            for (r, y), (nr, ny) in zip(P, n2):
                pos.append((r * c, y, r * s))
                nrm.append((nr * c, ny, nr * s))
        idx = []
        for j in range(seg):
            for i in range(m - 1):
                a = j * m + i
                b = (j + 1) * m + i
                idx += [(a, b, a + 1), (a + 1, b, b + 1)]
        mesh = Mesh(pos, idx, nrm).fix_winding()
        out = mesh if out is None else out.merge(mesh)
    return out


def rounded_cyl(r, h, bevel, seg=64, steps=6, rings=14):
    inner = r - bevel
    prof = [(inner * i / rings, 0) for i in range(rings)]
    for k in range(steps + 1):
        a = -math.pi / 2 + (math.pi / 2) * k / steps
        prof.append((r - bevel + bevel * math.cos(a), bevel + bevel * math.sin(a)))
    for k in range(steps + 1):
        a = (math.pi / 2) * k / steps
        prof.append((r - bevel + bevel * math.cos(a), h - bevel + bevel * math.sin(a)))
    prof += [(inner * (rings - 1 - i) / rings, h) for i in range(rings)]
    return lathe(prof, seg)


def ellipsoid(rx, ry, rz, seg=48, rings=24, ymin=-1.0):
    """Elipsoide (o casquete si ymin > -1, en fracción de ry), con tapa plana."""
    prof = []
    a0 = math.asin(max(-1.0, min(1.0, ymin)))
    if ymin > -1:
        prof.append((0, ymin))
    for k in range(rings + 1):
        a = a0 + (math.pi / 2 - a0) * k / rings
        prof.append((math.cos(a), math.sin(a)))
    m = lathe(prof, seg, sharp=(1,) if ymin > -1 else ())
    return m.transform(scale=(rx, ry, rz))


def grid(nx, nz, sx, sz):
    xs = np.linspace(-sx / 2, sx / 2, nx + 1)
    zs = np.linspace(-sz / 2, sz / 2, nz + 1)
    X, Z = np.meshgrid(xs, zs, indexing="ij")
    pos = np.stack([X.ravel(), np.zeros(X.size), Z.ravel()], axis=1)
    idx = []
    for i in range(nx):
        for j in range(nz):
            a = i * (nz + 1) + j
            b = (i + 1) * (nz + 1) + j
            idx += [(a, a + 1, b), (b, a + 1, b + 1)]
    return Mesh(pos, idx, np.tile([0, 1, 0], (len(pos), 1)))


def box(sx, sy, sz):
    """Caja con aristas duras (24 vértices)."""
    hx, hy, hz = sx / 2, sy / 2, sz / 2
    faces = [
        ((1, 0, 0), [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz), (-hx, -hy, -hz)]),
        ((0, 1, 0), [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, hz), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz)]),
        ((0, 0, 1), [(hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz), (-hx, -hy, hz)]),
        ((0, 0, -1), [(-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz), (hx, -hy, -hz)]),
    ]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        b = len(pos)
        pos += quad
        nrm += [n] * 4
        idx += [(b, b + 1, b + 2), (b, b + 2, b + 3)]
    return Mesh(pos, idx, nrm).fix_winding()


# ---------------------------------------------------------------- color
def paint(mesh, base_hex, noise=None, amount=0.15, spots=None):
    """Colores por vértice: color base * (1 + ruido); spots=(hex, umbral, ruido)."""
    base = hexrgb(base_hex)
    col = np.tile(base, (len(mesh.pos), 1))
    if noise is not None:
        v = noise(mesh.pos)
        col = col * (1 + amount * v)[:, None]
    if spots is not None:
        hx, thr, nz = spots
        mask = nz(mesh.pos) > thr
        col[mask] = hexrgb(hx) * (0.9 + 0.2 * RNG.random((mask.sum(), 1)))
    return srgb_to_linear(np.clip(col, 0, 1))


# ---------------------------------------------------------------- exportador
class GLB:
    def __init__(self):
        self.parts = []  # (nombre, mesh, colores, material)

    def add(self, name, mesh, colors, rough=0.7, metal=0.0, clearcoat=None):
        self.parts.append((name, mesh, colors, dict(rough=rough, metal=metal, clearcoat=clearcoat)))

    def save(self, path, title):
        buf = bytearray()
        views, accs, meshes, nodes, mats = [], [], [], [], []
        uses_cc = False

        def add_view(data, target):
            while len(buf) % 4:
                buf.append(0)
            off = len(buf)
            buf.extend(data)
            views.append({"buffer": 0, "byteOffset": off, "byteLength": len(data), "target": target})
            return len(views) - 1

        for i, (name, m, col, mat) in enumerate(self.parts):
            pos = m.pos.astype(np.float32)
            nrm = m.nrm.astype(np.float32)
            colf = np.asarray(col, dtype=np.float32)
            idx = m.idx.astype(np.uint32).ravel()
            a_pos = len(accs)
            accs.append({"bufferView": add_view(pos.tobytes(), 34962), "componentType": 5126,
                         "count": len(pos), "type": "VEC3",
                         "min": pos.min(axis=0).tolist(), "max": pos.max(axis=0).tolist()})
            accs.append({"bufferView": add_view(nrm.tobytes(), 34962), "componentType": 5126,
                         "count": len(nrm), "type": "VEC3"})
            accs.append({"bufferView": add_view(colf.tobytes(), 34962), "componentType": 5126,
                         "count": len(colf), "type": "VEC3"})
            accs.append({"bufferView": add_view(idx.tobytes(), 34963), "componentType": 5125,
                         "count": len(idx), "type": "SCALAR"})
            material = {"name": name, "pbrMetallicRoughness": {
                "baseColorFactor": [1, 1, 1, 1], "metallicFactor": mat["metal"],
                "roughnessFactor": mat["rough"]}}
            if mat["clearcoat"]:
                uses_cc = True
                material["extensions"] = {"KHR_materials_clearcoat": {
                    "clearcoatFactor": mat["clearcoat"], "clearcoatRoughnessFactor": 0.15}}
            mats.append(material)
            meshes.append({"name": name, "primitives": [{
                "attributes": {"POSITION": a_pos, "NORMAL": a_pos + 1, "COLOR_0": a_pos + 2},
                "indices": a_pos + 3, "material": i}]})
            nodes.append({"name": name, "mesh": i})

        nodes.append({"name": title, "children": list(range(len(self.parts)))})
        gltf = {
            "asset": {"version": "2.0", "generator": "PlatoAR procedural v1"},
            "scene": 0,
            "scenes": [{"name": title, "nodes": [len(nodes) - 1]}],
            "nodes": nodes, "meshes": meshes, "materials": mats,
            "accessors": accs, "bufferViews": views,
            "buffers": [{"byteLength": len(buf)}],
        }
        if uses_cc:
            gltf["extensionsUsed"] = ["KHR_materials_clearcoat"]
        js = json.dumps(gltf, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        while len(buf) % 4:
            buf.append(0)
        total = 12 + 8 + len(js) + 8 + len(buf)
        with open(path, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(js), 0x4E4F534A))
            f.write(js)
            f.write(struct.pack("<II", len(buf), 0x004E4942))
            f.write(buf)
        return total
