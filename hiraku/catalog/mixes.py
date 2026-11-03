"""Model mixes (data/catalog/model_mixes.yaml): the model and effort of each role of a real Claude run."""

from __future__ import annotations

from typing import Any

from hiraku.catalog.loader import CATALOG_DIR, read_data

MIXES_FILE = CATALOG_DIR / "model_mixes.yaml"
DEFAULT_MIX = "default"
ROLES = ("lead", "designer", "packager", "cae")
EFFORTS = ("low", "medium", "high", "xhigh", "max")

Mix = dict[str, dict[str, Any]]


class UnknownMix(ValueError):
    pass


def load_mixes() -> dict[str, Mix]:
    """Every mix by name, each checked: all four roles, a model id and a known effort."""
    mixes = read_data(MIXES_FILE)["mixes"]
    for name, mix in mixes.items():
        if set(mix) != set(ROLES):
            raise ValueError(f"mix {name}: roles {sorted(mix)}, expected {sorted(ROLES)}")
        for role, spec in mix.items():
            if not isinstance(spec.get("model"), str) or not spec["model"] or spec.get("effort") not in EFFORTS:
                raise ValueError(f"mix {name}, role {role}: needs a model id and an effort in {EFFORTS}")
    return mixes


def mix_names() -> list[str]:
    return list(load_mixes())


def get_mix(name: str = DEFAULT_MIX) -> Mix:
    mixes = load_mixes()
    if name not in mixes:
        raise UnknownMix(f"unknown model mix {name!r}; the mixes are: {', '.join(mixes)}")
    return mixes[name]


def role_key(agent_id: str) -> str:
    """The mix role of a contract agent id: cae.<domain> -> cae; lead, designer, packager as they are."""
    if agent_id.startswith("cae."):
        return "cae"
    if agent_id in ROLES:
        return agent_id
    raise ValueError(f"no mix role for agent {agent_id!r}")
