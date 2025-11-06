"""Entry point for the modular File Inventory Tool."""
from __future__ import annotations

import json
import mimetypes
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from app import deps

deps.ensure_ready()

from colorama import Fore, Style, init as colorama_init
from rich.console import Console
from rich.table import Table

from app.classify import classify_text, summarize_text
from app.config import Config, load_config, save_config, test_llm_connection
from app.dedup import DuplicateGroup, detect_exact_duplicates
from app.extract import ExtractionResult, extract_text
from app.inventory import InventoryRow, RunSummary, write_inventory
from app.loggingx import log_event, log_readable, setup_logging
from app.progress import ProgressTracker
from app.rename import plan_renames
from app.scan import FileMeta, ensure_hash, scan_directory
from app.sortout import delete_duplicates, quarantine_files, sort_files

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
    try:
        cfg = load_config()
    except Exception as exc:
        console.print(f"[red]Помилка завантаження конфігурації: {exc}[/red]")
        console.print("Використовуємо налаштування за замовчуванням.")
        cfg = Config()

    while True:
        try:
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
                console.print("[yellow]Невірний вибір. Спробуйте ще раз.[/yellow]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Переривання... Зберігаю прогрес...[/yellow]")
            break
        except Exception as exc:
            console.print(f"\n[red]═══ Неочікувана помилка ═══[/red]")
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
            console.print("\n[yellow]Натисніть Enter щоб повернутися до меню...[/yellow]")
            input()  # Чекаємо натискання Enter
            # Продовжуємо цикл - повертаємось до меню


def configure(cfg: Config) -> Config:
    console.print("\n[bold cyan]═══ Налаштування File Inventory Tool ═══[/bold cyan]\n")

    # Налаштування папки
    console.print(f"[cyan]1. Папка для аналізу:[/cyan] {cfg.root}")
    new_root = input("   Вкажіть новий шлях (Enter щоб лишити): ").strip()
    if new_root:
        cfg.root = Path(new_root)

    # Налаштування OCR
    console.print(f"\n[cyan]2. Мова OCR:[/cyan] {cfg.ocr_lang}")
    ocr = input("   Вкажіть мову (ukr+eng/eng/off, Enter щоб лишити): ").strip()
    if ocr:
        cfg.ocr_lang = ocr

    # Налаштування LLM
    console.print(f"\n[cyan]3. LLM налаштування:[/cyan]")
    console.print(f"   Поточний провайдер: {cfg.llm_provider}")
    console.print(f"   LLM увімкнено: {cfg.llm_enabled}")

    llm_choice = input("\n   Налаштувати LLM? [y/N]: ").strip().lower()
    if llm_choice in {"y", "yes"}:
        console.print("\n   [bold]Оберіть LLM провайдера:[/bold]")
        console.print("   1 = Claude (Anthropic)")
        console.print("   2 = ChatGPT (OpenAI)")
        console.print("   3 = Вимкнути LLM")

        provider_choice = input("   Ваш вибір [1-3]: ").strip()

        if provider_choice == "1":
            cfg.llm_provider = "claude"
            cfg.llm_enabled = True

            # API ключ Claude
            current_key = cfg.llm_api_key_claude
            if current_key:
                console.print(f"   Поточний ключ: {current_key[:10]}...{current_key[-4:]}")
                change = input("   Змінити API ключ? [y/N]: ").strip().lower()
                if change in {"y", "yes"}:
                    new_key = input("   Введіть API ключ Claude: ").strip()
                    if new_key:
                        cfg.llm_api_key_claude = new_key
            else:
                new_key = input("   Введіть API ключ Claude: ").strip()
                if new_key:
                    cfg.llm_api_key_claude = new_key

            # Модель
            console.print("   Рекомендовані моделі:")
            console.print("   - claude-3-haiku-20240307 (швидка, дешева)")
            console.print("   - claude-3-sonnet-20240229 (баланс)")
            console.print("   - claude-3-opus-20240229 (найкраща)")
            model = input(f"   Модель (Enter для {cfg.llm_model or 'claude-3-haiku-20240307'}): ").strip()
            if model:
                cfg.llm_model = model
            elif not cfg.llm_model:
                cfg.llm_model = "claude-3-haiku-20240307"

            # Перевірка підключення
            if cfg.llm_api_key_claude:
                console.print("\n   [yellow]Перевірка підключення...[/yellow]")
                success, message = test_llm_connection("claude", cfg.llm_api_key_claude, cfg.llm_model)
                console.print(f"   {message}")

        elif provider_choice == "2":
            cfg.llm_provider = "chatgpt"
            cfg.llm_enabled = True

            # API ключ OpenAI
            current_key = cfg.llm_api_key_openai
            if current_key:
                console.print(f"   Поточний ключ: {current_key[:10]}...{current_key[-4:]}")
                change = input("   Змінити API ключ? [y/N]: ").strip().lower()
                if change in {"y", "yes"}:
                    new_key = input("   Введіть API ключ OpenAI: ").strip()
                    if new_key:
                        cfg.llm_api_key_openai = new_key
            else:
                new_key = input("   Введіть API ключ OpenAI: ").strip()
                if new_key:
                    cfg.llm_api_key_openai = new_key

            # Модель
            console.print("   Рекомендовані моделі:")
            console.print("   - gpt-3.5-turbo (швидка, дешева)")
            console.print("   - gpt-4 (краща якість)")
            console.print("   - gpt-4-turbo (найновіша)")
            model = input(f"   Модель (Enter для {cfg.llm_model or 'gpt-3.5-turbo'}): ").strip()
            if model:
                cfg.llm_model = model
            elif not cfg.llm_model:
                cfg.llm_model = "gpt-3.5-turbo"

            # Перевірка підключення
            if cfg.llm_api_key_openai:
                console.print("\n   [yellow]Перевірка підключення...[/yellow]")
                success, message = test_llm_connection("chatgpt", cfg.llm_api_key_openai, cfg.llm_model)
                console.print(f"   {message}")

        elif provider_choice == "3":
            cfg.llm_provider = "none"
            cfg.llm_enabled = False
            console.print("   [yellow]LLM вимкнено[/yellow]")

    save_config(cfg)
    console.print("\n[green]✓ Налаштування збережено.[/green]")
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
    start_time = datetime.now(timezone.utc)
    run_id = start_time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path("runs") / run_id

    try:
        setup_logging(run_dir)
        save_config(cfg, run_dir)
    except Exception as exc:
        console.print(f"[red]Помилка ініціалізації: {exc}[/red]")
        return

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

    # Запустити візуальний прогрес-бар
    console.print(f"\n[bold green]Запуск {'швидкого аналізу' if mode == 'dry-run' else 'застосування змін'}...[/bold green]")
    tracker.start_visual()

    try:
        root = cfg.root_path

        # Validate root path exists
        if not root.exists():
            tracker.stop_visual()
            console.print(f"[red]Помилка: Шлях {root} не існує[/red]")
            return

        if not root.is_dir():
            tracker.stop_visual()
            console.print(f"[red]Помилка: {root} не є директорією[/red]")
            return

        try:
            metas = scan_directory(root)
        except Exception as exc:
            tracker.stop_visual()
            console.print(f"[red]Помилка сканування: {exc}[/red]")
            return

        if not metas:
            tracker.stop_visual()
            console.print("[yellow]Попередження: Не знайдено файлів для обробки[/yellow]")
            return

        # Після сканування встановлюємо total для всіх етапів
        tracker.set_all_totals(len(metas))
        tracker.set_stage_total("scan", len(metas))
        tracker.increment("scan", len(metas))
        tracker.update_description("scan", f"Знайдено {len(metas)} файлів")
        update_progress(run_dir, tracker)

        tracker.update_description("dedup", "Аналіз дублікатів...")
        exact_groups: List[DuplicateGroup] = detect_exact_duplicates(metas) if cfg.dedup.exact else []
        tracker.increment("dedup", len(metas))
        tracker.update_description("dedup", f"Знайдено {len(exact_groups)} груп дублікатів")
        update_progress(run_dir, tracker)

        file_contexts: Dict[Path, FileContext] = {}
        tracker.set_stage_total("extract", len(metas))
        for idx, meta in enumerate(metas, 1):
            tracker.update_description("extract", f"{meta.path.name} ({idx}/{len(metas)})")
            try:
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
            except Exception as exc:
                # Use fallback values if extraction fails
                console.print(f"[yellow]Попередження: Не вдалося обробити {meta.path.name}: {exc}[/yellow]")
                file_contexts[meta.path] = FileContext(
                    meta=meta,
                    text=ExtractionResult(text="", source="error", quality=0.0),
                    classification={"category": "інше", "date_doc": None},
                    summary="",
                    category="інше",
                    date_doc=datetime.fromtimestamp(meta.mtime).date().isoformat(),
                )
            tracker.increment("extract")
        update_progress(run_dir, tracker)

        tracker.set_stage_total("classify", len(metas))
        tracker.increment("classify", len(metas))
        tracker.update_description("classify", "Класифікацію завершено")
        update_progress(run_dir, tracker)

        duplicates_map: Dict[Path, Dict[str, Optional[str]]] = {}
        duplicates_files_map: Dict[str, List[Path]] = {}
        for group in exact_groups:
            canonical = group.canonical()
            duplicates_files_map[group.group_id] = []
            ordered_files = sorted(
                group.files,
                key=lambda m: (m.path != canonical.path, m.path),
            )
            for idx, file_meta in enumerate(ordered_files):
                info: Dict[str, Optional[str]] = {
                    "dup_type": "exact_dup",
                    "dup_group_id": group.group_id,
                    "dup_rank": f"V{idx + 1}",
                    "dup_master_path": str(canonical.path),
                }
                duplicates_map[file_meta.path] = info
                if file_meta.path != canonical.path:
                    duplicates_files_map[group.group_id].append(file_meta.path)

        rename_candidates = [meta for meta in metas if duplicates_map.get(meta.path, {}).get("dup_rank", "V1") == "V1"]
        contexts_for_rename: Dict[Path, Dict[str, str]] = {}
        for meta in rename_candidates:
            ctx = file_contexts[meta.path]
            # Обмежуємо категорію до 15 символів щоб вмістити дату (10 символів) + розділювач
            category_short = ctx.category[:15] if ctx.category else "інше"
            contexts_for_rename[meta.path] = {
                "category": category_short,
                "yyyy": ctx.date_doc[:4],
                "mm": ctx.date_doc[5:7],
                "dd": ctx.date_doc[8:10],
                "ext": meta.path.suffix,
            }
        rename_plans = plan_renames(rename_candidates, cfg.rename_template, contexts_for_rename)

        rows: List[InventoryRow] = []
        row_map: Dict[Path, InventoryRow] = {}
        path_to_row: Dict[Path, InventoryRow] = {}

        tracker.set_stage_total("rename", len(rename_plans))
        renamed_ok = 0
        renamed_failed = 0
        for idx, plan in enumerate(rename_plans, 1):
            tracker.update_description("rename", f"{plan.meta.path.name} → {plan.new_name} ({idx}/{len(rename_plans)})")
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
            now = datetime.now(timezone.utc)
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
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
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

        try:
            write_inventory(rows, summary, run_dir)
            update_progress(run_dir, tracker)
            tracker.stop_visual()
            console.print(f"\n[green]✓[/green] Завершено. Дані у {run_dir}")
            console.print(f"[cyan]Оброблено файлів:[/cyan] {summary.files_processed}")
            console.print(f"[cyan]Перейменовано:[/cyan] {summary.renamed_ok}")
            if summary.duplicate_files > 0:
                console.print(f"[yellow]Дублікатів:[/yellow] {summary.duplicate_files}")
        except Exception as exc:
            tracker.stop_visual()
            console.print(f"\n[red]Помилка запису інвентаризації: {exc}[/red]")
            return

    except Exception as exc:
        # Глобальна обробка помилок - зупиняємо прогрес-бар
        tracker.stop_visual()
        console.print(f"\n[red]═══ Помилка виконання ═══[/red]")
        console.print(f"[red]{type(exc).__name__}: {exc}[/red]")
        import traceback
        console.print(f"\n[dim]Детальна інформація:[/dim]")
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise  # Передаємо помилку вище


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

