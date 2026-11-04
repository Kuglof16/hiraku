"""Which modules' designers answer a red of a load case (load_case.modules, docs/contract.md モジュールに分けた設計):
by load case id first, then by test id, then by domain. Set by the builders on every load case of a design that is
split into modules (design.modules); package where only the layout (or the packager's space and interfaces) fixes it.
HIRAKU's reading of which module's variables move the quantity, not a source."""

from __future__ import annotations

BY_LOAD_CASE = {
    "H1-interference": ["package"],  # two modules in one place: the packager gives them their spaces
    "H1-motion": ["suspension", "steering", "package"],
    "H1-steer": ["steering", "suspension", "package"],
    "H1-envelope": ["package"],
    "H1-joints": ["frame", "suspension", "steering", "occupant", "powertrain", "body"],  # the fittings of each module
    "H5-order": ["package"],
    "R8-push": ["occupant", "frame"],
}
BY_TEST = {
    "S1": ["package", "suspension"], "S2": ["package", "suspension"], "S3": ["suspension", "steering", "package"],
    "S4": ["suspension", "package"], "S5": ["suspension", "package"],
    "F1": ["body", "package"], "F2": ["body"], "F3": ["body"],
    "P1": ["powertrain"], "P2": ["powertrain"], "P3": ["powertrain"], "P4": ["powertrain"], "P5": ["powertrain"],
    "C1": ["frame", "body"], "C2": ["body"], "C3": ["occupant", "frame"],
    "R1": ["frame"], "R1s": ["frame"], "R2": ["frame", "suspension"], "R3": ["frame"], "R4": ["occupant", "frame"],
    "R5": ["frame"], "R6": ["powertrain", "frame"], "R7": ["steering", "frame"], "R8": ["frame", "occupant"],
    "N1": ["frame"], "N2": ["suspension"], "N3": ["powertrain"],
    "T1": ["powertrain"], "T2": ["powertrain"], "T3": ["powertrain"],
    "H1": ["package"], "H2": ["package"], "H3": ["occupant", "steering", "package"], "H4": ["occupant"],
    "H5": ["package"], "H6": ["body"], "H7": ["occupant"], "G1": ["package"], "G2": ["package"],
}
BY_DOMAIN = {"handling": ["suspension", "package"], "aero": ["body"], "powertrain": ["powertrain"],
             "crash": ["frame"], "strength": ["frame"], "nvh": ["frame"], "thermal": ["powertrain"],
             "assembly": ["package"]}


def modules_for(load_case: dict) -> list[str]:
    lc_id = load_case["load_case_id"]
    for key, mods in BY_LOAD_CASE.items():
        if lc_id == key or lc_id.startswith(key + "-"):
            return list(mods)
    tid = load_case.get("test_id", "")
    if tid in BY_TEST:
        return list(BY_TEST[tid])
    return list(BY_DOMAIN.get(load_case.get("domain"), ["package"]))


def with_modules(build):
    """A builder that sets load_case.modules when the design is split into modules (and leaves the cases alone
    otherwise, or when the builder set them)."""
    def wrapped(spec, req, design):
        out = build(spec, req, design)
        if design.get("modules"):
            for lc in out if isinstance(out, list) else [out]:
                lc.setdefault("modules", modules_for(lc))
        return out

    wrapped.__name__ = getattr(build, "__name__", "build")
    wrapped.general = getattr(build, "general", build)
    return wrapped
