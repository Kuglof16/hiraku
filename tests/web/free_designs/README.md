# A design Claude could write (no kind of design)

`serving-robot-hand-v1.json`: the design department's trial of a hand-written design (a serving
robot's frame: 4 legs 500 x 400 mm, shelves at z 400 and 850, 20 x 20 x 1.6 square tubes, 12 nodes,
20 members, every node `joint: weld`; no `archetype`, `variables: {}`). Copied as it is on
2026-11-07 from that department's notes (not in this repository) for the screen's tests: it passes
`contract.errors("design", ...)`. Its numbers are the trial's own (the department's choice for the
trial, no outside source); nothing here checks them.

Used by `tests/web/test_joints_browser.py` (node.joint node by node in a design without an
archetype).
