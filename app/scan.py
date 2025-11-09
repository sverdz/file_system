"""Filesystem scanning utilities."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List
from fnmatch import fnmatch

try:
    from chardet import detect
except ImportError:  # pragma: no cover - fallback when dependency missing
    detect = None


@dataclass
class FileMeta:
    path: Path
    size: int
    ctime: float
    mtime: float
    sha256: str | None = None
    should_process: bool = True  # Чи потрібно обробляти цей файл (хешувати, перейменовувати)

    @property
    def ext(self) -> str:
        return self.path.suffix.lower()


def to_long_path(path: Path) -> Path:
    if os.name == "nt":
        path_str = str(path)
        if not path_str.startswith("\\\\?\\"):
            return Path("\\\\?\\" + path_str)
    return path


def should_process_file(
    path: Path,
    root: Path,
    exclude_dirs: List[str],
    exclude_files: List[str],
    include_extensions: List[str],
    use_extension_filter: bool = True,
) -> bool:
    """
    Перевірити, чи потрібно обробляти файл (хешувати, перейменовувати).

    Args:
        path: Шлях до файлу
        root: Кореневий шлях сканування
        exclude_dirs: Список виключених папок
        exclude_files: Список виключених патернів файлів
        include_extensions: Список дозволених розширень
        use_extension_filter: Використовувати фільтр розширень

    Returns:
        True якщо файл потрібно обробляти, False якщо ігнорувати
    """
    # Перевірити чи файл в виключених папках
    relative_path = path.relative_to(root) if path.is_relative_to(root) else path
    path_parts = relative_path.parts

    for part in path_parts[:-1]:  # Перевірити всі частини шляху крім імені файлу
        if part in exclude_dirs:
            return False

    # Перевірити чи ім'я файлу відповідає виключеним патернам
    filename = path.name
    for pattern in exclude_files:
        if fnmatch(filename, pattern):
            return False

    # Якщо увімкнено фільтр розширень, перевірити чи розширення в списку дозволених
    if use_extension_filter:
        ext = path.suffix.lower()
        if ext not in include_extensions:
            return False

    return True


def scan_directory(
    root: Path,
    exclude_dirs: List[str] | None = None,
    exclude_files: List[str] | None = None,
    include_extensions: List[str] | None = None,
    use_extension_filter: bool = True,
) -> List[FileMeta]:
    """Сканувати директорію і повернути список файлів. Використовує scan_directory_progressive."""
    return list(
        scan_directory_progressive(root, exclude_dirs, exclude_files, include_extensions, use_extension_filter)
    )


def scan_directory_progressive(
    root: Path,
    exclude_dirs: List[str] | None = None,
    exclude_files: List[str] | None = None,
    include_extensions: List[str] | None = None,
    use_extension_filter: bool = True,
) -> Iterator[FileMeta]:
    """
    Сканувати директорію з прогресивним yield для відображення прогресу.

    Args:
        root: Кореневий шлях для сканування
        exclude_dirs: Список виключених папок
        exclude_files: Список виключених патернів файлів
        include_extensions: Список дозволених розширень
        use_extension_filter: Використовувати фільтр розширень

    Yields:
        FileMeta: Метадані кожного знайденого файлу по одному
    """
    exclude_dirs = exclude_dirs or []
    exclude_files = exclude_files or []
    include_extensions = include_extensions or []

    for dirpath, _, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue

            # Перевірити чи потрібно обробляти файл
            process = should_process_file(
                path,
                root,
                exclude_dirs,
                exclude_files,
                include_extensions,
                use_extension_filter,
            )

            yield FileMeta(
                path=path,
                size=stat.st_size,
                ctime=stat.st_ctime,
                mtime=stat.st_mtime,
                should_process=process,
            )


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
    if detect is None:
        return None
    result = detect(sample)
    return result["encoding"] if result else None

