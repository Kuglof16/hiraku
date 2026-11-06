# Result viewer

One page (`index.html`) shows the analysis records of a run, field by field, with a field switch. It opens from disk (file://). three.js 0.170.0 comes from jsDelivr, the same pinned version as `web/index.html`.

## Make the data, open the page

From the repository root:

```
python -m viewer.src.nvh      <run dir>   # modal record      -> viewer/data/nvh.js
python -m viewer.src.crash    <run dir>   # crash record      -> viewer/data/crash.js
python -m viewer.src.assembly <run dir>   # assembly record   -> viewer/data/assembly.js
```

`<run dir>` is a run folder (`runs/<id>`, with `v001/`, ...) or one version folder. By default each script takes the newest version that has the tool's record. `--version v003` or `--analysis ana-0038` picks another one, and `--out` sets the output folder. Then open `viewer/index.html` in a browser; `index.html#crash` opens one field. `viewer/data/` is not committed.

## What each field shows

- **NVH** (`modal`): the frame beams moved by a mode shape, sin(phase) × amplitude. Colour (switch): each member's strain relative to the mode's largest (default), or the node displacement |u|/|u|max. The strain is computed by `src/nvh.py` (`member_strain`, its `STRAIN_METHOD` text is shown on the page): axial from the end translations; bending estimated from translations only (the record has no rotations): node rotation = the mean chord rotation of the members at the node, end rotation relative to the chord, curvature (4φ_near + 2φ_far)/L, times the half depth. Each member's share of the mode's strain energy (EA, EI from the design's sections and E; torsion left out) and the top 5 per mode are listed. Mode shapes have no physical size; the page also gives the largest strain per 1 mm of the largest node's motion so modes can be compared.
- **Crash** (C1), either record kind (`python -m viewer.src.crash <run> [--tool crash|crash_beam]`; by default crash_beam when the run has one):
  - **B** (`crash_beam`, K2's co-rotational beams with plastic hinges): the run's own `node_motion` positions over time, real size. Colour, computed from the same positions (the record has no member stresses): element axial strain or the fold angle at the element ends. Broken members light grey. Part boxes move by the mean motion of their carried_by nodes. The page counts frame nodes beyond the wall face (and the depth) per frame and over the run, and part boxes past it. Plots: deceleration, crush, nodes beyond the wall.
  - **A** (`crash`, 1-D mass and spring): "A is a 1-D model: no deformed shape is drawn". The vehicle stands still (never moved into the wall); colour = the stored peak stress × a(t)/a_max; the elastic deflection solved at the peak is off by default (scale 0). Plots: deceleration, crush and wall force over t.
- **Assembly** (`assembly`, H1/H3; `entry_opening` H7 and `spray_reach` H6 as numbers): part boxes coloured by step order, by status, or by interference depth. The parts move along `path_mm` in step order. A plot shows the tool gap per step.

Every field lists its inputs (and where each one comes from).

## Parts in the calculation (every field)

Every part of the design is drawn as its box (with rpy), and `src/common.py` `parts_ledger` says how each enters that field's calculation, by calling the tools' own functions (`hiraku.contract.loading.parts_on_board`, `hiraku.solvers.k0_handcalc.vehicle.mass_items`) and following the named function's rule:

- NVH (`k1_frame.modal.add_masses`): parts with carried_by as lumped masses in equal shares on those frame nodes; parts without carried_by (unsprung) at the nearest support's wheel centre node `wc:<id>` (or its frame node if the support is there); members with stiffness and their own consistent mass.
- Crash A (`shelf.crash`): every part on board in the one rigid mass; only the crush zone's rails crush (`library_crash.crush_zone`); K1 at the peak loads the parts on the same nodes.
- Crash B (`k2_vehicle.crash_beam.mesh._lump_parts`, branch claude/k2-crash-beam-b, rule copied): point masses on carried_by nodes (a node no member uses: the nearest frame node), unsprung parts at their support's frame node; no part stiffness, no contact of parts with the wall. Part boxes move with those nodes.
- Assembly (`k4_geometry.model.bodies`): every part on board is a body (shape only, masses not used).

Categories (legend, click info): mass on frame nodes / unsprung / modelled with stiffness / mass only / shape only / not in this calculation (left off by the loading). The mass table compares the per-category sums and the members' mass with the record's own mass (crash `vehicle_mass`, `frame_mass`; crash_beam's mass assumption per support); NVH and assembly records state none.

## Screen rules (keep for new fields)

- No file paths on screen or in the data: `source.run` is the run folder's name.
- Short Japanese text; longer explanations in closed panels (`説明`, `計算での扱い`); nothing drawn over the 3D view (header above it, legend and click info in the side panel).
- No lists of what is not computed; only what is needed to read the picture: the deformation scale (変形 ×N、実寸ではない), relative values, the colour's quantity.
- OK / NG for pass / fail, 判定 for a judged result, vN for design versions.

## Files

- `src/common.py`: reads a run folder, packs arrays (gzip + base64), and turns the design into beam lines and part boxes. Its docstring gives the data shape.
- `src/<field>.py`: one script per field.
- `js/core.js`: shared drawing: beams, boxes, scene boxes, arrows, contour bar, clipping, picking.
- `js/plot.js`: the plot pane.
- `js/main.js`: the page: switch, time and scale, layers, inputs, and the info shown on a click.
- `js/<field>.js`: one file per field.

Tests: `python -m unittest tests.viewer.test_viewer`. The browser test needs Playwright for the system Python (`py`) and is skipped without it.
