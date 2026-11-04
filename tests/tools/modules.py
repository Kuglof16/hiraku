"""A stand-in design split into modules (contract design.modules) and a stand-in generator for it.

The real generator with modules is written elsewhere; these tests only need: parts and members with
`module`, the design's `modules` (owner, envelope boxes, variables, choices) and a regenerate that
moves the seat and the battery with their variables.
"""

from __future__ import annotations

import json
from unittest import mock

from tests.tools import fakes
from tests.tools.helpers import RunCase

PART_MODULE = {"battery": "powertrain", "seat": "occupant", "driver": "occupant", "passenger": "occupant",
               "cargo": "body"}
_LOADED_DESIGN = fakes.loaded_design  # ModuleCase patches fakes.loaded_design with module_design


def _bounds_box(boxes: list[dict], margin: list[float]) -> dict:
    lo = [min(b["center_mm"][i] - b["size_mm"][i] / 2 for b in boxes) - margin[i] for i in range(3)]
    hi = [max(b["center_mm"][i] + b["size_mm"][i] / 2 for b in boxes) + margin[i] for i in range(3)]
    return {"center_mm": [(lo[i] + hi[i]) / 2 for i in range(3)], "size_mm": [hi[i] - lo[i] for i in range(3)]}


def module_design() -> dict:
    """fakes.loaded_design() with seat_x, battery_x and rail_t, every part and member in a module, and modules
    whose envelopes hold version 1 with room: 250 mm along x for the occupant's, 100 mm for the powertrain's."""
    d = _LOADED_DESIGN()
    d["variables"].update(seat_x={"value": -900.0, "unit": "mm"}, battery_x={"value": -900.0, "unit": "mm"},
                          rail_t={"value": 2.0, "unit": "mm"})
    parts = {p["id"]: p for p in d["parts"]}
    for p in d["parts"]:
        p["module"] = PART_MODULE[p["id"]]
    for m in d["members"]:
        m["module"] = "frame"
    nodes = [{"center_mm": n["xyz_mm"], "size_mm": [0.0, 0.0, 0.0]} for n in d["nodes"]]
    box = lambda *ids: [parts[i]["box"] for i in ids]  # noqa: E731
    d["modules"] = [
        {"module_id": "package", "owner": "packager", "envelope": [], "variables": ["track"]},
        {"module_id": "frame", "owner": "designer.frame", "envelope": [_bounds_box(nodes, [50.0, 50.0, 50.0])],
         "variables": ["rail_t"],
         "interfaces": [{"id": "seat_mount", "between": ["frame", "occupant"], "node": "L1", "via": "bolt"}]},
        {"module_id": "occupant", "owner": "designer.occupant",
         "envelope": [_bounds_box(box("seat", "driver", "passenger"), [250.0, 20.0, 20.0])],
         "variables": ["seat_x"], "choices": ["seat_kind"],
         "interfaces": [{"id": "seat_mount", "between": ["frame", "occupant"], "node": "L1", "via": "bolt"}]},
        {"module_id": "powertrain", "owner": "designer.powertrain",
         "envelope": [_bounds_box(box("battery"), [100.0, 50.0, 50.0])],
         "variables": ["battery_x"], "choices": ["battery_pack"]},
        {"module_id": "body", "owner": "designer.body", "envelope": [_bounds_box(box("cargo"), [50.0, 50.0, 50.0])],
         "variables": [], "choices": ["low_stance"]},
    ]
    return d


class ModuleDesigngen(fakes.FakeDesigngen):
    """The stand-in generator of module_design(): seat_x moves the seat, battery_x the battery; the choices
    battery_pack big makes the battery 600 mm long and low_stance on sets track 1200 mm."""

    SPEC = fakes.FakeDesigngen.SPEC + [
        {"name": "seat_x", "unit": "mm", "default": -900.0, "revisable": True, "min": -1200.0, "max": -600.0,
         "meaning": "seat centre along x"},
        {"name": "battery_x", "unit": "mm", "default": -900.0, "revisable": True, "min": -1200.0, "max": -500.0,
         "meaning": "battery centre along x"},
        {"name": "rail_t", "unit": "mm", "default": 2.0, "revisable": True, "min": 1.5, "max": 4.0,
         "meaning": "rail wall"},
    ]
    CHOICES = {"seat_kind": ["fixed", "slide"], "battery_pack": ["small", "big"], "low_stance": ["off", "on"]}

    def generator_of(self, design):
        return self

    def regenerate(self, design, changes):
        self.calls.append(changes)
        out = json.loads(json.dumps(design))
        for k, v in changes.items():
            if isinstance(v, dict):
                out["variables"][k] = dict(v)
        if changes.get("low_stance") == "on":
            out["variables"]["track"] = {"value": 1200.0, "unit": "mm"}
        parts = {p["id"]: p for p in out["parts"]}
        parts["seat"]["box"]["center_mm"][0] = out["variables"]["seat_x"]["value"]
        parts["battery"]["box"]["center_mm"][0] = out["variables"]["battery_x"]["value"]
        if changes.get("battery_pack") == "big":
            parts["battery"]["box"]["size_mm"][0] = 600.0
        out.pop("module_changes", None)
        return out


class ModuleCase(RunCase):
    """RunCase on module_design() with ModuleDesigngen (round_limit 1, as RunCase)."""

    def setUp(self):
        with mock.patch.object(fakes, "loaded_design", module_design), \
                mock.patch.object(fakes, "FakeDesigngen", ModuleDesigngen):
            super().setUp()

    @staticmethod
    def mm(v):
        return {"value": float(v), "unit": "mm"}
