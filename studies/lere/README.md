# LE/RE + N1 study

Two questions on the v005 offroad-4w design, judged on the same design changes:

- **Step hit, the rear rail-end joints LE / RE.** K2 `step_climb_dynamic` (record_run speed, 100 mm step, 10 km/h)
  gives the mount loads at each instant; the frame shell (`k1_shell.frame.analyze_shell`, mount_path `footprint`)
  is solved at every instant. Read: the hot spots at LB/RB and LE/RE, the largest hot spot, the beam
  max_stress_over_time, the R9 bracket plate / weld utilisation. Criterion 143.3 MPa (beam, hot spots), 1.0 (R9).
- **N1, the shell's first frame mode.** `k1_shell.modal` at 10 mm elements on the catalog's N1 load case
  (`data/requirements/offroad-4w.yaml`): the first 8 modes, their kind (body / roll bar / frame) and where the
  strain is. Frequencies are reported, not just pass / fail.

The base design is `v005.json` (a design.json produced by HIRAKU's scripted offroad-4w demo run) plus BASE in
`common.py`: tower_b 35, tower_t 4.5, rail_b 40, gusset_ends 1, gusset_leg 120, gusset_thickness 4.5,
mount_plate_t 9, mount_weld_leg 6.3, and the environment `HIRAKU_LCA_FULL_WALL=1` (the rear lca bracket's base
across the whole rail bottom wall). `HIRAKU_SHELL_WORKERS=0` (no process pool). The scripts set both themselves.

## Commands (from the repository root, with the venv's python)

```sh
python -m studies.lere.runq --list                       # the sets
python -m studies.lere.runq --out runs/lere --jobs 6     # every set, 6 at a time
python -m studies.lere.runq --out runs/lere --jobs 6 A B50 D50c45   # some sets
python -m studies.lere.table runs/lere                   # runs/lere/table.md and table.json
python -m studies.lere.run_set A --out runs/lere [--tyre 60] [--no-step] [--no-modal] [--save] [--save-modal]
python -m studies.lere.checks D50c45 --out runs/lere     # H1 / H3 / H7 / C1 on a set (light)
python -m studies.lere.sets --regen                      # regenerate every set (light, no solve)
```

`runq` starts each set as its own `python -m studies.lere.run_set NAME --out DIR` process, at most `--jobs` at a
time (default 4), the starts `--stagger` seconds apart (default 120). Each set waits for 15 GB free memory
(`/proc/meminfo` MemAvailable on Linux) before each shell solve. A set whose result.json already holds both the
step hit and the modes is skipped (`--force` re-runs). Flags after `--` go to every run_set
(e.g. `-- --save`). `runs/` is not committed.

The contour images need matplotlib (not in requirements.txt): `python -m pip install matplotlib`. Without it the
sets run and the images are skipped.

## Sets (tyre 60 N/mm unless noted; the variables are over BASE)

| set | what |
|---|---|
| A | the base re-run |
| A45, A90 | the base at tyre 45 and 90 N/mm |
| B50, B33 | + tower brace (rear tower post to cross_B, the tower's section 35 x 4.5), post end at 1/2, 1/3 of the post's height |
| C32, C45 | cross members 3.2, 4.5 mm wall |
| Ch60f | cross members 60 mm tall, roll bar feet 70 mm tall (cross_h 60 alone clashes with the 40 x 60 feet at LS2) |
| E1 | rails 100 mm tall |
| E2 | corner gusset legs 150 mm |
| S1g0 | roll bar rear stays on, corner gussets off |
| S2g0 | roll bar rear stays 48.6 x 3.2, corner gussets off |
| P2 | roll bar tube 48.6 x 3.2 |
| O1 | roll bar offset 200 mm |
| H1 | roll bar height 1000 mm |
| M2 | battery at x -700 mm (mass placement) |
| D50c32, D50c45, D33c32, D33c45 | a tower brace (B50 / B33) with a cross wall (C32 / C45) |
| D50h60, D33h60 | a tower brace with Ch60f |
| DU | N1 upper bound: B50 + C45 + S2g0 + P2 (corner gussets off: with them on the rear stays run into gusset_LE_front_upper) |

The exact variables are in `sets.py` (`runq --list` prints them).

## Whole-vehicle step hit (W sets: the base design, step hit only, no modal)

`analyze_shell(..., model="whole", adds=[...])` (`hiraku/solvers/k1_shell/whole.py`; no adds = today's solve). The
additions, cumulative in the practitioner's order:

| set | adds | what is new in the model |
|---|---|---|
| W0 | - | today's solve (frame shell, K2's mount forces at the footprints), through model "whole" |
| W1 | arms | the linkage of k1_shell.linkage (arms and tie rod as beams on K2's bushings, knuckle + wheel rigid, coil spring along the damper axis fitted to K2's wheel rate, tyre spring to a ground node). Each frame-side mount is a node at the hard point tied (RBE3 type, no stiffness) to the nodes today's solve loads. Load: K2's knuckle load of each corner (the resultant of its mount forces: road force + m_u (g - a_u), moment about the wheel centre), its vertical part through the tyre spring at the contact point. Held as now: inertia relief, 3-2-1. |
| W2 | + damper_mounts | the damper's upper mount rigid (RBE2) on its footprint: the damper bracket is not meshed in v005 (its base face is not on a tube wall along the pin), so this is its stiffness as rigid, an upper bound |
| W3 | + brackets | the arm / tie rod mounts rigid: a pin through both cheeks of the meshed rear lca brackets (their plates are in the shell already in W0), rigid footprints for the brackets not meshed, rigid joint nodes for the front lca mounts |
| W4 | + seats | the seats (with their occupants) and the battery as rigid bodies on the top walls of their carried_by joints (the frame between them rigid: an upper bound) |
| W5 | + panels | floor_front, deck (at their fit_* fixings, as rigid spacers), floor and bulkhead (no fixings: bonded where they touch the frame, an upper bound) as shell plates, their mass kept |
| W6 | as W5 | addition 6, the tyre spring (60 N/mm), is in W1 already |
| W1t45, W1t90 | arms | W1 at tyre 45 / 90 N/mm (K2 re-run; in the shell the tyre spring is in series with the load and changes no stress) |

```sh
python -m studies.lere.runq --out runs/whole --jobs 6 W0 W1 W2 W3 W4 W5 W1t45 W1t90
python -m studies.lere.table runs/whole
python -m studies.lere.run_set W1 --out runs/whole_check --h 20 --no-plot   # a quicker look at 20 mm elements
```

result.json carries the same fields plus `adds`, `model_mass_kg` (the sprung mass of the shell solve),
`element_size_mm`, `whole_notes`, `corners` (motion ratio, spring rate, wheel rate, tyre per corner), `mount_ties`,
`best["post <member>"]` (the post bases: the largest element von Mises within 70 mm of the foot of tower_post_LB /
RB and rollbar_foot_L / R; their joints are tied, without weld strips, so they have no IIW hot spot), and with arms
`linkage_check`: per mount K2's force and the linkage's at the instants of the largest LE / RE / any hot spot and
of the largest K2 mount force. The record's tyre force series (25 Hz) are not on K2's mount instants, so the
corners are loaded by the knuckle load, not by the record's tyre forces.

## Outputs (DIR/NAME/)

- `result.json`: name, variables, extra, tyre_stiffness, mass_kg (parts + members), post_spare_mm (the rail top
  face left beside each rear tower post), doc; `modal` (frequencies, kinds, flex, ladder_flex, first_frame_mode,
  first_frame_index, digest per mode, ortho, max_residual, model_mass_kg, load_case, timings); k2_status, instants,
  beam_max_stress_over_time, n_elements, `best` (per hot spot / R9 item: [largest value, its instant t s, where]).
- `step_LE.png`, `step_LB.png`: von Mises contours zoomed on the joint at the instant of its largest hot spot, fixed
  0-300 MPa, marks at 143.3 (criterion) and 245 (STKR400 yield). `mode_frame.png`: the first frame mode's strain.
- `log.txt`: the set's output. With `--save`: frames.npz, frames_meta.pkl, load_paths.json; `--save-modal`:
  modal.npz. `python -m studies.lere.plot DIR/NAME LE LB` redraws the step images from a --save run.
- `DIR/table.md`, `DIR/table.json` from `studies.lere.table`; `DIR/runq.log` the queue.

## Memory and time

At 10 mm elements one set holds about 10-15 GB at its peak (the shell solve; the frame mesh has about 100 000
elements). With `--jobs 6`, give the machine about 6 x 15 GB = 90 GB, or fewer jobs. The 15 GB wait makes a set
pause rather than swap, but it does not reserve memory: the stagger keeps the sets from all starting at once.

## Tests

This folder has no tests of its own: the study uses the tools' own tests (`tests/solvers/test_k1_shell.py`,
`tests/solvers/test_designgen_gusset_mesh.py`, the whole model's `tests/solvers/test_k1_shell_whole.py` and the rest
of `tests/`).
