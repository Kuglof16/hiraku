"""Digest of a modal_shell record folder: per mode the motion direction, strain share per group and member, the
hottest element.

  python -m studies.lere.rec_modes RECORD_DIR   (RECORD_DIR/analysis.json, RECORD_DIR/meshes/mesh-0001.json; the
                                                 record's fields: shape then strain per mode)"""
import json
import os
import sys


def main():
    import numpy as np
    from . import modeinfo
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    D = sys.argv[1]
    with open(os.path.join(D, "analysis.json"), encoding="utf-8") as f:
        rec = json.load(f)
    with open(os.path.join(D, "meshes", "mesh-0001.json"), encoding="utf-8") as f:
        mesh = json.load(f)
    X = np.asarray(mesh["nodes_mm"], float)
    E = np.asarray(mesh["elements"], int)
    mem = mesh["element_member"]
    fl = rec["fields"]
    for i in range(0, min(len(fl), 16), 2):
        sh, st = fl[i], fl[i + 1]
        u = np.asarray(sh["per_mesh_node"], float)
        s = np.asarray(st["per_element"], float)
        dg = modeinfo.digest(X, E, mem, u, s, top=8)
        print(f"{sh['label'][:60]} {sh.get('frequency_Hz', 0):.3f} Hz  motion xyz {dg['motion_xyz_share']}")
        print("   groups", modeinfo.group_share(mem, X, E, s, modeinfo.GROUPS))
        print("   strain", dg["strain_share"])
        print("   motion", dg["motion"][:5])
        print("   hottest", dg["hot_member"], dg["hot_xyz"])


if __name__ == "__main__":
    main()
