"""The products HIRAKU knows requirement files for: the demo vehicle and the three from N09/N14."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hiraku.catalog import REPO_ROOT, load_requirements, read_data

Record = dict[str, Any]

PRODUCTS_DIR = REPO_ROOT / "data" / "requirements" / "products"
DEMO_FILE = REPO_ROOT / "data" / "requirements" / "offroad-4w.yaml"
ANSWERS_SUFFIX = ".answers.yaml"


def product_files() -> dict[str, Path]:
    """product slug -> requirements file. The demo vehicle first, then data/requirements/products/*.yaml."""
    out: dict[str, Path] = {}
    for path in [DEMO_FILE, *sorted(PRODUCTS_DIR.glob("*.yaml"))]:
        if path.name.endswith(ANSWERS_SUFFIX):
            continue
        req = read_data(path)
        out[req["product"]] = path
    return out


def answers_file(slug: str) -> Path | None:
    """The sample answers to a product's hearing (data/requirements/products/<slug>.answers.yaml), if any."""
    path = PRODUCTS_DIR / f"{slug}{ANSWERS_SUFFIX}"
    return path if path.exists() else None


def load_product(slug: str) -> Record:
    files = product_files()
    if slug not in files:
        raise KeyError(f"product {slug!r} has no requirements file; known: {', '.join(files)}")
    return load_requirements(files[slug])
