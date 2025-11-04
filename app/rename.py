"""File renaming utilities."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List

from unidecode import unidecode

from .scan import FileMeta

INVALID_CHARS = re.compile(r"[^A-Za-z0-9_.-]+")


def slugify(text: str, limit: int = 50) -> str:
    transliterated = unidecode(text).strip().lower()
    cleaned = INVALID_CHARS.sub("-", transliterated)
    cleaned = re.sub(r"-+", "-", cleaned)
    return cleaned[:limit].strip("-") or "document"


@dataclass
class RenamePlan:
    meta: FileMeta
    new_name: str
    collision: bool = False


def build_filename(template: str, context: Dict[str, str]) -> str:
    name = template.format(**context)
    name = re.sub(r"[\\/:*?\"<>|]", "_", name)
    return name


def plan_renames(files: List[FileMeta], template: str, contexts: Dict[Path, Dict[str, str]]) -> List[RenamePlan]:
    plans: List[RenamePlan] = []
    used: Dict[Path, set[str]] = {}
    for meta in sorted(files, key=lambda m: str(m.path)):
        ctx = contexts.get(meta.path, {})
        ctx.setdefault("short_title", slugify(ctx.get("short_title", meta.path.stem)))
        ctx.setdefault("hash8", (meta.sha256 or "0" * 8)[:8])
        ctx.setdefault("ext", meta.path.suffix)
        name = build_filename(template, ctx)
        parent = meta.path.parent
        used.setdefault(parent, set())
        version = 1
        candidate = name
        while candidate in used[parent]:
            version += 1
            ctx["version"] = f"{version:02d}"
            candidate = build_filename(template, ctx)
        used[parent].add(candidate)
        plans.append(RenamePlan(meta=meta, new_name=candidate, collision=version > 1))
    return plans

