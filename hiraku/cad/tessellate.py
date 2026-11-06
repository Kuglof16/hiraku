"""Volume and first moments of a closed shell, face by face (divergence theorem).

For a face on a curved surface x(u, v), the flux of the fields F = x/3 (div F = 1) and G = x (x.n)/4
(div G = x) through it is the integral over the trimmed region D in (u, v) of
g(u, v) = [x.N/3, x (x.N)/4] with N = x_u x x_v. The face's bounds are sampled in 3D (shared edges
are sampled once), mapped into (u, v) by the surface's inversion, and joined into closed polygons:

- periodic parameters are unwrapped along each loop; a seam edge (an edge used twice in one loop)
  is put one period away the second time, on the side the face lies;
- a pole (where x_u or x_v is zero: a sphere's poles, a cone's apex, a degenerate B-spline edge)
  becomes a segment along the pole, from the neighbours' parameter to the next one's;
- a loop that goes round the surface once is closed along the pole on the face's side, and two that
  go round in opposite senses are joined into one band (the joining segments are one period apart
  and cancel).

D is then split into signed triangles fanned from one point (c, p_i, p_i+1): their signed sum is
D's indicator for any closed polygons (outer loops counter-clockwise, holes clockwise in (u, v)
when the face agrees with the surface's normal, all reversed when it does not, and N's sign then
follows), so no ear clipping is needed. Each triangle is integrated with Gauss points in the
collapsed square (radially in pieces, two points across).
Planar faces are integrated exactly from their polygons.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np

from hiraku.cad import CadError
from hiraku.cad.surfaces import Surface

LINE_SUB = 8  # pieces per straight edge segment on a curved face, times the refinement
SPLIT = 32  # no polygon side in (u, v) longer than the face's extent / (SPLIT x refinement)
_GX6, _GW6 = np.polynomial.legendre.leggauss(4)  # per radial piece
_GX2, _GW2 = np.polynomial.legendre.leggauss(2)


def plane_integrals(loops: list[np.ndarray]) -> np.ndarray:
    """[V, Sx, Sy, Sz] of the cones from the origin to a planar face (signed by loop orientation)."""
    out = np.zeros(4)
    q = next((L[0] for L in loops if len(L)), None)
    if q is None:
        return out
    for L in loops:
        if len(L) < 3:
            continue
        a = L
        b = np.roll(L, -1, axis=0)
        qa = np.broadcast_to(q, a.shape)
        vol = np.einsum("ij,ij->i", qa, np.cross(a, b)) / 6.0
        out[0] += vol.sum()
        out[1:] += ((qa + a + b) / 4.0 * vol[:, None]).sum(axis=0)
    return out


def _densify(pts: np.ndarray, k: int) -> np.ndarray:
    if k <= 1 or len(pts) < 2:
        return pts
    s = np.arange(k) / k
    a, b = pts[:-1], pts[1:]
    out = (a[:, None, :] + s[None, :, None] * (b - a)[:, None, :]).reshape(-1, 3)
    return np.vstack([out, pts[-1:]])


def _unwrap(x: np.ndarray, period: float) -> np.ndarray:
    if len(x) < 2:
        return x.copy()
    d = np.diff(x)
    d -= period * np.round(d / period)
    return np.concatenate([[x[0]], x[0] + np.cumsum(d)])


def _spans(S: Surface) -> list[float]:
    out = []
    for ax in (0, 1):
        if S.periods[ax]:
            out.append(S.periods[ax])
        elif S.domain[ax] is not None:
            out.append(max(S.domain[ax][1] - S.domain[ax][0], 1e-300))
        else:
            out.append(1.0)
    return out


def _singular(S: Surface, uv: np.ndarray) -> np.ndarray:
    _, Su, Sv = S.ev(uv[:, 0], uv[:, 1])
    sp = _spans(S)
    out = np.zeros(uv.shape, bool)
    out[:, 0] = np.linalg.norm(Su, axis=1) * sp[0] <= 1e-7 * S.scale
    out[:, 1] = np.linalg.norm(Sv, axis=1) * sp[1] <= 1e-7 * S.scale
    out[:, 1] &= ~out[:, 0]
    return out


def _mapped(S: Surface, loops: list, refine: int) -> list:
    """Every chunk's points in (u, v) and their pole flags, with one inversion for the whole face."""
    dense = [[_densify(p, LINE_SUB * refine) if straight else p for p, _, straight in chunks] for chunks in loops]
    flat = [p for d in dense for p in d]
    if not flat:
        return [[] for _ in loops]
    allp = np.vstack(flat)
    uv = S.inv(allp)
    sing = _singular(S, uv)
    out, a = [], 0
    for d, chunks in zip(dense, loops):
        row = []
        for p, (_, key, _) in zip(d, chunks):
            row.append((uv[a:a + len(p)].copy(), sing[a:a + len(p)], key))
            a += len(p)
        out.append(row)
    return out


def loop_uv(S: Surface, mapped: list, same_sense: bool) -> tuple[np.ndarray, list]:
    """The loop in (u, v) (the closing point left out) and how many times it goes round each periodic
    parameter. mapped: [((u, v) of the edge's points from its start to its end in the loop's
    direction, pole flags, edge key)]."""
    per = S.periods
    counts = Counter(k for _, _, k in mapped if k is not None)
    seam: dict = {}
    prev = [None, None]
    parts, sings = [], []
    for uv, sing, key in mapped:
        for ax in (0, 1):
            P = per[ax]
            good = ~sing[:, ax]
            if P is None or not good.any():
                continue
            vals = _unwrap(uv[good, ax], P)
            if key in seam and seam[key][0] == ax:
                target, ref = seam[key][1], float(vals.mean())
            elif prev[ax] is not None:
                target, ref = prev[ax], vals[0]
            else:
                target = ref = vals[0]
            uv[good, ax] = vals + P * round((target - ref) / P)
        if key is not None and counts[key] == 2 and key not in seam:
            ax = _seam_axis(uv, sing, per)
            if ax is not None:
                g = ~sing.any(axis=1)
                if g.sum() >= 2:
                    d = np.sign(uv[g][-1, 1 - ax] - uv[g][0, 1 - ax])
                    side = (-d if ax == 0 else d) * (1 if same_sense else -1)
                    seam[key] = (ax, float(uv[~sing[:, ax], ax].mean()) + side * per[ax])
        for ax in (0, 1):
            good = ~sing[:, ax]
            if good.any():
                prev[ax] = float(uv[good][-1, ax])
        parts.append(uv[:-1])
        sings.append(sing[:-1])
    uv = np.vstack(parts) if parts else np.zeros((0, 2))
    sing = np.vstack(sings) if sings else np.zeros((0, 2), bool)
    ks = [0, 0]
    for ax in (0, 1):
        if per[ax] and len(uv) and not sing[0, ax] and prev[ax] is not None:
            ks[ax] = int(round((prev[ax] - uv[0, ax]) / per[ax]))
    if sing.any():
        uv = _split_poles(uv, sing, ks, per)
    return uv, ks


def _seam_axis(uv, sing, per):
    axes = [ax for ax in (0, 1) if per[ax]]
    if len(axes) == 1:
        return axes[0]
    if not axes:
        return None
    rng = [np.ptp(uv[~sing[:, ax], ax]) if (~sing[:, ax]).any() else np.inf for ax in (0, 1)]
    return int(np.argmin(rng))


def _split_poles(uv, sing, ks, per):
    """Each point where one parameter is undefined becomes two: with the previous and with the next
    point's value of that parameter."""
    n = len(uv)
    out = []
    for i in range(n):
        if not sing[i].any():
            out.append(uv[i])
            continue
        ax = 0 if sing[i, 0] else 1
        good = np.flatnonzero(~sing[:, ax])
        if not len(good):
            out.append(uv[i])
            continue
        shift = (ks[ax] * per[ax]) if per[ax] else 0.0
        before = good[good < i]
        after = good[good > i]
        pv = uv[before[-1], ax] if len(before) else uv[good[-1], ax] - shift
        nv = uv[after[0], ax] if len(after) else uv[good[0], ax] + shift
        a, b = uv[i].copy(), uv[i].copy()
        a[ax], b[ax] = pv, nv
        out += [a, b]
    return np.array(out)


def face_polygons(S: Surface, loops: list, same_sense: bool, refine: int) -> list[np.ndarray]:
    """Closed polygons in (u, v) whose signed fan gives the face's region."""
    closed, opened = [], []
    loops = [c for c in loops if c and not all(len(p) < 2 for p, _, _ in c)]  # vertex loops: apex or pole
    for mapped in _mapped(S, loops, refine):
        uv, ks = loop_uv(S, mapped, same_sense)
        if len(uv) < 2:
            continue
        if ks[0] and ks[1]:
            raise CadError(f"a {S.kind} face with a loop that goes round it both ways")
        if ks[0] or ks[1]:
            ax = 0 if ks[0] else 1
            if abs(ks[ax]) != 1:
                raise CadError(f"a {S.kind} face with a loop that goes round it {abs(ks[ax])} times")
            opened.append((uv, ax, ks[ax]))
        else:
            closed.append(uv)
    polys = []
    if opened:
        axes = {ax for _, ax, _ in opened}
        if len(axes) > 1:
            raise CadError(f"a {S.kind} face with loops that go round it in both parameters")
        ax = axes.pop()
        P = S.periods[ax]
        ss = 1 if same_sense else -1
        if len(opened) == 1:
            uv, _, k = opened[0]
            side = (k if ax == 0 else -k) * ss
            b = S.pole(ax, side, uv)
            a0, a1 = uv[0, ax], uv[0, ax] + k * P
            e1, e2 = np.empty(2), np.empty(2)
            e1[ax], e1[1 - ax] = a1, b
            e2[ax], e2[1 - ax] = a0, b
            polys.append(np.vstack([_with_end(uv, ax, k * P), e1, e2]))
        elif len(opened) == 2 and opened[0][2] == -opened[1][2]:
            (A, _, ka), (B, _, kb) = opened
            A, B = _with_end(A, ax, ka * P), _with_end(B, ax, kb * P)
            B[:, ax] += P * round((A[-1, ax] - B[0, ax]) / P)
            polys.append(np.vstack([A, B]))
        else:
            raise CadError(f"a {S.kind} face bounded by {len(opened)} loops that go round it "
                           f"({', '.join(str(k) for _, _, k in opened)} times)")
    polys += closed
    if len(polys) > 1:
        ref = polys[int(np.argmax([abs(_area2(p)) for p in polys]))].mean(axis=0)
        for i, p in enumerate(polys):
            for ax in (0, 1):
                if S.periods[ax]:
                    P = S.periods[ax]
                    polys[i] = p = p.copy()
                    p[:, ax] += P * round((ref[ax] - p[:, ax].mean()) / P)
    return polys


def _with_end(uv: np.ndarray, ax: int, shift: float) -> np.ndarray:
    """The open loop with its closing point (the start, one period on) put back."""
    end = uv[0].copy()
    end[ax] += shift
    return np.vstack([uv, end])


def _area2(p: np.ndarray) -> float:
    q = np.roll(p, -1, axis=0)
    return float(np.sum(p[:, 0] * q[:, 1] - p[:, 1] * q[:, 0]))


def _split_long(p: np.ndarray, hmax: float) -> np.ndarray:
    """The closed polygon with every side longer than hmax cut into equal pieces (a fan triangle
    on a long side gets Gauss points across it)."""
    q = np.roll(p, -1, axis=0)
    n = np.maximum(1, np.ceil(np.linalg.norm(q - p, axis=1) / hmax).astype(int))
    if (n == 1).all():
        return p
    idx = np.repeat(np.arange(len(p)), n)
    frac = (np.arange(len(idx)) - np.repeat(np.cumsum(n) - n, n)) / n[idx]
    return p[idx] + (q - p)[idx] * frac[:, None]


def _composite(m: int):
    s = np.concatenate([(k + (_GX6 + 1) / 2) / m for k in range(m)])
    w = np.concatenate([_GW6 / (2 * m)] * m)
    return s, w


def fan_integrals(S: Surface, polys: list[np.ndarray], about: np.ndarray, refine: int) -> np.ndarray:
    """[V, Sx, Sy, Sz] (about `about`) of the face's flux, from the signed fan of its polygons."""
    polys = [p for p in polys if len(p) >= 2]
    if not polys:
        return np.zeros(4)
    allp = np.vstack(polys)
    hmax = float(np.ptp(allp, axis=0).max()) / (SPLIT * refine)
    polys = [_split_long(p, hmax) for p in polys]
    A = np.vstack(polys)
    B = np.vstack([np.roll(p, -1, axis=0) for p in polys])
    c = A.mean(axis=0)
    D, F = A - c, B - A
    cr = D[:, 0] * F[:, 1] - D[:, 1] * F[:, 0]
    keep = cr != 0
    D, F, cr = D[keep], F[keep], cr[keep]
    if not len(cr):
        return np.zeros(4)
    m = max(1, int(S.pieces(A.min(axis=0), A.max(axis=0)))) * refine
    s, ws = _composite(m)
    t, wt = (_GX2 + 1) / 2, _GW2 / 2
    uv = c + s[None, :, None, None] * (D[:, None, None, :] + t[None, None, :, None] * F[:, None, None, :])
    w = cr[:, None, None] * (s * ws)[None, :, None] * wt[None, None, :]
    uv, w = uv.reshape(-1, 2), w.ravel()
    total = np.zeros(4)
    step = 40000
    for a in range(0, len(w), step):
        P, Su, Sv = S.ev(uv[a:a + step, 0], uv[a:a + step, 1])
        x = P - about
        n = np.cross(Su, Sv)
        xn = np.einsum("ij,ij->i", x, n) * w[a:a + step]
        total[0] += xn.sum() / 3.0
        total[1:] += (x * xn[:, None]).sum(axis=0) / 4.0
    return total


def inside(polys: list[np.ndarray], pts: np.ndarray) -> np.ndarray:
    """Even-odd rule over all polygons."""
    out = np.zeros(len(pts), bool)
    x, y = pts[:, 0][:, None], pts[:, 1][:, None]
    for poly in polys:
        q = np.vstack([poly, poly[:1]])
        x1, y1 = q[:-1, 0][None], q[:-1, 1][None]
        x2, y2 = q[1:, 0][None], q[1:, 1][None]
        cond = (y1 > y) != (y2 > y)
        with np.errstate(divide="ignore", invalid="ignore"):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
        out ^= (cond & (x < xc)).sum(axis=1) % 2 == 1
    return out


def surface_samples(S: Surface, polys: list[np.ndarray], n: int = 25) -> np.ndarray:
    """Points of the surface inside the face (for its bounding box)."""
    polys = [p for p in polys if len(p) >= 3]
    if not polys:
        return np.zeros((0, 3))
    allp = np.vstack(polys)
    lo, hi = allp.min(axis=0), allp.max(axis=0)
    g = np.stack(np.meshgrid(np.linspace(lo[0], hi[0], n), np.linspace(lo[1], hi[1], n)), -1).reshape(-1, 2)
    g = np.vstack([g[inside(polys, g)], allp])  # the polygon's own points reach the poles
    return S.ev(g[:, 0], g[:, 1])[0]


def curved_face(S: Surface, loops: list, same_sense: bool, about: np.ndarray, refine: int):
    """([V, Sx, Sy, Sz] about `about`, sample points) of one face on a curved surface."""
    polys = face_polygons(S, loops, same_sense, refine)
    if not math.isfinite(float(np.sum([p.sum() for p in polys]) if polys else 0.0)):
        raise CadError(f"a {S.kind} face that could not be put in its parameters")
    return fan_integrals(S, polys, about, refine), surface_samples(S, polys)
