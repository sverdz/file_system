"""Filesystem scanning utilities."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List

from chardet import detect


@dataclass
class FileMeta:
    path: Path
    size: int
    ctime: float
    mtime: float
    sha256: str | None = None

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()


def to_long_path(path: Path) -> Path:
    if os.name == "nt":
        path_str = str(path)
        if not path_str.startswith("\\\\?\\"):
            return Path("\\\\?\\" + path_str)
    return path


def scan_directory(root: Path) -> List[FileMeta]:
    results: List[FileMeta] = []
    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            results.append(FileMeta(path=path, size=stat.st_size, ctime=stat.st_ctime, mtime=stat.st_mtime))
    return results


def compute_sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def ensure_hash(meta: FileMeta) -> FileMeta:
    if meta.sha256:
        return meta
    try:
        meta.sha256 = compute_sha256(meta.path)
    except OSError:
        meta.sha256 = None
    return meta


def detect_encoding(path: Path, chunk_size: int = 65536) -> str | None:
    try:
        with path.open("rb") as f:
            sample = f.read(chunk_size)
    except OSError:
        return None
    result = detect(sample)
    return result["encoding"] if result else None

