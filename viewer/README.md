# Result viewer

One page (`index.html`) shows the analysis records of a run, field by field, with a field switch. It opens from disk (file://). three.js 0.170.0 comes from jsDelivr, the same pinned version as `web/index.html`.

## Make the data, open the page

From the repository root:

```
python -m viewer.src.nvh      <run dir>   # modal record      -> viewer/data/nvh.js
python -m viewer.src.crash    <run dir>   # crash record      -> viewer/data/crash.js
python -m viewer.src.assembly <run dir>   # assembly record   -> viewer/data/assembly.js
python -m viewer.src.thermal  <run dir> [--energy <run dir>]          # thermal (+ drive_energy) -> viewer/data/thermal.js
python -m viewer.src.fatigue  <run dir>                               # fatigue record           -> viewer/data/fatigue.js
python -m viewer.src.strength <run dir>                               # frame_static record      -> viewer/data/strength.js
python -m viewer.src.strength <version dir> --frames <dir> [--name N] [--every n]   # saved shell frames (the step hit)
python -m viewer.src.handling <run dir> --also <run dir> ...     # tilt / braking / lane_change / offroad_roll
python -m viewer.src.powertrain <run dir> --also <run dir> ...   # launch / step_climb_dynamic / drive_energy
python -m viewer.src.aero <run dir>                              # crosswind
```

`<run dir>` is a run folder (`runs/<id>`, with `v001/`, ...) or one version folder. By default each script takes the newest version that has the tool's record. `--version v003` or `--analysis ana-0038` picks another one, and `--out` sets the output folder. Then open `viewer/index.html` in a browser; `index.html#crash` opens one field. `viewer/data/` is not committed.

## What each field shows

- **NVH** (`modal`): the frame beams moved by a mode shape, sin(phase) × amplitude. The parts have no degrees of freedom of their own in the mode shape: each part box moves with the nodes it sits on (`src/nvh.py` `part_motion`, `src/common.py` `rigid_fit`): one node, its translation; several, their mean translation plus the least-squares rigid rotation, turned as a proper rotation on the page. Parts without carried_by move with the wheel centre node `wc:<id>` when the record holds it, else with the support's frame node. The tyres move the same way, and each support's spring is drawn as K1 has it (`k1_frame.model.restrain_on_wheels`): from the wheel node down to a fixed ground point at the support's position less its radius. Part colour option 「この分野の色」: each part's motion relative to the largest part's motion in the mode. Colour (switch): each member's strain relative to the mode's largest (default), or the node displacement |u|/|u|max. The strain is computed by `src/nvh.py` (`member_strain`, its `STRAIN_METHOD` text is shown on the page): axial from the end translations; bending estimated from translations only (the record has no rotations): node rotation = the mean chord rotation of the members at the node, end rotation relative to the chord, curvature (4φ_near + 2φ_far)/L, times the half depth. Each member's share of the mode's strain energy (EA, EI from the design's sections and E; torsion left out) and the top 5 per mode are listed. Mode shapes have no physical size; the page also gives the largest strain per 1 mm of the largest node's motion so modes can be compared.
- **Crash** (C1), either record kind (`python -m viewer.src.crash <run> [--tool crash|crash_beam]`; by default crash_beam when the run has one):
  - **B** (`crash_beam`, K2's co-rotational beams with plastic hinges): the run's own `node_motion` positions over time, real size. Colour, from the record (K2 182f148f on): the member's axial stress = its axial force (series `member_axial_force.<m>`) / the section's area from the design, or the larger of its end plastic hinge rotations (`member_hinge.<m>.<end>`); or, from the positions, the fold angle at the element ends. Broken members light grey. Part boxes move by the mean motion of their carried_by nodes and are cut at the wall face (they have no contact with the wall). The header gives the stop (the run stops when a piece of 3 % or more of the mass breaks off the seats' side: the time, the member that broke, the piece's share), the peak and mean deceleration and the crush up to the stop; the inputs pane adds the least room to the occupant boxes (the hot spots, OK / NG). Plots: deceleration, crush, and the axial force of the member clicked (at first the one whose break stopped the run). Playback runs at a chosen fraction of real time (0.005 to 1 times, default 0.01: the 66 ms run plays in about 7 s).
  - **A** (`crash`, 1-D mass and spring): "A is a 1-D model: no deformed shape is drawn". The vehicle stands still (never moved into the wall); colour = the stored peak stress × a(t)/a_max; the elastic deflection solved at the peak is off by default (scale 0). Plots: deceleration, crush and wall force over t.
- **Assembly** (`assembly`, H1/H3; `entry_opening` H7 and `spray_reach` H6 as numbers): part boxes coloured by step order, by status, or by interference depth. The parts move along `path_mm` in step order. A plot shows the tool gap per step.

- **Strength** (R1 / R1s), two kinds of data:
  - **beam** (`frame_static` record): the beams coloured by each member's von Mises (the record's per_member), deformed by the record's per_node displacement × scale; the record's vectors as arrows; 判定 from the load case's criterion; the yield marks are the frame members' materials. Part boxes move with the nodes the tool loads them on (`nvh.part_motion`).
  - **shell** (`--frames`: frames.npz, frames_meta.pkl and load_paths.json saved by a frame_shell step-hit run with K2's mount loads; nothing is solved): the shell mesh coloured by the element von Mises (max of the two surfaces, at the element centre) at each saved instant, deformed × scale (the displacement less the wheel mounts' rigid motion); the excluded wheel-mount patch grey; the IIW hot spot of each instant marked (purple ball) and plotted; colour up to the frame's yield (or the run's / this instant's largest); jumps to the joints (weld-line ends) and to the hot spot; the mount forces as arrows (at the one instant load_paths.json holds them) and the mesh nodes each force is spread on (the load path; click a mount in the list). The parts layer uses the version's design regenerated with the variables named in load_paths.json (those this generator does not know are listed, not applied); a part whose id is among the saved mesh's element labels is meshed (stiffness), the wheel kinds are left out (the mount forces carry them, `k1_frame.analyze.sprung_items`), the others are point masses on their joints; the boxes move with the mean displacement of the mesh nodes at their design nodes. Data: about 23 MB for 50 instants of a 110k-element mesh (`--every` keeps fewer).
- **Fatigue** (`fatigue`, R3): the beams coloured by each member's Miner damage over the distance (the largest weld point of the member; log scale, 1 = the life); 判定 against the case's criterion; the road profile of the record's scene passes under the tyres at the case's speed (road point x drawn at x_front + x − v t, as `shelf.fatigue.road_wheel_forces` meets it). Plots: the worst point's stress and the wheel loads over the simulated road, and the S-N curve with the stress ranges counted at the worst point (log-log). The frame does not move (the stresses come from influence coefficients).
- **Thermal** (`thermal`, T1–T3; `drive_energy` for the battery): the part boxes of each RC-network node's kind (battery, motor, controller: `k3_tools._parts_of`) coloured by that node's temperature (`temp.<node>`) at the time of the slider, from the air temperature to the run's highest; the network as `network_from_design` builds it drawn in the side panel (C, R to the air, τ); plots: the temperatures with their limits (design variables `<node>_max_temperature`, or the case's criterion), the selected node's loss and heat to the air, the power through the chain (power.* with part_id), and the battery's charge when the run (or `--energy`) has a drive_energy record. The thermal network has no shape: nothing moves.
- **Handling** (`tilt` S1, `braking` S4, `lane_change` S3, `offroad_roll` S5), **powertrain** (`launch` P3, `step_climb_dynamic` P1, `drive_energy` P4) and **aero** (`crosswind` F1): `python -m viewer.src.<field> <run> --also <run> ...`, one case per run folder (its newest record of the field's tools), a case switch on the page. The K2 replays (`src/common.py` `k2_motion`, `js/vehicle.js`) pose the vehicle from the record's own series at their times: the design origin carried by the body (`body_x/y/z`) and R = Rz(yaw) Ry(pitch) Rx(roll); the frame and the sprung part boxes move as the one rigid body (K2 has no frame flexing), wheel-kind parts with their wheel centre, suspension kinds halfway (K2's `model._unsprung` rule); tyres turn by steer and spin and are coloured by wheel load, friction use or (powertrain) slip; green / red arrows are each contact's normal and friction force. The tilt table and the slope are the road frame: the whole road is turned by the record's angle channel about its low edge over a fixed level floor. Gates, cones, the road profile (two wheel tracks) and the step come from the record's scene. Powertrain: per-wheel torque = the record's driven-wheel total shared equally; slip = (ωr − v)/max(|v|, 0.5 m/s), ω from the record's `wheel_spin`. drive_energy has no geometry: the parts stand still and the battery box is coloured by the capacity used up to t. Aero: the side force is drawn at K2's side centre of pressure (`CrosswindTool.aero`) as the record's full-gust `side_force` × (wind_speed(t)/gust)². Playback defaults to real time (×50 for drive_energy).

Every field lists its inputs (and where each one comes from).

## Parts in the calculation (every field)

Every part of the design is drawn as its box (with rpy), and `src/common.py` `parts_ledger` says how each enters that field's calculation, by calling the tools' own functions (`hiraku.contract.loading.parts_on_board`, `hiraku.solvers.k0_handcalc.vehicle.mass_items`) and following the named function's rule:

- NVH (`k1_frame.modal.add_masses`): parts with carried_by as lumped masses in equal shares on those frame nodes; parts without carried_by (unsprung) at the nearest support's wheel centre node `wc:<id>` (or its frame node if the support is there); members with stiffness and their own consistent mass.
- Crash A (`shelf.crash`): every part on board in the one rigid mass; only the crush zone's rails crush (`library_crash.crush_zone`); K1 at the peak loads the parts on the same nodes.
- Crash B (`k2_vehicle.crash_beam.mesh._lump_parts`, branch claude/k2-crash-beam-b, rule copied): point masses on carried_by nodes (a node no member uses: the nearest frame node), unsprung parts at their support's frame node; no part stiffness, no contact of parts with the wall. Part boxes move with those nodes.
- Assembly (`k4_geometry.model.bodies`): every part on board is a body (shape only, masses not used).
- Strength beam and fatigue (`src/ledgers.py` `k1_ledger`; `k1_frame.model.FrameModel.add_body_loads`): parts with carried_by as mass items on those nodes (their weight, or in fatigue their inertia relief), parts without at the support's wheel centre node; the record's vehicle_mass and frame_mass compared (frame_static).
- Strength shell (`shell_ledger`, `k1_shell.frame.analyze_shell`): see Strength above.
- Thermal (`thermal_ledger`): the RC network's nodes (熱回路の節点), the power chain's stages (電力の段), every other part in the vehicle mass of the drive only; the two thermal categories are coloured by `js/thermal.js`.

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

- K2 tools (`k2_vehicle.model.build_vehicle` on the loading's parts): ばね上 (the rigid body), ばね下 (wheel kinds at the nearest corner with travel), 半分 (suspension kinds); members as rods' mass in the body, no stiffness. drive_energy (K3): every part on board in one mass, compared with the record's `vehicle_mass`.

Tests: `python -m unittest tests.viewer.test_viewer tests.viewer.test_viewer_tfs tests.viewer.test_vehicle_fields`. The browser test needs Playwright for the system Python (`py`) and is skipped without it.
