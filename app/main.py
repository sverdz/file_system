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
from app.llm_client import LLMClient
from app.loggingx import log_event, log_readable, setup_logging
from app.progress import ProgressTracker
from app.rename import plan_renames
from app.scan import FileMeta, ensure_hash, scan_directory
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
                execute_pipeline(cfg, mode="dry-run")
            elif choice == "2":
                confirm = input("Виконати перейменування? [Y/n] ").strip().lower()
                if confirm in {"", "y", "yes"}:
                    delete_choice = input("Видаляти точні дублікати замість карантину? [Y/n] ").strip().lower()
                    delete_exact = delete_choice in {"", "y", "yes"}
                    sort_choice = input("Сортувати файли по підпапках? [Y/n] ").strip().lower()
                    sort_strategy = None
                    if sort_choice in {"", "y", "yes"}:
                        console.print(markup(THEME.info, "1 = by_category, 2 = by_date, 3 = by_type"))
                        mapping = {"1": "by_category", "2": "by_date", "3": "by_type"}
                        selected = input("Оберіть стратегію: ").strip()
                        sort_strategy = mapping.get(selected)
                    execute_pipeline(cfg, mode="commit", delete_exact=delete_exact, sort_strategy=sort_strategy)
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
            console.print("   - gpt-4o-mini (швидка, дешева)")
            console.print("   - gpt-4o (найкраща multimodal)")
            console.print("   - gpt-4-turbo (попередня топова)")
            model = input(f"   Модель (Enter для {cfg.llm_model or 'gpt-4o-mini'}): ").strip()
            if model:
                cfg.llm_model = model
            elif not cfg.llm_model:
                cfg.llm_model = "gpt-4o-mini"

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

        # Оновити інвентаризацію
        if file_updates:
            console.print(markup(THEME.processing, "\nОновлення інвентаризації..."))
            strategy = {"1": "by_category", "2": "by_date", "3": "by_type", "4": "flattened"}.get(choice, "manual")
            update_inventory_after_sort(latest_run, file_updates, strategy)
            console.print(format_status(f"Інвентаризація оновлена: {latest_run / 'inventory.xlsx'}", is_error=False))

    except Exception as e:
        console.print(format_error(f"\nПомилка виконання: {e}"))
        import traceback
        console.print(markup(THEME.dim_text, traceback.format_exc()))


def execute_pipeline(cfg: Config, mode: str, delete_exact: bool = False, sort_strategy: Optional[str] = None) -> None:
    start_time = datetime.now(timezone.utc)
    run_id = start_time.strftime("%Y%m%dT%H%M%S")
    run_dir = Path("runs") / run_id

    try:
        setup_logging(run_dir)
        save_config(cfg, run_dir)
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
        },
        scan_dir=str(cfg.root_path),  # Передати папку сканування
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

        # Після сканування встановлюємо total для всіх етапів
        tracker.set_all_totals(len(metas))
        tracker.set_stage_total("scan", len(metas))
        tracker.increment("scan", len(metas))
        tracker.update_description("scan", f"Знайдено {len(metas)} файлів")

        # Заповнити чергу файлів для хакерського інтерфейсу
        file_paths = [str(meta.path) for meta in metas]
        tracker.populate_queue(file_paths)

        update_progress(run_dir, tracker)

        tracker.update_description("dedup", "Аналіз дублікатів...")
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
            # Видалити з черги
            tracker.remove_from_queue(meta.path.name)

            # Встановити поточний файл
            tracker.set_current_file(
                name=meta.path.name,
                path=str(meta.path),
                stage="extract",
                status="processing",
            )

            tracker.update_description("extract", f"{meta.path.name} ({idx}/{len(metas)})")

            # Засікти час початку обробки
            start_time = time.time()
            try:
                ensure_hash(meta)

                # Етап 1: Вилучення тексту
                result = extract_text(meta, cfg.ocr_lang)

                # Оновити статус після extract
                tracker.set_current_file(
                    name=meta.path.name,
                    stage="extract",
                    status="success",
                )

                # Етап 2: Класифікація через LLM
                tracker.set_current_file(
                    name=meta.path.name,
                    stage="classify",
                    status="processing",
                )

                classification = classify_text(result.text, llm_client=llm_client)
                category = classification.get("category") or "інше"
                date_doc = classification.get("date_doc") or datetime.fromtimestamp(meta.mtime).date().isoformat()
                # Якщо LLM повернув summary, використовуємо його
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
                extract_time = time.time() - start_time

                # Оновити статус (БЕЗ зміни path!)
                tracker.set_current_file(
                    name=meta.path.name,
                    category=category,
                    stage="classify",
                    status="success",
                )

                # Додати в лог
                llm_response = classification.get("summary", "") or summary
                tracker.add_to_log(
                    status="success",
                    text_length=len(result.text),
                    llm_response=llm_response[:100] if llm_response else "",  # Перші 100 символів
                    category=category,
                    processing_time={
                        "extract": extract_time,
                        "classify": extract_time,  # Обидва етапи відбуваються разом
                    },
                )

            except Exception as exc:
                # Use fallback values if extraction fails
                error_count += 1
                error_msg = f"Не вдалося обробити: {exc}"
                console.print(markup(THEME.warning, f"⚠ {error_msg}"))
                extract_time = time.time() - start_time

                # Оновити статус помилки (БЕЗ зміни path!)
                tracker.set_current_file(
                    name=meta.path.name,
                    stage="extract",
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

                # Додати в лог як помилку (метрики оновляться автоматично)
                tracker.add_to_log(
                    status="error",
                    processing_time={"extract": extract_time},
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

        tracker.set_stage_total("rename", len(rename_plans))
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

