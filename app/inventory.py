"""Inventory export utilities for Excel workbooks."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Set

import pandas as pd


@dataclass
class InventoryRow:
    root: str
    folder_old: str
    path_old: str
    name_old: str
    name_new: str
    folder_new: str
    path_new: str
    sorted: bool
    sort_strategy: str
    sorted_subfolder: str
    path_final: str
    ext: str
    mime: str
    size_mb: float
    ctime: datetime
    mtime: datetime
    date_doc: str | None
    category: str
    short_title: str
    version: str
    hash8: str
    content_hash_sha256: str
    dup_type: str
    dup_group_id: str | None
    dup_rank: str
    dup_master_path: str | None
    near_dup_score: float | None
    lifecycle_state: str
    deleted_ts: datetime | None
    text_source: str
    ocr_lang: str
    text_len: int
    extract_quality: float
    llm_used: bool
    llm_confidence: float | None
    llm_keywords: str
    summary_200: str
    rename_status: str
    error_message: str
    collision: bool
    duration_s: float
    mode: str


@dataclass
class RunSummary:
    run_id: str
    files_total: int
    files_processed: int
    renamed_ok: int
    renamed_failed: int
    duplicate_groups: int
    duplicate_files: int
    near_duplicate_files: int
    quarantined_count: int
    deleted_count: int
    ocr_share: float
    llm_share: float
    collisions: int
    duration_total_s: float
    cost_total_usd: float
    total_size_mb: float
    sorted_enabled: bool
    sorting_strategy: str
    moved_count: int
    sorted_root: str
    excel_updated: bool


# Заборонені символи в назвах аркушів Excel: : \ / ? * [ ]
INVALID_SHEET_CHARS = re.compile(r'[\[\]:*?/\\]')
MAX_SHEET_NAME_LENGTH = 31


def normalize_sheet_name(name: str, used_names: Set[str] | None = None, fallback: str = "Sheet") -> str:
    """
    Нормалізація назви аркуша Excel для уникнення помилок.

    Excel обмеження:
    - Максимум 31 символ
    - Заборонені символи: : \ / ? * [ ]
    - Не може бути порожнім
    - Не може починатися/закінчуватися пробілами

    Args:
        name: Вхідна назва аркуша
        used_names: Множина вже використаних назв (для уникнення конфліктів)
        fallback: Назва за замовчуванням якщо після очищення порожня

    Returns:
        Нормалізована назва аркуша
    """
    if not name or not name.strip():
        name = fallback

    # Видалити керуючі та невидимі символи
    cleaned = ''.join(char for char in name if char.isprintable() or char in ' \t')

    # Замінити заборонені символи на підкреслення
    cleaned = INVALID_SHEET_CHARS.sub('_', cleaned)

    # Видалити повторювані підкреслення
    cleaned = re.sub(r'_+', '_', cleaned)

    # Прибрати пробіли на початку та в кінці
    cleaned = cleaned.strip()

    # Обрізати до максимальної довжини
    if len(cleaned) > MAX_SHEET_NAME_LENGTH:
        cleaned = cleaned[:MAX_SHEET_NAME_LENGTH]

    # Якщо після очищення порожня - використати fallback
    if not cleaned:
        cleaned = fallback

    # Обробка конфліктів назв
    if used_names is not None:
        original = cleaned
        counter = 2
        while cleaned in used_names:
            # Додати суфікс _2, _3, тощо
            suffix = f"_{counter}"
            max_base_len = MAX_SHEET_NAME_LENGTH - len(suffix)
            cleaned = original[:max_base_len] + suffix
            counter += 1

            # Безпека: максимум 1000 спроб
            if counter > 1000:
                cleaned = f"{fallback}_{counter}"
                break

        used_names.add(cleaned)

    return cleaned


def _dataframe(rows: Iterable[InventoryRow]) -> pd.DataFrame:
    df = pd.DataFrame([asdict(row) for row in rows])
    if df.empty:
        df = pd.DataFrame(columns=InventoryRow.__annotations__.keys())
    return df


def write_inventory(rows: Iterable[InventoryRow], summary: RunSummary, run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    df = _dataframe(rows)
    views = {
        "by_category": df.sort_values(by=["category", "path_new"], ascending=[True, True]).copy(),
        "by_date": df.assign(
            year=df["date_doc"].fillna("невідомо").str[:4],
            year_month=df["date_doc"].fillna("невідомо").str[:7],
        ).sort_values(by=["date_doc", "path_new"]),
        "by_type": df.sort_values(by=["ext", "path_new"]),
    }
    summary_df = pd.DataFrame([asdict(summary)])
    xlsx_path = run_dir / "inventory.xlsx"

    # Трекінг використаних назв аркушів для уникнення конфліктів
    used_names: Set[str] = set()

    # Створюємо тільки .xlsx файл (сучасний формат) з openpyxl
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        # Основний аркуш
        main_sheet = normalize_sheet_name("inventory", used_names, fallback="Inventory")
        df.to_excel(writer, sheet_name=main_sheet, index=False)

        # Аркуші з видами
        for sheet_key, view_df in views.items():
            sheet_name = normalize_sheet_name(sheet_key, used_names, fallback=f"View_{sheet_key}")
            view_df.to_excel(writer, sheet_name=sheet_name, index=False)

        # Аркуш з підсумком
        summary_sheet = normalize_sheet_name("run_summary", used_names, fallback="Summary")
        summary_df.to_excel(writer, sheet_name=summary_sheet, index=False)


def read_inventory(run_dir: Path) -> pd.DataFrame:
    """Прочитати інвентаризацію з Excel файлу."""
    xlsx_path = run_dir / "inventory.xlsx"
    if not xlsx_path.exists():
        raise FileNotFoundError(f"Інвентаризація не знайдена: {xlsx_path}")

    df = pd.read_excel(xlsx_path, sheet_name="inventory")
    return df


def find_latest_run() -> Path | None:
    """Знайти останній запуск в папці runs."""
    runs_dir = Path("runs")
    if not runs_dir.exists():
        return None

    run_dirs = sorted([p for p in runs_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        return None

    return run_dirs[-1]


def update_inventory_after_sort(
    run_dir: Path,
    file_updates: Dict[str, str],  # {old_path: new_path}
    sort_strategy: str,
) -> None:
    """
    Оновити інвентаризацію після сортування файлів.

    Args:
        run_dir: Директорія запуску
        file_updates: Мапа старих шляхів на нові
        sort_strategy: Стратегія сортування
    """
    df = read_inventory(run_dir)

    # Оновити записи про файли
    for old_path, new_path in file_updates.items():
        mask = df["path_final"] == old_path
        if mask.any():
            df.loc[mask, "path_final"] = new_path
            df.loc[mask, "sorted"] = True
            df.loc[mask, "sort_strategy"] = sort_strategy
            df.loc[mask, "sorted_subfolder"] = str(Path(new_path).parent)

    # Перезаписати Excel з оновленими даними
    rows = []
    for _, row_dict in df.iterrows():
        rows.append(InventoryRow(**row_dict))

    # Читаємо summary
    summary_df = pd.read_excel(run_dir / "inventory.xlsx", sheet_name="run_summary")
    summary_dict = summary_df.iloc[0].to_dict()
    summary = RunSummary(**summary_dict)

    # Оновлюємо статистику
    summary.sorted_enabled = True
    summary.sorting_strategy = sort_strategy
    summary.moved_count = len(file_updates)

    # Перезаписуємо
    write_inventory(rows, summary, run_dir)


__all__ = ["InventoryRow", "RunSummary", "write_inventory", "read_inventory", "find_latest_run", "update_inventory_after_sort"]

