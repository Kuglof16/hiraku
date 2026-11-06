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

- **NVH** (`modal`): the frame beams moved by a mode shape, sin(phase) × amplitude, and coloured by the shape's amplitude. Also a mode list, and the frequency response with the mode and excitation frequencies. Mode shapes have no physical size.
- **Crash** (`crash`, C1): the beams coloured by each member's stress and deflected by each node's displacement. The record stores both at the peak deceleration only, so the page shows them × a(t)/a_max at other times. The whole vehicle moves into the wall box by crush(t): this is a translation, not a crushed shape. Plots: deceleration, crush and wall force over t. Inputs: speed, wall and mass.
- **Assembly** (`assembly`, H1/H3; `entry_opening` H7 and `spray_reach` H6 as numbers): part boxes coloured by step order, by status, or by interference depth. The parts move along `path_mm` in step order. A plot shows the tool gap per step.

Every field lists its inputs (and where each one comes from) and a NOT COMPUTED note.

## Files

- `src/common.py`: reads a run folder, packs arrays (gzip + base64), and turns the design into beam lines and part boxes. Its docstring gives the data shape.
- `src/<field>.py`: one script per field.
- `js/core.js`: shared drawing: beams, boxes, scene boxes, arrows, contour bar, clipping, picking.
- `js/plot.js`: the plot pane.
- `js/main.js`: the page: switch, time and scale, layers, inputs, and the info shown on a click.
- `js/<field>.js`: one file per field.

Tests: `python -m unittest tests.viewer.test_viewer`. The browser test needs Playwright for the system Python (`py`) and is skipped without it.
