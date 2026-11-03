"""get_tool_template: one whole tool written in the shape make_tool and check_tool take.

The code (TEMPLATE_CODE) is checked by the same path as a tool written in a run
(hiraku.solvers.toolmaker: its own process, guard.py's imports, the class with
spec/check/run found by runner.find_tool, check() calling known_answers()); the
tests run check_tool on it. The explanations around it are read from that code:
the signature of check_record, the fields of the contract's tool_check and
result, the imports guard.py allows.
"""

from __future__ import annotations

import inspect
from typing import Any

from hiraku import contract

Record = dict[str, Any]

# A tool for a strength test: one member of the frame taken as a simply supported beam with a load at
# mid-span. The known answer is the closed form worked out by hand for a 40 x 80 x 2 mm rect tube,
# 1000 mm long, 1000 N at mid-span:
#   I = (40 * 80^3 - 36 * 76^3) / 12 = (20,480,000 - 15,803,136) / 12 = 389,738.67 mm^4
#   M = F L / 4 = 250,000 N*mm,  sigma = M (h / 2) / I = 250,000 * 40 / 389,738.67 = 25.6582 MPa
TEMPLATE_CODE = '''\
import math

from hiraku.solvers.shelf.base import BuiltinTool, LoadCaseError, Param, check_record, param_text, param_value, result


def midspan_stress(F, L, b, h, t):
    """Bending stress [MPa] at mid-span of a simply supported rect tube (b wide, h high, wall t, all mm)
    of length L [mm] under a load F [N] at mid-span: sigma = (F L / 4) (h / 2) / I."""
    I = (b * h ** 3 - (b - 2 * t) * (h - 2 * t) ** 3) / 12.0
    return F * L / 4.0 * (h / 2.0) / I


class MidspanBending(BuiltinTool):
    # the tool_spec fields: name (a-z 0-9 _, not on the shelf yet), title, domain, base (K0..K5),
    # tests (the test ids it analyses; write your own test here), formulas (the K0 formulas that
    # predict its results: list_formulas; a tool without them is refused)
    name = "example_midspan_bending"
    title = "部材 1 本を単純支持の梁とみた、真ん中の荷重の曲げ応力"
    domain = "strength"
    base = "K0"
    tests = ["R1"]
    formulas = ["frame_static.rail_bending"]
    # every load case parameter analyse() reads (others cannot be read); unit None is a string
    load_case_params = (
        Param("force", "N", "load at mid-span", default=1000.0),
        Param("member", None, "id of the member (design.members) taken as the beam", default="rail_L0"),
    )

    def known_answers(self):
        # through analyse() itself, on a design of one member made here, against the closed form
        design = {"nodes": [{"id": "a", "xyz_mm": [0.0, 0.0, 0.0]}, {"id": "b", "xyz_mm": [1000.0, 0.0, 0.0]}],
                  "members": [{"id": "m", "node_a": "a", "node_b": "b", "section": "s"}],
                  "sections": {"s": {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}}}
        lc = self.declared({"load_case_id": "check", "params": {"force": {"value": 1000.0, "unit": "N"},
                                                                "member": "m"}})
        got = self.analyse(design, lc, {})["results"][0]["value"]
        return [check_record("1000 N at mid-span of a 1000 mm rect tube 40 x 80 x 2 mm", "max_stress", 25.6582, got,
                             "MPa", 1e-4, "closed form sigma = (F L / 4)(h / 2) / I, "
                             "I = (b h^3 - (b - 2t)(h - 2t)^3) / 12, worked out by hand")]

    def analyse(self, design, load_case, params):
        member_id = param_text(load_case, "member")
        members = {m["id"]: m for m in design["members"]}
        if member_id not in members:
            raise LoadCaseError(f"member {member_id!r} is not in the design")  # the analysis fails with this text
        m = members[member_id]
        xyz = {n["id"]: n["xyz_mm"] for n in design["nodes"]}
        L = math.dist(xyz[m["node_a"]], xyz[m["node_b"]])
        dims = design["sections"][m["section"]]["dims_mm"]
        F = param_value(load_case, "force", "N")
        sigma = midspan_stress(F, L, dims["b"], dims["h"], dims["t"])
        return {"results": [result("max_stress", sigma, "MPa", where=member_id)],
                "assumptions": ["the member is simply supported at its two nodes, the load at mid-span"]}
'''

# A tool for a strength test on the K1 frame solver (hiraku.solvers.k1_frame): the run's design with the load
# case's loading on board (hiraku.contract.loading.on_board), standing on its supports under its weight times
# g_factor; the largest von Mises stress of the frame. Two known answers, worked out by hand:
#  - K1's beam elements: a cantilever of the 40 x 80 x 2 mm rect tube, 1000 mm, 1000 N at the tip:
#    I = 389,738.67 mm^4 (as above), M = F L = 1,000,000 N*mm at the root,
#    sigma = M (h / 2) / I = 1,000,000 * 40 / 389,738.67 = 102.6329 MPa
#  - through analyse(): a 1000 x 500 mm rectangle of that tube on four supports at its corners, only its own
#    weight: the supports carry the weight, rho A g sum(L) = 7850e-9 kg/mm^3 * 464 mm^2 * 3000 mm * 9.80665 m/s^2
#    = 107.1592 N
K1_TEMPLATE_CODE = '''\
from hiraku.contract.loading import on_board
from hiraku.solvers.k1_frame import analyze, build
from hiraku.solvers.shelf.base import G_FACTOR, LOADING, BuiltinTool, check_record, param_text, param_value, result

TUBE = {"shape": "rect_tube", "dims_mm": {"b": 40.0, "h": 80.0, "t": 2.0}}
STEEL = {"E_MPa": 205000.0, "nu": 0.3, "density_kg_m3": 7850.0, "yield_MPa": 235.0}


def frame(points, edges):
    """A design of rect tube members between named points (mm), for the known answers."""
    return {"nodes": [{"id": k, "xyz_mm": [float(x) for x in p]} for k, p in points.items()],
            "members": [{"id": f"m{i}", "node_a": a, "node_b": b, "section": "s", "material": "steel"}
                        for i, (a, b) in enumerate(edges)],
            "sections": {"s": TUBE}, "materials": {"steel": STEEL}, "parts": [], "supports": []}


class FrameAtRest(BuiltinTool):
    name = "example_frame_at_rest"
    title = "K1 の骨組: 載せた組の重さで支えの上に立つ骨組の最大の応力"
    domain = "strength"
    base = "K1"
    tests = ["R1"]
    formulas = ["frame_static.rail_bending"]
    # LOADING: who and what is on board (the load case's params.loading); G_FACTOR: the weights times this
    load_case_params = (LOADING, G_FACTOR)

    def known_answers(self):
        # 1. K1's own beam elements: build the frame, hold one end, push the other, solve, read the stress
        d = frame({"a": (0, 0, 0), "b": (1000, 0, 0)}, [("a", "b")])
        fm = build(d)
        fm.model.def_support("a", True, True, True, True, True, True)  # clamped: all six held
        fm.add_force_at("b", [1000.0, 0.0, 0.0], [0.0, 0.0, -1000.0])
        fm.solve()
        tip = fm.member_stress("m0")["max"]
        # 2. through analyse(): a rectangle on four supports, empty; the supports carry its weight
        pts = {"a": (0, 0, 0), "b": (1000, 0, 0), "c": (1000, 500, 0), "d": (0, 500, 0)}
        d = frame(pts, [("a", "b"), ("b", "c"), ("c", "d"), ("d", "a")])
        d["supports"] = [{"id": f"s_{k}", "kind": "caster", "position_mm": [float(x) for x in p], "frame_node": k,
                          "radius_mm": 50.0} for k, p in pts.items()]
        lc = self.declared({"load_case_id": "check", "params": {"loading": "empty"}})
        got = self.analyse(d, lc, {})["results"]
        carried = next(r["value"] for r in got if r["quantity"] == "support_force_total")
        return [
            check_record("1000 N at the tip of a 1000 mm cantilever, rect tube 40 x 80 x 2 mm", "max_stress",
                         102.6329, tip, "MPa", 1e-4, "closed form sigma = F L (h / 2) / I, "
                         "I = (b h^3 - (b - 2t)(h - 2t)^3) / 12, worked out by hand"),
            check_record("1000 x 500 mm rectangle of that tube on four supports, its own weight", "support_force_total",
                         107.1592, carried, "N", 1e-4, "closed form (statics): the supports carry the weight "
                         "rho A g sum(L), worked out by hand"),
        ]

    def analyse(self, design, load_case, params):
        loaded = on_board(design, param_text(load_case, "loading"))
        res = analyze(loaded, g_factor=param_value(load_case, "g_factor", "1"))
        worst = res["worst_member"]
        return {"results": [result("max_stress", res["stresses"][worst]["max"], "MPa", where=worst),
                            result("support_force_total", float(res["ground_total_N"][2]), "N")],
                "assumptions": ["quasi-static: the weights times g_factor, the frame standing on its supports"]}
'''

DEFINE = (
    "クラスを 1 つ (hiraku.solvers.shelf.base の BuiltinTool を継ぐ) か、TOOL = 道具のインスタンス を書く。"
    "BuiltinTool が spec()・check()・run() を持つので、書くのは欄 (name・title・domain・base・tests・load_case_params、"
    "任意で formulas・predicts・run_params) と known_answers()・analyse() の 2 つ。check() は known_answers() を呼ぶ"
)
ANALYSE_ARGS = {
    "design": ("今の版の設計 (契約の design)。座標は mm、x 前・y 左・z 上。nodes (id・xyz_mm)、members (id・node_a・"
               "node_b・section・material)、sections (id → shape・dims_mm)、materials (id → E_MPa・nu・yield_MPa など)、"
               "parts (kind・mass_kg・box)、supports (車輪・hardpoints_mm)。乗員と荷を載せるなら load_case_params に "
               "base の LOADING を足し、on_board(design, load_case) で載せた設計を作る"),
    "load_case": ("荷重条件 (load_case_id・test_id・params・criterion・tolerance_rel)。params は load_case_params で"
                  "宣言した名前だけ読める (宣言の default が入る)。量は param_value(load_case, 名前, 単位) で単位を換算して、"
                  "文字は param_text で読む"),
    "params": ("analyze の params。上流の荷重条件の解析は params[\"upstream_analyses\"] (荷重条件の ID → 照合で一致した "
               "analysis)。run_params で宣言した設定は run_param で読む"),
    "predictions": ("analyze に渡した予測 (record_prediction の記録)。run() が受け取り、ID を記録に入れる。"
                    "analyse() には渡らない"),
}
RETURNS = ("analyse() は dict を返す: results (結果の list、result(quantity, value, unit, where) で作る) と、任意で "
           "assumptions (設計にも荷重条件にも無くて仮定した値、文の list)・fields・vectors など。設計や荷重条件が悪くて"
           "解けないときは LoadCaseError (か DesignError) を文つきで投げる。解析は status failed になり、その文が error に入る")


def _fields(name: str) -> Record:
    d = contract.schema()["$defs"][name]
    return {"required": list(d.get("required", [])), "all": list(d.get("properties", {}))}


def _signature(obj: Any) -> str:
    try:
        return str(inspect.signature(obj))
    except (TypeError, ValueError):
        return "(...)"


def allowed_api() -> Record:
    """What a made tool may use, read from the toolmaker's allow list (guard.ALLOWED_MODULES): the
    modules it may import, and for HIRAKU's own the names each exports (its __all__, else its public
    functions and classes) with their signatures."""
    import importlib

    from hiraku.solvers.toolmaker import guard

    hiraku_api: Record = {}
    others = []
    for name in guard.ALLOWED_MODULES:
        if name == "__future__":
            continue
        if not name.startswith("hiraku."):
            others.append(name)
            continue
        mod = importlib.import_module(name)
        names = getattr(mod, "__all__", None)
        if names is None:
            names = [n for n, v in vars(mod).items() if not n.startswith("_") and (inspect.isfunction(v) or
                     inspect.isclass(v)) and getattr(v, "__module__", "") == mod.__name__]
        api = {}
        for n in names:
            v = getattr(mod, n, None)
            if inspect.isfunction(v) or inspect.isclass(v):
                api[n] = _signature(v)
            elif v is not None and not inspect.ismodule(v):
                api[n] = type(v).__name__
        hiraku_api[name] = api
    return {"modules": others, "hiraku": hiraku_api,
            "note": ("hiraku.* はその下のモジュールも import できる (上はパッケージが出す名前)。"
                     f"使えない組み込み: {', '.join(guard.DENIED_BUILTINS)}")}


# The rule this department's make_tool and check_tool add to the toolmaker's checks (functions.made_refusals).
RULE = ("道具は 2 つのことを満たさないと使えない: (1) spec の formulas に、結果を予測する K0 の式 (list_formulas の "
        "formula_id) が少なくとも 1 つある (判定には予測が要る); (2) known_answers に、expected が 0 でなく、source が"
        "閉じた式・教科書・規格など道具の外の出典を名指しする例が少なくとも 1 つある (道具が使う式そのものの値は出典にならない)")


def tool_template() -> Record:
    """The template and what the code that loads and checks made tools requires of it."""
    from hiraku.solvers.shelf.base import check_record
    from hiraku.solvers.toolmaker import guard

    return {
        "code": TEMPLATE_CODE,
        "examples": {"k1_frame": K1_TEMPLATE_CODE},
        "examples_note": ("examples.k1_frame は K1 の骨組で解く強度の道具: 設計に荷重条件の組を載せ "
                          "(hiraku.contract.loading.on_board)、hiraku.solvers.k1_frame の analyze で支えの上に立たせ、"
                          "build で作った骨組を known_answers で閉じた式と比べる"),
        "rule": RULE,
        "define": DEFINE,
        "analyse": "analyse(self, design, load_case, params)",
        "analyse_args": ANALYSE_ARGS,
        "returns": RETURNS,
        "result_fields": _fields("result"),
        "known_answers": ("known_answers(self) は答えの分かっている例の list を返す。1 件は check_record"
                          f"{inspect.signature(check_record)} で作る (tool_check の欄 {_fields('tool_check')['required']})。"
                          "expected は閉じた式や教科書の値とその出典 (source) で、自分の予想した数にしない。pass は HIRAKU が "
                          "expected・got・tolerance_rel から計算し直す。少なくとも 1 件、全部が通ると使える。"
                          "そのうち 1 件は expected が 0 でなく、source が閉じた式・教科書・規格を名指しすること"),
        "imports": guard.allowed_text() + " (呼べる関数とクラスは check_tool の答えの api)",
        "not_allowed": ("open・exec・eval・getattr などと、ファイルの読み書き、__ で囲んだ属性 (__init__ のほか) は使えない。"
                        "道具は別のプロセスで、時間の上限つきで走る"),
        "next": ("code を自分の試験に合わせて書き換え (name は棚に無いもの、tests は自分の試験の ID、結果の quantity は "
                 "荷重条件の criterion.quantity)、check_tool(code) で試し、problems に従って直してから make_tool(code, reason)。"
                 "判定には、その量を予測する K0 の式 (list_formulas) が要る"),
    }
