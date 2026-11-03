"""Mesh convergence of a judged value (h-refinement, Richardson) and a per-element error estimate (ZZ).

richardson(values, ratio): three values from coarse to fine meshes whose
element sizes shrink by `ratio` each time. The observed order
p = ln((v2 - v1) / (v3 - v2)) / ln(ratio), the extrapolated value at zero
size v3 + (v3 - v2) / (ratio^p - 1), and the finest value's error against it
(L. F. Richardson, The approximate arithmetical solution by finite
differences of physical problems..., Phil. Trans. R. Soc. A 210 (1911) 307-357;
the form with the observed order as in P. J. Roache, Verification and
Validation in Computational Science and Engineering, Hermosa 1998; neither
checked against the text in this repository).

convergence_record(...) makes the contract's analysis.mesh_convergence:
change_pct between the two finest levels, the Richardson numbers when they
exist, and the verdict (singular, converged, not_converged) by the rules
and thresholds below (HIRAKU's choices, no source).

zz_error(model, st, ...) is the Zienkiewicz-Zhu estimate per element: the
element-centre stresses (constant over each element) against a smooth
stress recovered by averaging at the nodes (O. C. Zienkiewicz and J. Z. Zhu,
A simple error estimator and adaptive procedure for practical engineering
analysis, Int. J. Numer. Meth. Engng 24 (1987) 337-357; not checked against
the paper in this repository). See zz_error for how a shell is handled.
"""

from __future__ import annotations

import math

import numpy as np

REFINE_RATIO = 2.0  # element size of one level over the next finer one
LIMIT_PCT = 5.0  # largest change between the two finest levels accepted as converged
SINGULAR_ORDER = 0.5  # values growing in size with an observed order at or below this (or diverging): singular
FLAT_TOL = 1e-6  # relative size of a level-to-level difference counted as zero
SAME_WALL_COS = math.cos(math.radians(20.0))  # nodal averaging only over elements whose normals are within 20 degrees
FLOOR_FACTOR = 1.0  # denominator floor: this times the model's mean recovered-stress energy per area, times the area

RICHARDSON_SOURCE = ("Richardson extrapolation with the observed order: L. F. Richardson, Phil. Trans. R. Soc. A 210 "
                     "(1911) 307-357; P. J. Roache, Verification and Validation in Computational Science and "
                     "Engineering, Hermosa 1998 (neither checked in this repository)")
ZZ_SOURCE = ("Zienkiewicz-Zhu error estimate: O. C. Zienkiewicz and J. Z. Zhu, Int. J. Numer. Meth. Engng 24 (1987) "
             "337-357 (not checked in this repository)")

CONVERGENCE_ASSUMPTION = (
    "メッシュの収束 (仮定、出典なし): 要素の大きさを {r:g} 倍ずつ細かくした 3 段で同じ量を解き直す (細かくする比 {r:g} は"
    "仮定)。決め方は変化を先に見る: 最も細かい 2 段の差の割合 change_pct が {lim:g} % 以下なら、次数 p に関係なく "
    "converged (限度 {lim:g} % は仮定)。それより大きく、下の singular の条件に当たれば singular、それ以外は not_converged。3 段の差 "
    "d1 = v2 - v1、d2 = v3 - v2 (粗い順) が同じ向きで |d1| が 0 でないとき Richardson: 観測した次数 "
    "p = ln(d1/d2)/ln {r:g}、外挿 v3 + d2/({r:g}^p - 1)、最も細かい値の誤差 |v3 - 外挿|/|外挿|。差が値の {flat:g} 倍より"
    "小さいときは 0 とみなす。d1 と d2 の向きが違う (振れる) ときと d2/d1 >= 1 (縮まない) ときは外挿しない。"
    "change_pct が限度を超え、値の大きさが段ごとに増え続け、p <= {ps:g} (d2/d1 >= {q:.3f}) または差が縮まないときは singular (鋭い角・点の荷重の"
    "ように、細かくするほど上がり続ける所。境の {ps:g} は仮定)。singular の値はそのまま判定に使えない (外挿もしない)")
ZZ_ASSUMPTION = (
    "誤差の目安 (Zienkiewicz-Zhu の考え方、仮定を含む): 外と内の面の要素の応力を全体の軸の 3 次元の応力に直し、節ごとに"
    "平均してなめらかな応力 σ* を作る。平均に入れるのは、その要素と法線のなす角が {deg:g} 度以内で厚さが同じ要素だけ "
    "(管の角やつなぎ目で、向きの違う壁の応力を混ぜないため。{deg:g} 度は仮定。角に接する要素は片側だけの平均になるので、角を回って続く応力 (管の軸方向の曲げ応力など) でも目安が少し大きく出る)。σ* は要素の中で 4 つの節の値の双一次、"
    "要素の応力 σ_h は中心の値で一定とし、差のエネルギー (平面応力のコンプライアンス、2x2 の Gauss 積分、外と内の面の和) "
    "を σ* のエネルギーで割った平方根を % で出す。分母が小さい要素 (応力のほとんど無い所) で跳ねないよう、分母に下限 "
    "(全体の σ* のエネルギーの面積あたりの平均 x {ff:g} x その要素の面積) を置いた (下限の取り方は仮定)。全体の目安は"
    "全要素の和どうしの比")


def _flat(d: float, scale: float) -> bool:
    return abs(d) <= FLAT_TOL * max(scale, 1e-300)


def richardson(values, ratio: float = REFINE_RATIO) -> dict:
    """Richardson numbers of three values coarse to fine.

    Returns {"d1", "d2", "q" (d2/d1 or None), "observed_order", "extrapolated", "error_pct"} with None
    where it is not defined, and "why" saying why not (empty when all are defined).
    """
    v1, v2, v3 = (float(v) for v in values)
    d1, d2 = v2 - v1, v3 - v2
    scale = max(abs(v1), abs(v2), abs(v3))
    out = {"d1": d1, "d2": d2, "q": None, "observed_order": None, "extrapolated": None, "error_pct": None, "why": ""}
    if _flat(d2, scale) and _flat(d1, scale):
        out.update(extrapolated=v3, error_pct=0.0, why="the three values are the same (differences below "
                                                          f"{FLAT_TOL:g} of the value); no order")
        return out
    if _flat(d2, scale):
        out.update(q=0.0, extrapolated=v3, error_pct=0.0, why="the two finest values are the same; no order")
        return out
    if _flat(d1, scale):
        out["why"] = "the two coarsest values are the same and the finest differs; no order"
        return out
    q = d2 / d1
    out["q"] = q
    if q < 0:
        out["why"] = "the differences change sign (the value swings); not extrapolated"
        return out
    if q >= 1.0:
        out["why"] = "the difference does not shrink (d2/d1 >= 1); not extrapolated"
        return out
    p = math.log(1.0 / q) / math.log(ratio)
    ext = v3 + d2 / (ratio**p - 1.0)
    out.update(observed_order=p, extrapolated=ext,
               error_pct=abs(v3 - ext) / abs(ext) * 100.0 if ext != 0 else None)
    return out


def verdict_of(values, limit_pct: float = LIMIT_PCT, ratio: float = REFINE_RATIO) -> tuple[str, float, dict | None]:
    """(verdict, change_pct, richardson numbers or None) of two or three values coarse to fine (sizes)."""
    v = [float(x) for x in values]
    if v[-1] != 0:
        change = abs(v[-1] - v[-2]) / abs(v[-1]) * 100.0
    else:  # the finest is 0: 0 % when the next is 0 too, else measured against the next (100 %), not infinite
        change = 0.0 if v[-2] == 0 else 100.0
    rich = richardson(v[-3:], ratio) if len(v) >= 3 else None
    if change <= limit_pct:  # change first: a small change is converged whatever the order
        return "converged", change, rich
    if rich is not None:
        d1, d2 = rich["d1"], rich["d2"]
        scale = max(abs(x) for x in v[-3:])
        growing = (not _flat(d1, scale) and not _flat(d2, scale) and d1 * d2 > 0
                   and abs(v[-1]) > abs(v[-2]) > abs(v[-3]))
        q_singular = ratio ** (-SINGULAR_ORDER)
        if growing and rich["q"] is not None and rich["q"] >= q_singular:
            return "singular", change, rich
    return "not_converged", change, rich


def convergence_record(quantity: str, unit: str, levels: list[dict], limit_pct: float = LIMIT_PCT,
                       ratio: float = REFINE_RATIO, notes: str = "") -> dict:
    """The contract's analysis.mesh_convergence from levels coarse to fine ({element_size_mm, value, elements?,
    mesh_id?}); values compared as given (pass sizes, e.g. |stress|, when the sign is not the point)."""
    vals = [lv["value"] for lv in levels]
    verdict, change, rich = verdict_of(vals, limit_pct, ratio)
    out = {"quantity": quantity, "unit": unit, "levels": levels, "change_pct": change, "limit_pct": limit_pct,
           "verdict": verdict}
    words = [notes] if notes else []
    if float(vals[-1]) == 0 and float(vals[-2]) != 0:
        words.append("the finest value is 0, so the change is measured against the next value (100 %)")
    if rich is not None:
        # a singular value has no limit: its order is kept (it shows how slowly it settles), not the extrapolation
        keys = ("observed_order",) if verdict == "singular" else ("observed_order", "extrapolated", "error_pct")
        for k in keys:
            if rich[k] is not None:
                out[k] = rich[k]
        if rich["why"]:
            words.append(rich["why"])
    if verdict == "singular":
        words.append("the value keeps rising as the elements shrink (a singular point: sharp corner or point load); "
                     "it cannot be judged as it stands")
    if words:
        out["notes"] = "; ".join(words)
    return out


# ------------------------------------------------------------------ Zienkiewicz-Zhu

_G = 1.0 / math.sqrt(3.0)
_GAUSS = np.array([[-_G, -_G], [_G, -_G], [_G, _G], [-_G, _G]])  # 2 x 2 points, weight 1 each
_SHAPE = 0.25 * (1 + _GAUSS[:, :1] * np.array([-1, 1, 1, -1])) * (1 + _GAUSS[:, 1:] * np.array([-1, -1, 1, 1]))


def _global_tensors(st, surface: str) -> np.ndarray:
    """(E, 3, 3) the surface's plane stress of every element in the global axes: R^T S R, R rows e1, e2, n."""
    sx, sy, txy = st.components(surface).T
    S = np.zeros((len(sx), 3, 3))
    S[:, 0, 0], S[:, 1, 1], S[:, 0, 1], S[:, 1, 0] = sx, sy, txy, txy
    R = st.frames
    return np.einsum("eki,ekl,elj->eij", R, S, R)


def _energy(s: np.ndarray, nu: np.ndarray) -> np.ndarray:
    """E x the plane-stress complementary energy density x 2 for rows (sxx, syy, sxy) (..., 3); nu broadcast."""
    sx, sy, txy = s[..., 0], s[..., 1], s[..., 2]
    return sx * sx + sy * sy - 2.0 * nu * sx * sy + 2.0 * (1.0 + nu) * txy * txy


def zz_error(model, st, same_wall_cos: float = SAME_WALL_COS, floor_factor: float = FLOOR_FACTOR) -> dict:
    """Zienkiewicz-Zhu estimate per element of a shell model's element-centre stresses `st`.

    For the outer (top) and inner (bottom) surfaces: each element's plane
    stress is turned into a 3-D tensor in the global axes. The recovered
    stress at node a of element e is the mean of those tensors over the
    elements at node a whose normal is within acos(same_wall_cos) of e's
    and whose thickness is e's (so the walls that meet at a tube's corner or
    at a joint are not mixed; each element gets its own recovered values at
    its nodes). Back in e's axes, sigma* is bilinear over e from its four
    nodes and sigma_h is the centre value; with the plane-stress compliance
    (1/E)(sx^2 + sy^2 - 2 nu sx sy + 2 (1 + nu) txy^2) and 2 x 2 Gauss points,
    err_e^2 = sum over the two surfaces of the integral of (sigma* - sigma_h)'s
    energy and ref_e^2 the same of sigma*. eta_e = 100 sqrt(err_e^2 /
    max(ref_e^2, floor_factor x A_e x sum(ref^2) / sum(A))) %, the global
    eta = 100 sqrt(sum err^2 / sum ref^2) %.

    Returns {"per_element" (E,) %, "global_pct", "err2", "ref2", "area", "floored" (E,) bool}.
    """
    elems = np.asarray(model.elements)
    xyz = model.element_xyz()  # (E, 4, 3)
    R = st.frames
    n_el = len(elems)
    E = np.asarray(model.E, float)
    nu = np.asarray(model.nu, float)
    t = np.asarray(model.thickness, float)
    normals = R[:, 2, :]

    # element-node incidence: for each (element, corner) the elements sharing that node
    flat = elems.ravel()
    order = np.argsort(flat, kind="stable")
    sorted_nodes = flat[order]
    starts = np.searchsorted(sorted_nodes, np.arange(model.n_nodes), "left")
    ends = np.searchsorted(sorted_nodes, np.arange(model.n_nodes), "right")
    owner = order // 4  # element of each sorted slot

    # 2D isoparametric geometry in each element's plane
    c = xyz.mean(axis=1)
    loc = np.einsum("eij,ekj->eki", R[:, :2, :], xyz - c[:, None, :])  # (E, 4, 2)
    dN_dxi = 0.25 * np.array([[-(1 - _GAUSS[:, 1]), (1 - _GAUSS[:, 1]), (1 + _GAUSS[:, 1]), -(1 + _GAUSS[:, 1])],
                              [-(1 - _GAUSS[:, 0]), -(1 + _GAUSS[:, 0]), (1 + _GAUSS[:, 0]), (1 - _GAUSS[:, 0])]])
    # dN_dxi (2, 4 shape, 4 gauss) -> J (E, gauss, 2, 2)
    J = np.einsum("dag,eak->egdk", dN_dxi, loc)
    detJ = np.abs(J[..., 0, 0] * J[..., 1, 1] - J[..., 0, 1] * J[..., 1, 0])  # (E, 4)
    area = detJ.sum(axis=1)

    err2 = np.zeros(n_el)
    ref2 = np.zeros(n_el)
    for surface in ("top", "bottom"):
        G = _global_tensors(st, surface)  # (E, 3, 3)
        rec = np.zeros((n_el, 4, 3, 3))
        for k in range(4):
            node = elems[:, k]
            # loop over the largest valence; nodes have few elements
            acc = np.zeros((n_el, 3, 3))
            cnt = np.zeros(n_el)
            val = ends[node] - starts[node]
            for j in range(int(val.max())):
                has = j < val
                idx = np.nonzero(has)[0]
                f = owner[starts[node[idx]] + j]
                ok = (np.einsum("ij,ij->i", normals[idx], normals[f]) >= same_wall_cos) & (np.abs(t[f] - t[idx]) <= 1e-9)
                acc[idx[ok]] += G[f[ok]]
                cnt[idx[ok]] += 1
            rec[:, k] = acc / cnt[:, None, None]  # cnt >= 1: the element itself is always in
        # back to each element's in-plane axes: (sxx, syy, sxy) at the four nodes
        r2 = np.einsum("eai,ekij,ebj->ekab", R[:, :2, :], rec, R[:, :2, :])  # (E, 4, 2, 2)
        nodal = np.stack([r2[..., 0, 0], r2[..., 1, 1], r2[..., 0, 1]], axis=-1)  # (E, 4, 3)
        at_gauss = np.einsum("ga,eac->egc", _SHAPE, nodal)  # (E, 4 gauss, 3)
        sh = st.components(surface)[:, None, :]
        nu_e = nu[:, None]
        err2 += np.sum(_energy(at_gauss - sh, nu_e) * detJ, axis=1) / E
        ref2 += np.sum(_energy(at_gauss, nu_e) * detJ, axis=1) / E
    total_ref = float(ref2.sum())
    floor = floor_factor * area * total_ref / float(area.sum()) if total_ref > 0 else np.zeros(n_el)
    den = np.maximum(ref2, floor)
    eta = np.where(den > 0, 100.0 * np.sqrt(err2 / np.where(den > 0, den, 1.0)), 0.0)
    glob = 100.0 * math.sqrt(float(err2.sum()) / total_ref) if total_ref > 0 else 0.0
    return {"per_element": eta, "global_pct": glob, "err2": err2, "ref2": ref2, "area": area,
            "floored": ref2 < floor}


def zz_assumption() -> str:
    return ZZ_ASSUMPTION.format(deg=math.degrees(math.acos(SAME_WALL_COS)), ff=FLOOR_FACTOR)


def convergence_assumption(ratio: float = REFINE_RATIO, limit_pct: float = LIMIT_PCT) -> str:
    return CONVERGENCE_ASSUMPTION.format(r=ratio, lim=limit_pct, flat=FLAT_TOL, ps=SINGULAR_ORDER,
                                         q=ratio ** (-SINGULAR_ORDER))
