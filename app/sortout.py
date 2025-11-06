"""Physical sorting, quarantine, and safe deletion utilities."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, Iterable, List

from send2trash import send2trash

from .loggingx import log_readable


def quarantine_files(root: Path, duplicates: Dict[str, List[Path]], quarantine_root: str = "_duplicates") -> Dict[Path, Path]:
    mapping: Dict[Path, Path] = {}
    for group_id, files in duplicates.items():
        target_dir = root / quarantine_root / group_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for idx, path in enumerate(files, start=1):
            suffix = path.suffix
            target_name = f"{path.stem}_dupV{idx:02d}{suffix}"
            target = target_dir / target_name
            shutil.move(str(path), str(target))
            mapping[path] = target
            log_readable(f"У карантин: {path.name} → {target}")
    return mapping


def quarantine_near_duplicates(root: Path, duplicates: Dict[str, List[Path]]) -> Dict[Path, Path]:
    mapping: Dict[Path, Path] = {}
    near_root = root / "_near_duplicates"
    for group_id, files in duplicates.items():
        target_dir = near_root / group_id
        target_dir.mkdir(parents=True, exist_ok=True)
        for idx, path in enumerate(files, start=1):
            suffix = path.suffix
            target = target_dir / f"{path.stem}_nDupV{idx:02d}{suffix}"
            shutil.move(str(path), str(target))
            mapping[path] = target
            log_readable(f"У карантин (near): {path.name} → {target}")
    return mapping


def delete_duplicates(paths: Iterable[Path]) -> None:
    for path in paths:
        send2trash(str(path))
        log_readable(f"Видалено дублікат у кошик: {path}")


def sort_files(root: Path, files: Iterable[Path], strategy: str, sorted_root: str = "_sorted") -> Dict[Path, Path]:
    mapping: Dict[Path, Path] = {}
    base = root / sorted_root
    for path in files:
        if strategy == "by_category":
            category = path.stem.split("_")[0]
            target_dir = base / "by_category" / category
        elif strategy == "by_date":
            parts = path.stem.split("_")
            date = parts[1] if len(parts) > 1 else "unknown"
            year = date.split("-")[0] if "-" in date else "unknown"
            target_dir = base / "by_date" / year / date
        else:
            ext = path.suffix.lower().lstrip(".") or "noext"
            target_dir = base / "by_type" / ext
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / path.name
        if target_path.exists():
            target_path = target_dir / f"{path.stem}_sorted{path.suffix}"
        try:
            path.rename(target_path)
        except OSError:
            shutil.copy2(str(path), str(target_path))
            path.unlink()
        mapping[path] = target_path
        log_readable(f"Переміщено: {path} → {target_path}")
    return mapping


def flatten_directory(root: Path, target_dir: Path, recursive: bool = True) -> Dict[Path, Path]:
    """
    Об'єднати всі файли з підпапок в одну папку.

    Args:
        root: Кореневадиректорія для пошуку файлів
        target_dir: Цільова директорія куди переміщувати файли
        recursive: Рекурсивний пошук в підпапках

    Returns:
        Мапа {старий_шлях: новий_шлях}
    """
    mapping: Dict[Path, Path] = {}
    target_dir.mkdir(parents=True, exist_ok=True)

    # Знайти всі файли
    if recursive:
        files = [f for f in root.rglob("*") if f.is_file()]
    else:
        files = [f for f in root.glob("*") if f.is_file()]

    # Переміщення файлів з обробкою колізій
    for file_path in files:
        # Пропускаємо якщо вже в цільовій папці
        if file_path.parent == target_dir:
            continue

        target_path = target_dir / file_path.name

        # Обробка колізій імен
        if target_path.exists():
            # Додаємо суфікс з номером
            counter = 1
            stem = file_path.stem
            suffix = file_path.suffix
            while target_path.exists():
                target_path = target_dir / f"{stem}_{counter:02d}{suffix}"
                counter += 1

        try:
            # Спробувати перемістити
            file_path.rename(target_path)
        except OSError:
            # Якщо не вдалося (наприклад, різні диски), копіюємо і видаляємо
            shutil.copy2(str(file_path), str(target_path))
            file_path.unlink()

        mapping[file_path] = target_path
        log_readable(f"Об'єднано: {file_path} → {target_path}")

    return mapping


__all__ = ["quarantine_files", "quarantine_near_duplicates", "delete_duplicates", "sort_files", "flatten_directory"]

