"""System checks: the systems a product needs are in the design and joined end to end.

A rule of category "system" (data/knowledge/systems.yaml) has `check: "system:<name>"`; `<name>` is a
function of this module, not an expression. The rule applies when the product's flags say so (`when` in
the yaml, an expression over the flags). The functions read the parts' `kind` and `system` and what a part
`connects_to` (hiraku.contract: part.connects_to [{to, via, carries}]), and follow the links:

    drive    motor / engine --torque--> a support whose actuation.drive is true
    steering steering --steer--> supports with actuation.steer
    brake    brake pedal / lever --brake--> supports with actuation.brake
    power    battery --power--> motor, with a fuse / switch on the way
    seat, restraint, rops are joined to the frame (members, nodes, parts of kind frame)

What is missing is a hit: the part where the chain stops, the two sides of the gap, and what to add. A
design where no part has a connects_to cannot be followed: the parts the chain would run between are
reported as not evaluable (missing connects_to). A part that is not there at all is a hit either way.
The whole product ("design") stands for a hit that belongs to no part.

targets (contract: every part_id in hits and not_evaluable is among them): the parts a chain looks at (its
sources and its ends, or the seats and what holds them), and "design" when a hit belongs to the whole design.
"""

from __future__ import annotations

from collections import deque
from typing import Any, Callable

from hiraku.catalog import REPO_ROOT, read_data
from hiraku.intake import expression
from hiraku.intake.flags import FLAGS

Record = dict[str, Any]
Result = tuple[list[str], list[Record], list[Record]]  # targets, hits, not_evaluable

PREFIX = "system:"
SPEC_FILE = REPO_ROOT / "data" / "knowledge" / "systems.yaml"
WHOLE = "design"

POWER_KINDS = ("motor", "engine")
STEER_INPUT = ("steering",)
BRAKE_INPUT = ("brake_pedal", "brake_lever", "brake_controller")
PROTECTORS = ("fuse", "switch", "contactor", "breaker")
LAMPS = {"headlamp": "前照灯", "taillamp": "尾灯", "stoplamp": "制動灯", "turn_signal": "方向指示器"}
CARRIES = {"torque": "トルク", "steer": "操舵", "brake": "制動", "power": "電力"}


class Net:
    """The parts of a design and the links between them (a link joins both ways)."""

    def __init__(self, design: Record) -> None:
        self.parts: dict[str, Record] = {p["id"]: p for p in design.get("parts", [])}
        self.supports: dict[str, Record] = {s["id"]: s for s in design.get("supports", [])}
        self.frame: set[str] = {m["id"] for m in design.get("members", [])} | {n["id"] for n in design.get("nodes", [])}
        self.frame |= {pid for pid, p in self.parts.items() if p.get("kind") == "frame"}
        self.member_part: dict[str, str | None] = {m["id"]: m.get("part") for m in design.get("members", [])}
        self.adj: dict[str, list[tuple[str, str | None]]] = {}
        self.linked = False  # does any part say what it is joined to
        for p in self.parts.values():
            for link in p.get("connects_to") or []:
                self.linked = True
                a, b = p["id"], self.node(link["to"])
                self.adj.setdefault(a, []).append((b, link.get("carries")))
                self.adj.setdefault(b, []).append((a, link.get("carries")))

    def node(self, to: str) -> str:
        """A link's `to` as a node: a support, or one of its hardpoints ("FL.tie_rod_outer"), is "support:FL"."""
        if to in self.parts or to in self.frame:
            return to
        head = to.split(".", 1)[0]
        return f"support:{head}" if head in self.supports else to

    def of_kind(self, *kinds: str) -> list[str]:
        return [pid for pid, p in self.parts.items() if p.get("kind") in kinds]

    def of_system(self, system: str) -> list[str]:
        return [pid for pid, p in self.parts.items() if p.get("system") == system]

    def supports_with(self, action: str) -> list[str]:
        return [f"support:{sid}" for sid, s in self.supports.items() if (s.get("actuation") or {}).get(action)]

    def neighbours(self, node: str) -> set[str]:
        return {m for m, _ in self.adj.get(node, ())}

    def reach(self, starts: list[str], carries: str | None = None, avoid: set[str] | None = None) -> set[str]:
        """Every node joined to the starts by links that carry `carries` (any link when None), not going through `avoid`."""
        seen = set(starts)
        queue = deque(starts)
        while queue:
            n = queue.popleft()
            for m, c in self.adj.get(n, ()):
                if (carries is None or c == carries) and m not in seen and m not in (avoid or ()):
                    seen.add(m)
                    queue.append(m)
        return seen

    def reaches_frame(self, node: str, avoid: set[str] | None = None, frame: set[str] | None = None) -> bool:
        return bool(self.reach([node], avoid=avoid) & (self.frame if frame is None else frame))


def _hit(part_id: str, message: str, **values: float) -> Record:
    return {"part_id": part_id, "values": {k: float(v) for k, v in values.items()}, "message": message}


def _missing_links(*ids: str) -> list[Record]:
    return [{"part_id": i, "missing": ["connects_to"]} for i in dict.fromkeys(ids)]


def _ends(ids: set[str] | list[str], limit: int = 6) -> str:
    items = sorted(ids)
    return "・".join(items[:limit]) + (f" ほか {len(items) - limit} 件" if len(items) > limit else "")


def _chain(net: Net, sources: list[str], ends: list[str], carries: str, from_label: str, to_label: str) -> Result:
    """Every end reached from the sources through links that carry `carries`; else a hit saying where it stops."""
    if not net.linked:
        return [*sources, *ends], [], _missing_links(*sources, *ends)
    reached = net.reach(sources, carries)
    hits = []
    for end in ends:
        if end in reached:
            continue
        other_side = net.reach([end], carries)
        hits.append(_hit(end, (
            f"{from_label} ({_ends(sources)}) から{to_label} {end} まで、{CARRIES[carries]}を伝える鎖 (carries: {carries}) が切れています。"
            f"{from_label}の側でつながっているのは {_ends(reached)}、{end} の側は {_ends(other_side)} です。"
            f"この間をつなぐ部品を足し、両側の部品の connects_to に carries: {carries} で書いてください"), connected=0))
    return [*sources, *ends], hits, []


# ------------------------------------------------------------------------------------------- the checks


def drive_chain(net: Net) -> Result:
    driven = net.supports_with("drive")
    sources = net.of_kind(*POWER_KINDS)
    if not driven:
        return [WHOLE], [_hit(WHOLE, "駆動輪がありません (actuation.drive が true の接地点が 1 つもありません)。駆動する接地点を決めてください", driven=0)], []
    if not sources:
        return [*driven, WHOLE], [_hit(WHOLE, "動力源がありません (kind が motor か engine の部品)。足して、駆動輪まで torque の鎖でつないでください", sources=0)], []
    return _chain(net, sources, driven, "torque", "動力源", "駆動輪")


def steer_chain(net: Net) -> Result:
    steered = net.supports_with("steer")
    inputs = net.of_kind(*STEER_INPUT)
    if not steered:
        return [WHOLE], [_hit(WHOLE, "操舵輪がありません (actuation.steer が true の接地点が 1 つもありません)。操舵する接地点を決めてください", steered=0)], []
    if not inputs:
        return [*steered, WHOLE], [_hit(WHOLE, "ハンドルがありません (kind が steering の部品)。足して、操舵輪まで steer の鎖でつないでください", inputs=0)], []
    return _chain(net, inputs, steered, "steer", "ハンドル", "操舵輪")


def brake_chain(net: Net) -> Result:
    braked = net.supports_with("brake")
    inputs = net.of_kind(*BRAKE_INPUT)
    if not braked:
        return [WHOLE], [_hit(WHOLE, "制動する輪がありません (actuation.brake が true の接地点が 1 つもありません)。制動する接地点を決めてください", braked=0)], []
    if not inputs:
        return [*braked, WHOLE], [_hit(WHOLE, "制動の入力がありません (kind が brake_pedal・brake_lever・brake_controller のどれかの部品)。"
                                    "足して、制動する輪まで brake の鎖でつないでください", inputs=0)], []
    return _chain(net, inputs, braked, "brake", "制動の入力", "制動する輪")


def seat_attach(net: Net) -> Result:
    seats = net.of_kind("seat")
    if not seats:
        return [WHOLE], [_hit(WHOLE, "座席がありません (kind が seat の部品)。足して、骨組みにつないでください", seats=0)], []
    if not net.frame:
        return seats, [], [{"part_id": s, "missing": ["frame"]} for s in seats]
    if not net.linked:
        return seats, [], _missing_links(*seats)
    belts = set(net.of_system("restraint")) | set(net.of_kind("occupant"))  # a seat is not held by its belt or its occupant
    hits = [_hit(s, f"座席 {s} が骨組みにつながっていません。座席の側でつながっているのは {_ends(net.reach([s], avoid=belts))} です。"
                    "骨組み (member・node・kind が frame の部品) まで connects_to でつないでください", attached=0)
            for s in seats if not net.reaches_frame(s, belts)]
    return seats, hits, []


def restraint(net: Net) -> Result:
    seats = net.of_kind("seat")
    belts = net.of_system("restraint")
    if not seats:
        return [], [], []  # the seat rule says the seats are missing
    if not belts:
        return seats, [_hit(s, f"座席 {s} に拘束 (シートベルトなど。system が restraint の部品) がありません。足して、座席と骨組みにつないでください",
                            restraints=0) for s in seats], []
    if not net.linked:
        return [*seats, *belts], [], _missing_links(*seats, *belts)
    hits = []
    for s in seats:
        mine = [b for b in belts if b in net.neighbours(s)]
        if not mine:
            hits.append(_hit(s, f"座席 {s} に拘束がつながっていません (拘束の部品は {_ends(belts)} にあります)。"
                                "拘束の connects_to に座席を書いてください", restraints=0))
        elif not any(net.reaches_frame(b) for b in mine):
            hits.append(_hit(s, f"座席 {s} の拘束 {_ends(mine)} が骨組みにつながっていません。拘束の connects_to に骨組みを書いてください",
                             anchored=0))
    return seats, hits, []


def rops(net: Net) -> Result:
    bars = list(dict.fromkeys([*net.of_system("rops"), *net.of_kind("rollbar", "rops")]))
    if not bars:
        return [WHOLE], [_hit(WHOLE, "乗員を囲む転倒保護の枠 (ROPS。system が rops の部品) がありません。足して、骨組みにつないでください", rops=0)], []
    if not net.linked:
        return bars, [], _missing_links(*bars)
    own = {m for m, p in net.member_part.items() if p in bars}  # the bar's own tubes are not the frame it stands on
    hits = [_hit(r, f"転倒保護の枠 {r} が骨組みにつながっていません。枠の部材 ({_ends(own)}) ではなく、枠が立つ骨組みの部材か節まで "
                    "connects_to でつないでください", attached=0)
            for r in bars if not net.reaches_frame(r, frame=net.frame - own)]
    return bars, hits, []


def lighting(net: Net) -> Result:
    found = {k for k in LAMPS if net.of_kind(k)}
    hits = [_hit(WHOLE, f"{name}がありません (kind が {kind} の部品)。足してください", **{kind: 0})
            for kind, name in LAMPS.items() if kind not in found]
    return [WHOLE], hits, []


def power_chain(net: Net) -> Result:
    batteries = net.of_kind("battery")
    motors = net.of_kind("motor")
    if not batteries:
        return [*motors, WHOLE], [_hit(WHOLE, "電池がありません (kind が battery の部品)。足して、モーターまで power の鎖でつないでください", batteries=0)], []
    if not motors:
        return [], [], []  # nothing to feed
    return _chain(net, batteries, motors, "power", "電池", "モーター")


def disconnect(net: Net) -> Result:
    batteries = net.of_kind("battery")
    if not batteries:
        return [WHOLE], [_hit(WHOLE, "電池がありません (kind が battery の部品)", batteries=0)], []
    protectors = net.of_kind(*PROTECTORS)
    if not protectors:
        return batteries, [_hit(b, f"電池 {b} の電力を切る部品 (kind が {'・'.join(PROTECTORS)} のどれか) がありません。足して、"
                                   "電池の power の鎖に入れてください", protectors=0) for b in batteries], []
    if not net.linked:
        return [*batteries, *protectors], [], _missing_links(*batteries, *protectors)
    inside = net.reach(batteries, "power")
    if any(p in inside for p in protectors):
        return batteries, [], []
    return batteries, [_hit(b, f"遮断の部品 ({_ends(protectors)}) が電池 {b} の power の鎖に入っていません。"
                               "電池とモーターの間の connects_to に carries: power で入れてください", on_chain=0) for b in batteries], []


CHECKS: dict[str, Callable[[Net], Result]] = {
    "drive_chain": drive_chain, "steer_chain": steer_chain, "brake_chain": brake_chain, "seat_attach": seat_attach,
    "restraint": restraint, "rops": rops, "lighting": lighting, "power_chain": power_chain, "disconnect": disconnect,
}


# ------------------------------------------------------------------------------------------ the rules


def is_system_check(check: str) -> bool:
    return check.startswith(PREFIX)


def check_name(check: str) -> str:
    return check[len(PREFIX):]


def specs() -> dict[str, Record]:
    """rule_id -> {when, check} from systems.yaml, checked: every check is a function here, every `when` an
    expression over flags."""
    data = read_data(SPEC_FILE)
    out: dict[str, Record] = {}
    for r in data["rules"]:
        name = check_name(r["check"]) if is_system_check(r["check"]) else None
        if name not in CHECKS:
            raise ValueError(f"{r['id']}: check {r['check']!r} is not one of {', '.join(PREFIX + n for n in CHECKS)}")
        bad = [n for n in expression.names(r["when"]) if n not in FLAGS]
        if bad:
            raise ValueError(f"{r['id']}: when {r['when']!r} uses {', '.join(bad)}, which are not flags")
        out[r["id"]] = {"when": r["when"], "check": r["check"]}
    return out


def applies(when: str, flags: dict[str, Any]) -> bool:
    answer, _ = expression.evaluate(when, {f: 1.0 if flags.get(f) else 0.0 for f in FLAGS})
    return bool(answer)


def check_rule(rule: Record, design: Record, flags: dict[str, Any] | None) -> Record:
    """One system rule against a design: the rule_check record without its id and design version."""
    base: Record = {"rule_id": rule["rule_id"], "targets": [], "hits": [], "not_evaluable": [], "pass": True}
    if flags is None:  # which systems the product needs is not known
        return {**base, "targets": [WHOLE], "not_evaluable": [{"part_id": WHOLE, "missing": ["flags"]}]}
    spec = specs().get(rule["rule_id"])
    if spec is None:
        raise ValueError(f"{rule['rule_id']} is not in {SPEC_FILE.name}")
    if not applies(spec["when"], flags):
        return base
    targets, hits, not_evaluable = CHECKS[check_name(rule["check"])](Net(design))
    named = [r["part_id"] for r in (*hits, *not_evaluable)]
    return {**base, "targets": list(dict.fromkeys([*targets, *named])), "hits": hits, "not_evaluable": not_evaluable,
            "pass": not hits}
