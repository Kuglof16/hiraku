"""One mesh level of tests.solvers.test_fe_tubes.SpaceFrameConvergenceTest in a process of its own:

    python -m tests.solvers.tube_level 20

prints one JSON line {"size", "series", "seconds", "peak_mb"}. A module run as a program (not a multiprocessing
pool, whose spawned workers import the parent's main module again: under `python -m unittest` that is the test
run itself)."""

import json
import os
import sys
import time


def level(size: float) -> dict:
    """The hot spots at D0 and the largest, standing still and at the lift-off, with the time and this process's
    peak memory."""
    from hiraku.designgen import space_frame_utv as sf
    from hiraku.solvers.fe import tubes

    t0 = time.perf_counter()
    d = sf.build()
    r = tubes.analyse(d, size, states=tubes.k2_states(d))
    series = {}
    for case in ("static", "lift"):
        c = r["cases"][case]
        series[f"D0 {case}"] = max(abs(h["value"]) for h in c["hot_spots"] if h["joint"] == "D0")
        series[f"max {case}"] = abs(c["hot_spot_max"]["value"])
    return {"size": size, "series": series, "seconds": time.perf_counter() - t0, "peak_mb": peak_memory_mb()}


def peak_memory_mb() -> float:
    """This process's peak working set (MB) on Windows, its peak resident size elsewhere; nan if not known."""
    try:
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
            c = Counters()
            c.cb = ctypes.sizeof(c)
            get = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            get.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
            if get(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(c), c.cb):
                return c.PeakWorkingSetSize / 2**20
            return float("nan")
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0  # kB on Linux
    except Exception:
        return float("nan")


def free_memory_gb() -> float:
    """The memory free for new work now (GB): Windows' available physical memory, Linux's MemAvailable; nan if
    not known."""
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
                    return int(line.split()[1]) / 2**20  # kB
        return float("nan")
    except Exception:
        return float("nan")


if __name__ == "__main__":
    os.environ["HIRAKU_TUBE_LEVEL_CHILD"] = "1"  # nothing started from here starts levels again
    print(json.dumps(level(float(sys.argv[1]))))
