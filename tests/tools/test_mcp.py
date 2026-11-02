import asyncio
import json
import unittest

from hiraku import contract
from hiraku.tools import Attribution
from hiraku.tools.mcp import INPUT_SCHEMAS, build_server, make_handler
from hiraku.tools.functions import FUNCTIONS
from tests.tools.helpers import RunCase


def has_ref(node):
    if isinstance(node, dict):
        return "$ref" in node or any(has_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(has_ref(v) for v in node)
    return False


class SchemaTest(unittest.TestCase):
    def test_every_mvp_tool_has_a_standalone_schema(self):
        self.assertEqual(set(INPUT_SCHEMAS), set(contract.MCP_TOOLS_MVP))
        for name, schema in INPUT_SCHEMAS.items():
            with self.subTest(name=name):
                self.assertFalse(has_ref(schema))
                self.assertEqual(schema["type"], "object")
        units = INPUT_SCHEMAS["record_prediction"]["properties"]["inputs"]["additionalProperties"]["properties"]["unit"]
        self.assertIn("MPa", units["enum"])


class HandlerTest(RunCase, unittest.TestCase):
    def call(self, name, args):
        h = make_handler(self.ctx, self.attribution, name, FUNCTIONS[name])
        return asyncio.run(h(args))

    def setUp(self):
        super().setUp()
        self.attribution = Attribution()

    def test_server_builds(self):
        cfg = build_server(self.ctx, self.attribution)
        self.assertEqual(cfg["name"], "hiraku")

    def test_result_is_the_record_as_json(self):
        out = self.call("list_tools", {})
        self.assertNotIn("is_error", out)
        names = [t["name"] for t in json.loads(out["content"][0]["text"])]
        self.assertEqual(sorted(names), ["frame_static", "step_climb"])

    def test_tool_error_comes_back_as_is_error(self):
        out = self.call("analyze", {"tool": "step_climb", "load_case_id": "P1-step100", "prediction_ids": ["pred-0001"]})
        self.assertTrue(out["is_error"])
        self.assertIn("record_prediction", out["content"][0]["text"])

    def test_attribution_reaches_the_record(self):
        args = {"load_case_id": "P1-step100", "quantity": "front_wheel_force", "formula_id": "step_climb.wheel_force_rigid",
                "output": "F", "inputs": {"r": {"value": 300.0, "unit": "mm"}, "h": {"value": 100.0, "unit": "mm"},
                                          "W": {"value": 1000.0, "unit": "N"}}, "assumptions": []}
        self.attribution.push("mcp__hiraku__record_prediction", json.loads(json.dumps(args)), "cae.powertrain")
        out = self.call("record_prediction", args)
        self.assertEqual(json.loads(out["content"][0]["text"])["agent"], "cae.powertrain")
        out = self.call("record_prediction", args)  # nothing pushed: the load case owner
        self.assertEqual(json.loads(out["content"][0]["text"])["agent"], "cae.strength")


if __name__ == "__main__":
    unittest.main()
