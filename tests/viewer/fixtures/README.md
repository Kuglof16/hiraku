# Viewer test fixtures

Small run folders trimmed from real records (made 2026-11-06). Each design keeps only its name, nodes, members, sections, materials and a few parts (id, name, kind, module, box, mass).

- `nvh/`: the modal record (N1-40kmh) of version v005 of the scripted demo run `20261106-081501-offroad-4w-scripted`. The first 3 mode shapes are kept, with the frequency response at 50 points. `project.json` holds only that load case.
- `crash/`: a C1 run made for the viewer. It is the `crash` tool on `data/designs/offroad-4w-v1.json`, with the C1 load case from the catalog builders (`data/requirements/offroad-4w.yaml`, C1-40kmh). The series are kept at 40 points, and the design at 8 parts.
- `crash_b/`: a C1 run made for the viewer (2026-11-06) with K2's `crash_beam` tool (branch claude/k2-crash-beam-b, 77f9ef54, not in main yet) on `data/designs/offroad-4w-v1.json` with the catalog's C1-40kmh load case. The node_motion field keeps every 6th of its 122 frames and the last (22); the series every 3rd point, the last and the peak. The design keeps 8 parts with carried_by.
- `assembly/`: the H1-interference assembly record of v001 of the scripted run `runs/20261103-013318-offroad-4w-scripted` (19 steps, 4 motion interferences), with the parts its steps and interferences name.
