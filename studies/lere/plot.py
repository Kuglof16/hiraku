"""Contour images (library + script). Fixed 0-300 MPa, marks 143.3 (criterion) and 245 (STKR400 yield).

Library: step_joint(...) and mode_strain(...) take arrays and write a PNG (long side <= 1500 px, <= 1 MB).
Script:  python -m studies.lere.plot DIR/NAME [JOINT ...]   (from DIR/NAME/frames.npz + frames_meta.pkl, written by
         run_set --save; default joints LE LB) -> DIR/NAME/step_<JOINT>.png
matplotlib is needed (not in requirements.txt); without it the images are skipped."""
import os
import pickle
import sys

import numpy as np

STEP_TICKS = [0, 50, 100, 143.3, 200, 245, 300]
STEP_LABELS = ["0", "50", "100", "143.3 criterion", "200", "245 STKR400 yield", "300"]


def have_matplotlib():
    try:
        import matplotlib  # noqa: F401
        return True
    except ImportError:
        return False


def _fig(xyz, v, norm, cmap, box, view, title, cb_ticks, cb_labels, cb_label, marks=(), star=None, out=""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    from PIL import Image
    fig = plt.figure(figsize=(10, 8), dpi=150)
    ax = fig.add_subplot(111, projection="3d")
    ax.set_proj_type("ortho")
    ax.computed_zorder = False
    ax.add_collection3d(Poly3DCollection(xyz, facecolors=cmap(norm(v)), edgecolors="k", linewidths=0.15))
    lo, hi = box
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1), zoom=1.25)
    ax.set_axis_off()
    ax.view_init(elev=view[1], azim=view[0])
    if star is not None:
        ax.scatter(*star, s=120, c="magenta", edgecolors="k", marker="*", zorder=12, depthshade=False)
    fig.subplots_adjust(left=0.0, right=0.95, bottom=0.02, top=0.86)
    fig.text(0.01, 0.01, f"orthographic; +x forward, +y left, +z up; cube {2 * r:.0f} mm on a side", fontsize=8)
    fig.suptitle(title, fontsize=9, y=0.985)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = fig.colorbar(sm, ax=ax, shrink=0.7, extend="max", pad=0.08)
    cb.set_ticks(cb_ticks); cb.set_ticklabels(cb_labels)
    for y in marks:
        cb.ax.axhline(y, color="k", lw=1.5)
    cb.set_label(cb_label, fontsize=8)
    fig.savefig(out, dpi=150)
    plt.close(fig)
    im = Image.open(out)
    if max(im.size) > 1500 or os.path.getsize(out) > 1_000_000:
        im.thumbnail((1500, 1500)); im.save(out, optimize=True)
    return Image.open(out).size


def step_joint(joint, nodes, els, centres, member, vm_max, t_s, joint_hot, weld_lines, design, out):
    """Zoom on joint at the instant of its largest hot spot. vm_max: (instants, elements) MPa; joint_hot: per instant
    {joint: {value, member, position_mm}}; weld_lines: [{joint, p0, p1}]. Returns a facts line, or None."""
    import matplotlib.pyplot as plt
    from matplotlib import colors
    vals = [abs(h[joint]["value"]) if joint in h else -1 for h in joint_hot]
    if not vals or max(vals) < 0:
        return None
    i = int(np.argmax(vals))
    h = joint_hot[i][joint]
    pts = [np.array(w[k]) for w in weld_lines if w["joint"] == joint for k in ("p0", "p1")]
    c = np.mean(pts, axis=0) if pts else np.asarray(h["position_mm"], float)
    lo, hi = c - 110.0, c + 110.0
    sel = np.nonzero(np.all((centres >= lo) & (centres <= hi), axis=1))[0]
    v = vm_max[i][sel]
    k = sel[int(np.argmax(v))]
    cmap = plt.get_cmap("turbo").copy(); cmap.set_over(cmap(1.0))
    norm = colors.Normalize(0.0, 300.0)
    view = (-135, 25) if joint.endswith("E") else (-120, 25)
    title = (f"{design}\nt = {t_s[i]:.3f} s (instant of joint {joint}'s largest hot spot {h['value']:.0f} MPa on "
             f"{h['member']}, magenta star)\njoint {joint} zoom, from inboard-rear-above (azim {view[0]}, elev "
             f"{view[1]}); largest element value in view {v.max():.0f} MPa at {member[k]}")
    size = _fig(nodes[els[sel]], v, norm, cmap, (lo, hi), view, title, STEP_TICKS, STEP_LABELS,
                "von Mises, element centre, max(outer, inner) [MPa]; > 300 in top colour", (143.3, 245.0),
                np.asarray(h["position_mm"]), out)
    return (f"{out}\t{size}\tt {t_s[i]:.3f}\thot {h['value']:.1f} {h['member']}\tmax in view {v.max():.1f} at "
            f"{member[k]} {np.round(centres[k], 0).tolist()}")


def mode_strain(name, nodes, els, member, strain, index, freq, out):
    """Whole frame, the relative strain (0-0.5 scale) of one mode."""
    import matplotlib.pyplot as plt
    from matplotlib import colors
    cmap = plt.get_cmap("turbo").copy(); cmap.set_over(cmap(1.0))
    norm = colors.Normalize(0.0, 0.5)
    cen = nodes[els].mean(axis=1)
    k = int(np.argmax(strain))
    title = (f"set {name}: first frame mode, mode {index + 1}, {freq:.2f} Hz (k1_shell.modal 10 mm, N1)\n"
             f"relative von Mises strain of the mode shape (largest element 1); whole frame from rear-right-above "
             f"(azim -135, elev 30)\nlargest at {member[k]} {np.round(cen[k], 0).tolist()}")
    size = _fig(nodes[els], strain, norm, cmap, (nodes.min(axis=0), nodes.max(axis=0)), (-135, 30), title,
                [0, 0.1, 0.2, 0.3, 0.4, 0.5], ["0", "0.1", "0.2", "0.3", "0.4", "0.5"],
                "relative strain of the mode (von Mises, element); > 0.5 top colour", (), None, out)
    return f"{out}\t{size}\tmode {index + 1} {freq:.3f} Hz\tlargest {member[k]}"


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    d = sys.argv[1]
    joints = sys.argv[2:] or ["LE", "LB"]
    z = np.load(os.path.join(d, "frames.npz"))
    with open(os.path.join(d, "frames_meta.pkl"), "rb") as f:
        m = pickle.load(f)
    for j in joints:
        print(step_joint(j, z["nodes"], z["elements"], z["centres"], z["member"], z["vm_max"], z["t_s"],
                         m["joint_hot"], m["weld_lines"], m["design"], os.path.join(d, f"step_{j}.png")), flush=True)


if __name__ == "__main__":
    main()
