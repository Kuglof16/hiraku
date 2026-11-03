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
    # tests (the test ids it analyses; write your own test here)
    name = "example_midspan_bending"
    title = "部材 1 本を単純支持の梁とみた、真ん中の荷重の曲げ応力"
    domain = "strength"
    base = "K0"
    tests = ["R1"]
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


def tool_template() -> Record:
    """The template and what the code that loads and checks made tools requires of it."""
    from hiraku.solvers.shelf.base import check_record
    from hiraku.solvers.toolmaker import guard

    return {
        "code": TEMPLATE_CODE,
        "define": DEFINE,
        "analyse": "analyse(self, design, load_case, params)",
        "analyse_args": ANALYSE_ARGS,
        "returns": RETURNS,
        "result_fields": _fields("result"),
        "known_answers": ("known_answers(self) は答えの分かっている例の list を返す。1 件は check_record"
                          f"{inspect.signature(check_record)} で作る (tool_check の欄 {_fields('tool_check')['required']})。"
                          "expected は閉じた式や教科書の値とその出典 (source) で、自分の予想した数にしない。pass は HIRAKU が "
                          "expected・got・tolerance_rel から計算し直す。少なくとも 1 件、全部が通ると使える"),
        "imports": guard.allowed_text(),
        "not_allowed": ("open・exec・eval・getattr などと、ファイルの読み書き、__ で囲んだ属性 (__init__ のほか) は使えない。"
                        "道具は別のプロセスで、時間の上限つきで走る"),
        "next": ("code を自分の試験に合わせて書き換え (name は棚に無いもの、tests は自分の試験の ID、結果の quantity は "
                 "荷重条件の criterion.quantity)、check_tool(code) で試し、problems に従って直してから make_tool(code, reason)。"
                 "判定には、その量を予測する K0 の式 (list_formulas) が要る"),
    }
