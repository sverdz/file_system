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
from app.session import SessionManager
from app.sortout import delete_duplicates, quarantine_files, sort_files, flatten_directory
from app.theme import THEME, markup, format_number, format_status, format_error, header_line

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


def show_rename_preview(rename_plans: list, max_preview: int = 50) -> bool:
    """
    Показати попередній перегляд перейменування файлів у вигляді таблиці.

    Args:
        rename_plans: Список RenamePlan з планами перейменування
        max_preview: Максимальна кількість файлів для відображення (за замовчуванням 50)

    Returns:
        True якщо користувач підтвердив, False якщо скасував
    """
    console.print(header_line("ПОПЕРЕДНІЙ ПЕРЕГЛЯД ПЕРЕЙМЕНУВАННЯ ФАЙЛІВ"))

    # Створити таблицю з новою кольоровою схемою
    table = Table(show_header=True, header_style=THEME.header, show_lines=True, border_style=THEME.border)
    table.add_column("№", style=THEME.dim_text, width=5)
    table.add_column("Старе ім'я", style=THEME.file_name, max_width=40)
    table.add_column("→", justify="center", width=3, style=THEME.info)
    table.add_column("Нове ім'я", style=THEME.success, max_width=40)
    table.add_column("Довжина", justify="right", width=8)
    table.add_column("Колізія", justify="center", width=8)

    total_files = len(rename_plans)
    preview_count = min(max_preview, total_files)

    # Додати рядки до таблиці
    for idx, plan in enumerate(rename_plans[:preview_count], 1):
        old_name = plan.meta.path.name
        new_name = plan.new_name
        # Довжина без розширення
        name_without_ext = Path(new_name).stem
        length = len(name_without_ext)
        collision_mark = markup(THEME.warning, "✓") if plan.collision else ""

        # Підсвітка якщо довжина більше 20
        if length > 20:
            length_str = markup(THEME.error, str(length))
        else:
            length_str = markup(THEME.success, str(length))

        table.add_row(
            str(idx),
            old_name,
            "→",
            new_name,
            length_str,
            collision_mark
        )

    console.print(table)

    # Якщо файлів більше ніж max_preview
    if total_files > preview_count:
        console.print(markup(THEME.dim_text, f"\n... і ще {total_files - preview_count} файлів"))

    # Статистика з новою кольоровою схемою
    console.print(f"\n{markup(THEME.title, 'Підсумок:')}")
    console.print(f"  • Всього файлів для перейменування: {format_number(total_files)}")

    collisions = sum(1 for p in rename_plans if p.collision)
    if collisions > 0:
        console.print(f"  • Файлів з колізіями (додано суфікс): {format_number(collisions, THEME.warning)}")

    # Перевірка довжин
    too_long = sum(1 for p in rename_plans if len(Path(p.new_name).stem) > 20)
    if too_long > 0:
        console.print(format_error(f"УВАГА: {too_long} файлів перевищують ліміт 20 символів!"))

    # Запит підтвердження
    console.print(f"\n{markup(THEME.warning, 'Застосувати перейменування?')}")
    prompt_text = markup(THEME.secondary_text, "Введіть 'y' або 'yes' для підтвердження: ")
    response = input(prompt_text).strip().lower()

    return response in ('y', 'yes', 'так', 'т')


def main() -> None:
    try:
        cfg = load_config()
    except Exception as exc:
        console.print(format_error(f"Помилка завантаження конфігурації: {exc}"))
        console.print(markup(THEME.dim_text, "Використовуємо налаштування за замовчуванням."))
        cfg = Config()

    while True:
        try:
            console.print(f"\n{markup(THEME.title, 'File Inventory Tool')}")
            console.print(markup(THEME.primary_text, "[1] Швидкий аналіз (dry-run)"))
            console.print(markup(THEME.primary_text, "[2] Застосувати перейменування (commit)"))
            console.print(markup(THEME.primary_text, "[3] Переглянути підсумок останнього запуску"))
            console.print(markup(THEME.primary_text, "[4] Налаштування"))
            console.print(markup(THEME.primary_text, "[5] Відновити незавершений запуск"))
            console.print(markup(THEME.primary_text, "[6] Сортування та подання"))
            console.print(markup(THEME.primary_text, "[7] Перевірити/переінсталювати залежності"))
            console.print(markup(THEME.primary_text, "[8] Вихід"))
            choice = input("Оберіть опцію: ").strip()
            if choice == "1":
                execute_pipeline(cfg, mode="dry-run", operation_type="SCAN")
            elif choice == "2":
                confirm = input("Виконати перейменування? [Y/n] ").strip().lower()
                if confirm in {"", "y", "yes"}:
                    delete_choice = input("Видаляти точні дублікати замість карантину? [Y/n] ").strip().lower()
                    delete_exact = delete_choice in {"", "y", "yes"}
                    sort_choice = input("Сортувати файли по підпапках? [Y/n] ").strip().lower()
                    sort_strategy = None
                    operation_type = "RENAME"
                    if sort_choice in {"", "y", "yes"}:
                        console.print(markup(THEME.info, "1 = by_category, 2 = by_date, 3 = by_type"))
                        mapping = {"1": "by_category", "2": "by_date", "3": "by_type"}
                        selected = input("Оберіть стратегію: ").strip()
                        sort_strategy = mapping.get(selected)
                        if sort_strategy:
                            operation_type = "RENAME_SORT"
                    execute_pipeline(cfg, mode="commit", operation_type=operation_type, delete_exact=delete_exact, sort_strategy=sort_strategy)
            elif choice == "3":
                show_last_summary()
            elif choice == "4":
                cfg = configure(cfg)
            elif choice == "5":
                console.print(markup(THEME.warning, "Відновлення ще не реалізоване у цій версії."))
            elif choice == "6":
                sort_and_organize(cfg)
            elif choice == "7":
                deps.ensure_ready()
            elif choice == "8":
                console.print(markup(THEME.success, "До побачення!"))
                break
            else:
                console.print(markup(THEME.warning, "Невірний вибір. Спробуйте ще раз."))
        except KeyboardInterrupt:
            console.print(markup(THEME.warning, "\nПереривання... Зберігаю прогрес..."))
            break
        except Exception as exc:
            console.print(f"\n{markup(THEME.error, '═══ Неочікувана помилка ═══')}")
            console.print(format_error(f"{type(exc).__name__}: {exc}"))
            console.print(markup(THEME.warning, "\nНатисніть Enter щоб повернутися до меню..."))
            input()  # Чекаємо натискання Enter
            # Продовжуємо цикл - повертаємось до меню


def configure(cfg: Config) -> Config:
    console.print(header_line("Налаштування File Inventory Tool"))

    # Налаштування папки
    console.print(f"{markup(THEME.header, '1. Папка для аналізу:')} {cfg.root}")
    new_root = input("   Вкажіть новий шлях (Enter щоб лишити): ").strip()
    if new_root:
        cfg.root = Path(new_root)

    # Налаштування OCR
    console.print(f"\n{markup(THEME.header, '2. Мова OCR:')} {cfg.ocr_lang}")
    ocr = input("   Вкажіть мову (ukr+eng/eng/off, Enter щоб лишити): ").strip()
    if ocr:
        cfg.ocr_lang = ocr

    # Налаштування LLM
    console.print(f"\n{markup(THEME.header, '3. LLM налаштування:')}")
    console.print(f"   {markup(THEME.dim_text, 'Поточний провайдер:')} {cfg.llm_provider}")
    console.print(f"   {markup(THEME.dim_text, 'LLM увімкнено:')} {cfg.llm_enabled}")

    llm_choice = input("\n   Налаштувати LLM? [y/N]: ").strip().lower()
    if llm_choice in {"y", "yes"}:
        console.print(f"\n   {markup(THEME.title, 'Оберіть LLM провайдера:')}")
        console.print(markup(THEME.primary_text, "   1 = Claude (Anthropic)"))
        console.print(markup(THEME.primary_text, "   2 = ChatGPT (OpenAI)"))
        console.print(markup(THEME.primary_text, "   3 = Вимкнути LLM"))

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
                console.print(markup(THEME.warning, "\n   Перевірка підключення..."))
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
                console.print(markup(THEME.warning, "\n   Перевірка підключення..."))
                success, message = test_llm_connection("chatgpt", cfg.llm_api_key_openai, cfg.llm_model)
                console.print(f"   {message}")

        elif provider_choice == "3":
            cfg.llm_provider = "none"
            cfg.llm_enabled = False
            console.print(markup(THEME.warning, "   LLM вимкнено"))

    save_config(cfg)
    console.print(format_status("\nНалаштування збережено.", is_error=False))
    return cfg


def show_last_summary() -> None:
    runs_dir = Path("runs")
    if not runs_dir.exists():
        console.print(markup(THEME.warning, "Немає запусків."))
        return
    run_dirs = sorted([p for p in runs_dir.iterdir() if p.is_dir()])
    if not run_dirs:
        console.print(markup(THEME.warning, "Немає запусків."))
        return
    latest = run_dirs[-1]
    summary_path = latest / "inventory.xlsx"
    console.print(f"{markup(THEME.header, 'Останній запуск:')} {latest.name}")
    console.print(f"{markup(THEME.header, 'Файл інвентаризації:')} {summary_path}")


def sort_and_organize(cfg: Config) -> None:
    """Окреме меню для сортування та організації файлів."""
    console.print(header_line("Сортування та організація файлів"))

    # Знайти останній запуск
    latest_run = find_latest_run()
    if not latest_run:
        console.print(format_error("Помилка: Немає жодного запуску. Спочатку виконайте аналіз (пункт 1)."))
        return

    console.print(format_status(f"Використовується запуск: {latest_run.name}", is_error=False))

    # Прочитати інвентаризацію
    try:
        df = read_inventory(latest_run)
        console.print(format_status(f"Завантажено {len(df)} записів", is_error=False))
    except Exception as e:
        console.print(format_error(f"Помилка читання інвентаризації: {e}"))
        return

    # Меню опцій
    console.print(f"\n{markup(THEME.title, 'Оберіть дію:')}")
    console.print(markup(THEME.primary_text, "[1] Сортувати файли за категоріями"))
    console.print(markup(THEME.primary_text, "[2] Сортувати файли за датами"))
    console.print(markup(THEME.primary_text, "[3] Сортувати файли за типами"))
    console.print(markup(THEME.primary_text, "[4] Об'єднати всі файли з підпапок в одну папку"))
    console.print(markup(THEME.primary_text, "[5] Повернутися до головного меню"))

    choice = input("\nВаш вибір: ").strip()

    root = cfg.root_path
    file_updates: Dict[str, str] = {}

    try:
        if choice == "1":
            # Сортування за категоріями
            console.print(markup(THEME.processing, "\nСортування за категоріями..."))
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_category", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(format_status(f"Відсортовано {len(mapping)} файлів за категоріями", is_error=False))

        elif choice == "2":
            # Сортування за датами
            console.print(markup(THEME.processing, "\nСортування за датами..."))
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_date", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(format_status(f"Відсортовано {len(mapping)} файлів за датами", is_error=False))

        elif choice == "3":
            # Сортування за типами
            console.print(markup(THEME.processing, "\nСортування за типами файлів..."))
            files_to_sort = [Path(row["path_final"]) for _, row in df.iterrows() if Path(row["path_final"]).exists()]
            mapping = sort_files(root, files_to_sort, "by_type", cfg.sorted_root)
            file_updates = {str(k): str(v) for k, v in mapping.items()}
            console.print(format_status(f"Відсортовано {len(mapping)} файлів за типами", is_error=False))

        elif choice == "4":
            # Об'єднання файлів
            console.print(markup(THEME.processing, "\nОб'єднання файлів з підпапок..."))
            target_name = input("Назва цільової папки (Enter для '_flattened'): ").strip() or "_flattened"
            target_dir = root / target_name

            console.print(markup(THEME.warning, f"Всі файли з {root} будуть переміщені в {target_dir}"))
            confirm = input("Продовжити? [y/N]: ").strip().lower()

            if confirm in {"y", "yes"}:
                mapping = flatten_directory(root, target_dir, recursive=True)
                file_updates = {str(k): str(v) for k, v in mapping.items()}
                console.print(format_status(f"Об'єднано {len(mapping)} файлів в {target_dir}", is_error=False))
            else:
                console.print(markup(THEME.warning, "Скасовано"))
                return

        elif choice == "5":
            return

        else:
            console.print(markup(THEME.warning, "Невірний вибір"))
            return

        # Створити нову сесію для сортування
        if file_updates:
            console.print(markup(THEME.processing, "\nОновлення інвентаризації..."))
            strategy = {"1": "by_category", "2": "by_date", "3": "by_type", "4": "flattened"}.get(choice, "manual")
            update_inventory_after_sort(latest_run, file_updates, strategy)
            console.print(format_status(f"Інвентаризація оновлена: {latest_run / 'inventory.xlsx'}", is_error=False))

    except Exception as e:
        console.print(format_error(f"\nПомилка виконання: {e}"))
        import traceback
        console.print(markup(THEME.dim_text, traceback.format_exc()))


def execute_pipeline(
    cfg: Config,
    mode: str,
    operation_type: str,
    delete_exact: bool = False,
    sort_strategy: Optional[str] = None
) -> None:
    """
    Виконати pipeline обробки файлів.

    Args:
        cfg: Конфігурація
        mode: Режим роботи ('dry-run' або 'commit')
        operation_type: Тип операції (SCAN, RENAME, SORT, тощо)
        delete_exact: Видаляти дублікати замість карантину
        sort_strategy: Стратегія сортування (опціонально)
    """
    start_time = datetime.now(timezone.utc)

    # Створити нову сесію
    session_manager = SessionManager()
    session = session_manager.create_session(operation_type)

    console.print(f"[cyan]Створено сесію:[/cyan] {session.session_id}")
    console.print(f"[cyan]Директорія:[/cyan] {session.session_dir}\n")

    try:
        setup_logging(session.session_dir)
        save_config(cfg, session.session_dir)
    except Exception as exc:
        console.print(format_error(f"Помилка ініціалізації: {exc}"))
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
    mode_text = "швидкого аналізу" if mode == "dry-run" else "застосування змін"
    console.print(f"\n{markup(THEME.success, f'Запуск {mode_text}...')}")
    tracker.start_visual()

    try:
        root = cfg.root_path

        # Validate root path exists
        if not root.exists():
            tracker.stop_visual()
            console.print(format_error(f"Помилка: Шлях {root} не існує"))
            return

        if not root.is_dir():
            tracker.stop_visual()
            console.print(format_error(f"Помилка: {root} не є директорією"))
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
                    session_dir=session.session_dir,
                )
                console.print(
                    format_status(f"LLM увімкнено: {cfg.llm_provider} ({cfg.llm_model or 'default'})", is_error=False)
                )
            else:
                console.print(
                    markup(THEME.warning, "⚠ LLM увімкнено але API ключ не налаштовано")
                )

        try:
            metas = scan_directory(root)
        except Exception as exc:
            tracker.stop_visual()
            console.print(format_error(f"Помилка сканування: {exc}"))
            return

        if not metas:
            tracker.stop_visual()
            console.print(markup(THEME.warning, "Попередження: Не знайдено файлів для обробки"))
            return

        # Запустити LiveTUI після сканування
        console.print(f"[green]✓[/green] Знайдено {len(metas)} файлів")
        time.sleep(1)  # Пауза щоб побачити результат сканування
        tui.start(total_files=len(metas))

        tui.update_stage("Пошук дублікатів")
        exact_groups: List[DuplicateGroup] = detect_exact_duplicates(metas) if cfg.dedup.exact else []

        # Підрахунок файлів-дублікатів
        duplicate_files_count = sum(len(group.files) - 1 for group in exact_groups)

        # Оновити метрики дублікатів
        tracker.update_metrics(
            duplicate_groups=len(exact_groups),
            duplicate_files=duplicate_files_count
        )

        tracker.increment("dedup", len(metas))
        tracker.update_description("dedup", f"Знайдено {len(exact_groups)} груп дублікатів")
        update_progress(run_dir, tracker)

        file_contexts: Dict[Path, FileContext] = {}
        tracker.set_stage_total("extract", len(metas))
        error_count = 0
        for idx, meta in enumerate(metas, 1):
            # Встановити поточний файл
            tracker.set_current_file(
                name=meta.path.name,
                path=str(meta.path),
                stage="витяг тексту",
                status="processing",
            )

            tracker.update_description("extract", f"{meta.path.name} ({idx}/{len(metas)})")
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

                    classification = classify_text(
                        result.text,
                        llm_client=llm_client,
                        filename=meta.path.name
                    )

                    # Обчислюємо різницю токенів
                    stats_after = llm_client.get_stats()
                    new_sent = stats_after["tokens_sent"] - prev_sent
                    new_recv = stats_after["tokens_received"] - prev_recv

                    if new_sent > 0 or new_recv > 0:
                        tui.update_llm(responses=1)
                        tui.update_llm_tokens(sent=new_sent, received=new_recv)
                else:
                    classification = classify_text(result.text, filename=meta.path.name)

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

                # Успішно оброблено
                tracker.set_current_file(
                    name=meta.path.name,
                    category=category,
                    stage="витяг тексту",
                    status="success",
                )
            except Exception as exc:
                # Use fallback values if extraction fails
                error_count += 1
                error_msg = f"Не вдалося обробити: {exc}"
                console.print(markup(THEME.warning, f"⚠ {error_msg}"))

                tracker.set_current_file(
                    name=meta.path.name,
                    stage="витяг тексту",
                    status="error",
                    error_msg=str(exc),
                )

                file_contexts[meta.path] = FileContext(
                    meta=meta,
                    text=ExtractionResult(text="", source="error", quality=0.0),
                    classification={"category": "інше", "date_doc": None},
                    summary="",
                    category="інше",
                    date_doc=datetime.fromtimestamp(meta.mtime).date().isoformat(),
                )

                tracker.update_metrics(error_count=error_count)

            tracker.increment("extract")

            # Показати статус кожні 10 файлів
            if idx % 10 == 0:
                tracker.show_status()

        update_progress(run_dir, tracker)

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
            # Передаємо повну категорію та дату для нового формату
            contexts_for_rename[meta.path] = {
                "category": ctx.category,
                "date_doc": ctx.date_doc,
                "yyyy": ctx.date_doc[:4] if len(ctx.date_doc) >= 4 else "2024",
                "mm": ctx.date_doc[5:7] if len(ctx.date_doc) >= 7 else "01",
                "dd": ctx.date_doc[8:10] if len(ctx.date_doc) >= 10 else "01",
                "ext": meta.path.suffix,
            }

        # Використання нових параметрів для короткого формату
        rename_plans = plan_renames(
            rename_candidates,
            cfg.rename_template,
            contexts_for_rename,
            use_short_format=cfg.use_short_format,
            use_short_date=cfg.use_short_date
        )

        # Попередній перегляд перейменування (тільки для commit режиму)
        if mode == "commit" and rename_plans:
            tracker.stop_visual()
            console.print(f"\n{markup(THEME.success, 'Планування перейменування завершено!')}")

            # Показати попередній перегляд і запитати підтвердження
            confirmed = show_rename_preview(rename_plans)

            if not confirmed:
                console.print(markup(THEME.warning, "\n✗ Перейменування скасовано користувачем"))
                console.print(markup(THEME.dim_text, "Інвентаризація буде збережена без застосування перейменування\n"))
                # Встановити режим на dry-run щоб не застосовувати зміни
                mode = "dry-run"

            # Відновити візуальний прогрес
            continue_text = "застосування змін" if mode == "commit" else "без змін"
            console.print(f"\n{markup(THEME.success, f'Продовження {continue_text}...')}")
            tracker.start_visual()

        rows: List[InventoryRow] = []
        row_map: Dict[Path, InventoryRow] = {}
        path_to_row: Dict[Path, InventoryRow] = {}

        tui.update_stage("Перейменування файлів" if mode == "commit" else "Планування перейменування")
        renamed_ok = 0
        renamed_failed = 0
        for idx, plan in enumerate(rename_plans, 1):
            # Отримати контекст файлу для категорії
            ctx = file_contexts.get(plan.meta.path)
            category = ctx.category if ctx else "інше"

            # Встановити поточний файл
            tracker.set_current_file(
                name=plan.meta.path.name,
                path=str(plan.meta.path),
                category=category,
                stage="перейменування",
                status="processing",
            )

            tracker.update_description("rename", f"{plan.meta.path.name} → {plan.new_name} ({idx}/{len(rename_plans)})")
            target = plan.meta.path.with_name(plan.new_name)
            status = "skipped" if mode == "dry-run" else "success"
            error = ""
            if mode == "commit":
                try:
                    plan.meta.path.rename(target)
                    renamed_ok += 1
                    # Успішно перейменовано
                    tracker.set_current_file(
                        name=plan.new_name,
                        category=category,
                        stage="перейменування",
                        status="success",
                    )
                except Exception as exc:
                    status = "failed"
                    error = str(exc)
                    renamed_failed += 1
                    target = plan.meta.path
                    # Помилка перейменування
                    tracker.set_current_file(
                        name=plan.meta.path.name,
                        category=category,
                        stage="перейменування",
                        status="error",
                        error_msg=str(exc),
                    )
            tracker.increment("rename")

            # Оновити метрики успішності
            tracker.update_metrics(
                success_count=renamed_ok,
                error_count=renamed_failed
            )

            # Показати статус кожні 10 файлів
            if idx % 10 == 0:
                tracker.show_status()
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
            run_id=session.session_id,
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
            console.print(format_status(f"\nЗавершено. Дані у {run_dir}", is_error=False))
            console.print(f"{markup(THEME.header, 'Оброблено файлів:')} {format_number(summary.files_processed)}")
            console.print(f"{markup(THEME.header, 'Перейменовано:')} {format_number(summary.renamed_ok)}")
            if summary.duplicate_files > 0:
                console.print(f"{markup(THEME.duplicate, 'Дублікатів:')} {format_number(summary.duplicate_files, THEME.duplicate_count)}")

            # Статистика LLM
            if llm_client:
                stats = llm_client.get_stats()
                if stats["requests"] > 0:
                    # Оновити метрики LLM
                    tracker.update_metrics(
                        llm_requests=stats["requests"],
                        llm_responses=stats["requests"]  # Кількість відповідей = кількості запитів
                    )
                    console.print(
                        f"{markup(THEME.llm_request, '🤖 LLM запитів:')} {format_number(stats['requests'])}, "
                        f"{markup(THEME.llm_request, 'токенів:')} {format_number(stats['tokens'])}"
                    )
        except Exception as exc:
            tracker.stop_visual()
            console.print(format_error(f"\nПомилка запису інвентаризації: {exc}"))
            return

    except Exception as exc:
        # Глобальна обробка помилок - зупиняємо прогрес-бар
        tracker.stop_visual()
        console.print(f"\n{markup(THEME.error, '═══ Помилка виконання ═══')}")
        console.print(format_error(f"{type(exc).__name__}: {exc}"))
        import traceback
        console.print(f"\n{markup(THEME.dim_text, 'Детальна інформація:')}")
        console.print(markup(THEME.dim_text, traceback.format_exc()))
        raise  # Передаємо помилку вище


def _create_session_reports(
    session,
    metas: List[FileMeta],
    exact_groups: List[DuplicateGroup],
    rename_plans: List,
    llm_client: Optional[LLMClient],
    summary: RunSummary,
) -> None:
    """Створити додаткові звіти в директорії сесії."""

    # 1. Список просканованих файлів
    scanned_files_report = []
    scanned_files_report.append("=" * 80)
    scanned_files_report.append(f"ЗВІТ: Проскановані файли")
    scanned_files_report.append(f"Сесія: {session.session_id}")
    scanned_files_report.append(f"Дата: {session.timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    scanned_files_report.append("=" * 80)
    scanned_files_report.append(f"\nЗагальна кількість файлів: {len(metas)}\n")

    for idx, meta in enumerate(metas, 1):
        size_mb = meta.size / (1024 * 1024)
        scanned_files_report.append(f"{idx:4d}. {meta.path.name}")
        scanned_files_report.append(f"      Шлях: {meta.path}")
        scanned_files_report.append(f"      Розмір: {size_mb:.2f} MB")
        scanned_files_report.append(f"      SHA256: {meta.sha256[:16] if meta.sha256 else 'N/A'}...")
        scanned_files_report.append("")

    (session.session_dir / "01_scanned_files.txt").write_text(
        "\n".join(scanned_files_report), encoding="utf-8"
    )

    # 2. Звіт про дублікати
    if exact_groups:
        duplicates_report = []
        duplicates_report.append("=" * 80)
        duplicates_report.append(f"ЗВІТ: Знайдені дублікати")
        duplicates_report.append(f"Сесія: {session.session_id}")
        duplicates_report.append("=" * 80)
        duplicates_report.append(f"\nКількість груп дублікатів: {len(exact_groups)}\n")

        for idx, group in enumerate(exact_groups, 1):
            duplicates_report.append(f"\nГрупа #{idx} (ID: {group.group_id})")
            duplicates_report.append(f"  Кількість файлів: {len(group.files)}")
            duplicates_report.append(f"  Канонічний файл: {group.canonical().path}")
            duplicates_report.append(f"  Дублікати:")
            for file_meta in group.files:
                if file_meta.path != group.canonical().path:
                    duplicates_report.append(f"    - {file_meta.path}")

        (session.session_dir / "02_duplicates.txt").write_text(
            "\n".join(duplicates_report), encoding="utf-8"
        )

    # 3. Звіт про перейменування
    if rename_plans:
        rename_report = []
        rename_report.append("=" * 80)
        rename_report.append(f"ЗВІТ: План перейменування")
        rename_report.append(f"Сесія: {session.session_id}")
        rename_report.append("=" * 80)
        rename_report.append(f"\nКількість файлів для перейменування: {len(rename_plans)}\n")

        for idx, plan in enumerate(rename_plans, 1):
            rename_report.append(f"{idx:4d}. {plan.meta.path.name}")
            rename_report.append(f"      Нова назва: {plan.new_name}")
            if plan.collision:
                rename_report.append(f"      ⚠️  Колізія імені!")
            rename_report.append("")

        (session.session_dir / "03_rename_plan.txt").write_text(
            "\n".join(rename_report), encoding="utf-8"
        )

    # 4. Статистика LLM
    if llm_client:
        stats = llm_client.get_stats()
        if stats["requests"] > 0:
            llm_report = []
            llm_report.append("=" * 80)
            llm_report.append(f"ЗВІТ: Статистика LLM")
            llm_report.append(f"Сесія: {session.session_id}")
            llm_report.append("=" * 80)
            llm_report.append(f"\nПровайдер: {llm_client.provider}")
            llm_report.append(f"Модель: {llm_client.model}")
            llm_report.append(f"\nЛІМІТИ:")
            llm_report.append(f"  Максимум символів на вхід: {llm_client.MAX_INPUT_LENGTH}")
            llm_report.append(f"  Максимум символів відображення: {llm_client.MAX_OUTPUT_DISPLAY}")
            llm_report.append(f"\nСТАТИСТИКА:")
            llm_report.append(f"  Запитів надіслано: {stats['requests']}")
            llm_report.append(f"  Відповідей отримано: {stats['responses']}")
            llm_report.append(f"\nТОКЕНИ:")
            llm_report.append(f"  Токенів надіслано: {stats['tokens_sent']:,}")
            llm_report.append(f"  Токенів отримано: {stats['tokens_received']:,}")
            llm_report.append(f"  Всього токенів: {stats['tokens']:,}")
            llm_report.append(f"\nДЕТАЛІ:")
            llm_report.append(f"  Повний лог запитів/відповідей: llm_full_log.json")
            llm_report.append(f"  У логу збережено повні тексти відповідей (без обрізання)")

            (session.session_dir / "04_llm_stats.txt").write_text(
                "\n".join(llm_report), encoding="utf-8"
            )

    # 5. Підсумковий звіт сесії
    session_summary = []
    session_summary.append("=" * 80)
    session_summary.append(f"ПІДСУМКОВИЙ ЗВІТ СЕСІЇ")
    session_summary.append("=" * 80)
    session_summary.append(f"\nID сесії: {session.session_id}")
    session_summary.append(f"Тип операції: {session.operation_type}")
    session_summary.append(f"Дата та час: {session.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    session_summary.append(f"\nСТАТИСТИКА:")
    session_summary.append(f"  Файлів просканов ано: {summary.files_total}")
    session_summary.append(f"  Файлів оброблено: {summary.files_processed}")
    session_summary.append(f"  Перейменовано успішно: {summary.renamed_ok}")
    session_summary.append(f"  Помилок перейменування: {summary.renamed_failed}")
    session_summary.append(f"  Груп дублікатів: {summary.duplicate_groups}")
    session_summary.append(f"  Файлів-дублікатів: {summary.duplicate_files}")
    session_summary.append(f"  Файлів у карантині: {summary.quarantined_count}")
    session_summary.append(f"  Файлів видалено: {summary.deleted_count}")
    session_summary.append(f"  Тривалість: {summary.duration_total_s:.2f} секунд")
    session_summary.append(f"\nФАЙЛИ СЕСІЇ:")
    session_summary.append(f"  - inventory.xlsx - повна інвентаризація")
    session_summary.append(f"  - 01_scanned_files.txt - список файлів")
    if exact_groups:
        session_summary.append(f"  - 02_duplicates.txt - знайдені дублікати")
    if rename_plans:
        session_summary.append(f"  - 03_rename_plan.txt - план перейменування")
    if llm_client and llm_client.get_stats()["requests"] > 0:
        session_summary.append(f"  - 04_llm_stats.txt - статистика LLM")
        session_summary.append(f"  - llm_full_log.json - повний лог LLM (включає необрізані відповіді)")
    session_summary.append(f"  - session_summary.txt - цей файл")
    session_summary.append(f"  - session_metadata.json - метадані сесії")

    session_summary.append(f"\nОБМЕЖЕННЯ LLM:")
    if llm_client:
        session_summary.append(f"  - Вхідний текст: макс. {llm_client.MAX_INPUT_LENGTH} символів")
        session_summary.append(f"  - Відображення в TUI: макс. {llm_client.MAX_OUTPUT_DISPLAY} символів")
        session_summary.append(f"  - Повні відповіді збережено в llm_full_log.json")

    (session.session_dir / "session_summary.txt").write_text(
        "\n".join(session_summary), encoding="utf-8"
    )


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

