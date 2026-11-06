"""A run whose analysis carries the crash tool's note on the break angle's edge, for the screen's tests.

Shape example on the contract sample (one strength analysis of max_stress): the note is written in
the form of hiraku/solvers/k2_vehicle/shelf/crash_beam.py break_band_note on the K2 branch
(claude/k2-crash-contact), with the sample's quantity in place of C1's. The angles, the members and
the numbers are chosen here, not results. Two members get names (design.members[].name) and one is
left without, to check how the screen names them.
"""

from __future__ import annotations

import copy
import json

from hiraku.webserve.server import SAMPLE_EVENTS

LIM_DEG = 60.0  # the break angle (shape example)
BAND_DEG = 1.0  # the run made again this much higher (shape example)
NEAR = [("rail_L1", 59.987), ("cross1", 59.5)]  # unbroken members with a hinge within the band under LIM_DEG
NAMES = {"rail_L1": "左のレール (座席の前まで)", "rail_R1": "右のレール"}
FLIP = "max_stress <= 156.7"  # the sample's verdict in the note's form "<quantity> <op> <limit>"
PLAIN = ["the frame is beams with rigid joints (shape example)", "予測と照合する前提: 板は <b> 無し (形の見本)"]


def band_note(flips=(FLIP,), near=NEAR, lim=LIM_DEG, band=BAND_DEG):
    """The note's text, in break_band_note's form."""
    text = (f"break angle band (the run made again with the break angle {band:+g} deg): "
            f"{lim:g} deg: 2 broken (rail_R0, cross0), occupant_space_margin -191 mm, frame_split_fraction 0.157; "
            f"{lim + band:g} deg: 3 broken (rail_L1, rail_R0, cross0), occupant_space_margin -546 mm, frame_split_fraction 0.157. ")
    text += "Members that break in one run and not in another: rail_L1. "
    if near:
        text += (f"Unbroken at {lim:g} deg with a hinge within {band:g} deg under it: "
                 + ", ".join(f"{m} {d:.3f} deg" for m, d in near) + ". ")
    if flips:
        text += ("C1's verdict changes inside the band (" + ", ".join(flips) + "): the result is at the edge of the "
                 "break angle, and the last digits of the arithmetic (another computer) can turn it either way")
    else:
        text += "C1's verdicts are the same in these runs; a member a hair under the break angle could still turn them on another computer"
    return text


def edge_events(flips=(FLIP,), near=NEAR):
    """The sample with the note (and two plain assumptions) on its analysis and names on two members."""
    events = [json.loads(line) for line in SAMPLE_EVENTS.read_text(encoding="utf-8").splitlines()]
    events = copy.deepcopy(events)
    design = next(e for e in events if e["kind"] == "design.created")["data"]
    for m in design["members"]:
        if m["id"] in NAMES:
            m["name"] = NAMES[m["id"]]
    ana = next(e for e in events if e["kind"] == "analysis.finished")["data"]
    ana["assumptions"] = [PLAIN[0], band_note(flips, near), PLAIN[1]]
    return events
