# N10: Claude without tools, asked the same questions

The answers shown in the video's N10 cut. Claude Opus 5.5 (`claude-opus-5-5`) was asked through Claude Code's
`claude -p` with no tools (`--tools ""`), no local settings (`--setting-sources ""`) and `--strict-mcp-config`,
three times per question, on 2026-11-03.

| Question | File | Answers |
|---|---|---|
| Step 100 mm: horizontal force on one front wheel | `prompt.txt` | `answer_1.json` - `answer_3.json` |
| Tilt table: angle at which the upper wheels lift | `prompt_tilt.txt` | `tilt_answer_1.json` - `tilt_answer_3.json` |
| Step 100 mm: largest stress in the frame | `prompt_stress.txt` | `stress_answer_1.json` - `stress_answer_3.json` |

Each answer file keeps the CLI's JSON output fields `result` (the answer's full text), `total_cost_usd`,
`modelUsage`, `num_turns`, `permission_denials`, `duration_ms`, `is_error` and `stop_reason`. Changed from the
originals: a warning line the CLI printed before the JSON was dropped (so the files read as JSON), the session
and request ids and the timing details were dropped, and in `stress_answer_3.json` a path to a temporary folder
on the machine used is written `<scratchpad>`.

HIRAKU's values for the same questions, from version 1 of the scripted run 20261103-041110-offroad-4w-scripted
(not kept here; the demo vehicle, two people and cargo):

| Quantity | Hand calculation (K0) | Analysis | Comparison |
|---|---|---|---|
| Front wheel force (step 100 mm) | 1092.86 N | 1103.75 N (K1) | cmp-0003 |
| Tilt angle | 39.85 deg | 38.26 deg (K2) | cmp-0015 |
| Frame stress (R1-step100) | 40.03 MPa (simple beam), 77.09 MPa (joint, safe side) | 61.15 MPa (K1) | cmp-0004, cmp-0005 |
