"""Duplicate detection utilities."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from .scan import FileMeta, ensure_hash


@dataclass
class DuplicateGroup:
    group_id: str
    files: List[FileMeta]

    def canonical(self) -> FileMeta:
        return sorted(self.files, key=lambda f: (f.mtime, len(str(f.path))))[0]


@dataclass
class NearDuplicateGroup:
    group_id: str
    items: List[tuple[FileMeta, FileMeta, float]] = field(default_factory=list)


def group_by_size(files: Sequence[FileMeta]) -> Dict[int, List[FileMeta]]:
    buckets: Dict[int, List[FileMeta]] = defaultdict(list)
    for meta in files:
        buckets[meta.size].append(meta)
    return buckets


def detect_exact_duplicates(files: Sequence[FileMeta]) -> List[DuplicateGroup]:
    groups: List[DuplicateGroup] = []
    for metas in group_by_size(files).values():
        if len(metas) < 2:
            continue
        by_hash: Dict[str, List[FileMeta]] = defaultdict(list)
        for meta in metas:
            ensure_hash(meta)
            if meta.sha256:
                by_hash[meta.sha256].append(meta)
        for idx, (hash_value, duplicates) in enumerate(by_hash.items(), start=1):
            if len(duplicates) < 2:
                continue
            groups.append(DuplicateGroup(group_id=f"dup_{hash_value[:8]}_{idx}", files=duplicates))
    return groups


def simhash(text: str, bits: int = 64) -> int:
    from hashlib import md5

    if not text:
        return 0
    vector = [0] * bits
    for token in text.split():
        digest = md5(token.encode("utf-8", errors="ignore")).hexdigest()
        binary = bin(int(digest, 16))[2:].zfill(bits)
        for i, bit in enumerate(binary[:bits]):
            vector[i] += 1 if bit == "1" else -1
    result = 0
    for i, value in enumerate(vector):
        if value >= 0:
            result |= 1 << (bits - i - 1)
    return result


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def detect_near_duplicates(texts: Dict[FileMeta, str], threshold: float) -> NearDuplicateGroup:
    hashes = {meta: simhash(text) for meta, text in texts.items() if text}
    items = list(hashes.items())
    group = NearDuplicateGroup(group_id="near_simhash")
    for i, (meta_a, hash_a) in enumerate(items):
        for meta_b, hash_b in items[i + 1 :]:
            score = 1 - hamming_distance(hash_a, hash_b) / 64
            if score >= threshold:
                group.items.append((meta_a, meta_b, score))
    return group


__all__ = ["DuplicateGroup", "NearDuplicateGroup", "detect_exact_duplicates", "detect_near_duplicates"]

