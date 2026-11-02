"""The intake department (受付部): take what a person wants to make and get it ready to analyse.

N09  domains.py    pick the analysis domains for a product (pick_domains)
N14  hearing.py    ask the missing requirements once (ask_user)
N14  knowledge.py  rules (built in, or made from a dropped file) applied to the parts of a design

Numbers come from code; Claude supplies the words (flags and reasons, names, the formula of a rule
written in a sentence). tools.py holds the definitions of the MCP tools for hiraku/tools/mcp.py.
"""

from hiraku.intake.domains import IntakeError, pick_domains, project_picks, reasons_missing, summary_line
from hiraku.intake.flags import FLAGS, SPEED_CLASSES, describe_condition, flags_text
from hiraku.intake.products import load_product, product_files

__all__ = [
    "FLAGS",
    "IntakeError",
    "SPEED_CLASSES",
    "describe_condition",
    "flags_text",
    "load_product",
    "pick_domains",
    "product_files",
    "project_picks",
    "reasons_missing",
    "summary_line",
]
