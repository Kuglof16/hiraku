"""Load cases for getting in and out of a side seat, the entry_opening tool's test: H7 (proposed id; the catalog,
data/catalog/tests.yaml, has no such test yet, so a run does not reach it; tests call the builder directly).

- H7-opening: a doorway Q93 wide above the seat top (up to max(seat top + Q95, floor + Q96)) and Q94 wide from the
  floor to the seat top, with no fixed thing in it (entry_blocked <= 0) at its best place in the seat's span. With Q47
  (the opening opens by hand, one hand and one action) a net on quick-release clips and a door on hinges with a latch
  open and are not counted; without Q47 everything is fixed.
- H7-step: the first surface a foot goes on (a step, else the floor) at most Q97 over the ground
  (entry_step_height <= Q97).
- H7-handhold: at least one hand hold beside each side seat (entry_handhold >= 1): Q49 to Q89 across, no higher
  than Q48 over that surface. That one is needed at all is an assumption: no source read says so for a floor under
  1 m (the intake department found ISO 2867:1994 4.7's three-point support only above 1 m).

The numbers are the intake department's reading of ISO 4252:1983 and ISO 2867:1980 / 1994 (older editions of
standards for agricultural tractors and earth-moving machinery, borrowed for this vehicle), of the AIST body sizes
(Q95, an assumption) and of CPSC's proposed rule (Q47, an assumption); said in each criterion's basis. A case whose
keys are missing is not built; with none left the test is not evaluated (BuildError naming the keys). Q44 to Q46
(between steps, a step's width and the toe room) are not judged: the tool measures the rise between steps only
(entry_step_rise) and the designs have no steps.
"""

from __future__ import annotations

from hiraku.catalog.builders.common import BuildError, Record, entry, loading, number, q, tolerance
from hiraku.catalog.loading import LOADING_JA

TEST_ID = "H7"  # proposed: not in data/catalog/tests.yaml yet
# Tolerances of the K0 predictions against the tool (HIRAKU's choice):
# - opening: the count. K0 sees the side frame and the nets only, at one place; the tool every member and part at
#   every place. Measured (2026-11-04): version 2 (bars_net) K0 3 against the tool's 3 (K0 counts the front post, the
#   tool the bolted net instead), the side_opening designs 0 against 0. A count off by one of three is 33 %: 0.34.
H7_OPENING_TOL = 0.34
# - step: the same heights added (the floor's thickness and gap read off its part): K0 equals the tool. 1 %.
H7_STEP_TOL = 0.01
# - handhold: K0 counts round members only, the tool also hand-hold parts and rectangular tubes. Measured
#   (2026-11-04) on the grab_bar designs: 3 against 3, without grab bars 0 against 0. 34 % as the opening's.
H7_HAND_TOL = 0.34
KEY_NAMES = {"Q93": "開口の前後の幅 (座面より上)", "Q94": "開口の前後の幅 (床から座面まで)", "Q95": "開口の高さ (座面から)",
             "Q96": "開口の高さ (床から)", "Q97": "地面から最初の足場までの高さ", "Q47": "開口を片手・一つの動作で開けられること",
             "Q48": "手がかりの高さの上限", "Q49": "手がかりの太さの下限", "Q89": "手がかりの太さの上限"}
OPENING_KEYS, STEP_KEYS, HAND_KEYS = ("Q93", "Q94", "Q95", "Q96"), ("Q97",), ("Q48", "Q49", "Q89")
BORROWED = ("the intake department's reading of ISO 4252:1983 Figure 1 and Table 1 and ISO 2867:1980 / 1994 (older "
            "editions of standards for agricultural tractors and earth-moving machinery), borrowed for this vehicle "
            "(an assumption)")
SPAN = ("the opening is looked for beside the seat (outboard of its outer face), from the seat back to the front of "
        "the seated person's feet (an assumption)")
YES = ("はい", "yes", "true", "1")


def _missing(req: Record, keys) -> list[str]:
    return [k for k in keys if entry(req, k) is None]


def _absent(keys: list[str]) -> str:
    return "、".join(f"{k} {KEY_NAMES[k]}" for k in keys) + "が要件に無い"


def _openable(req: Record) -> str:
    r = entry(req, "Q47")
    return "yes" if r is not None and str(r.get("value")).strip().lower() in YES else "no"


def h7_entry(spec: Record, req: Record, design: Record) -> list[Record]:
    from hiraku.solvers.k4_geometry.entry import HEIGHT_CAP_MM

    load, keys = loading(req, design)
    params: Record = {"loading": load, "height_cap": q(HEIGHT_CAP_MM, "mm")}
    why = []
    have = {}
    for group, names in ((OPENING_KEYS, ("width_upper", "width_lower", "height_seat", "height_floor")),
                         (HAND_KEYS, ("grip_reach", "grip_min", "grip_max"))):
        miss = _missing(req, group)
        if miss:
            why.append(_absent(miss))
            continue
        for k, name in zip(group, names):
            v = number(req, k, "mm")
            if v < 0:
                raise BuildError(f"{k} ({v:g} mm) must not be negative")
            params[name] = q(v, "mm")
        have[group] = True
    if have.get(HAND_KEYS) and params["grip_min"]["value"] > params["grip_max"]["value"]:
        raise BuildError(f"Q49 ({params['grip_min']['value']:g} mm) is more than Q89 ({params['grip_max']['value']:g} mm)")
    params["openable"] = _openable(req)
    opens = "開けられる網・扉は数えない (Q47)" if params["openable"] == "yes" else "Q47 が無いので、網・扉も数える"
    base = {"test_id": TEST_ID, "domain": spec["domain"]}
    cases = []
    if have.get(OPENING_KEYS):
        w_up, w_low, h_s, h_f = (params[k]["value"] for k in ("width_upper", "width_lower", "height_seat", "height_floor"))
        cases.append({
            **base,
            "load_case_id": f"{TEST_ID}-opening",
            "title": (f"座席の横に、座面から上へ幅 {w_up:g} mm・床から座面まで幅 {w_low:g} mm、座面から {h_s:g} mm・床から "
                      f"{h_f:g} mm の開口があり、動かない物でふさがっていない ({opens}、{LOADING_JA[load]})"),
            "params": dict(params),
            "requirement_keys": [*OPENING_KEYS, *(["Q47"] if entry(req, "Q47") else []), *keys],
            "criterion": {"quantity": "entry_blocked", "op": "<=", "limit": q(0.0, "1"),
                          "basis": (f"H7: no fixed member or part in a doorway {w_up:g} mm wide (Q93) from the seat top "
                                    f"up to the higher of seat top + {h_s:g} mm (Q95) and floor + {h_f:g} mm (Q96), with "
                                    f"{w_low:g} mm wide (Q94) from the floor up to the seat top at its front end, at its "
                                    f"best place; {SPAN}. Q93, Q94, Q96: {BORROWED}; Q95: AIST sitting height I1, young "
                                    "men 95th percentile, as the intake department read it (an assumption). "
                                    + ("A net held only by quick-release clips and a door with hinges and a latch open "
                                       "(Q47: opened by hand with one hand in one action, CPSC's proposed rule's preamble "
                                       "applied to opening, an assumption) and are not counted"
                                       if params["openable"] == "yes" else
                                       "Q47 is not in the requirements: nets and doors count as fixed")),
                          "requirement_keys": list(OPENING_KEYS) + (["Q47"] if entry(req, "Q47") else [])},
            "tolerance_rel": tolerance(spec, H7_OPENING_TOL),
        })
    miss = _missing(req, STEP_KEYS)
    if miss:
        why.append(_absent(miss))
    else:
        lim = number(req, "Q97", "mm")
        cases.append({
            **base,
            "load_case_id": f"{TEST_ID}-step",
            "title": f"地面から最初の足場 (段か床の上面) まで {lim:g} mm 以下 ({LOADING_JA[load]})",
            "params": dict(params),
            "requirement_keys": ["Q97", *keys],
            "criterion": {"quantity": "entry_step_height", "op": "<=", "limit": q(lim, "mm"),
                          "basis": (f"H7: the first surface a foot goes on (a step beside the seat, else the floor's top) "
                                    f"at most {lim:g} mm (Q97) over the ground (the lowest tyre bottom). Q97: {BORROWED}"),
                          "requirement_keys": ["Q97"]},
            "tolerance_rel": tolerance(spec, H7_STEP_TOL),
        })
    if have.get(HAND_KEYS):
        lo, hi, reach = (params[k]["value"] for k in ("grip_min", "grip_max", "grip_reach"))
        cases.append({
            **base,
            "load_case_id": f"{TEST_ID}-handhold",
            "title": (f"座席の横に、太さ {lo:g}〜{hi:g} mm で、足場から {reach:g} mm までの手がかりがある "
                      f"({LOADING_JA[load]})"),
            "params": dict(params),
            "requirement_keys": [*HAND_KEYS, *keys],
            "criterion": {"quantity": "entry_handhold", "op": ">=", "limit": q(1.0, "1"),
                          "basis": (f"H7: beside each side seat at least one member or hand-hold part {lo:g} to {hi:g} mm "
                                    f"across (Q49, Q89) within {reach:g} mm (Q48) over the first surface a foot goes on, "
                                    f"outboard of the seat's middle; {SPAN}. Q48, Q49, Q89: {BORROWED}. That one is "
                                    "needed at all is an assumption (no source read asks for one under a floor of 1 m)"),
                          "requirement_keys": list(HAND_KEYS)},
            "tolerance_rel": tolerance(spec, H7_HAND_TOL),
        })
    if not cases:
        raise BuildError("評価できない: " + "。".join(why))
    return cases


def _read(fn, design: Record, load_case: Record, names) -> Record:
    from hiraku.solvers.k0_handcalc.vehicle import DesignError

    try:
        got = fn(design, load_case)
    except (DesignError, ValueError) as e:
        raise BuildError(str(e)) from None
    return {k: got[k] for k in names}


def h7_width_calc(design: Record, load_case: Record) -> Record:
    """entry.opening_width's inputs: hiraku.solvers.k0_handcalc.entry_inputs.entry_width_inputs."""
    from hiraku.solvers.k0_handcalc.entry_inputs import entry_width_inputs

    return _read(entry_width_inputs, design, load_case, ("x_f", "x_b", "x_p", "d"))


def h7_height_calc(design: Record, load_case: Record) -> Record:
    """entry.opening_height's inputs from the design (entry_height_inputs); W_req is lc.width_upper."""
    from hiraku.solvers.k0_handcalc.entry_inputs import entry_height_inputs

    return _read(entry_height_inputs, design, load_case, ("x_f", "x_b", "x_p", "d", "h_low", "H_cap"))


def h7_blocked_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.entry_inputs import entry_blocked_inputs

    return _read(entry_blocked_inputs, design, load_case, ("n_frame", "n_fixed"))


def h7_step_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.entry_inputs import entry_step_inputs

    return _read(entry_step_inputs, design, load_case, ("z_rail", "h_rail", "gap", "t", "z_g"))


def h7_hand_calc(design: Record, load_case: Record) -> Record:
    from hiraku.solvers.k0_handcalc.entry_inputs import entry_handhold_inputs

    return _read(entry_handhold_inputs, design, load_case, ("n_grip",))


_SPAN_JA = "開口は、座席の外の面より外で、座席の後ろの面から座った人の足先までの前後の範囲で探す (仮定)"
_BORROWED_JA = "要件の値は、トラクタと土工機械の規格の古い版 (ISO 4252:1983、ISO 2867) を受付部が読んで借りたもの (仮定)"
H7_PLAN = {
    "tool": "entry_opening",
    "say": ("座席の横の枠 (前の柱と棒) だけを見て、前の柱から足先までの空きの幅と、開口の上の空きの高さを先に出します。"
            "次に、開口の形を前の柱のすぐ前に置いて、横の枠の部材と開けられない網がいくつ入るかを数えます"),
    "predictions": [
        {"quantity": "entry_opening_width", "formula_id": "entry.opening_width", "output": "W",
         "assumptions": [_SPAN_JA, "横の枠の棒は後ろの柱から前の柱まで続き、その後ろをふさぐ。前の柱より前には何も無いとみる "
                                   "(ツールはほかの部材・部品も見る)"],
         "inputs": {"x_f": "from.x_f", "x_b": "from.x_b", "x_p": "from.x_p", "d": "from.d"},
         "inputs_from": h7_width_calc},
        {"quantity": "entry_opening_height", "formula_id": "entry.opening_height", "output": "H",
         "assumptions": [_SPAN_JA, "前の柱より前の空きが Q93 より狭ければ、横の枠の下の棒の下面まで。広ければ上に何も無い "
                                   "(屋根は無く、ロールバーの上の梁は座席の後ろ)", _BORROWED_JA],
         "inputs": {"x_f": "from.x_f", "x_b": "from.x_b", "x_p": "from.x_p", "d": "from.d", "h_low": "from.h_low",
                    "W_req": "lc.width_upper", "H_cap": "from.H_cap"},
         "inputs_from": h7_height_calc},
        {"quantity": "entry_blocked", "formula_id": "entry.blocked", "output": "n",
         "assumptions": [_SPAN_JA, "開口の形を前の柱のすぐ前に置き、横の枠の丸い柱と棒、開けられない網だけを数える "
                                   "(ツールはどこに置くと少ないかを試し、全部の部材・部品を見る)", _BORROWED_JA],
         "inputs": {"n_frame": "from.n_frame", "n_fixed": "from.n_fixed"}, "inputs_from": h7_blocked_calc},
    ],
}
H7_STEP_PLAN = {
    "tool": "entry_opening",
    "say": "レールの高さ、床とレールのすき間、床の板の厚さを足して、地面から床の上面までの高さを出します",
    "predictions": [
        {"quantity": "entry_step_height", "formula_id": "entry.step_height", "output": "h",
         "assumptions": ["段は無く、床の上面が最初の足場。平らな地面に止まっている", _BORROWED_JA],
         "inputs": {"z_rail": "from.z_rail", "h_rail": "from.h_rail", "gap": "from.gap", "t": "from.t",
                    "z_g": "from.z_g"},
         "inputs_from": h7_step_calc},
    ],
}
H7_HAND_PLAN = {
    "tool": "entry_opening",
    "say": "握れる太さ (Q49〜Q89) の丸い部材で、座席の横の範囲にあるものを数えます",
    "predictions": [
        {"quantity": "entry_handhold", "formula_id": "entry.handholds", "output": "n",
         "assumptions": [_SPAN_JA, "部材だけを数える (ツールは手がかりの部品と角の管も数える)",
                         "手がかりが要ること自体が仮定 (床が 1 m より低いときに要るとする出典は読めていない)", _BORROWED_JA],
         "inputs": {"n_grip": "from.n_grip"}, "inputs_from": h7_hand_calc},
    ],
}

# Joined to the package's BUILDERS and PLANS in analysis.py, as H6's. The catalog (data/catalog/tests.yaml) has no H7
# yet, so a run does not reach it; tests call it directly.
BUILDERS = {TEST_ID: h7_entry}
PLANS = {TEST_ID: H7_PLAN, f"{TEST_ID}-step": H7_STEP_PLAN, f"{TEST_ID}-handhold": H7_HAND_PLAN}
