"""Shared helpers (library only: no work and no environment changes at import).

The base design (studies/lere/v005.json + BASE + a set's variables), mass, the rear post footprint spare, the
free-memory wait and a python process count. Call setup_env() in a script's main() before importing hiraku's
solvers: it sets HIRAKU_LCA_FULL_WALL=1 (the rear lca bracket's base across the whole rail bottom wall) and
HIRAKU_SHELL_WORKERS=0 (no process pool)."""
import json
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.normpath(os.path.join(HERE, "..", ".."))
V5 = os.path.join(HERE, "v005.json")
REQUIREMENTS = os.path.join(REPO, "data", "requirements", "offroad-4w.yaml")
BASE = {"tower_b": 35, "tower_t": 4.5, "rail_b": 40, "gusset_ends": 1, "gusset_leg": 120, "gusset_thickness": 4.5,
        "mount_plate_t": 9, "mount_weld_leg": 6.3}
ENV = {"HIRAKU_LCA_FULL_WALL": "1", "HIRAKU_SHELL_WORKERS": "0"}
MEM_GB = 15.0


def setup_env():
    """Set the study's environment variables and put the repository root on sys.path."""
    os.environ.update(ENV)
    if REPO not in sys.path:
        sys.path.insert(0, REPO)


def regen(vars_):
    """v005 regenerated with the given design variables (name -> value in the variable's unit)."""
    from hiraku import designgen
    from hiraku.designgen import ladder_4w
    with open(V5, encoding="utf-8") as f:
        v5 = json.load(f)
    var = {k: {"value": float(v), "unit": ladder_4w.VARIABLES[k].unit} for k, v in vars_.items()}
    return designgen.regenerate(v5, var)


def _area(sec):
    dm = sec["dims_mm"]
    if sec["shape"] == "rect_tube":
        return 2 * dm["t"] * (dm["b"] + dm["h"]) - 4 * dm["t"] ** 2
    if sec["shape"] == "round_tube":
        return math.pi * dm["t"] * (dm["d"] - dm["t"])
    return 0.0


def mass(d):
    """Parts plus members (length x section area x density), kg."""
    import numpy as np
    nodes = {n["id"]: np.asarray(n["position_mm"] if "position_mm" in n else n["xyz_mm"], float) for n in d["nodes"]}
    m = sum(float(p.get("mass_kg", 0)) for p in d["parts"])
    for mb in d["members"]:
        L = float(np.linalg.norm(nodes[mb["node_b"]] - nodes[mb["node_a"]]))
        m += L * _area(d["sections"][mb["section"]]) * d["materials"][mb["material"]]["density_kg_m3"] * 1e-9
    return m


def spare(d):
    """The smallest rail top face left beside each rear tower post (ladder_4w.rail_post_footprints), mm."""
    from hiraku.designgen.ladder_4w import rail_post_footprints
    out = {}
    for f in rail_post_footprints(d):
        if f["member"].startswith(("tower_post_LB", "tower_post_RB")):
            out[f["member"]] = round(min(out.get(f["member"], 1e9), f["spare_mm"]), 2)
    return out


def free_memory_gb():
    """Memory free for new work now (GB): Linux /proc/meminfo MemAvailable, Windows available physical memory
    (as tests/solvers/tube_level.free_memory_gb); nan if not known."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Status(ctypes.Structure):
                _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            s = Status()
            s.dwLength = ctypes.sizeof(s)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s)):
                return s.ullAvailPhys / 2**30
            return float("nan")
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 2**20  # kB -> GB
        return float("nan")
    except Exception:
        return float("nan")


def wait_mem(need_gb=MEM_GB, poll_s=30):
    """Block until at least need_gb is free (goes on at once if free memory cannot be read). Returns the free GB."""
    while True:
        f = free_memory_gb()
        if f != f or f >= need_gb:  # nan: unknown
            return f
        print(f"free memory {f:.1f} GB < {need_gb:g}, waiting", flush=True)
        time.sleep(poll_s)


def py_count():
    """Number of python processes on the machine (psutil if installed, else /proc), or None if not known."""
    try:
        import psutil
        n = 0
        for p in psutil.process_iter(["name"]):
            if "python" in (p.info.get("name") or "").lower():
                n += 1
        return n
    except ImportError:
        pass
    except Exception:
        return None
    if os.path.isdir("/proc"):
        n = 0
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
                    if "python" in f.read().lower():
                        n += 1
            except OSError:
                pass
        return n
    return None
