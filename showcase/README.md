# showcase: runs kept for replay

Run folders the screen replays without running anything (`python -m hiraku.webserve` from the repository with
the Python that has `requirements.txt` installed, then `http://127.0.0.1:8765/`; a Python without them shows no
tool shelf). With no `?run=` in the URL the screen opens the newest run here by id; today that is
the N15 scripted run. Others: `?run=<run_id>`, or the run list `?list=1`.

Each folder holds what the screen reads: `events.jsonl`, `project.json`, `report.json` and, for a run where
Claude wrote a tool, `tools/`. The record folders `v00N/` are left out (every record is also in its event).
Each analysis' time series is thinned to at most 160 samples per channel, keeping the ends and the peaks
(`prepare.py`); results, predictions, comparisons and verdicts are as recorded. `prepare.py` makes a folder
from a run folder: `python showcase/prepare.py runs/<run_id>`.

Suggested order: 1, 2, 3.

## 1. `20261105-150416-offroad-4w-scripted`: N15, every tile green (scripted)

The video's N15 requirements (`data/requirements/offroad-4w-n15.yaml`, 23 tests) on the demo vehicle, scripted
(no Claude, cost 0). The vehicle is split into modules: each round the module designers propose and the
packager builds the version. HK5 checks light stages first, so each version stops at its first red stage.

| Version | Red | Next version's change |
|---|---|---|
| 1 | assembly: H1-motion, H1-steer, H3-body-size, H3-knee-angle, H6-spray, H7-handhold | layout, a hand hold to get in by |
| 2 | assembly: H7-opening | the side net held by clips that open |
| 3 | powertrain: P3-accel40 | motor 17 kW |
| 4 | strength: R1s-step100 (shell hot spot at the rail joints) | rail wall, gussets, cross members |
| 5 | none: done | |

R7-hand (the steering grip's movement) is judged on the tool's value after the load path is checked by hand
(`path_checked`): the hand calculation is an upper bound, 9.66 mm against 0.815 mm on versions 3 and 4, 9.10 mm
against 0.538 mm on version 5 (limit 5 mm). The hooks stop the run once at HK1 and three times at HK3.

## 2. `20261105-094333-offroad-4w`: N13 with Claude (Claude Code plugin)

Claude Code runs HIRAKU through its plugin (`plugins/hiraku`), with the roll-over wish (R4) and no R4 tool on
the shelf: the strength role (cae.strength) writes the tools in the run and R4-rops is judged on them (`tools/rops_pushover_frame-1.py`,
`tools/rops_pushover_frame_all-1.py`, with their checks).

| Version | Red |
|---|---|
| 1 | assembly: H1-motion, H1-steer, H3-body-size, H3-knee-angle |
| 2 | strength: R8-push-725N |
| 3 | strength: R1-step100, 643 MPa from the shell tool |
| 4 | none red |

R4-rops passes on `rops_pushover_frame_all` (4.14-4.18 mm on versions 2-4). The hooks deny one call at HK5 and one at
HK6. The run stops not judged: R7-hand ended without a verdict (the rule before `path_checked`). Version 4's
R1 passes on step_climb's beam model (67.8 MPa) after version 3's 643 MPa from the shell: the case behind green rule (d)
(a lighter tool's pass does not clear a heavier tool's exceedance).

## 3. `20261105-091217-offroad-4w`: a run that stopped at version 2 (Claude Code plugin)

The same request as 2. Version 1 red at assembly, version 2 red at R8-push-725N; the lead's stop was refused
three times (HK4) and the run ended at the turn limit. R4-rops and R7-hand ended without a verdict. Kept as the
record of a way HIRAKU stopped short, which the rules were changed for afterwards.

## Runs not kept

- `20261105-150415-offroad-4w-scripted` (the demo, scripted): the same story as N15 (run 1) with fewer tests
- `20261104-002518-offroad-4w` (Claude Code plugin): stopped before the first version was designed
- `20261103-234711-offroad-4w` (Claude Code plugin): R4 alone with one round of revision; run 2 shows R4 more fully
- `20261104-070258-offroad-4w` (Claude Code plugin): an earlier run of run 2's story

## `n10/`

Claude without tools, asked the questions of the video's N10 cut, with HIRAKU's values (`n10/README.md`).
