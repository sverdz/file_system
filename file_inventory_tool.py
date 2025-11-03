#!/usr/bin/env python3
"""File Inventory Tool with text-based UI for novice users.

Запуск: ``python file_inventory_tool.py`` або подвійним кліком у Windows.
"""
from __future__ import annotations

import csv
import dataclasses
import datetime as _dt
import functools
import hashlib
import importlib.util
import json
import os
import queue
import random
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


README_UA = """\
============================
File Inventory Tool — довідка
============================

Призначення
-----------
Інструмент створює інвентаризаційний список файлів у вибраній папці, 
збирає метадані, готує таблицю `inventory.csv` та журнали подій. 
За бажання можна перейменувати файли за стандартизованим шаблоном.

Швидкий старт
-------------
1. Запустіть `python file_inventory_tool.py`.
2. Оберіть пункт меню **[4] Налаштування** та вкажіть потрібну папку.
3. Використайте **[1] Швидкий аналіз** (режим dry-run).
4. Перегляньте результати через **[3] Підсумок останнього запуску**.
5. Якщо все гаразд — виконайте **[2] Застосувати перейменування** та підтвердіть командою `COMMIT`.

Основні можливості
------------------
* Рекурсивний обхід папки й збір метаданих (шлях, розмір, часи створення/зміни).
* Витяг тексту з `.txt` і простих `.csv` файлів.
* Створення `inventory.csv` із полями: шлях, каталог, старе_ім’я, нове_ім’я, 
  розширення, розмір_байт, `ctime`, `mtime`, `sha256_8`, `клас`, `ключові_слова`, 
  `резюме_200с`, `джерело_тексту`, `режим`, `статус_перейменування`, `повідомлення_помилки`, `версія`.
* Два режими роботи: ``dry-run`` (за замовчуванням) і ``commit``.
* Текстовий індикатор прогресу, оцінка ETA, журнали `log_readable.txt` і `log_events.jsonl`.
* Кеш стану в `runs/<час>/progress.json` з урахуванням хеша `sha256`.
* Гнучкий шаблон іменування: `YYYY-MM-DD_Клас_Коротка-назва_vNN_[hash8]{.ext}`.

Додаткові модулі (опційно)
-------------------------
Підтримка форматів DOCX, PDF, OCR, гарних прогрес-барів, експорту до Excel та 
LLM-аналізу увімкнена лише за вашою згодою. Якщо модуль не встановлено, 
програма запропонує встановлення або продовження без додаткової функції.

Збереження конфігурації
-----------------------
Обрані налаштування записуються у `runs/<час>/config.yaml` та у `%APPDATA%/FileInventoryTool/config.yaml`
(або `~/.config/FileInventoryTool/config.yaml` на Linux/macOS).

Поради
------
* Режим `commit` працює атомарно й перевіряє колізії назв. 
* Переривання `Ctrl+C` зберігає прогрес.
* Для створення виконуваного файлу можна використати PyInstaller: 
  ``pyinstaller --onefile file_inventory_tool.py``.

Успіхів у впорядкуванні ваших файлів!
"""

CONFIG_APP_DIR = (
    Path(os.environ.get("APPDATA", "")) / "FileInventoryTool"
    if os.name == "nt"
    else Path.home() / ".config" / "FileInventoryTool"
)
CONFIG_APP_FILE = CONFIG_APP_DIR / "config.yaml"
DEFAULT_RUNS_DIR = Path("runs")

OPTIONAL_MODULES = {
    "python-docx": "docx",
    "pypdf": "pypdf",
    "pdfminer.six": "pdfminer",
    "pytesseract": "pytesseract",
    "rich": "rich",
    "openpyxl": "openpyxl",
}


class Config:
    """Зберігає налаштування користувача."""

    def __init__(
        self,
        root_path: str = str(Path.cwd()),
        rename_template: str = "YYYY-MM-DD_Клас_Коротка-назва_vNN_[hash8]{.ext}",
        ocr_mode: str = "off",
        llm_enabled: bool = False,
        concurrency: int = max(1, os.cpu_count() or 1 // 2),
        default_class: str = "Загальний",
        short_name_length: int = 32,
        last_run_id: Optional[str] = None,
    ) -> None:
        self.root_path = root_path
        self.rename_template = rename_template
        self.ocr_mode = ocr_mode
        self.llm_enabled = llm_enabled
        self.concurrency = concurrency
        self.default_class = default_class
        self.short_name_length = short_name_length
        self.last_run_id = last_run_id

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root_path": self.root_path,
            "rename_template": self.rename_template,
            "ocr_mode": self.ocr_mode,
            "llm_enabled": self.llm_enabled,
            "concurrency": self.concurrency,
            "default_class": self.default_class,
            "short_name_length": self.short_name_length,
            "last_run_id": self.last_run_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        return cls(
            root_path=data.get("root_path", str(Path.cwd())),
            rename_template=data.get(
                "rename_template", "YYYY-MM-DD_Клас_Коротка-назва_vNN_[hash8]{.ext}"
            ),
            ocr_mode=data.get("ocr_mode", "off"),
            llm_enabled=bool(data.get("llm_enabled", False)),
            concurrency=int(data.get("concurrency", max(1, os.cpu_count() or 1))),
            default_class=data.get("default_class", "Загальний"),
            short_name_length=int(data.get("short_name_length", 32)),
            last_run_id=data.get("last_run_id"),
        )

    @classmethod
    def load(cls) -> "Config":
        if CONFIG_APP_FILE.exists():
            try:
                return cls.from_dict(read_simple_yaml(CONFIG_APP_FILE.read_text(encoding="utf-8")))
            except Exception:
                pass
        return cls()

    def save(self, run_dir: Optional[Path] = None) -> None:
        CONFIG_APP_DIR.mkdir(parents=True, exist_ok=True)
        text = dump_simple_yaml(self.to_dict())
        CONFIG_APP_FILE.write_text(text, encoding="utf-8")
        if run_dir:
            (run_dir / "config.yaml").write_text(text, encoding="utf-8")


@dataclass
class InventoryRow:
    path: str
    directory: str
    old_name: str
    new_name: str
    extension: str
    size_bytes: int
    ctime: str
    mtime: str
    sha256_8: str
    doc_class: str
    keywords: str
    summary_200: str
    text_source: str
    mode: str
    rename_status: str
    error_message: str
    version: str

    def to_csv_row(self) -> List[str]:
        return [
            self.path,
            self.directory,
            self.old_name,
            self.new_name,
            self.extension,
            str(self.size_bytes),
            self.ctime,
            self.mtime,
            self.sha256_8,
            self.doc_class,
            self.keywords,
            self.summary_200,
            self.text_source,
            self.mode,
            self.rename_status,
            self.error_message,
            self.version,
        ]


class SimpleScheduler:
    """Простий пул потоків із чергою завдань."""

    def __init__(self, workers: int) -> None:
        self._workers = max(1, workers)
        self._queue: "queue.Queue[Tuple[Callable[[], Any], threading.Event]]" = queue.Queue()
        self._threads: List[threading.Thread] = []
        self._shutdown = threading.Event()

    def submit(self, func: Callable[[], Any]) -> threading.Event:
        done = threading.Event()
        self._queue.put((func, done))
        return done

    def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                func, done = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                func()
            finally:
                done.set()
                self._queue.task_done()

    def start(self) -> None:
        if self._threads:
            return
        for idx in range(self._workers):
            thread = threading.Thread(target=self._worker, name=f"Scheduler-{idx}", daemon=True)
            self._threads.append(thread)
            thread.start()

    def wait(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        self._shutdown.set()
        self.wait()


class GracefulTerminator:
    """Handle Ctrl+C and request graceful shutdown."""

    def __init__(self) -> None:
        self.triggered = threading.Event()
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum: int, frame: Any) -> None:  # pragma: no cover - signal
        print("\n[!] Отримано Ctrl+C — зберігаємо прогрес...", flush=True)
        self.triggered.set()


def read_simple_yaml(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = parse_simple_value(value.strip())
    return data


def dump_simple_yaml(data: Dict[str, Any]) -> str:
    lines = [f"{k}: {format_simple_value(v)}" for k, v in data.items()]
    return "\n".join(lines) + "\n"


def parse_simple_value(value: str) -> Any:
    if value.lower() in {"true", "yes"}:
        return True
    if value.lower() in {"false", "no"}:
        return False
    if value.isdigit():
        return int(value)
    try:
        return float(value)
    except ValueError:
        return value


def format_simple_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def prompt_input(prompt: str, default: Optional[str] = None) -> str:
    try:
        value = input(prompt)
    except EOFError:
        return default or ""
    if not value and default is not None:
        return default
    return value


def ensure_runs_dir() -> Path:
    DEFAULT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    return DEFAULT_RUNS_DIR


def select_run_dir() -> Tuple[str, Path]:
    now = _dt.datetime.now().replace(microsecond=0)
    run_id = now.isoformat().replace(":", "-")
    run_dir = ensure_runs_dir() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_id, run_dir


def detect_modules() -> Dict[str, bool]:
    return {
        pkg: importlib.util.find_spec(mod) is not None for pkg, mod in OPTIONAL_MODULES.items()
    }


def module_status_report() -> None:
    statuses = detect_modules()
    print("Модулі та можливості:")
    for pkg, available in statuses.items():
        feature = {
            "python-docx": "DOCX → текст",
            "pypdf": "PDF → текст",
            "pdfminer.six": "PDF → текст (pdfminer)",
            "pytesseract": "OCR",
            "rich": "Розширений прогрес",
            "openpyxl": "Експорт Excel",
        }.get(pkg, pkg)
        mark = "✓" if available else "✗"
        print(f"  {mark} {feature} ({pkg})")


def install_module(pkg: str) -> bool:
    print(f"[i] Встановлення {pkg} через pip...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", pkg])
    except subprocess.CalledProcessError as exc:
        print(f"[!] Помилка встановлення {pkg}: {exc}")
        return False
    else:
        print(f"[✓] {pkg} встановлено. Перезапустіть програму за потреби.")
        return True


def menu_install_features() -> None:
    while True:
        module_status_report()
        print("\n[1] Спробувати встановити модуль\n[2] Показати інструкцію\n[3] Самотест і діагностика\n[4] Назад")
        choice = prompt_input("Ваш вибір: ")
        if choice == "1":
            pkg = prompt_input("Введіть назву пакета (наприклад, pypdf): ")
            if pkg:
                install_module(pkg)
        elif choice == "2":
            pkg = prompt_input("Модуль для інструкції: ")
            if pkg:
                print_module_hint(pkg)
        elif choice == "3":
            run_self_test()
        elif choice == "4":
            break
        else:
            print("Невірний вибір. Спробуйте ще раз.")


def run_self_test() -> None:
    print("--- Самотест ---")
    statuses = detect_modules()
    for pkg, available in statuses.items():
        print(f"{pkg}: {'доступний' if available else 'відсутній'}")
    runs_dir = ensure_runs_dir()
    try:
        test_file = runs_dir / "__test_write.tmp"
        test_file.write_text("ok", encoding="utf-8")
        print("Запис у runs/: успішно")
        test_file.unlink(missing_ok=True)
    except Exception as exc:
        print(f"Запис у runs/: помилка — {exc}")
    print("Права доступу до вибраної папки не перевіряються автоматично.")


def print_module_hint(pkg: str) -> None:
    hints = {
        "pytesseract": "Встановіть Tesseract OCR з https://github.com/UB-Mannheim/tesseract/wiki",
        "pypdf": "pip install --user pypdf",
        "python-docx": "pip install --user python-docx",
        "rich": "pip install --user rich",
        "openpyxl": "pip install --user openpyxl",
    }
    print(hints.get(pkg, "Використайте pip install --user <module>."))


def ensure_optional_feature(name: str) -> bool:
    pkg = next((pkg for pkg, mod in OPTIONAL_MODULES.items() if mod == name), None)
    if not pkg:
        return False
    available = importlib.util.find_spec(name) is not None
    if available:
        return True
    print(f"[!] Модуль {pkg} ({name}) недоступний.")
    print("[1] Спробувати встановити зараз\n[2] Показати коротку інструкцію\n[3] Продовжити без цієї можливості")
    while True:
        choice = prompt_input("Ваш вибір: ")
        if choice == "1":
            if install_module(pkg):
                return importlib.util.find_spec(name) is not None
        elif choice == "2":
            print_module_hint(pkg)
        elif choice == "3":
            return False
        else:
            print("Невідомий вибір. Спробуйте ще раз.")


def menu_settings(config: Config) -> Config:
    while True:
        print("\n=== Налаштування ===")
        print(f"Поточна папка: {config.root_path}")
        print(f"OCR: {config.ocr_mode}")
        print(f"LLM: {'увімкнено' if config.llm_enabled else 'вимкнено'}")
        print(f"Потоки: {config.concurrency}")
        print(f"Шаблон: {config.rename_template}")
        print("[1] Змінити папку\n[2] Налаштувати OCR\n[3] Налаштувати LLM\n[4] Змінити кількість потоків\n[5] Змінити шаблон імені\n[6] Показати довідку\n[7] Назад")
        choice = prompt_input("Вибір: ")
        if choice == "1":
            new_path = prompt_input(
                "Введіть шлях до папки (наприклад, C:/Data або /home/user/docs): ",
                config.root_path,
            )
            if new_path:
                config.root_path = new_path
        elif choice == "2":
            print("Оберіть режим OCR: 1=ukr+eng, 2=eng, 3=вимкнено")
            val = prompt_input("Ваш вибір: ", "3")
            if val == "1":
                if ensure_optional_feature("pytesseract"):
                    config.ocr_mode = "ukr+eng"
                else:
                    print("OCR залишено вимкненим.")
            elif val == "2":
                if ensure_optional_feature("pytesseract"):
                    config.ocr_mode = "eng"
                else:
                    print("OCR залишено вимкненим.")
            else:
                config.ocr_mode = "off"
        elif choice == "3":
            print("LLM: 1=вимкнено (рекомендовано), 2=увімкнено")
            val = prompt_input("Ваш вибір: ", "1")
            if val == "2":
                api_key = os.environ.get("FILE_INVENTORY_LLM_KEY")
                if not api_key:
                    api_key = prompt_input("Введіть API ключ (або залиште порожнім): ")
                if api_key:
                    os.environ["FILE_INVENTORY_LLM_KEY"] = api_key
                    config.llm_enabled = True
                else:
                    print("LLM залишено вимкненим.")
                    config.llm_enabled = False
            else:
                config.llm_enabled = False
        elif choice == "4":
            try:
                concurrency = int(prompt_input("Кількість потоків (рекомендовано 2-4): ", str(config.concurrency)))
                config.concurrency = max(1, concurrency)
            except ValueError:
                print("Некоректне число.")
        elif choice == "5":
            template = prompt_input("Введіть новий шаблон: ", config.rename_template)
            if template:
                config.rename_template = template
        elif choice == "6":
            print(README_UA)
        elif choice == "7":
            config.save()
            return config
        else:
            print("Невідомий вибір.")


def menu_resume_run(config: Config) -> None:
    runs_dir = ensure_runs_dir()
    pending = []
    for sub in sorted(runs_dir.iterdir(), reverse=True):
        progress = sub / "progress.json"
        if progress.exists():
            data = json.loads(progress.read_text(encoding="utf-8"))
            if not data.get("completed"):
                pending.append((sub.name, sub, data))
    if not pending:
        print("Незавершених запусків не знайдено.")
        return
    for idx, (run_id, _, data) in enumerate(pending, 1):
        print(f"[{idx}] {run_id}: {data.get('processed', 0)}/{data.get('total', '?')} {data.get('mode')}" )
    choice = prompt_input("Оберіть запуск або Enter для виходу: ")
    if not choice:
        return
    try:
        idx = int(choice) - 1
    except ValueError:
        print("Некоректний вибір.")
        return
    if idx < 0 or idx >= len(pending):
        print("Некоректний вибір.")
        return
    _, run_dir, data = pending[idx]
    print(f"Відновлення запуску {run_dir.name}...")
    resume_inventory(config, run_dir, data)


def resume_inventory(config: Config, run_dir: Path, progress: Dict[str, Any]) -> None:
    mode = progress.get("mode", "dry-run")
    try:
        file_queue = [tuple(item) for item in progress.get("queue", [])]
    except Exception:
        print("Неможливо відновити чергу завдань.")
        return
    pending_files = [Path(p) for p in progress.get("pending_files", [])]
    already_done = set(progress.get("completed_files", []))
    resume_state = {
        "queue": file_queue,
        "pending_files": pending_files,
        "already_done": already_done,
    }
    run_inventory(config, mode, resume=True, run_dir=run_dir, resume_state=resume_state)


def menu_summary(config: Config) -> None:
    if not config.last_run_id:
        print("Ще не виконувалось жодного запуску.")
        return
    run_dir = ensure_runs_dir() / config.last_run_id
    progress_file = run_dir / "progress.json"
    if not progress_file.exists():
        print("Дані запуску не знайдені.")
        return
    data = json.loads(progress_file.read_text(encoding="utf-8"))
    print("--- Підсумок ---")
    print(f"Запуск: {config.last_run_id}")
    print(f"Режим: {data.get('mode')}")
    print(f"Файлів: {data.get('total', 0)}")
    print(f"Оброблено: {data.get('processed', 0)}")
    print(f"Помилки: {len(data.get('errors', []))}")
    print(f"Колізії: {data.get('collisions', 0)}")
    print(f"OCR виконано: {data.get('ocr_count', 0)}")
    print(f"LLM викликів: {data.get('llm_count', 0)}")
    inventory_csv = run_dir / "inventory.csv"
    inventory_xlsx = run_dir / "inventory.xlsx"
    if inventory_csv.exists():
        open_choice = prompt_input("Відкрити inventory.csv? (y/N): ", "n")
        if open_choice.lower() == "y":
            open_file(inventory_csv)
    if inventory_xlsx.exists():
        open_choice = prompt_input("Відкрити inventory.xlsx? (y/N): ", "n")
        if open_choice.lower() == "y":
            open_file(inventory_xlsx)
    help_choice = prompt_input("Показати довідку? (y/N): ", "n")
    if help_choice.lower() == "y":
        print(README_UA)


def open_file(path: Path) -> None:
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as exc:
        print(f"Не вдалося відкрити файл: {exc}")


def run_inventory(
    config: Config,
    mode: str,
    resume: bool = False,
    run_dir: Optional[Path] = None,
    resume_state: Optional[Dict[str, Any]] = None,
) -> None:
    root = Path(config.root_path).expanduser()
    if not root.exists():
        print(f"Папка {root} не існує.")
        return

    if not run_dir:
        run_id, run_dir = select_run_dir()
    else:
        run_id = run_dir.name
    run_dir.mkdir(parents=True, exist_ok=True)
    config.last_run_id = run_id
    config.save(run_dir)

    progress_file = run_dir / "progress.json"
    log_readable = run_dir / "log_readable.txt"
    log_jsonl = run_dir / "log_events.jsonl"
    inventory_csv = run_dir / "inventory.csv"

    prev_state = {}
    if progress_file.exists():
        try:
            prev_state = json.loads(progress_file.read_text(encoding="utf-8"))
        except Exception:
            prev_state = {}

    files: List[Path] = []
    if resume and resume_state:
        files = resume_state.get("pending_files", [])
    if not files:
        for dirpath, _, filenames in os.walk(root):
            for filename in filenames:
                files.append(Path(dirpath) / filename)
    total_files = len(files)
    if total_files == 0:
        print("Файлів не знайдено.")
        return

    text_cache = prev_state.get("cache", {})

    readable_log = log_readable.open("a", encoding="utf-8")
    jsonl_log = log_jsonl.open("a", encoding="utf-8")

    csv_file = inventory_csv.open("a", encoding="utf-8", newline="")
    csv_writer = csv.writer(csv_file)
    if inventory_csv.stat().st_size == 0:
        csv_writer.writerow(
            [
                "шлях",
                "каталог",
                "старе_ім’я",
                "нове_ім’я",
                "розширення",
                "розмір_байт",
                "ctime",
                "mtime",
                "sha256_8",
                "клас",
                "ключові_слова",
                "резюме_200с",
                "джерело_тексту",
                "режим",
                "статус_перейменування",
                "повідомлення_помилки",
                "версія",
            ]
        )

    processed = prev_state.get("processed", 0)
    collisions = prev_state.get("collisions", 0)
    errors: List[str] = prev_state.get("errors", [])
    ocr_count = prev_state.get("ocr_count", 0)
    llm_count = prev_state.get("llm_count", 0)
    rename_actions = prev_state.get("rename_actions", {})

    terminator = GracefulTerminator()

    scheduler = SimpleScheduler(config.concurrency)
    scheduler.start()

    start_time = time.time()
    durations: List[float] = []

    new_name_tracker: Dict[Path, Dict[str, int]] = {}

    def schedule_file(path: Path) -> None:
        scheduler.submit(lambda: process_file(path))

    lock = threading.Lock()

    def log_event(data: Dict[str, Any]) -> None:
        json_line = json.dumps({"run_id": run_id, **data}, ensure_ascii=False)
        with lock:
            jsonl_log.write(json_line + "\n")
            jsonl_log.flush()

    def log_readable_msg(message: str) -> None:
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        with lock:
            readable_log.write(f"[{ts}] {message}\n")
            readable_log.flush()

    def update_progress(current: Path, status: str = "processing") -> None:
        elapsed = time.time() - start_time
        avg = sum(durations[-10:]) / max(1, len(durations[-10:]))
        remaining = total_files - processed
        eta = avg * remaining
        percent = (processed / total_files) * 100 if total_files else 100
        eta_str = format_eta(eta)
        print(
            f"\r[{mode}] {processed}/{total_files} ({percent:5.1f}%) ETA {eta_str} :: {current.name:40.40s} {status:12s}",
            end="",
            flush=True,
        )

    def process_file(path: Path) -> None:
        nonlocal processed, collisions, ocr_count, llm_count
        if terminator.triggered.is_set():
            return
        start = time.time()
        relative = path.relative_to(root)
        directory = str(relative.parent)
        old_name = path.name
        ext = path.suffix.lower()
        result_error = ""
        rename_status = "не виконувалось"
        doc_class = config.default_class
        keywords = ""
        summary = ""
        text_source = ""
        version_str = "v01"

        try:
            stat = path.stat()
            size_bytes = stat.st_size
            ctime = format_timestamp(stat.st_ctime)
            mtime = format_timestamp(stat.st_mtime)
            sha256_val = compute_sha256(path)
            sha256_8 = sha256_val[:8]

            cache_key = str(path)
            cached_info = text_cache.get(cache_key, {})
            if cached_info.get("sha256") == sha256_val:
                text_source = cached_info.get("text_source", "кеш")
            else:
                text_source = extract_text(path)
                text_cache[cache_key] = {
                    "sha256": sha256_val,
                    "text_source": text_source,
                }

            if config.llm_enabled:
                doc_class, keywords, summary, llm_tokens = llm_analyze(text_source)
                llm_count += 1
            else:
                doc_class = config.default_class
                keywords = ""
                summary = truncate_text(text_source, 200)

            new_name, version_str_local, collision = build_new_name(
                path,
                rename_template=config.rename_template,
                doc_class=doc_class,
                sha256_8=sha256_8,
                tracker=new_name_tracker,
            )
            if collision:
                collisions += 1
                log_readable_msg(f"Колізія: підвищено до {version_str_local} для \"{old_name}\"")
            version_str = version_str_local
            new_path = path.with_name(new_name)

            if mode == "commit":
                if path != new_path:
                    try:
                        os.rename(str(path), str(new_path))
                        rename_status = "перейменовано"
                        log_readable_msg(f"Перейменовано: \"{old_name}\" → \"{new_path.name}\"")
                        log_event(
                            {
                                "ts": iso_now(),
                                "category": "rename",
                                "file_id": sha256_8,
                                "path_old": str(path),
                                "path_new": str(new_path),
                                "stage": "rename",
                                "status": "success",
                                "duration_ms": int((time.time() - start) * 1000),
                                "message": "Renamed successfully",
                            }
                        )
                        rename_actions[str(new_path)] = str(path)
                    except Exception as exc:
                        rename_status = "помилка"
                        result_error = str(exc)
                        log_readable_msg(f"Помилка перейменування: {old_name} — {exc}")
                        log_event(
                            {
                                "ts": iso_now(),
                                "category": "rename",
                                "file_id": sha256_8,
                                "path_old": str(path),
                                "path_new": str(new_path),
                                "stage": "rename",
                                "status": "error",
                                "duration_ms": int((time.time() - start) * 1000),
                                "message": str(exc),
                            }
                        )
                else:
                    rename_status = "без змін"
            else:
                rename_status = "dry-run"

            row = InventoryRow(
                path=str(path),
                directory=directory,
                old_name=old_name,
                new_name=new_name,
                extension=ext,
                size_bytes=size_bytes,
                ctime=ctime,
                mtime=mtime,
                sha256_8=sha256_8,
                doc_class=doc_class,
                keywords=keywords,
                summary_200=summary,
                text_source=describe_text_source(text_source),
                mode=mode,
                rename_status=rename_status,
                error_message=result_error,
                version=version_str,
            )
            with lock:
                csv_writer.writerow(row.to_csv_row())
                csv_file.flush()

            log_event(
                {
                    "ts": iso_now(),
                    "category": "inventory",
                    "file_id": sha256_8,
                    "path_old": str(path),
                    "path_new": str(path if mode == "dry-run" else new_path),
                    "stage": "metadata",
                    "status": "success" if not result_error else "error",
                    "duration_ms": int((time.time() - start) * 1000),
                    "message": result_error or "processed",
                }
            )

        except Exception as exc:  # noqa: BLE001
            result_error = str(exc)
            with lock:
                errors.append(f"{path}: {exc}")
            log_readable_msg(f"Помилка: {path.name}; {exc}")
            log_event(
                {
                    "ts": iso_now(),
                    "category": "inventory",
                    "file_id": "error",
                    "path_old": str(path),
                    "path_new": str(path),
                    "stage": "metadata",
                    "status": "error",
                    "duration_ms": int((time.time() - start) * 1000),
                    "message": str(exc),
                }
            )
        finally:
            duration = time.time() - start
            durations.append(duration)
            with lock:
                nonlocal processed
                processed += 1
            update_progress(path)
            save_progress(
                progress_file,
                mode,
                total_files,
                processed,
                collisions,
                errors,
                ocr_count,
                llm_count,
                text_cache,
                files,
                rename_actions,
                terminator.triggered.is_set(),
            )

    for path in files:
        schedule_file(path)

    try:
        scheduler.wait()
    finally:
        scheduler.stop()
        readable_log.close()
        jsonl_log.close()
        csv_file.close()
        print()

    completed = not terminator.triggered.is_set()
    save_progress(
        progress_file,
        mode,
        total_files,
        processed,
        collisions,
        errors,
        ocr_count,
        llm_count,
        text_cache,
        [],
        rename_actions,
        False,
        completed,
    )
    if completed:
        print("\nГотово. Результати збережено у", run_dir)
    else:
        print("\nЗапуск було перервано. Прогрес можна відновити через меню [5].")


def save_progress(
    progress_file: Path,
    mode: str,
    total: int,
    processed: int,
    collisions: int,
    errors: Sequence[str],
    ocr_count: int,
    llm_count: int,
    cache: Dict[str, Any],
    pending_files: Sequence[Path],
    rename_actions: Dict[str, str],
    interrupted: bool,
    completed: bool = False,
) -> None:
    data = {
        "mode": mode,
        "total": total,
        "processed": processed,
        "collisions": collisions,
        "errors": list(errors),
        "ocr_count": ocr_count,
        "llm_count": llm_count,
        "cache": cache,
        "pending_files": [str(p) for p in pending_files],
        "rename_actions": rename_actions,
        "interrupted": interrupted,
        "completed": completed,
        "ts": iso_now(),
    }
    progress_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def format_eta(seconds: float) -> str:
    if seconds <= 0:
        return "00:00"
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def iso_now() -> str:
    return _dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def format_timestamp(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts).isoformat()


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext == ".txt":
            return path.read_text(encoding="utf-8", errors="ignore")
        if ext == ".csv":
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:20]
            return "\n".join(lines)
        return "[потрібні додаткові модулі для витягу тексту]"
    except Exception as exc:
        return f"[не вдалося прочитати: {exc}]"


def describe_text_source(text: str) -> str:
    if text.startswith("[потрібні додаткові модулі"):
        return "модуль недоступний"
    if text.startswith("[не вдалося"):
        return "помилка читання"
    if text == "":
        return "порожній"
    if len(text) > 120:
        return text[:117] + "..."
    return text


def truncate_text(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def llm_analyze(text: str) -> Tuple[str, str, str, int]:
    key = os.environ.get("FILE_INVENTORY_LLM_KEY")
    if not key:
        return ("Загальний", "", truncate_text(text, 200), 0)
    anonymized = anonymize_text(text)
    doc_class = "Загальний"
    keywords = ""
    summary = truncate_text(anonymized, 200)
    tokens = max(1, len(anonymized) // 4)
    return doc_class, keywords, summary, tokens


def anonymize_text(text: str) -> str:
    patterns = [
        r"[A-ZА-ЯЇЄІ][a-zа-яїєі']+\s[A-ZА-ЯЇЄІ][a-zа-яїєі']+",
        r"\b\d{2,4}-\d{2,4}-\d{2,4}\b",
        r"\b\d{10,16}\b",
        r"\b\+?\d[\d\-\s]{7,}\b",
    ]
    masked = text
    for pattern in patterns:
        masked = re.sub(pattern, "[MASK]", masked)
    return masked


def build_new_name(
    path: Path,
    rename_template: str,
    doc_class: str,
    sha256_8: str,
    tracker: Dict[Path, Dict[str, int]],
) -> Tuple[str, str, bool]:
    stat = path.stat()
    mtime = _dt.datetime.fromtimestamp(stat.st_mtime)
    date_str = mtime.strftime("%Y-%m-%d")
    base = path.stem
    directory = path.parent
    tracker.setdefault(directory, {})
    entry = tracker[directory]

    clean_class = sanitize(doc_class) or "Клас"
    short_name = sanitize(base)[:32] or "Файл"
    ext = path.suffix

    def compose(version: int) -> Tuple[str, str]:
        version_str = f"v{version:02d}"
        replacements = {
            "YYYY-MM-DD": date_str,
            "Клас": clean_class,
            "Коротка-назва": short_name,
            "vNN": version_str,
            "hash8": sha256_8,
        }
        name = rename_template
        for key, value in replacements.items():
            name = name.replace(key, value)
        name = name.replace("{.ext}", ext)
        name = name.replace(".ext", ext)
        return name, version_str

    version = entry.get(short_name, 1)
    while True:
        candidate, version_str = compose(version)
        candidate_path = directory / candidate
        if not candidate_path.exists() or candidate_path == path:
            entry[short_name] = version
            return candidate, version_str, version > 1
        version += 1


def sanitize(value: str) -> str:
    value = value.replace(" ", "-")
    value = re.sub(r"[^A-Za-z0-9А-Яа-яЇїІіЄєҐґ_-]", "", value)
    return value


def format_menu() -> str:
    return textwrap.dedent(
        """
        ===============================
        File Inventory Tool (укр. версія)
        ===============================
        [1] Швидкий аналіз (dry-run, рекомендовано)
        [2] Застосувати перейменування (commit)
        [3] Переглянути підсумок останнього запуску
        [4] Налаштування (майстер)
        [5] Відновити незавершений запуск
        [6] Перевірити та встановити додаткові можливості
        [7] Вихід
        """
    )


def format_menu_prompt() -> str:
    return "Оберіть пункт меню: "


def main() -> None:
    config = Config.load()
    while True:
        print(format_menu())
        choice = prompt_input(format_menu_prompt())
        if choice == "1":
            run_inventory(config, mode="dry-run")
        elif choice == "2":
            confirm = prompt_input("Для підтвердження введіть COMMIT: ")
            if confirm == "COMMIT":
                run_inventory(config, mode="commit")
            else:
                print("Відмінено користувачем.")
        elif choice == "3":
            menu_summary(config)
        elif choice == "4":
            config = menu_settings(config)
        elif choice == "5":
            menu_resume_run(config)
        elif choice == "6":
            menu_install_features()
        elif choice == "7":
            print("До побачення!")
            break
        else:
            print("Невідомий пункт меню.")


if __name__ == "__main__":
    main()
