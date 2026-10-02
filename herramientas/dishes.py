"""Tres platos de muestra a escala real (1 unidad = 1 metro)."""
import math

import numpy as np

from glb import (GLB, Mesh, Noise, align_y_to, box, ellipsoid, grid, hexrgb, lathe, paint,
                 rot_y, rot_z, rot_x, rounded_cyl, srgb_to_linear)

RNG = np.random.default_rng(11)


# ---------------------------------------------------------------- piezas comunes
PLATE_PROFILE = [(0, 0.003), (0.08, 0.003), (0.085, 0.0), (0.09, 0.0), (0.12, 0.014),
                 (0.1245, 0.0165), (0.121, 0.018), (0.09, 0.006), (0, 0.006)]


def plate(radius):
    k = radius / 0.1245
    prof = [(r * k, y * (1 + 0.3 * (k - 1))) for r, y in PLATE_PROFILE]
    return lathe(prof, seg=96, sharp=(1, 3, 6))


def plate_top(radius, d):
    """Altura de la cara interna del plato a una distancia d del centro."""
    k = radius / 0.1245
    yk = 1 + 0.3 * (k - 1)
    flat_r, rim_r = 0.09 * k, 0.121 * k
    y0, y1 = 0.006 * yk, 0.018 * yk
    if d <= flat_r:
        return y0
    t = min(1.0, (d - flat_r) / (rim_r - flat_r))
    return y0 + t * (y1 - y0)


def ceramic(mesh, hexcol="#f6f3ee"):
    return paint(mesh, hexcol, Noise(2, 30, seed=3), 0.02)


def sit(mesh, y):
    """Desplaza la malla para que su punto más bajo quede a la altura y."""
    mesh.pos = mesh.pos + np.array([0, y - mesh.pos[:, 1].min(), 0])
    return mesh


def displace(mesh, noise, amt):
    mesh.pos = mesh.pos + mesh.nrm * (noise(mesh.pos) * amt)[:, None]
    return mesh.recompute_normals()


# ---------------------------------------------------------------- 1. Hamburguesa
def hamburguesa(path):
    g = GLB()
    cx = -0.03  # la hamburguesa queda corrida para dejar espacio a las papas

    tabla = rounded_cyl(0.14, 0.018, 0.004, seg=96)
    grain = Noise(3, 18, seed=21)
    wood = lambda p: np.sin(p[:, 0] * 260 + grain(p) * 4.0)
    g.add("tabla", tabla, paint(tabla, "#a8713f", wood, 0.12), rough=0.75)

    base_y = 0.018
    pan_inf = rounded_cyl(0.055, 0.022, 0.009).transform(trans=(cx, base_y, 0))
    g.add("pan_inferior", pan_inf, paint(pan_inf, "#c98a46", Noise(3, 60, seed=1), 0.12), rough=0.8)

    # lechuga: disco delgado con borde ondulado
    prof = [(0, 0)] + [(0.064 * i / 10, 0) for i in range(1, 11)] + \
           [(0.064, 0.003)] + [(0.064 * (10 - i) / 10, 0.003) for i in range(1, 11)]
    lech = lathe(prof, seg=128, sharp=(10, 11))
    ang = np.arctan2(lech.pos[:, 2], lech.pos[:, 0])
    rr = np.hypot(lech.pos[:, 0], lech.pos[:, 2]) / 0.064
    lech.pos[:, 1] += 0.0045 * np.sin(9 * ang) * rr ** 3 - 0.004 * rr ** 2
    lech.recompute_normals()
    lech = lech.transform(trans=(cx, base_y + 0.021, 0))
    g.add("lechuga", lech, paint(lech, "#6fae3a", Noise(3, 90, seed=4), 0.25), rough=0.55)

    carne = rounded_cyl(0.058, 0.017, 0.006, seg=72)
    displace(carne, Noise(4, 120, seed=5), 0.0012)
    carne = carne.transform(trans=(cx, base_y + 0.024, 0))
    g.add("carne", carne, paint(carne, "#4a2c1c", Noise(4, 140, seed=6), 0.35,
                                spots=("#2b170d", 0.45, Noise(3, 200, seed=7))), rough=0.6)

    # queso: lámina cuadrada a 45° que se derrite sobre los bordes
    q = grid(40, 40, 0.105, 0.105)
    d = np.hypot(q.pos[:, 0], q.pos[:, 2])
    q.pos[:, 1] -= np.clip(d - 0.052, 0, None) * 0.7
    q.recompute_normals()
    under = Mesh(q.pos - np.array([0, 0.0018, 0]), q.idx[:, [0, 2, 1]], -q.nrm)
    queso = q.merge(under).transform(rot=rot_y(math.pi / 4), trans=(cx, base_y + 0.0415, 0))
    g.add("queso", queso, paint(queso, "#f2b632", Noise(2, 50, seed=8), 0.06),
          rough=0.35, clearcoat=0.5)

    tomate = rounded_cyl(0.047, 0.007, 0.0025).transform(trans=(cx, base_y + 0.0425, 0.004))
    g.add("tomate", tomate, paint(tomate, "#d2322d", Noise(2, 80, seed=9), 0.1),
          rough=0.3, clearcoat=0.7)

    top_y = base_y + 0.049
    rx, ry = 0.058, 0.038
    pan = ellipsoid(rx, ry, rx, seg=72, rings=28, ymin=0.0)
    col = paint(pan, "#c27a34", Noise(3, 50, seed=10), 0.1)
    h = np.clip(pan.pos[:, 1] / ry, 0, 1)[:, None]
    col = col * (1.0 - 0.35 * h)  # más tostado arriba
    g.add("pan_superior", pan.transform(trans=(cx, top_y, 0)), col, rough=0.45, clearcoat=0.35)

    # ajonjolí
    seed_mesh = None
    for _ in range(70):
        phi = math.acos(RNG.uniform(0.45, 1.0))
        th = RNG.uniform(0, 2 * math.pi)
        p = np.array([rx * math.sin(phi) * math.cos(th), ry * math.cos(phi), rx * math.sin(phi) * math.sin(th)])
        n = np.array([p[0] / rx ** 2, p[1] / ry ** 2, p[2] / rx ** 2])
        s = ellipsoid(0.0023, 0.0008, 0.0012, seg=10, rings=5)
        s = s.transform(rot=align_y_to(n) @ rot_y(RNG.uniform(0, math.pi)), trans=p + (cx, top_y, 0))
        seed_mesh = s if seed_mesh is None else seed_mesh.merge(s)
    g.add("ajonjoli", seed_mesh, paint(seed_mesh, "#f1e1b6", Noise(1, 300, seed=2), 0.1), rough=0.5)

    # papas fritas
    fries = None
    for i in range(16):
        L = RNG.uniform(0.055, 0.085)
        f = box(0.0095, 0.0095, L)
        layer = i // 6
        rot = rot_y(RNG.uniform(-0.6, 0.6) + (0.4 if layer % 2 else 0)) @ rot_x(RNG.uniform(-0.08, 0.08))
        pos = (0.075 + RNG.uniform(-0.018, 0.018), base_y + 0.005 + layer * 0.009, RNG.uniform(-0.05, 0.05))
        f = f.transform(rot=rot, trans=pos)
        fries = f if fries is None else fries.merge(f)
    g.add("papas", fries, paint(fries, "#e7b04b", Noise(3, 120, seed=12), 0.18), rough=0.55)
    return g.save(path, "Hamburguesa artesanal")


# ---------------------------------------------------------------- 2. Empanadas
def empanada():
    m = ellipsoid(0.048, 0.017, 0.03, seg=72, rings=24)
    m = Mesh(np.vstack([m.pos, m.pos * [1, -1, 1]]),
             np.vstack([m.idx, m.idx[:, [0, 2, 1]] + len(m.pos)]))  # elipsoide completo
    p = m.pos
    ang = np.arctan2(p[:, 2] / 0.03, p[:, 0] / 0.048)
    eq = np.exp(-(p[:, 1] / 0.004) ** 2)  # zona del borde (repulgue)
    rad = 1 + 0.06 * eq * np.sin(36 * ang)
    p[:, 0] *= rad
    p[:, 2] *= rad
    p[:, 1] = np.maximum(p[:, 1], -0.005)  # base plana
    p[:, 2] += 0.016 * (1 - (p[:, 0] / 0.05) ** 2)  # forma de media luna
    p[:, 1] += 0.005
    m.recompute_normals()
    displace(m, Noise(4, 150, seed=13), 0.0007)
    return m


def empanadas(path):
    g = GLB()
    R = 0.13
    pl = plate(R)
    g.add("plato", pl, ceramic(pl), rough=0.25, clearcoat=0.6)
    y = plate_top(R, 0.0) - 0.0005
    allm = None
    for i, (x, z, yaw) in enumerate([(-0.03, -0.055, 0.25), (-0.035, 0.005, -0.1), (-0.02, 0.065, 0.2)]):
        e = empanada().transform(rot=rot_y(yaw + math.pi), trans=(x, y + 0.002 * i, z))
        allm = e if allm is None else allm.merge(e)
    g.add("empanadas", allm, paint(allm, "#d98e2c", Noise(3, 70, seed=14), 0.2,
                                   spots=("#a8601c", 0.5, Noise(3, 160, seed=15))), rough=0.5, clearcoat=0.2)

    bowl_prof = [(0, 0), (0.02, 0), (0.032, 0.026), (0.034, 0.027), (0.0305, 0.027), (0.019, 0.004), (0, 0.004)]
    bx, bz = 0.072, 0.0
    bowl = lathe(bowl_prof, seg=64, sharp=(1,)).transform(trans=(bx, y, bz))
    g.add("cuenco", bowl, paint(bowl, "#b5532c", Noise(2, 40, seed=16), 0.08), rough=0.6)
    salsa = rounded_cyl(0.0285, 0.002, 0.0008, seg=64).transform(trans=(bx, y + 0.019, bz))
    sp = Noise(3, 400, seed=17)
    col = paint(salsa, "#4f8a2a", Noise(2, 200, seed=18), 0.2, spots=("#c23b22", 0.55, sp))
    col2 = paint(salsa, "#f2f0e6", None, 0, spots=("#f2f0e6", 0.6, Noise(3, 420, seed=19)))
    mask = Noise(3, 420, seed=19)(salsa.pos) > 0.6
    col[mask] = col2[mask]
    g.add("aji", salsa, col, rough=0.15, clearcoat=1.0)

    limon = ellipsoid(0.022, 0.016, 0.014, seg=40, rings=16, ymin=0.0)
    limon = sit(limon.transform(rot=rot_z(math.pi / 2) @ rot_y(0.4), trans=(0.065, 0, 0.075)), y)
    g.add("limon", limon, paint(limon, "#6aa83a", Noise(2, 120, seed=20), 0.15), rough=0.4, clearcoat=0.5)
    return g.save(path, "Empanadas vallunas")


# ---------------------------------------------------------------- 3. Chuleta valluna
def chuleta_valluna(path):
    g = GLB()
    R = 0.15
    pl = plate(R)
    g.add("plato", pl, ceramic(pl), rough=0.25, clearcoat=0.6)

    ch = rounded_cyl(0.1, 0.011, 0.005, seg=120)
    th = np.arctan2(ch.pos[:, 2], ch.pos[:, 0])
    f = 1 + 0.07 * np.sin(2 * th + 1.0) + 0.04 * np.sin(3 * th + 2.0) + 0.02 * np.sin(7 * th)
    ch.pos[:, 0] *= f * 1.05
    ch.pos[:, 2] *= f * 0.68
    ch.recompute_normals()
    displace(ch, Noise(5, 160, seed=22), 0.0016)
    cx, cz = -0.03, 0.03
    ch = ch.transform(rot=rot_y(-0.35), trans=(cx, plate_top(R, math.hypot(cx, cz)), cz))
    g.add("chuleta", ch, paint(ch, "#bb7a32", Noise(4, 90, seed=23), 0.28,
                               spots=("#7a4a1e", 0.48, Noise(4, 210, seed=24))), rough=0.65)

    rx_, rz_ = 0.065, -0.07
    arroz = ellipsoid(0.042, 0.032, 0.038, seg=72, rings=24, ymin=0.0)
    displace(arroz, Noise(4, 420, seed=25), 0.0012)
    arroz = arroz.transform(trans=(rx_, plate_top(R, math.hypot(rx_, rz_)), rz_))
    g.add("arroz", arroz, paint(arroz, "#f4efe2", Noise(3, 500, seed=26), 0.06), rough=0.7)

    pats = None
    for (x, z, s) in [(-0.07, -0.075, 1.0), (0.088, 0.055, 0.92)]:
        p = rounded_cyl(0.032 * s, 0.008, 0.003, seg=64)
        t = np.arctan2(p.pos[:, 2], p.pos[:, 0])
        k = 1 + 0.08 * np.sin(5 * t + x * 50) + 0.05 * np.sin(3 * t)
        p.pos[:, 0] *= k
        p.pos[:, 2] *= k
        p.recompute_normals()
        displace(p, Noise(3, 180, seed=27), 0.0008)
        p = p.transform(rot=rot_y(RNG.uniform(0, 6)), trans=(x, plate_top(R, math.hypot(x, z)) + 0.001, z))
        pats = p if pats is None else pats.merge(p)
    g.add("patacones", pats, paint(pats, "#d8a43a", Noise(3, 100, seed=28), 0.22,
                                   spots=("#a46c1e", 0.55, Noise(3, 220, seed=29))), rough=0.55)

    lim = ellipsoid(0.02, 0.014, 0.013, seg=40, rings=16, ymin=0.0)
    lim = sit(lim.transform(rot=rot_z(math.pi / 2) @ rot_y(-0.6), trans=(0.0, 0, -0.105)), plate_top(R, 0.105))
    g.add("limon", lim, paint(lim, "#6aa83a", Noise(2, 120, seed=30), 0.15), rough=0.4, clearcoat=0.5)
    return g.save(path, "Chuleta valluna")


if __name__ == "__main__":
    import os
    os.makedirs("out", exist_ok=True)
    for fn, name in [(hamburguesa, "hamburguesa"), (empanadas, "empanadas"), (chuleta_valluna, "chuleta-valluna")]:
        size = fn(f"out/{name}.glb")
        print(f"{name}.glb  {size / 1024:.0f} KB")
