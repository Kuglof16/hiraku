"""Write the plugin's agents and command from hiraku.agent.prompts (the same text as the SDK run).

    python -m hiraku.agent.plugin_files        rewrite plugins/hiraku/agents/*.md and commands/run.md

tests/agent/test_plugin_path.py checks that the files equal what this writes now, so a change
of a prompt is followed by running this.

- agents/<name>.md: designer, designer-<module> for every module designer, packager and cae-<domain>
  for every domain but assembly. In the person's Claude Code they are hiraku:designer,
  hiraku:designer-<module>, hiraku:packager, hiraku:cae-<domain> (agent_id_from_sdk takes the
  "hiraku:" off). The lead is the main session.
- commands/run.md: /hiraku:run <requirements file>: start_run, then the lead's prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from hiraku import contract
from hiraku.agent import prompts
from hiraku.agent.roles import (CAE_EFFORT, CAE_TOOLS, DESIGNER_EFFORT, DESIGNER_TOOLS, MODULE_DESIGNER_TOOLS,
                                MODULE_PACKAGER_TOOLS, PLUGIN_DIR, PLUGIN_NAME, module_designer_skills, plugin_skills,
                                sdk_name)

GENERATED = "<!-- written by python -m hiraku.agent.plugin_files from hiraku/agent/prompts.py; do not edit -->"


def plugin_tool(name: str) -> str:
    """A tool of the plugin's MCP server as Claude Code names it: mcp__plugin_<plugin>_<server>__<tool>."""
    return f"mcp__plugin_{PLUGIN_NAME}_{contract.MCP_SERVER}__{name}"


def _frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            lines += [f"  - {json.dumps(x, ensure_ascii=False)}" for x in v]
        else:
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) else v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _agent(name: str, description: str, prompt: str, tools: tuple[str, ...], effort: str, skills: bool,
           skill_names: list[str] | None = None) -> str:
    fields: dict = {"name": name, "description": description,
                    "tools": ", ".join(plugin_tool(t) for t in tools), "model": "inherit", "effort": effort}
    if skills or skill_names:
        fields["skills"] = skill_names if skill_names is not None else plugin_skills()
    return _frontmatter(fields) + "\n" + GENERATED + "\n\n" + prompt.rstrip("\n") + "\n"


def agent_files() -> dict[str, str]:
    """The plugin's agents are fixed files, so they cover designs with and without modules: the single designer,
    a designer per module (designer-<module>; HK6 lets only designer.<module> propose on a design with modules), and
    the packager with build_version and its rules for modules (they say they hold for a design with modules)."""
    out = {"agents/designer.md": _agent("designer", prompts.DESIGNER_DESCRIPTION, prompts.DESIGNER_PROMPT,
                                        DESIGNER_TOOLS, DESIGNER_EFFORT, skills=False),
           "agents/packager.md": _agent("packager", prompts.cae_description("assembly", modules=True),
                                        prompts.cae_prompt("assembly", modules=True), MODULE_PACKAGER_TOOLS,
                                        CAE_EFFORT, skills=True)}
    for agent_id in contract.MODULE_DESIGNERS:
        module, name = agent_id.split(".", 1)[1], sdk_name(agent_id)
        out[f"agents/{name}.md"] = _agent(name, prompts.module_designer_description(module),
                                          prompts.module_designer_prompt(module), MODULE_DESIGNER_TOOLS,
                                          DESIGNER_EFFORT, skills=False, skill_names=module_designer_skills())
    for domain in contract.DOMAINS:
        if domain == "assembly":
            continue
        name = sdk_name(f"cae.{domain}")
        out[f"agents/{name}.md"] = _agent(name, prompts.cae_description(domain), prompts.cae_prompt(domain),
                                          CAE_TOOLS, CAE_EFFORT, skills=True)
    return out


RUN_COMMAND_INTRO = f"""\
HIRAKU の実行を、この会話で進めます。あなたはまとめ役です。

1. MCP のツール {plugin_tool("start_run")} を requirements_file="$ARGUMENTS" で 1 回呼びます (ツールの棚を確かめるので、しばらくかかります)。断られたら、その文をそのまま人に伝えて終わります。
2. 返った task が、まとめ役の最初の仕事です。下の「まとめ役の決まり」に従って進めます。
   - HIRAKU のツールは {plugin_tool("<名前>")} です (決まりの中では <名前> だけで書いています)。
   - 役のサブエージェントは {PLUGIN_NAME}:designer・{PLUGIN_NAME}:packager・{PLUGIN_NAME}:cae-<分野> です。設計がモジュールに分かれているときは、{PLUGIN_NAME}:designer の代わりに、モジュールの設計役 {PLUGIN_NAME}:designer-<モジュール> ({"・".join(m.split(".", 1)[1] for m in contract.MODULE_DESIGNERS)}) を使います。
   - Skill の hiraku:<名前> は、このプラグインの skill です。
   - 役を裏で動かしたまま番を終えません (Claude Code が役を打ち切ることがあります)。役の結果を待つときは {plugin_tool("wait_for_roles")} を呼びます。
   - ツールが断り、その理由をツールの入力で直せないとき (ツールの誤り・棚に無いツールや式・足りない値など) は、HIRAKU のコード (hiraku/*.py) を読んで原因を探しません。何が・どこで・記録の ID を issues に書いて write_report を呼び、終えます (設計の版がまだ無くても、summary と issues で書けます)。
3. start_run の答えの screen に、画面で見る手順と URL があります。人にそのまま伝えます。

まとめ役の決まり:

"""


def command_files() -> dict[str, str]:
    tools = [plugin_tool(n) for n in ("start_run", "open_run", "get_context", "write_report", "pick_domains", "ask_user",
                                      "import_knowledge", "rule_check", "wait_for_roles")]
    fields = {"description": "要件のファイルから HIRAKU の実行を始め、まとめ役として予測・解析・照合・合否まで進める",
              "argument-hint": "<要件のファイル (例 data/requirements/offroad-4w.yaml)>",
              "allowed-tools": ", ".join(tools)}
    # the module rules say they hold for a design with modules, so the one command serves both kinds of design
    body = RUN_COMMAND_INTRO + prompts.lead_prompt(modules=True).rstrip("\n") + "\n"
    return {"commands/run.md": _frontmatter(fields) + "\n" + GENERATED + "\n\n" + body}


def files() -> dict[str, str]:
    return {**agent_files(), **command_files()}


def write(plugin_dir: Path = PLUGIN_DIR) -> list[Path]:
    """Write every file (LF line ends, UTF-8); returns their paths."""
    written = []
    for rel, text in files().items():
        path = Path(plugin_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write():
        print(p, file=sys.stdout)
