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
    """The mesh nodes at the front-most x at the start (they touch the wall first)."""
    x = run.mesh.x0[:, 0]
    return [int(i) for i in np.nonzero(x >= x.max() - 1e-9)[0]]


def shortening(run: CrashRun, ref: list[int], front: list[int] | None = None) -> np.ndarray:
    """(rows,) m: how much the x distance from the front nodes back to the ref nodes (their means) has shortened."""
    front = front_nodes(run) if front is None else front
    gap = run.positions[:, front, 0].mean(axis=1) - run.positions[:, ref, 0].mean(axis=1)
    return gap[0] - gap


def _points(run: CrashRun, k: int) -> np.ndarray:
    """The mesh nodes and the middles of the elements not broken by row k (m)."""
    p = run.positions[k]
    t = run.t_all[k]
    alive = [e for e in range(run.mesh.ne) if run.broken_at.get(e, np.inf) > t + 1e-12]
    con = run.mesh.con[alive]
    return np.vstack([p, 0.5 * (p[con[:, 0]] + p[con[:, 1]])]) if alive else p


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
        alive = [e for e in range(run.mesh.ne) if run.broken_at.get(e, np.inf) > t + 1e-12]
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
    for e, mid in enumerate(run.mesh.member_of):
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


def broken_members(run: CrashRun) -> list[str]:
    """The members with a broken element, in the order they broke."""
    out = []
    for e, _ in sorted(run.broken_at.items(), key=lambda kv: (kv[1], kv[0])):
        if run.mesh.member_of[e] not in out:
            out.append(run.mesh.member_of[e])
    return out
