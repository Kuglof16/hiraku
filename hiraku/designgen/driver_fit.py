"""The driver's place that gives the most room: a search over the ladder_4w variables that place the driver,
the seat's fore-and-aft travel (seat_slide), the pedals, the steering wheel and the front bulkhead, for the set
whose smallest occupant margin (K4, hiraku.solvers.k4_geometry.people.fit) is the largest.

    from hiraku.designgen import driver_fit
    got = driver_fit.best_driver_place(design)              # design: a ladder_4w design record
    got["changes"]       # {name: {value, unit}} for designgen.regenerate / revise_design
    got["smallest"]      # {name, value, unit, where}: the smallest margin with those values

The margins are the ones the assembly tool reports for H3 (hiraku.solvers.shelf.assembly: each is the
smallest over the occupants, `where` the part it is measured to): knee_room, pedal_reach, wheel_reach,
foot_room, foot_support, leg_clearance and knee_angle_room (MARGINS = COMFORT_MARGINS; names=REACH_MARGINS leaves
knee_angle_room out). No lower limit is put on any of them beyond what K4 measures
(all are in mm; 0 is where the body touches or does not reach). The other variables (track, battery_x, ...)
stay as the design has them.

How it searches (deterministic, no random numbers), within each variable's range:
1. the design as it is;
2. one variable at a time across its whole range (SCAN_LEVELS levels), the others as they are;
3. a grid of every combination of, for each variable, its value in the design and its best level in 2;
4. from the best so far, a compass search: each variable a step up and down, the first better move kept;
   when no single move is better, pairs of moves (one that raises the smallest margin, one that raises
   the margin that move made the smallest); when no pair is better either, the steps are halved, down to
   RESOLUTION.
"Better" is a smallest margin larger by more than TIE_MM; among sets whose smallest margins are within
TIE_MM, the one nearer the design (the sum over the variables of the change over the range) is kept, so a
variable that does not raise the smallest margin stays as the design has it. A set the generator cannot
build (DesignGenError) is not a candidate. The search stops at time_limit_s (or max_evals) and returns the
best set found so far, saying that it stopped there. Values are on a lattice of RESOLUTION (0.5 mm,
0.25 deg; design choices of this search).
"""

from __future__ import annotations

import itertools
import time
from typing import Any

from hiraku.designgen.ladder_4w import DesignGenError

Record = dict[str, Any]

VARIABLES = ("seat_x", "seat_height", "steering_x", "steering_column_angle", "steering_wheel_height", "pedal_x",
             "bulkhead_dx", "seat_slide")
REACH_MARGINS = ("knee_room", "pedal_reach", "wheel_reach", "foot_room", "foot_support", "leg_clearance")
# REACH_MARGINS and K4's knee_angle_room (the pedal foot's knee angle inside the comfortable range, for the big and
# the small person). With the seat fixed (seat_slide 0) the search on run 10 and N15 finds no set with every margin
# at least 0; with seat_slide among the variables it does on v1, run 10 and N15 (tests/solvers/
# test_driver_knee_angle.py), so these are the default.
COMFORT_MARGINS = REACH_MARGINS + ("knee_angle_room",)
MARGINS = COMFORT_MARGINS
RESOLUTION = {"deg": 0.25, "mm": 0.5}  # the lattice the values are on (by unit)
SCAN_LEVELS = 9  # levels across each variable's range in step 2
FIRST_STEP_SHARE = 1.0 / 8.0  # the compass search's first step: this share of the range
TIE_MM = 0.01  # smallest margins closer than this are equal when two sets are compared
TIME_LIMIT_S = 120.0


class _Stop(Exception):
    pass


def margins(design: Record, names: tuple[str, ...] = MARGINS) -> dict[str, tuple[float, str]]:
    """{name: (mm, where)}: each of `names` as the assembly tool reports it (the smallest over the occupants of
    K4's people.fit). A margin K4 does not give for this design (nothing ahead of the knees) is left out."""
    from hiraku.solvers.k4_geometry.people import fit

    out: dict[str, tuple[float, str]] = {}
    for k, (v, where) in fit(design).items():
        g = k.split(".")[0]
        if g in names and (g not in out or v < out[g][0]):
            out[g] = (float(v), where)
    return out


def smallest(m: dict[str, tuple[float, str]] | None) -> float:
    """The smallest margin (mm); -inf for a set that was not built, +inf when K4 gives none of them."""
    if m is None:
        return float("-inf")
    return min((v for v, _ in m.values()), default=float("inf"))


def best_driver_place(design: Record, *, variables: tuple[str, ...] = VARIABLES, names: tuple[str, ...] = MARGINS,
                      time_limit_s: float = TIME_LIMIT_S, max_evals: int | None = None,
                      load_case: Record | None = None) -> Record:
    """The values of `variables` (within their ranges, the design's other variables as they are) whose smallest
    margin of `names` is the largest. load_case: the occupants on board as the tool's load case has them
    (hiraku.solvers.shelf.base.on_board); none given, every occupant in the design.

    Returns {"values": {name: {value, unit}}, "changes": the ones that differ from the design, "margins":
    {name: {value, unit, where}}, "smallest": {name, value, unit, where}, "start": {"values", "margins",
    "smallest"} of the design as given, "evaluations": designs built and measured, "not_built": of them the
    sets the generator refused, "seconds", "stopped": "converged" | "time" | "evaluations", "time_limit_s"}."""
    from hiraku import designgen

    t0 = time.monotonic()
    gen = designgen.generator_of(design)
    missing = [v for v in variables if v not in gen.VARIABLES or not gen.VARIABLES[v].revisable]
    if missing:
        raise DesignGenError(f"the design's generator has no revisable {', '.join(missing)}")
    on_board = None
    if load_case is not None:
        from hiraku.solvers.shelf.base import on_board
    var = {k: gen.VARIABLES[k] for k in variables}
    now = {k: q["value"] for k, q in gen.values_of(design).items()}
    start = {k: float(now.get(k, var[k].default)) for k in variables}
    res = {k: RESOLUTION[var[k].unit] for k in variables}

    def snap(k: str, x: float) -> float:
        """x on the lattice, inside the range."""
        lo, hi = var[k].lo, var[k].hi
        y = round(min(max(x, lo), hi) / res[k]) * res[k]
        return float(min(max(y, lo), hi))

    def distance(x: dict[str, float]) -> float:
        return sum(abs(x[k] - start[k]) / (var[k].hi - var[k].lo) for k in variables)

    cache: dict[tuple, dict | None] = {}
    count = {"evals": 0, "not_built": 0}

    def measure(x: dict[str, float]) -> dict | None:
        key = tuple(x[k] for k in variables)
        if key in cache:
            return cache[key]
        over = time.monotonic() - t0 > time_limit_s
        if over or (max_evals is not None and count["evals"] >= max_evals):
            raise _Stop("time" if over else "evaluations")
        count["evals"] += 1
        changes = {k: {"value": x[k], "unit": var[k].unit} for k in variables if x[k] != start[k]}
        try:
            d = designgen.regenerate(design, changes) if changes else design
        except (DesignGenError, ValueError):
            count["not_built"] += 1
            cache[key] = None
            return None
        if on_board is not None:
            d = on_board(d, load_case)
        cache[key] = margins(d, names)
        return cache[key]

    def better(a: dict[str, float], b: dict[str, float]) -> bool:
        """Leximin: the margins of each set sorted from the smallest, compared one by one; the first pair that differs
        by more than TIE_MM decides. Equal throughout: the set nearer the design."""
        ma, mb = measure(a), measure(b)
        if ma is None or mb is None:
            return ma is not None
        la, lb = sorted(v for v, _ in ma.values()), sorted(v for v, _ in mb.values())
        for va, vb in zip(la, lb):
            if va > vb + TIE_MM:
                return True
            if va < vb - TIE_MM:
                return False
        return distance(a) < distance(b)

    x = dict(start)
    stopped = "converged"
    try:
        measure(x)
        # 2. one variable at a time
        best_level = {}
        for k in variables:
            lo, hi = var[k].lo, var[k].hi
            top = dict(x)
            for lv in sorted({snap(k, lo + (hi - lo) * i / (SCAN_LEVELS - 1)) for i in range(SCAN_LEVELS)}):
                y = dict(x, **{k: lv})
                if better(y, top):
                    top = y
            best_level[k] = top[k]
        # 3. the grid of the design's values and the best levels
        for combo in itertools.product(*(sorted({start[k], best_level[k]}) for k in variables)):
            y = dict(zip(variables, combo))
            if better(y, x):
                x = y
        # 4. compass search, with pairs of moves where no single move is better
        step = {k: max(res[k], (var[k].hi - var[k].lo) * FIRST_STEP_SHARE) for k in variables}
        # the sets the search has stood on: a tie (within TIE_MM, nearer the design) could otherwise lead it round a
        # loop of sets already measured, where no new measure checks the time
        visited = {tuple(x[k] for k in variables)}
        while True:
            if time.monotonic() - t0 > time_limit_s:
                raise _Stop("time")
            moves = [(k, sgn) for k in variables for sgn in (1.0, -1.0)]

            def moved(x0: dict[str, float], *ms: tuple[str, float]) -> dict[str, float]:
                y = dict(x0)
                for k, sgn in ms:
                    y[k] = snap(k, y[k] + sgn * step[k])
                return y

            def new(y: dict[str, float]) -> bool:
                return tuple(y[k] for k in variables) not in visited

            nxt = None
            for m in moves:
                y = moved(x, m)
                if y != x and new(y) and better(y, x):
                    nxt = y
                    break
            if nxt is None:
                nxt = _pair_move(x, moves, moved, measure, lambda a, b: new(a) and better(a, b))
            if nxt is not None:
                x = nxt
                visited.add(tuple(x[k] for k in variables))
                continue
            if all(step[k] <= res[k] for k in variables):
                break
            step = {k: max(res[k], step[k] / 2.0) for k in variables}
        # 5. back toward the design: the leximin comparison also raises margins that have room already; keep the
        # smallest margin found (within TIE_MM) and take each move that brings the set nearer the design
        floor = smallest(measure(x))
        step = {k: max(res[k], (var[k].hi - var[k].lo) * FIRST_STEP_SHARE) for k in variables}
        while True:
            if time.monotonic() - t0 > time_limit_s:
                raise _Stop("time")
            nxt = None
            for k in variables:
                if x[k] == start[k]:
                    continue
                toward = 1.0 if start[k] > x[k] else -1.0
                # all the way back to the design's value first, then a step toward it
                for y in (dict(x, **{k: start[k]}),
                          dict(x, **{k: snap(k, x[k] + toward * min(step[k], abs(start[k] - x[k])))})):
                    if y != x and smallest(measure(y)) >= floor - TIE_MM and distance(y) < distance(x):
                        nxt = y
                        break
                if nxt is not None:
                    break
            if nxt is not None:
                x = nxt
                continue
            if all(step[k] <= res[k] for k in variables):
                break
            step = {k: max(res[k], step[k] / 2.0) for k in variables}
    except _Stop as e:
        stopped = str(e)

    def report(vals: dict[str, float]) -> Record:
        m = cache.get(tuple(vals[k] for k in variables))
        out: Record = {"values": {k: {"value": vals[k], "unit": var[k].unit} for k in variables}}
        if not m:
            out.update(margins=None if m is None else {}, smallest=None)
            return out
        out["margins"] = {n: {"value": v, "unit": "mm", "where": w} for n, (v, w) in m.items()}
        n, (v, w) = min(m.items(), key=lambda kv: kv[1][0])
        out["smallest"] = {"name": n, "value": v, "unit": "mm", "where": w}
        return out

    got = report(x)
    got["changes"] = {k: {"value": x[k], "unit": var[k].unit} for k in variables if x[k] != start[k]}
    got["start"] = report(start)
    got.update(evaluations=count["evals"], not_built=count["not_built"], seconds=time.monotonic() - t0,
               stopped=stopped, time_limit_s=time_limit_s)
    return got


def _pair_move(x, moves, moved, measure, better):
    """A better set two moves away: a move that raises the smallest margin at x (it lowered another below it,
    or it would have been kept), with a move that raises the margin which is the smallest after it. None
    when no such pair is better."""
    base = measure(x)
    if not base:
        return None
    low = min(base, key=lambda n: base[n][0])
    effect = {}
    for m in moves:
        y = moved(x, m)
        effect[m] = measure(y) if y != x else None
    for a in moves:
        ma = effect[a]
        if not ma or low not in ma or ma[low][0] <= base[low][0] + TIE_MM:
            continue
        now_low = min(ma, key=lambda n: ma[n][0])
        for b in moves:
            mb = effect[b]
            if b[0] == a[0] or not mb or now_low not in mb or mb[now_low][0] <= base[now_low][0] + TIE_MM:
                continue
            y = moved(x, a, b)
            if y != x and better(y, x):
                return y
    return None
