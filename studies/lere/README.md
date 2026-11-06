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
`tests/solvers/test_designgen_gusset_mesh.py` and the rest of `tests/`).
