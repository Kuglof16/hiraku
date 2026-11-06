"""What a crash run is read for: crush, the room left in front of the occupants, the battery mounts' forces,
the plastic hinges and the broken members.

The occupant's part boxes move with their carried_by nodes (the boxes keep their shape and turn with
nothing: the mean displacement of those nodes, weighted by the part's mass share on each). The
structure ahead of a box is every mesh node and the middle of every element not broken by then whose
(y, z) is inside the box's (y, z) extent and whose x is ahead of the box's rear face; the room left is
the least x distance from those points back to the box's front face (negative: the structure is
inside the box). Part boxes turned by rpy_deg are taken as their unturned size.
"""

from __future__ import annotations

import numpy as np

from .sim import WINDOW, CrashRun, hinge_rotations


def front_nodes(run: CrashRun) -> list[int]:
    """The frame's mesh nodes at the front-most x at the start (they touch the wall first; the wheel nodes are not
    among them)."""
    x = run.mesh.x0[:run.mesh.n_frame, 0]
    return [int(i) for i in np.nonzero(x >= x.max() - 1e-9)[0]]


def shortening(run: CrashRun, ref: list[int], front: list[int] | None = None) -> np.ndarray:
    """(rows,) m: how much the x distance from the front nodes back to the ref nodes (their means) has shortened."""
    front = front_nodes(run) if front is None else front
    gap = run.positions[:, front, 0].mean(axis=1) - run.positions[:, ref, 0].mean(axis=1)
    return gap[0] - gap


def crush(run: CrashRun, ref: list[int]) -> np.ndarray:
    """(rows,) m: how far the ref nodes (their mean) have moved toward the wall since the frame's front-most node
    reached it (the start, when the wall is at that node; later by the gap / the speed when the wall is ahead of
    it), at least 0. While the front is held at the wall this is the shortening of the structure ahead of the ref
    nodes (shortening()); unlike that, it does not count a broken-off front piece moving back from the wall."""
    gap = run.wall_x - float(run.mesh.x0[:run.mesh.n_frame, 0].max())
    travel = run.positions[:, ref, 0].mean(axis=1) - run.mesh.x0[ref, 0].mean()
    return np.maximum(travel - max(gap, 0.0), 0.0)


def _points(run: CrashRun, k: int) -> np.ndarray:
    """The frame's mesh nodes and the middles of its elements not broken by row k (m); the wheel nodes and links are
    left out."""
    p = run.positions[k]
    t = run.t_all[k]
    alive = [e for e in run.mesh.frame_elements() if run.broken_at.get(e, np.inf) > t + 1e-12]
    con = run.mesh.con[alive]
    nodes = p[:run.mesh.n_frame]
    return np.vstack([nodes, 0.5 * (p[con[:, 0]] + p[con[:, 1]])]) if alive else nodes


def occupant_room(run: CrashRun, parts: list[dict], shares: dict[str, dict[int, float]]) -> dict | None:
    """The least room left (m) over the run between the structure ahead of any occupant part box and the
    box's front face (see the module text): {"room", "part", "row", "start"} (start: the same at t = 0),
    or None when no structure is ahead of any box."""
    boxes = []
    for p in parts:
        sh = shares.get(p["id"])
        if not sh:
            continue
        idx = np.array(list(sh.keys()))
        wts = np.array(list(sh.values()))
        c = np.asarray(p["box"]["center_mm"], float) / 1000.0
        h = np.asarray(p["box"]["size_mm"], float) / 2000.0
        boxes.append((p["id"], idx, wts / wts.sum(), c, h))
    if not boxes:
        return None
    best = None
    start = None
    for k in range(len(run.t_all)):
        pts = _points(run, k)
        for pid, idx, wts, c, h in boxes:
            move = (run.positions[k][idx] - run.mesh.x0[idx]).T @ wts
            cc = c + move
            inside = ((np.abs(pts[:, 1] - cc[1]) <= h[1]) & (np.abs(pts[:, 2] - cc[2]) <= h[2])
                      & (pts[:, 0] > cc[0] - h[0]))
            if not inside.any():
                continue
            room = float((pts[inside, 0] - (cc[0] + h[0])).min())
            if k == 0 and (start is None or room < start):
                start = room
            if best is None or room < best["room"]:
                best = {"room": room, "part": pid, "row": k}
    if best is not None:
        best["start"] = start
    return best


def occupant_room_by_member(run: CrashRun, parts: list[dict], shares: dict[str, dict[int, float]]) -> dict:
    """occupant_room per frame member: {member: {"room" (m, the least over the run), "part", "row"}} for every
    member some point of which (its elements' ends and middles, not broken by then) comes ahead of an
    occupant part box within the box's (y, z) extent; the same reading as occupant_room."""
    boxes = []
    for p in parts:
        sh = shares.get(p["id"])
        if not sh:
            continue
        idx = np.array(list(sh.keys()))
        wts = np.array(list(sh.values()))
        boxes.append((p["id"], idx, wts / wts.sum(), np.asarray(p["box"]["center_mm"], float) / 1000.0,
                      np.asarray(p["box"]["size_mm"], float) / 2000.0))
    out: dict[str, dict] = {}
    con = run.mesh.con
    for k in range(len(run.t_all)):
        p = run.positions[k]
        t = run.t_all[k]
        alive = [e for e in run.mesh.frame_elements() if run.broken_at.get(e, np.inf) > t + 1e-12]
        if not alive:
            continue
        ends_a, ends_b = p[con[alive, 0]], p[con[alive, 1]]
        pts = np.stack([ends_a, 0.5 * (ends_a + ends_b), ends_b], axis=1)  # (elements, 3, xyz)
        for pid, idx, wts, c, h in boxes:
            cc = c + (p[idx] - run.mesh.x0[idx]).T @ wts
            inside = ((np.abs(pts[..., 1] - cc[1]) <= h[1]) & (np.abs(pts[..., 2] - cc[2]) <= h[2])
                      & (pts[..., 0] > cc[0] - h[0]))
            if not inside.any():
                continue
            room = np.where(inside, pts[..., 0] - (cc[0] + h[0]), np.inf).min(axis=1)
            for j in np.flatnonzero(np.isfinite(room)):
                mid = run.mesh.member_of[alive[j]]
                if mid not in out or room[j] < out[mid]["room"]:
                    out[mid] = {"room": float(room[j]), "part": pid, "row": k}
    return out


def mount_force(run: CrashRun, share: dict[int, float], window: float = WINDOW) -> tuple[float, int, int]:
    """The largest force (N) between a part and one of its carried_by nodes: the part's share on the node
    times the node's acceleration, averaged over the window (its velocity change over it). (force, node, row)."""
    w = run.window_rows(window)
    best = (0.0, -1, 0)
    vel = run.velocities
    for node, kg in share.items():
        dv = vel[w:, node, :] - vel[:-w, node, :]
        a = np.linalg.norm(dv, axis=1) / (w * run.chunk)
        k = int(np.argmax(a)) if len(a) else 0
        f = kg * float(a[k]) if len(a) else 0.0
        if f > best[0]:
            best = (f, int(node), k + w)
    return best


def member_hinges(run: CrashRun) -> dict[str, float]:
    """rad, the largest plastic hinge rotation at the end of the run over each member's elements."""
    hr = hinge_rotations(run.final)
    out: dict[str, float] = {}
    for e in run.mesh.frame_elements():
        mid = run.mesh.member_of[e]
        out[mid] = max(out.get(mid, 0.0), float(hr[e]))
    return out


def box_front(part: dict) -> float:
    """m, the x of a part box's front face at the start."""
    return (float(part["box"]["center_mm"][0]) + float(part["box"]["size_mm"][0]) / 2.0) / 1000.0


def parts_past_wall(run: CrashRun, parts: list[dict], shares: dict[str, dict[int, float]]) -> dict[str, float]:
    """{part: m} the part boxes whose front face, moved with their carried_by nodes, goes past the wall face over
    the run, and the deepest (the boxes have no contact: such a part is taken as crushed against the wall)."""
    out = {}
    for p in parts:
        sh = shares.get(p["id"])
        if not sh or not p.get("box"):
            continue
        idx = np.array(list(sh.keys()))
        wts = np.array(list(sh.values()), dtype=float)
        if wts.sum() <= 0.0:  # a massless part: its nodes alike
            wts = np.ones(len(idx))
        move = (run.positions[:, idx, 0] - run.mesh.x0[idx, 0]) @ (wts / wts.sum())
        depth = float((box_front(p) + move - run.wall_x).max())
        if depth > 0.0:
            out[p["id"]] = depth
    return out


def reaches_occupants(run: CrashRun, nodes: list[int], parts: list[dict]) -> bool:
    """Whether a piece (its nodes, at the start) has a node inside some occupant part box's y-z extent and ahead of
    the box's rear face: a piece that can come into the occupants' space."""
    x0 = run.mesh.x0[nodes]
    for p in parts:
        c = np.asarray(p["box"]["center_mm"], float) / 1000.0
        h = np.asarray(p["box"]["size_mm"], float) / 2000.0
        inside = ((np.abs(x0[:, 1] - c[1]) <= h[1]) & (np.abs(x0[:, 2] - c[2]) <= h[2]) & (x0[:, 0] > c[0] - h[0]))
        if inside.any():
            return True
    return False


def rigid_fit(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(R, t): the rotation and shift that take the points a (k, 3) nearest to the points b in least squares,
    b ~ a R^T + t (Kabsch's method: the SVD of the cross covariance, with the sign fixed to a rotation)."""
    ca, cb = a.mean(axis=0), b.mean(axis=0)
    H = (a - ca).T @ (b - cb)
    U, _, Vt = np.linalg.svd(H)
    s = np.sign(np.linalg.det(Vt.T @ U.T))
    R = Vt.T @ np.diag([1.0, 1.0, s]) @ U.T
    return R, cb - ca @ R.T


def in_body_frame(run: CrashRun, ref: list[int]) -> np.ndarray:
    """(rows, n, 3) m: every record row's node positions with the rigid motion of the ref nodes taken out (rigid_fit
    of their start positions onto their positions then): the deformation seen from the body the ref nodes belong to,
    in the design frame."""
    out = np.empty_like(run.positions)
    a = run.mesh.x0[ref]
    for k, p in enumerate(run.positions):
        R, t = rigid_fit(a, p[ref])
        out[k] = (p - t) @ R
    return out


def segment_box_distance(a: np.ndarray, b: np.ndarray, centre: np.ndarray, half: np.ndarray,
                         rot: np.ndarray | None = None, step: float = 0.002) -> float:
    """m, the least signed distance from the segment a-b to a box (centre, half sizes, its axes the columns of rot,
    default the design axes): positive outside (the Euclidean distance), negative inside (minus the depth to the
    nearest face). The segment is sampled every `step` (the ends included)."""
    n = max(2, int(np.ceil(float(np.linalg.norm(b - a)) / step)) + 1)
    s = np.linspace(0.0, 1.0, n)[:, None]
    q = a + s * (b - a) - centre
    if rot is not None:
        q = q @ rot
    d = np.abs(q) - half
    out = np.linalg.norm(np.maximum(d, 0.0), axis=1)
    inside = np.all(d <= 0.0, axis=1)
    sd = np.where(inside, d.max(axis=1), out)
    return float(sd.min())


def clearance(run: CrashRun, body: np.ndarray, parts: list[dict], shares: dict[str, dict[int, float]],
              elements: list[int], rows: list[int] | None = None, step: float = 0.005) -> dict:
    """The least room over the rows between the elements (their axes less their contact radius: the tube's surface,
    for a round tube) and each part box, in the body frame (body: in_body_frame's positions). A box moves with its
    carried_by nodes (the mean displacement, weighted by the part's shares) and keeps its axes (rpy_deg turns it).
    {part id: {"room" (m, negative: inside the box), "element", "row", "start" (row 0)}} for the parts with shares.
    An element is left out from the row its time is past its break."""
    from hiraku.solvers.k0_handcalc.rigid import rpy_matrix

    mesh = run.mesh
    rows = list(range(len(run.t_all))) if rows is None else rows
    out: dict[str, dict] = {}
    for p in parts:
        sh = shares.get(p["id"])
        if not sh or not p.get("box"):
            continue
        idx = np.array(list(sh.keys()))
        wts = np.array(list(sh.values()), dtype=float)
        wts = wts / wts.sum() if wts.sum() > 0.0 else np.ones(len(idx)) / len(idx)
        c0 = np.asarray(p["box"]["center_mm"], float) / 1000.0
        h = np.asarray(p["box"]["size_mm"], float) / 2000.0
        rot = rpy_matrix(p["box"]["rpy_deg"]) if p["box"].get("rpy_deg") else None
        best, start = None, None
        for k in rows:
            pk = body[k]
            c = c0 + (pk[idx] - mesh.x0[idx]).T @ wts
            for e in elements:
                if run.broken_at.get(e, np.inf) <= run.t_all[k] + 1e-12:
                    continue
                i, j = mesh.con[e]
                r = segment_box_distance(pk[i], pk[j], c, h, rot, step) - float(mesh.crad[e])
                if k == rows[0] and (start is None or r < start):
                    start = r
                if best is None or r < best["room"]:
                    best = {"room": r, "element": int(e), "row": int(k)}
        if best is not None:
            best["start"] = start
            out[p["id"]] = best
    return out


def room_to_boxes(run: CrashRun, body: np.ndarray, boxes: list[dict], elements: list[int], radius: np.ndarray,
                  rows: list[int] | None = None, step: float = 0.005) -> dict:
    """clearance() for many elements at once, with a radius per element: the least room over the rows between the
    elements (their axes sampled every `step` or closer at the start length, the ends included, less radius[e]) and
    each box, in the body frame (body: in_body_frame's positions).

    boxes: [{"id", "center_mm", "size_mm", "rpy_deg" (optional), "shares": {node: kg}}]; a box moves with its share
    nodes (the mean displacement, weighted) and keeps its axes. An element is left out from the row its time is past
    its break. Returns {box id: {"room" (m), "element", "row", "start" (row 0), "size_mm" (the box's), "by_member":
    {member: least room}, "series": [least room per row], "depth": {member: intrusion}}}.

    The room inside a box is minus the depth to its nearest face, so it bottoms out at the box's middle. "depth" does
    not: for each member whose room went below 0, the face it came in through (at the first such row, the face of the
    largest of |q| - h at its deepest sample, q in the box's axes) and from then on the largest depth of its capsule
    past that face's plane along the face's inward normal, h_a + r - s q_a (s = the face's sign), over the samples
    whose other two coordinates are within the box's half sizes + r: {"depth" (m), "face" ("+y" etc., the box's
    axes), "entered_row", "row", "element"}."""
    from hiraku.solvers.k0_handcalc.rigid import rpy_matrix

    mesh = run.mesh
    rows = list(range(len(run.t_all))) if rows is None else rows
    els = np.asarray(elements, dtype=np.int64)
    if not len(els):
        return {}
    ii, jj = mesh.con[els, 0], mesh.con[els, 1]
    counts = np.maximum(2, np.ceil(mesh.l0[els] / step).astype(np.int64) + 1)
    owner = np.repeat(np.arange(len(els)), counts)  # sample -> position in els
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    frac = (np.arange(int(counts.sum())) - np.repeat(starts, counts)) / np.repeat(counts - 1, counts)
    rad = np.asarray(radius, float)[els]
    members = [mesh.member_of[e] for e in els]
    names = sorted(set(members))
    mem_idx = np.array([names.index(m) for m in members])
    broke = np.array([run.broken_at.get(int(e), np.inf) for e in els])
    tt = run.t_all
    prep = []
    for b in boxes:
        sh = b["shares"]
        idx = np.array(list(sh.keys()), dtype=np.int64)
        wts = np.array(list(sh.values()), dtype=float)
        wts = wts / wts.sum() if wts.sum() > 0.0 else np.ones(len(idx)) / len(idx)
        rot = rpy_matrix(b["rpy_deg"]) if b.get("rpy_deg") else None
        prep.append((b["id"], np.asarray(b["center_mm"], float) / 1000.0, np.asarray(b["size_mm"], float) / 2000.0,
                     rot, idx, wts))
    out = {}
    for bid, c0, h, rot, idx, wts in prep:
        out[bid] = {"room": np.inf, "element": -1, "row": -1, "start": None, "size_mm": (2000.0 * h).tolist(),
                    "by_member": dict.fromkeys(names, np.inf), "series": [], "depth": {}}
    s_mem = mem_idx[owner]  # sample -> member index
    s_rad = rad[owner]
    for k in rows:
        pk = body[k]
        a, d = pk[ii], pk[jj] - pk[ii]
        pts = a[owner] + frac[:, None] * d[owner]
        gone = broke <= tt[k] + 1e-12
        for bid, c0, h, rot, idx, wts in prep:
            c = c0 + (pk[idx] - mesh.x0[idx]).T @ wts
            q = pts - c
            if rot is not None:
                q = q @ rot
            dd = np.abs(q) - h
            outside = np.linalg.norm(np.maximum(dd, 0.0), axis=1)
            sd = np.where(np.all(dd <= 0.0, axis=1), dd.max(axis=1), outside)
            per = np.minimum.reduceat(sd, starts) - rad
            per[gone] = np.inf
            o = out[bid]
            j = int(np.argmin(per))
            if k == rows[0]:
                o["start"] = float(per[j])
            o["series"].append(float(per[j]))
            if per[j] < o["room"]:
                o["room"], o["element"], o["row"] = float(per[j]), int(els[j]), int(k)
            low = np.full(len(names), np.inf)
            np.minimum.at(low, mem_idx, per)
            for m_i, m in enumerate(names):
                if low[m_i] < o["by_member"][m]:
                    o["by_member"][m] = float(low[m_i])
                if low[m_i] < 0.0 and m not in o["depth"]:  # it came in: the face of its deepest sample
                    s_all = np.flatnonzero((s_mem == m_i) & ~gone[owner])
                    s0 = int(s_all[np.argmin(sd[s_all] - s_rad[s_all])])
                    a = int(np.argmax(dd[s0]))
                    o["depth"][m] = {"axis": a, "sign": 1.0 if q[s0, a] >= 0.0 else -1.0, "depth": -np.inf,
                                     "entered_row": int(k), "row": -1, "element": -1}
            for m, dp in o["depth"].items():
                sel = np.flatnonzero((s_mem == names.index(m)) & ~gone[owner])
                if not len(sel):
                    continue
                a, sg = dp["axis"], dp["sign"]
                qa, r_s = q[sel], s_rad[sel]
                lat = np.ones(len(sel), bool)
                for b_ in range(3):
                    if b_ != a:
                        lat &= np.abs(qa[:, b_]) <= h[b_] + r_s
                if not lat.any():
                    continue
                dep = np.where(lat, h[a] + r_s - sg * qa[:, a], -np.inf)
                j = int(np.argmax(dep))
                if dep[j] > dp["depth"]:
                    dp.update(depth=float(dep[j]), row=int(k), element=int(els[owner[sel[j]]]))
    for o in out.values():
        for dp in o["depth"].values():
            dp["face"] = ("+" if dp.pop("sign") > 0 else "-") + "xyz"[dp.pop("axis")]
    return out


def broken_members(run: CrashRun) -> list[str]:
    """The members with a broken element, in the order they broke."""
    out = []
    for e, _ in sorted(run.broken_at.items(), key=lambda kv: (kv[1], kv[0])):
        if run.mesh.member_of[e] not in out:
            out.append(run.mesh.member_of[e])
    return out
