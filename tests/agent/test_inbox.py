"""The screen's requests to a running run (inbox/): the run emits the events and removes the files."""

import json
import unittest

from claude_agent_sdk import ResultMessage

from hiraku.agent import inbox
from hiraku.agent.hooks import Hooks
from hiraku.agent.scripted import run_scripted
from hiraku.contract import layout
from hiraku.tools import Attribution
from tests.agent.test_conversation import ConversationCase, result
from tests.tools.helpers import RunCase

NOTES = "- 電池の質量は 30 kg 以下\n- 板金の部品は、傷が見えない向きに置く\n"


class InboxCase(RunCase):
    def put(self, name, body):
        folder = self.ctx.store.path / layout.INBOX_DIR
        folder.mkdir(exist_ok=True)
        p = folder / name
        p.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False), encoding="utf-8")
        return p

    def drop(self, filename, text):
        folder = self.ctx.store.path / layout.KNOWLEDGE_DIR
        folder.mkdir(exist_ok=True)
        (folder / filename).write_text(text, encoding="utf-8")

    def said(self, kind):
        return [e.get("text") for e in self.ctx.store.events() if e["kind"] == kind]


class TakeTest(InboxCase, unittest.TestCase):
    def test_messages_in_number_order(self):
        self.put("10-message.json", {"kind": "message", "text": "二番目"})
        self.put("2-message.json", {"kind": "message", "text": "一番目"})
        half = self.put("3-message.json.tmp", {"kind": "message", "text": "書きかけ"})
        told = inbox.take(self.ctx)
        self.assertEqual(told, ["人からの伝言です: 一番目", "人からの伝言です: 二番目"])
        self.assertEqual(self.said("user.said"), ["一番目", "二番目"])
        self.assertEqual([e["agent"] for e in self.ctx.store.events() if e["kind"] == "user.said"], ["user", "user"])
        self.assertEqual([p.name for p in half.parent.iterdir()], [half.name])  # read ones are removed
        self.assertEqual(inbox.take(self.ctx), [])
        self.assert_events_fit()

    def test_a_knowledge_file_becomes_rules(self):
        self.drop("notes.md", NOTES)
        self.put("1-knowledge.json", {"kind": "knowledge", "file": "notes.md", "text": "社内のメモです"})
        told = inbox.take(self.ctx)
        self.assertEqual(self.said("user.said"), ["社内のメモです"])
        dropped = [e["data"] for e in self.ctx.store.events()
                   if e["kind"] == "knowledge.imported" and e["data"]["doc"]["origin"] == "dropped"]
        self.assertEqual([d["doc"]["filename"] for d in dropped], ["notes.md"])
        self.assertEqual(len(dropped[0]["rules"]), 1)
        (line,) = told  # the sentence without a formula goes to the lead, with the names a formula may use
        self.assertIn("式が要る文が 1 件", line)
        self.assertIn('path="knowledge/notes.md"', line)
        detail = json.loads(line.split("\n", 1)[1])
        self.assertEqual([n["text"] for n in detail["needs_formula"]], ["板金の部品は、傷が見えない向きに置く"])
        self.assertTrue(detail["facts"])
        self.assert_events_fit()

    def test_requests_it_cannot_read_are_named_and_removed(self):
        self.put("1-message.json", "{not json")
        self.put("2-note.json", {"kind": "note", "text": "x"})
        self.put("3-knowledge.json", {"kind": "knowledge", "file": "missing.md"})
        self.put("4-knowledge.json", {"kind": "knowledge", "file": "../project.json"})
        self.put("5-message.json", {"kind": "message", "text": "  "})
        self.assertEqual(inbox.take(self.ctx), [])
        lines = [t for t in self.said("agent.said") if t.startswith("inbox の")]
        self.assertEqual([t.split()[2] for t in lines],
                         ["1-message.json", "2-note.json", "3-knowledge.json", "4-knowledge.json", "5-message.json"])
        self.assertIn("missing.md", lines[2])
        self.assertEqual(list((self.ctx.store.path / layout.INBOX_DIR).iterdir()), [])
        self.assertEqual(self.said("user.said"), [])


class ScriptedInboxTest(InboxCase, unittest.TestCase):
    def test_the_scripted_run_records_what_was_sent(self):
        self.put("1-message.json", {"kind": "message", "text": "フレームを軽くしたい"})
        run_scripted(self.ctx)
        self.assertEqual(self.said("user.said"), ["フレームを軽くしたい"])
        self.assertIn("台本の運転なので、Claude には渡しません", self.said("agent.said"))
        self.assertEqual(list((self.ctx.store.path / layout.INBOX_DIR).iterdir()), [])
        self.assert_events_fit()


class HooksTellTheLeadTest(RunCase, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.hooks = Hooks(self.ctx, Attribution())
        self.hooks.for_lead.append("人からの伝言です: 急いで")

    def post(self, **who):
        import asyncio

        return asyncio.run(self.hooks.post_tool_use({"tool_name": "Agent", "tool_input": {}, **who}, "tu", None))

    def test_after_a_tool_call_of_the_leads(self):
        self.assertEqual(self.post(agent_type="cae-strength", agent_id="a1"), {})  # a role's call: not told
        out = self.post()
        self.assertEqual(out["hookSpecificOutput"]["additionalContext"], "人からの伝言です: 急いで")
        self.assertEqual(self.post(), {})  # told once

    def test_the_leads_stop_is_held_once_for_it(self):
        import asyncio

        held = asyncio.run(self.hooks.stop({"stop_hook_active": False}, None, None))
        self.assertEqual(held, {"decision": "block", "reason": "人からの伝言です: 急いで"})
        self.assertEqual(self.hooks.stop_blocks, 0)  # not an HK4 block
        self.assertEqual([e for e in self.ctx.store.events() if e["kind"] == "hook.blocked_stop"], [])


class ClaudeRunInboxTest(ConversationCase, InboxCase, unittest.TestCase):
    def test_a_message_while_the_lead_waits_for_its_roles_starts_a_turn(self):
        case = self

        async def script(client):
            import asyncio

            await client.inbox.get()  # the task
            await client.hook("SubagentStart")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            yield result(0.01)  # the lead's turn ends while the role works
            case.put("1-message.json", {"kind": "message", "text": "R1 を先に"})
            told = await client.inbox.get()  # the run gives the lead a turn for it
            client.told = told
            case.strength_work()
            await client.hook("SubagentStop")({"agent_id": "a1", "agent_type": "cae-strength"}, None, None)
            yield result(0.02)
            await asyncio.Event().wait()

        status, client = self.run_claude(script)
        self.assertEqual(client.told, "人からの伝言です: R1 を先に")
        self.assertEqual(self.said("user.said"), ["R1 を先に"])
        self.assertEqual(status, "done")
        self.assert_events_fit()


if __name__ == "__main__":
    unittest.main()
