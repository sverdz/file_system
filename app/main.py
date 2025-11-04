"""Entry point for the modular File Inventory Tool."""
from __future__ import annotations

import json
import mimetypes
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from . import deps

deps.ensure_ready()

from colorama import Fore, Style, init as colorama_init
from rich.console import Console
from rich.table import Table

from .classify import classify_text, summarize_text
from .config import Config, load_config, save_config
from .dedup import DuplicateGroup, detect_exact_duplicates
from .extract import ExtractionResult, extract_text
from .inventory import InventoryRow, RunSummary, write_inventory
from .loggingx import log_event, log_readable, setup_logging
from .progress import ProgressTracker
from .rename import plan_renames
from .scan import FileMeta, ensure_hash, scan_directory
from .sortout import delete_duplicates, quarantine_files, sort_files

colorama_init()
console = Console()


@dataclass
class FileContext:
    meta: FileMeta
    text: ExtractionResult
    classification: Dict[str, Optional[str]]
    summary: str
    category: str
    date_doc: str


def main() -> None:
    cfg = load_config()
    while True:
        console.print("\n[bold cyan]File Inventory Tool[/bold cyan]")
        console.print("[1] Швидкий аналіз (dry-run)")
        console.print("[2] Застосувати перейменування (commit)")
        console.print("[3] Переглянути підсумок останнього запуску")
        console.print("[4] Налаштування")
        console.print("[5] Відновити незавершений запуск")
        console.print("[6] Сортування та подання")
        console.print("[7] Перевірити/переінсталювати залежності")
        console.print("[8] Вихід")
        choice = input("Оберіть опцію: ").strip()
        if choice == "1":
            execute_pipeline(cfg, mode="dry-run")
        elif choice == "2":
            confirm = input("Виконати перейменування? [Y/n] ").strip().lower()
            if confirm in {"", "y", "yes"}:
                delete_choice = input("Видаляти точні дублікати замість карантину? [Y/n] ").strip().lower()
                delete_exact = delete_choice in {"", "y", "yes"}
                sort_choice = input("Сортувати файли по підпапках? [Y/n] ").strip().lower()
                sort_strategy = None
                if sort_choice in {"", "y", "yes"}:
                    console.print("1 = by_category, 2 = by_date, 3 = by_type")
                    mapping = {"1": "by_category", "2": "by_date", "3": "by_type"}
                    selected = input("Оберіть стратегію: ").strip()
                    sort_strategy = mapping.get(selected)
                execute_pipeline(cfg, mode="commit", delete_exact=delete_exact, sort_strategy=sort_strategy)
        elif choice == "3":
            show_last_summary()
        elif choice == "4":
            cfg = configure(cfg)
        elif choice == "5":
            console.print("Відновлення ще не реалізоване у цій версії.")
        elif choice == "6":
            console.print("Перегенерація подань буде виконана при наступному запуску.")
        elif choice == "7":
            deps.ensure_ready()
        elif choice == "8":
            console.print("До побачення!")
            break
        else:
            console.print("Невірний вибір.")


def configure(cfg: Config) -> Config:
    console.print(f"Поточна папка: {cfg.root}")
    new_root = input("Вкажіть шлях до папки (Enter щоб лишити): ").strip()
    if new_root:
        cfg.root = Path(new_root)
    console.print(f"Поточна мова OCR: {cfg.ocr_lang}")
    ocr = input("OCR (ukr+eng/eng/off): ").strip()
    if ocr:
        cfg.ocr_lang = ocr
    console.print(f"LLM увімкнено: {cfg.llm_enabled}")
    llm = input("Увімкнути LLM? [y/N]: ").strip().lower()
    if llm in {"y", "yes"}:
        cfg.llm_enabled = True
    elif llm in {"n", "no"}:
        cfg.llm_enabled = False
    save_config(cfg)
    console.print("Налаштування збережено.")
    return cfg


def show_last_summary() -> None:
    runs_dir = Path("runs")
    if not runs_dir.exists():
        console.print("Немає запусків.")
        return
    run_dirs = sorted([p for p in runs_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        console.print("Немає запусків.")
        return
    latest = run_dirs[-1]
    summary_path = latest / "inventory.xlsx"
    console.print(f"Останній запуск: {latest.name}")
    console.print(f"Файл інвентаризації: {summary_path}")


def execute_pipeline(cfg: Config, mode: str, delete_exact: bool = False, sort_strategy: Optional[str] = None) -> None:
    start_time = datetime.utcnow()
    run_id = start_time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path("runs") / run_id
    setup_logging(run_dir)
    save_config(cfg, run_dir)
    tracker = ProgressTracker(
        {
            "scan": 1.0,
            "dedup": 1.0,
            "extract": 2.0,
            "classify": 1.0,
            "rename": 1.0,
            "inventory": 1.0,
        }
    )

    root = cfg.root_path
    metas = scan_directory(root)
    tracker.set_stage_total("scan", len(metas))
    tracker.increment("scan", len(metas))
    update_progress(run_dir, tracker)

    exact_groups: List[DuplicateGroup] = detect_exact_duplicates(metas) if cfg.dedup.exact else []
    tracker.increment("dedup", len(metas))
    update_progress(run_dir, tracker)

    file_contexts: Dict[Path, FileContext] = {}
    tracker.set_stage_total("extract", len(metas))
    for meta in metas:
        ensure_hash(meta)
        result = extract_text(meta, cfg.ocr_lang)
        classification = classify_text(result.text)
        category = classification.get("category") or "інше"
        date_doc = classification.get("date_doc") or datetime.fromtimestamp(meta.mtime).date().isoformat()
        summary = summarize_text(result.text)
        file_contexts[meta.path] = FileContext(
            meta=meta,
            text=result,
            classification=classification,
            summary=summary,
            category=category,
            date_doc=date_doc,
        )
        tracker.increment("extract")
    update_progress(run_dir, tracker)

    tracker.set_stage_total("classify", len(metas))
    tracker.increment("classify", len(metas))
    update_progress(run_dir, tracker)

    duplicates_map: Dict[Path, Dict[str, Optional[str]]] = {}
    duplicates_files_map: Dict[str, List[Path]] = {}
    for group in exact_groups:
        canonical = group.canonical()
        duplicates_files_map[group.group_id] = []
        for idx, file_meta in enumerate(sorted(group.files, key=lambda m: m.path)):
            info: Dict[str, Optional[str]] = {
                "dup_type": "exact_dup",
                "dup_group_id": group.group_id,
                "dup_rank": f"V{idx + 1}",
                "dup_master_path": str(canonical.path),
            }
            duplicates_map[file_meta.path] = info
            if idx > 0:
                duplicates_files_map[group.group_id].append(file_meta.path)

    rename_candidates = [meta for meta in metas if duplicates_map.get(meta.path, {}).get("dup_rank", "V1") == "V1"]
    contexts_for_rename: Dict[Path, Dict[str, str]] = {}
    for meta in rename_candidates:
        ctx = file_contexts[meta.path]
        contexts_for_rename[meta.path] = {
            "category": ctx.category,
            "yyyy": ctx.date_doc[:4],
            "mm": ctx.date_doc[5:7],
            "dd": ctx.date_doc[8:10],
            "short_title": ctx.summary or meta.path.stem,
            "version": "01",
            "hash8": (meta.sha256 or "0" * 8)[:8],
            "ext": meta.path.suffix,
        }
    rename_plans = plan_renames(rename_candidates, cfg.rename_template, contexts_for_rename)

    rows: List[InventoryRow] = []
    row_map: Dict[Path, InventoryRow] = {}
    path_to_row: Dict[Path, InventoryRow] = {}

    tracker.set_stage_total("rename", len(rename_plans))
    renamed_ok = 0
    renamed_failed = 0
    for plan in rename_plans:
        target = plan.meta.path.with_name(plan.new_name)
        status = "skipped" if mode == "dry-run" else "success"
        error = ""
        if mode == "commit":
            try:
                plan.meta.path.rename(target)
                renamed_ok += 1
            except Exception as exc:
                status = "failed"
                error = str(exc)
                renamed_failed += 1
                target = plan.meta.path
        tracker.increment("rename")
        meta_path = plan.meta.path
        ctx = file_contexts[meta_path]
        dup_info = duplicates_map.get(
            meta_path,
            {"dup_type": "unique", "dup_group_id": None, "dup_rank": "V1", "dup_master_path": None},
        )
        row = InventoryRow(
            root=str(root),
            folder_old=str(meta_path.parent),
            path_old=str(meta_path),
            name_old=meta_path.name,
            name_new=target.name,
            folder_new=str(target.parent),
            path_new=str(target),
            sorted=False,
            sort_strategy=sort_strategy or "",
            sorted_subfolder="",
            path_final=str(target),
            ext=meta_path.suffix.lower(),
            mime=mimetypes.guess_type(meta_path.name)[0] or "application/octet-stream",
            size_mb=plan.meta.size / (1024 * 1024),
            ctime=datetime.fromtimestamp(plan.meta.ctime),
            mtime=datetime.fromtimestamp(plan.meta.mtime),
            date_doc=ctx.date_doc,
            category=ctx.category,
            short_title=ctx.summary,
            version="01",
            hash8=(plan.meta.sha256 or "0" * 8)[:8],
            content_hash_sha256=plan.meta.sha256 or "",
            dup_type=dup_info["dup_type"],
            dup_group_id=dup_info["dup_group_id"],
            dup_rank=dup_info["dup_rank"],
            dup_master_path=dup_info["dup_master_path"],
            near_dup_score=None,
            lifecycle_state="present",
            deleted_ts=None,
            text_source=ctx.text.source,
            ocr_lang=cfg.ocr_lang,
            text_len=len(ctx.text.text),
            extract_quality=ctx.text.quality,
            llm_used=cfg.llm_enabled,
            llm_confidence=None,
            llm_keywords="",
            summary_200=ctx.summary,
            rename_status=status,
            error_message=error,
            collision=plan.collision,
            duration_s=0.0,
            mode=mode,
        )
        rows.append(row)
        row_map[meta_path] = row
        path_to_row[Path(row.path_new)] = row
    update_progress(run_dir, tracker)

    for meta in metas:
        if meta.path in row_map:
            continue
        ctx = file_contexts[meta.path]
        dup_info = duplicates_map.get(
            meta.path,
            {"dup_type": "unique", "dup_group_id": None, "dup_rank": "V1", "dup_master_path": None},
        )
        row = InventoryRow(
            root=str(root),
            folder_old=str(meta.path.parent),
            path_old=str(meta.path),
            name_old=meta.path.name,
            name_new=meta.path.name,
            folder_new=str(meta.path.parent),
            path_new=str(meta.path),
            sorted=False,
            sort_strategy=sort_strategy or "",
            sorted_subfolder="",
            path_final=str(meta.path),
            ext=meta.path.suffix.lower(),
            mime=mimetypes.guess_type(meta.path.name)[0] or "application/octet-stream",
            size_mb=meta.size / (1024 * 1024),
            ctime=datetime.fromtimestamp(meta.ctime),
            mtime=datetime.fromtimestamp(meta.mtime),
            date_doc=ctx.date_doc,
            category=ctx.category,
            short_title=ctx.summary,
            version="01",
            hash8=(meta.sha256 or "0" * 8)[:8],
            content_hash_sha256=meta.sha256 or "",
            dup_type=dup_info["dup_type"],
            dup_group_id=dup_info["dup_group_id"],
            dup_rank=dup_info["dup_rank"],
            dup_master_path=dup_info["dup_master_path"],
            near_dup_score=None,
            lifecycle_state="present",
            deleted_ts=None,
            text_source=ctx.text.source,
            ocr_lang=cfg.ocr_lang,
            text_len=len(ctx.text.text),
            extract_quality=ctx.text.quality,
            llm_used=cfg.llm_enabled,
            llm_confidence=None,
            llm_keywords="",
            summary_200=ctx.summary,
            rename_status="skipped",
            error_message="",
            collision=False,
            duration_s=0.0,
            mode=mode,
        )
        rows.append(row)
        row_map[meta.path] = row
        path_to_row[Path(row.path_new)] = row

    deleted_set: set[Path] = set()
    quarantine_updates: Dict[Path, str] = {}
    duplicates_flat = [path for paths in duplicates_files_map.values() for path in paths]
    if mode == "commit" and duplicates_flat:
        if delete_exact:
            delete_duplicates(duplicates_flat)
            deleted_set.update(duplicates_flat)
        else:
            mapping = quarantine_files(root, duplicates_files_map)
            for original, new_path in mapping.items():
                quarantine_updates[original] = str(new_path)

    sorted_updates: Dict[Path, Path] = {}
    if mode == "commit" and sort_strategy:
        excluded = {Path(str(p)) for p in deleted_set.union(quarantine_updates.keys())}
        sortable_paths = [
            Path(row.path_new)
            for row in rows
            if row.lifecycle_state == "present" and Path(row.path_new) not in excluded
        ]
        sorted_mapping = sort_files(root, sortable_paths, sort_strategy, cfg.sorted_root)
        sorted_updates.update(sorted_mapping)

    if deleted_set or quarantine_updates:
        now = datetime.utcnow()
        for original in deleted_set:
            row = row_map.get(original)
            if not row:
                row = path_to_row.get(original)
            if not row:
                continue
            row.lifecycle_state = "deleted"
            row.deleted_ts = now
            row.path_final = ""
        for original, state in quarantine_updates.items():
            row = row_map.get(original)
            if not row:
                row = path_to_row.get(original)
            if not row:
                continue
            row.lifecycle_state = "quarantined"
            row.path_final = state
            path_to_row[Path(state)] = row

    if sorted_updates:
        for original, new_path in sorted_updates.items():
            row = path_to_row.get(original)
            if not row:
                continue
            row.sorted = True
            row.sort_strategy = sort_strategy or ""
            row.sorted_subfolder = str(Path(new_path).parent)
            row.path_final = str(new_path)
            path_to_row[Path(new_path)] = row

    tracker.increment("inventory")
    duration = (datetime.utcnow() - start_time).total_seconds()
    summary = RunSummary(
        run_id=run_id,
        files_total=len(metas),
        files_processed=len(rows),
        renamed_ok=renamed_ok,
        renamed_failed=renamed_failed,
        duplicate_groups=len(exact_groups),
        duplicate_files=len(duplicates_flat),
        near_duplicate_files=0,
        quarantined_count=len(quarantine_updates),
        deleted_count=len(deleted_set),
        ocr_share=sum(1 for row in rows if row.text_source == "ocr") / len(rows) if rows else 0.0,
        llm_share=sum(1 for row in rows if row.llm_used) / len(rows) if rows else 0.0,
        collisions=sum(1 for row in rows if row.collision),
        duration_total_s=duration,
        cost_total_usd=0.0,
        total_size_mb=sum(row.size_mb for row in rows),
        sorted_enabled=bool(sort_strategy),
        sorting_strategy=sort_strategy or "",
        moved_count=len(quarantine_updates) + len(sorted_updates),
        sorted_root=cfg.sorted_root,
        excel_updated=True,
    )
    write_inventory(rows, summary, run_dir)
    update_progress(run_dir, tracker)
    console.print(f"Завершено. Дані у {run_dir}")


def update_progress(run_dir: Path, tracker: ProgressTracker) -> None:
    snapshot = {
        "percentage": tracker.percentage(),
        "eta_seconds": tracker.eta_seconds(),
        "stages": tracker.snapshot(),
    }
    progress_path = run_dir / "progress.json"
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

