"""Entry point for the modular File Inventory Tool."""
from __future__ import annotations

import json
import mimetypes
import sys
import time
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
from app.inventory import InventoryRow, RunSummary, write_inventory, find_latest_run, read_inventory, update_inventory_after_sort
from app.live_tui import LiveTUI
from app.llm_client import LLMClient
from app.loggingx import log_event, log_readable, setup_logging
from app.progress import ProgressTracker
from app.rename import plan_renames
from app.scan import FileMeta, ensure_hash, scan_directory
from app.sortout import delete_duplicates, quarantine_files, sort_files, flatten_directory

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
                sort_and_organize(cfg)
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
            console.print("   - claude-3-5-haiku-20241022 (швидка, дешева)")
            console.print("   - claude-3-5-sonnet-20241022 (найкраща для більшості)")
            console.print("   - claude-3-opus-20240229 (найпотужніша)")
            model = input(f"   Модель (Enter для {cfg.llm_model or 'claude-3-5-haiku-20241022'}): ").strip()
            if model:
                cfg.llm_model = model
            elif not cfg.llm_model:
                cfg.llm_model = "claude-3-5-haiku-20241022"

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
            console.print("   - gpt-5-mini (найновіша економна, серпень 2025)")
            console.print("   - gpt-5 (найпотужніша, серпень 2025)")
            console.print("   - gpt-4.1 (квітень 2025)")
            console.print("   - gpt-4o-mini (попередня економна)")
            model = input(f"   Модель (Enter для {cfg.llm_model or 'gpt-5-mini'}): ").strip()
            if model:
                cfg.llm_model = model
            elif not cfg.llm_model:
                cfg.llm_model = "gpt-5-mini"

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


def sort_and_organize(cfg: Config) -> None:
    """Окреме меню для сортування та організації файлів."""
    console.print("\n[bold cyan]═══ Сортування та організація файлів ═══[/bold cyan]\n")

    # Знайти останній запуск
    latest_run = find_latest_run()
    if not latest_run:
        console.print("[red]Помилка: Немає жодного запуску. Спочатку виконайте аналіз (пункт 1).[/red]")
        return

    console.print(f"[green]✓[/green] Використовується запуск: {latest_run.name}")

    # Прочитати інвентаризацію
    try:
        df = read_inventory(latest_run)
        console.print(f"[green]✓[/green] Завантажено {len(df)} записів")
    except Exception as e:
        console.print(f"[red]Помилка читання інвентаризації: {e}[/red]")
        return

    # Меню опцій
    console.print("\n[bold]Оберіть дію:[/bold]")
    console.print("[1] Сортувати файли за категоріями")
    console.print("[2] Сортувати файли за датами")
    console.print("[3] Сортувати файли за типами")
    console.print("[4] Об'єднати всі файли з підпапок в одну папку")
    console.print("[5] Повернутися до головного меню")

    choice = input("\nВаш вибір: ").strip()

    root = cfg.root_path
    file_updates: Dict[str, str] = {}

    try:
        if choice == "1":
            # Сортування за категоріями
            console.print("\n[cyan]Сортування за категоріями...[/cyan]")
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_category", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(f"[green]✓[/green] Відсортовано {len(mapping)} файлів за категоріями")

        elif choice == "2":
            # Сортування за датами
            console.print("\n[cyan]Сортування за датами...[/cyan]")
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_date", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(f"[green]✓[/green] Відсортовано {len(mapping)} файлів за датами")

        elif choice == "3":
            # Сортування за типами
            console.print("\n[cyan]Сортування за типами файлів...[/cyan]")
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_type", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(f"[green]✓[/green] Відсортовано {len(mapping)} файлів за типами")

        elif choice == "4":
            # Об'єднання файлів
            console.print("\n[cyan]Об'єднання файлів з підпапок...[/cyan]")
            target_name = input("Назва цільової папки (Enter для '_flattened'): ").strip() or "_flattened"
            target_dir = root / target_name

            console.print(f"[yellow]Всі файли з {root} будуть переміщені в {target_dir}[/yellow]")
            confirm = input("Продовжити? [y/N]: ").strip().lower()

            if confirm in {"y", "yes"}:
                mapping = flatten_directory(root, target_dir, recursive=True)
                file_updates = {str(k): str(v) for k, v in mapping.items()}
                console.print(f"[green]✓[/green] Об'єднано {len(mapping)} файлів в {target_dir}")
            else:
                console.print("[yellow]Скасовано[/yellow]")
                return

        elif choice == "5":
            return

        else:
            console.print("[yellow]Невірний вибір[/yellow]")
            return

        # Оновити інвентаризацію
        if file_updates:
            console.print("\n[cyan]Оновлення інвентаризації...[/cyan]")
            strategy = {"1": "by_category", "2": "by_date", "3": "by_type", "4": "flattened"}.get(choice, "manual")
            update_inventory_after_sort(latest_run, file_updates, strategy)
            console.print(f"[green]✓[/green] Інвентаризація оновлена: {latest_run / 'inventory.xlsx'}")

    except Exception as e:
        console.print(f"\n[red]Помилка виконання: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")


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

    # Ініціалізація живого TUI
    tui = LiveTUI(console)

    try:
        root = cfg.root_path

        # Validate root path exists
        if not root.exists():
            console.print(f"[red]Помилка: Шлях {root} не існує[/red]")
            return

        if not root.is_dir():
            console.print(f"[red]Помилка: {root} не є директорією[/red]")
            return

        console.print(f"\n[bold green]Запуск {'швидкого аналізу' if mode == 'dry-run' else 'застосування змін'}...[/bold green]")
        console.print(f"[cyan]Швидке сканування директорії...[/cyan]\n")

        # Створити LLM клієнт якщо увімкнено
        llm_client = None
        if cfg.llm_enabled and cfg.llm_provider != "none":
            api_key = ""
            if cfg.llm_provider == "claude":
                api_key = cfg.llm_api_key_claude
            elif cfg.llm_provider == "chatgpt":
                api_key = cfg.llm_api_key_openai

            if api_key:
                llm_client = LLMClient(
                    provider=cfg.llm_provider,
                    api_key=api_key,
                    model=cfg.llm_model,
                    enabled=True,
                )
                console.print(
                    f"[green]✓[/green] LLM увімкнено: {cfg.llm_provider} ({cfg.llm_model or 'default'})"
                )
            else:
                console.print(
                    f"[yellow]⚠[/yellow] LLM увімкнено але API ключ не налаштовано"
                )

        try:
            metas = scan_directory(root)
        except Exception as exc:
            console.print(f"[red]Помилка сканування: {exc}[/red]")
            return

        if not metas:
            console.print("[yellow]Попередження: Не знайдено файлів для обробки[/yellow]")
            return

        # Запустити LiveTUI після сканування
        console.print(f"[green]✓[/green] Знайдено {len(metas)} файлів")
        time.sleep(1)  # Пауза щоб побачити результат сканування
        tui.start(total_files=len(metas))

        tui.update_stage("Пошук дублікатів")
        exact_groups: List[DuplicateGroup] = detect_exact_duplicates(metas) if cfg.dedup.exact else []

        # Оновити статистику дублікатів
        for group in exact_groups:
            tui.add_duplicate_group(files_count=len(group.files) - 1)  # -1 бо один canonical

        file_contexts: Dict[Path, FileContext] = {}
        tui.update_stage("Вилучення тексту та класифікація")

        for idx, meta in enumerate(metas, 1):
            # Почати обробку файлу
            tui.start_file(meta.path.name)

            try:
                # Хеш файлу
                ensure_hash(meta)

                # Перевірка дублікатів для цього файлу
                is_duplicate = any(meta.path in [f.path for f in group.files] for group in exact_groups)
                if is_duplicate:
                    tui.update_duplicates("Так, знайдено дублікати")
                else:
                    tui.update_duplicates("Немає")

                # Вилучення тексту
                result = extract_text(meta, cfg.ocr_lang)

                # Класифікація (можливо з LLM)
                if llm_client and llm_client.enabled and result.text.strip():
                    tui.update_llm(requests=1)  # Запит до LLM

                    # Зберігаємо попередні значення токенів
                    stats_before = llm_client.get_stats()
                    prev_sent = stats_before["tokens_sent"]
                    prev_recv = stats_before["tokens_received"]

                    classification = classify_text(result.text, llm_client=llm_client)

                    # Обчислюємо різницю токенів
                    stats_after = llm_client.get_stats()
                    new_sent = stats_after["tokens_sent"] - prev_sent
                    new_recv = stats_after["tokens_received"] - prev_recv

                    if new_sent > 0 or new_recv > 0:
                        tui.update_llm(responses=1)
                        tui.update_llm_tokens(sent=new_sent, received=new_recv)
                else:
                    classification = classify_text(result.text)

                category = classification.get("category") or "інше"
                tui.update_classification(category)

                date_doc = classification.get("date_doc") or datetime.fromtimestamp(meta.mtime).date().isoformat()
                summary = classification.get("summary") or summarize_text(result.text, llm_client=llm_client)

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
                tui.update_llm(error=True)
                file_contexts[meta.path] = FileContext(
                    meta=meta,
                    text=ExtractionResult(text="", source="error", quality=0.0),
                    classification={"category": "інше", "date_doc": None},
                    summary="",
                    category="інше",
                    date_doc=datetime.fromtimestamp(meta.mtime).date().isoformat(),
                )

            # Завершити обробку файлу
            tui.finish_file()

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

        tui.update_stage("Перейменування файлів" if mode == "commit" else "Планування перейменування")
        renamed_ok = 0
        renamed_failed = 0
        for idx, plan in enumerate(rename_plans, 1):
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

        tui.update_stage("Створення інвентаризації")
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

            # Зупинити TUI та показати фінальну статистику
            tui.show_final_stats()

            console.print(f"[green]✓[/green] Інвентаризація збережена: {run_dir / 'inventory.xlsx'}")
            console.print(f"[cyan]Оброблено файлів:[/cyan] {summary.files_processed}")
            console.print(f"[cyan]Перейменовано:[/cyan] {summary.renamed_ok}")
            if summary.duplicate_files > 0:
                console.print(f"[yellow]Дублікатів:[/yellow] {summary.duplicate_files}")
        except Exception as exc:
            tui.stop()
            console.print(f"\n[red]Помилка запису інвентаризації: {exc}[/red]")
            return

    except Exception as exc:
        # Глобальна обробка помилок - зупиняємо TUI
        tui.stop()
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

