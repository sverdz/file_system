"""Progress tracking utilities with ETA estimation."""
from __future__ import annotations

import time
import hashlib
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple, Optional, List
from pathlib import Path

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.text import Text
from rich.markup import escape

from .theme import THEME, markup, format_number, format_percent, format_status
from .hacker_ui import (
    generate_hex_id,
    calculate_sha256,
    format_file_size,
    format_date,
    render_ascii_logo,
    render_file_log_entry,
    render_current_file,
    render_queue,
)

WINDOW = 10


@dataclass
class StageProgress:
    weight: float
    completed: int = 0
    total: int = 0
    last_update: float = field(default_factory=time.time)

    def update(self, completed: int, total: int) -> None:
        self.completed = completed
        self.total = total
        self.last_update = time.time()


@dataclass
class ProcessingMetrics:
    """Агреговані метрики обробки файлів."""
    duplicate_groups: int = 0
    duplicate_files: int = 0
    error_count: int = 0
    success_count: int = 0
    skipped_count: int = 0
    llm_requests: int = 0
    llm_responses: int = 0


@dataclass
class CurrentFileStatus:
    """Статус поточного файлу."""
    name: str = ""
    path: str = ""
    category: str = ""
    stage: str = ""  # "extract", "classify", "rename", тощо
    status: str = ""  # "processing", "success", "error"
    error_msg: str = ""
    size: int = 0  # Розмір файлу в байтах
    modified_time: float = 0  # Час модифікації
    sha_hash: str = ""  # SHA-256 хеш
    hex_id: str = ""  # Hex адреса для хакерського вигляду


@dataclass
class FileLogEntry:
    """Запис у лозі обробки файлу."""
    hex_id: str
    timestamp: str
    filename: str
    size: int
    modified_date: str
    sha_hash: str
    status: str  # "success", "error", "duplicate", "skipped"
    duplicate_info: str = ""
    text_length: int = 0
    llm_response: str = ""
    category: str = ""
    destination: str = ""
    processing_time: Dict[str, float] = field(default_factory=dict)  # {"dedup": 0.24, "extract": 1.82, ...}


@dataclass
class QueuedFile:
    """Файл у черзі обробки."""
    hex_id: str
    filename: str
    size: int
    modified_date: str


class ProgressTracker:
    def __init__(self, stages: Dict[str, float], scan_dir: str = ""):
        self.stages = {name: StageProgress(weight=weight) for name, weight in stages.items()}
        self.history: list[Tuple[float, float]] = []
        self.progress: Optional[Progress] = None
        self.task_ids: Dict[str, int] = {}
        self.console = Console()
        self.live: Optional[Live] = None

        # Нові атрибути для компактного відображення
        self.metrics = ProcessingMetrics()
        self.current_file = CurrentFileStatus()
        self.start_time = time.time()
        self.use_compact_view = True  # За замовчуванням компактний вигляд

        # Хакерський інтерфейс
        self.scan_dir = scan_dir  # Поточна папка сканування
        self.file_log: List[FileLogEntry] = []  # Історія оброблених файлів (ВИМКНЕНО)
        self.file_queue: List[QueuedFile] = []  # Черга файлів (ВИМКНЕНО)
        self.all_files: List[str] = []  # ВСІ файли для обробки
        self.current_file_index: int = 0  # Поточний індекс в all_files
        self.hex_counter = 0x7F8A  # Лічильник для генерації hex адрес
        self.files_processed: int = 0  # Скільки файлів оброблено
        self.total_files: int = 0  # Загальна кількість файлів
        self.files_scanned: int = 0  # Скільки файлів знайдено під час сканування
        self.scanning_active: bool = False  # Чи триває сканування

        # Прогрес поточного файлу (для детального відображення)
        self.current_stage_progress: Dict[str, Dict[str, float]] = {}  # {"dedup": {"progress": 0.5, "time": 1.2}}

        # Список помилок
        self.error_list: List[Dict[str, str]] = []  # [{"file": "file.txt", "error": "помилка", "time": "12:34:56"}]

    def _update_display_now(self) -> None:
        """Оновити дисплей ЗАВЖДИ (без throttling)."""
        if self.live and self.use_compact_view:
            self.live.update(self._render_display())

    def update_scan_progress(self, files_found: int) -> None:
        """Оновити прогрес сканування (викликається для кожного знайденого файлу)."""
        self.files_scanned = files_found
        self.scanning_active = True
        # Оновлюємо дисплей кожні 10 файлів АБО кожні 0.5 секунди
        current_time = time.time()
        time_since_update = current_time - getattr(self, '_last_scan_update', 0)

        if files_found % 10 == 0 or time_since_update >= 0.5:
            self._last_scan_update = current_time
            self._update_display_now()

    def finish_scan(self, total_files: int) -> None:
        """Завершити сканування і встановити загальну кількість файлів."""
        self.scanning_active = False
        self.total_files = total_files
        self.files_scanned = total_files
        self._update_display_now()

    def update_stage_progress(self, stage: str, progress: float, elapsed_time: float) -> None:
        """Оновити прогрес конкретного етапу для поточного файлу."""
        self.current_stage_progress[stage] = {"progress": progress, "time": elapsed_time}
        self._update_display_now()

    def add_error(self, file_name: str, error_message: str) -> None:
        """Додати помилку до списку помилок."""
        timestamp = time.strftime("%H:%M:%S")
        self.error_list.append({
            "file": file_name,
            "error": error_message,
            "time": timestamp,
        })
        # Зберігати тільки останні 100 помилок
        if len(self.error_list) > 100:
            self.error_list = self.error_list[-100:]

    def start_visual(self) -> None:
        """Запустити візуальний прогрес-бар з Live display"""
        if self.use_compact_view:
            # Запустити Live display (БЕЗ прогрес-бару - він буде в _render_display)
            self.live = Live(
                self._render_display(),
                console=self.console,
                refresh_per_second=10,  # 10 FPS для плавного оновлення
                auto_refresh=True,  # ✅ Автоматичне оновлення таймера кожні 0.1 секунди
                transient=False,
                screen=False,  # Не використовувати alternate screen
            )
            self.live.start()
        else:
            # Старий вигляд: окремі етапи
            self.progress = Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TextColumn("•"),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                TextColumn("•"),
                TimeRemainingColumn(),
                console=self.console,
            )
            self.progress.start()

            # Створити завдання для кожного етапу
            for stage_name, stage_progress in self.stages.items():
                stage_name_ua = self._translate_stage(stage_name)
                task_id = self.progress.add_task(
                    f"[cyan]{stage_name_ua}[/cyan]",
                    total=1,
                    visible=True,
                    completed=0
                )
                self.task_ids[stage_name] = task_id

    def set_all_totals(self, total: int) -> None:
        """Встановити total для всіх етапів після сканування"""
        for stage in self.stages.keys():
            self.set_stage_total(stage, total)

    def update_description(self, stage: str, detail: str) -> None:
        """Оновити опис етапу з деталями"""
        if self.progress and stage in self.task_ids:
            stage_name_ua = self._translate_stage(stage)
            self.progress.update(
                self.task_ids[stage],
                description=f"[cyan]{stage_name_ua}[/cyan] [dim]{detail}[/dim]"
            )

    def stop_visual(self) -> None:
        """Зупинити візуальний прогрес-бар"""
        if self.live:
            self.live.stop()
            self.live = None
        if self.progress:
            self.progress.stop()
            self.progress = None

    def _translate_stage(self, stage: str) -> str:
        """Перекласти назву етапу на українську"""
        translations = {
            "scan": "Сканування файлів",
            "dedup": "Пошук дублікатів",
            "extract": "Вилучення тексту",
            "classify": "Класифікація",
            "rename": "Перейменування",
            "inventory": "Створення звіту",
        }
        return translations.get(stage, stage)

    def set_stage_total(self, stage: str, total: int) -> None:
        if stage in self.stages:
            self.stages[stage].total = total
            # Оновити візуальний прогрес-бар
            if self.progress and stage in self.task_ids:
                self.progress.update(self.task_ids[stage], total=total)

    def increment(self, stage: str, amount: int = 1) -> None:
        if stage not in self.stages:
            return
        sp = self.stages[stage]
        sp.completed += amount
        sp.last_update = time.time()
        self.history.append((time.time(), self.percentage()))
        if len(self.history) > WINDOW:
            self.history = self.history[-WINDOW:]

        # Оновити візуальний прогрес-бар
        if self.progress:
            if self.use_compact_view and "global" in self.task_ids:
                # Оновити глобальний прогрес
                global_percent = self.percentage()
                self.progress.update(self.task_ids["global"], completed=global_percent)
                # Оновити Live display
                if self.live:
                    self._update_display_now()
            elif stage in self.task_ids:
                self.progress.update(self.task_ids[stage], completed=sp.completed)

    def update_metrics(
        self,
        duplicate_groups: int | None = None,
        duplicate_files: int | None = None,
        error_count: int | None = None,
        success_count: int | None = None,
        skipped_count: int | None = None,
        llm_requests: int | None = None,
        llm_responses: int | None = None,
    ) -> None:
        """Оновити агреговані метрики."""
        if duplicate_groups is not None:
            self.metrics.duplicate_groups = duplicate_groups
        if duplicate_files is not None:
            self.metrics.duplicate_files = duplicate_files
        if error_count is not None:
            self.metrics.error_count = error_count
        if success_count is not None:
            self.metrics.success_count = success_count
        if skipped_count is not None:
            self.metrics.skipped_count = skipped_count
        if llm_requests is not None:
            self.metrics.llm_requests = llm_requests
        if llm_responses is not None:
            self.metrics.llm_responses = llm_responses

        # Оновити Live display (з throttling)
        self._update_display_now()

    def set_current_file(
        self,
        name: str = "",
        path: str = "",
        category: str = "",
        stage: str = "",
        status: str = "",
        error_msg: str = "",
    ) -> None:
        """Встановити статус поточного файлу."""
        # Якщо це той самий файл - просто оновити статус
        if name and name == self.current_file.name:
            self.current_file.category = category or self.current_file.category
            self.current_file.stage = stage or self.current_file.stage
            self.current_file.status = status or self.current_file.status
            self.current_file.error_msg = error_msg or self.current_file.error_msg

            # Оновити Live display
            if self.live and self.use_compact_view:
                self._update_display_now()
            return

        # Новий файл - скинути все
        self.current_file.name = name
        self.current_file.path = path
        self.current_file.category = category
        self.current_file.stage = stage
        self.current_file.status = status
        self.current_file.error_msg = error_msg
        self.current_file.hex_id = ""
        self.current_file.sha_hash = ""
        self.current_file.size = 0
        self.current_file.modified_time = 0

        # Генерувати hex ID для нового файлу
        if name:
            self.current_file.hex_id = generate_hex_id(self.hex_counter)
            self.hex_counter += 1

        # Отримати розмір та час модифікації (ШВИДКО)
        if path:
            from pathlib import Path
            file_path = Path(path)
            if file_path.exists():
                self.current_file.size = file_path.stat().st_size
                self.current_file.modified_time = file_path.stat().st_mtime
                # SHA hash обчислимо ПІЗНІШЕ, асинхронно
                # Поки що просто перші 6 символів з hex_id
                self.current_file.sha_hash = f"{self.hex_counter:06x}"

        # Оновити Live display
        if self.live and self.use_compact_view:
            self._update_display_now()

    def add_to_log(
        self,
        status: str,
        duplicate_info: str = "",
        text_length: int = 0,
        llm_response: str = "",
        category: str = "",
        destination: str = "",
        processing_time: Dict[str, float] = None,
    ) -> None:
        """Додати поточний файл до логу оброблених."""
        if not self.current_file.name:
            return

        entry = FileLogEntry(
            hex_id=self.current_file.hex_id,
            timestamp=time.strftime("%H:%M:%S"),
            filename=self.current_file.name,
            size=self.current_file.size,
            modified_date=format_date(self.current_file.modified_time),
            sha_hash=self.current_file.sha_hash,
            status=status,
            duplicate_info=duplicate_info,
            text_length=text_length,
            llm_response=llm_response,
            category=category,
            destination=destination,
            processing_time=processing_time or {},
        )

        self.file_log.append(entry)
        self.file_log = self.file_log[-500:]  # ✅ Зберігати тільки останні 500 файлів

        # Збільшити лічильник оброблених файлів
        self.files_processed += 1

        # Оновити метрики успішності
        if status == "success":
            self.metrics.success_count += 1
        elif status == "error":
            self.metrics.error_count += 1
        elif status == "skipped":
            self.metrics.skipped_count += 1

        # Оновити Live display
        if self.live and self.use_compact_view:
            self._update_display_now()

    def populate_queue(self, file_paths: List[str]) -> None:
        """Заповнити чергу файлів - зберігає ВСІ файли, показує тільки 5."""
        from pathlib import Path
        from urllib.parse import unquote

        self.all_files = file_paths
        self.total_files = len(file_paths)
        self.current_file_index = 0
        self.file_queue.clear()

        # Заповнити тільки перші 5 файлів
        self._update_queue()

    def _update_queue(self) -> None:
        """Оновити чергу - показати наступні 5 файлів."""
        from pathlib import Path
        from urllib.parse import unquote

        self.file_queue.clear()

        # Показати наступні 5 файлів після поточного
        start_idx = self.current_file_index
        end_idx = min(start_idx + 5, len(self.all_files))

        for i in range(start_idx, end_idx):
            file_path = self.all_files[i]
            p = Path(file_path)
            if p.exists():
                # Decode URL-encoded filename
                display_name = unquote(p.name)
                qf = QueuedFile(
                    hex_id=generate_hex_id(self.hex_counter + i),
                    filename=display_name[:60] + "..." if len(display_name) > 60 else display_name,  # Обрізати довгі імена
                    size=p.stat().st_size,
                    modified_date=format_date(p.stat().st_mtime),
                )
                self.file_queue.append(qf)

        # Оновити Live display
        if self.live and self.use_compact_view:
            self._update_display_now()

    def remove_from_queue(self, filename: str) -> None:
        """Видалити файл з черги - просто переходимо до наступного."""
        # Збільшити індекс поточного файлу
        self.current_file_index += 1
        # Оновити чергу (показати наступні 5)
        self._update_queue()

    def _render_detailed_current_file(self) -> List[Text]:
        """Відрендерити детальний вигляд поточного файлу з прогрес-барами."""
        lines = []

        # Отримати ширину консолі
        terminal_width = self.console.width
        max_filename_width = max(40, terminal_width - 40)  # Мінімум 40, максимум terminal_width - 40

        # Заголовок файлу (обрізати якщо занадто довгий)
        file_icon = "⚙️" if self.current_file.status == "processing" else "✅" if self.current_file.status == "success" else "❌"
        timestamp = time.strftime("%H:%M:%S")
        filename = self.current_file.name
        if len(filename) > max_filename_width:
            filename = filename[:max_filename_width - 3] + "..."
        header = f"[{file_icon}][{timestamp}][{self.current_file.hex_id}] {filename}"
        lines.append(Text.from_markup(header, overflow="ellipsis"))

        # Метадані файлу
        if self.current_file.size > 0:
            size_str = format_file_size(self.current_file.size)
            date_str = format_date(self.current_file.modified_time) if self.current_file.modified_time else "—"
            sha_preview = self.current_file.sha_hash[:6] if self.current_file.sha_hash else "—"
            meta_line = f"├─ 📏 {size_str} │ 📅 {date_str} │ 🔒 SHA-256: {sha_preview}..."
            lines.append(Text.from_markup(meta_line, overflow="crop"))

        # Прогрес-бари для кожного етапу (адаптивна ширина)
        # Визначити ширину прогрес-бару в залежності від ширини терміналу
        bar_width = min(20, max(10, terminal_width - 60))  # Від 10 до 20 символів

        stages_order = ["dedup", "extract", "classify", "rename"]
        stage_icons = {
            "dedup": "🔍 Duplicate scan",
            "extract": "📝 Text extract  ",
            "classify": "🤖 LLM classify  ",
            "rename": "✏️  Rename file   ",
        }

        for stage in stages_order:
            stage_data = self.current_stage_progress.get(stage)
            if stage_data:
                progress = stage_data.get("progress", 0.0)
                elapsed = stage_data.get("time", 0.0)

                # Прогрес-бар (адаптивна ширина)
                filled = int(progress * bar_width)
                bar = "█" * filled + "░" * (bar_width - filled)

                percent = int(progress * 100)
                time_str = f"{elapsed:.2f}s" if progress >= 1.0 else f"{elapsed:.2f}s..."

                icon = stage_icons.get(stage, f"{stage}")
                stage_line = f"├─ {icon} {bar} {percent:3d}% [{time_str}]"
                lines.append(Text.from_markup(stage_line, overflow="crop"))
            elif self.current_file.stage == stage:
                # Поточний етап але без даних - показуємо що чекаємо
                icon = stage_icons.get(stage, f"{stage}")
                bar = "░" * bar_width
                stage_line = f"├─ {icon} {bar}   0% [waiting...]"
                lines.append(Text.from_markup(stage_line, overflow="crop"))

        # Категорія якщо є (обрізати якщо занадто довга)
        if self.current_file.category:
            category = self.current_file.category
            max_category_width = max(20, terminal_width - 30)
            if len(category) > max_category_width:
                category = category[:max_category_width - 3] + "..."
            lines.append(Text.from_markup(f"└─ 🏷️  CATEGORY: {category}", overflow="crop"))

        # Помилка якщо є (обрізати якщо занадто довга)
        if self.current_file.error_msg:
            error = self.current_file.error_msg
            max_error_width = max(30, int(terminal_width * 0.90) - 20)  # 90% ширини - 20 для іконок
            if len(error) > max_error_width:
                error = error[:max_error_width - 3] + "..."
            error_text = Text.from_markup(f"└─ ❌ ПОМИЛКА: {error}")
            error_text.overflow = "ellipsis"
            lines.append(error_text)

        return lines

    def _render_display(self) -> Group:
        """Відрендерити спрощений дисплей БЕЗ LOG та QUEUE."""
        components = []

        # Отримати розмір терміналу
        terminal_width = self.console.width
        terminal_height = self.console.height

        # ═══════════════════════════════════════════════════════════
        # HEADER: ASCII LOGO + СТАТУС (тільки якщо вистачає місця)
        # ═══════════════════════════════════════════════════════════
        # Логотип показуємо тільки якщо ширина > 80
        if terminal_width >= 80:
            logo = render_ascii_logo(self.scan_dir or "/")
            components.append(logo)

        # ═══════════════════════════════════════════════════════════
        # ЗАГАЛЬНИЙ ПРОГРЕС-БАР (В ОДНУ ЛІНІЮ)
        # ═══════════════════════════════════════════════════════════
        overall_progress = self.percentage() / 100.0  # Від 0.0 до 1.0
        progress_bar_width = int(terminal_width * 0.70)  # 70% для бару
        filled = int(overall_progress * progress_bar_width)
        bar = "█" * filled + "░" * (progress_bar_width - filled)

        # Все в одну лінію: бар + відсоток + кількість
        progress_text = f"[{THEME.warning}]{bar}[/] [{THEME.number_primary}]{overall_progress*100:.1f}%[/] [{THEME.dim_text}]({self.files_processed}/{self.total_files} файлів)[/]"

        progress_panel = Panel(
            Text.from_markup(progress_text, overflow="crop"),
            title=f"[{THEME.header}]ЗАГАЛЬНИЙ ПРОГРЕС[/]",
            border_style=THEME.success if overall_progress >= 1.0 else THEME.warning,
            padding=(0, 1),
            expand=False,
            width=int(terminal_width * 0.95),
        )
        components.append(progress_panel)

        # Статистика в хедері
        elapsed = time.time() - self.start_time
        elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))

        # Під час сканування показуємо кількість знайдених файлів
        if self.scanning_active:
            files_progress = f"Сканування... знайдено {self.files_scanned} файлів"
        else:
            files_progress = f"{self.files_processed}/{self.total_files}" if self.total_files > 0 else "0/0"

        # Компактний статус-бар в 1 рядок: відсотки + кількість + час + метрики
        progress_percent = f"{overall_progress:.1f}%"
        if terminal_width < 80:
            # Компактний вигляд
            status_line = f"[{THEME.number_primary}]{progress_percent}[/] [{THEME.info}]({files_progress})[/] │ ⏱️ {elapsed_str} │ [{THEME.success}]✅{self.metrics.success_count}[/] [{THEME.warning}]⚠️{self.metrics.duplicate_groups}[/] [{THEME.error}]❌{self.metrics.error_count}[/]"
        else:
            # Повний вигляд в 1 рядок
            llm_part = ""
            if self.metrics.llm_requests > 0:
                llm_part = f" │ [{THEME.llm_request}]🤖 {self.metrics.llm_requests}/{self.metrics.llm_responses}[/]"
            status_line = f"[{THEME.number_primary}]{progress_percent}[/] [{THEME.info}]({files_progress})[/] │ ⏱️ {elapsed_str} │ [{THEME.success}]✅ {self.metrics.success_count}[/] │ [{THEME.warning}]⚠️ {self.metrics.duplicate_groups}[/] │ [{THEME.error}]❌ {self.metrics.error_count}[/]{llm_part}"

        header_panel = Panel(
            Text.from_markup(status_line, overflow="crop"),
            title=f"[{THEME.header}]СТАТУС[/]",
            border_style=THEME.border,
            padding=(0, 1),
            expand=False,
            width=int(terminal_width * 0.95),  # 95% від ширини терміналу
        )
        components.append(header_panel)

        # ═══════════════════════════════════════════════════════════
        # ПОТОЧНИЙ ФАЙЛ (ДЕТАЛЬНО З ПРОГРЕС-БАРАМИ)
        # ═══════════════════════════════════════════════════════════
        if self.current_file.name:
            current_lines = self._render_detailed_current_file()

            current_panel = Panel(
                Group(*current_lines) if current_lines else Text("Очікування файлів...", style="dim"),
                title=f"[{THEME.warning}]⚙️  ПОТОЧНИЙ ФАЙЛ[/]",
                border_style=THEME.warning,
                padding=(0, 1),
                expand=False,
                width=int(terminal_width * 0.95),  # 95% від ширини терміналу
            )
            components.append(current_panel)

        # ═══════════════════════════════════════════════════════════
        # FOOTER: Детальна статистика (тільки якщо вистачає висоти)
        # ═══════════════════════════════════════════════════════════
        # Показуємо footer тільки якщо висота терміналу > 20 рядків
        if terminal_height >= 20:
            stats_table = Table.grid(padding=(0, 1))

            # Адаптивна статистика
            if terminal_width < 80:
                # Компактний вигляд
                stats_table.add_row(
                    f"[{THEME.success}]✅ {self.metrics.success_count}[/]",
                    f"[{THEME.warning}]⚠️ {self.metrics.duplicate_groups}[/]",
                    f"[{THEME.error}]❌ {self.metrics.error_count}[/]",
                )
                if self.metrics.llm_requests > 0:
                    success_rate = (self.metrics.success_count / max(self.files_processed, 1) * 100)
                    stats_table.add_row(
                        f"[{THEME.llm_request}]🤖 {self.metrics.llm_requests}[/]",
                        f"[{THEME.success}]🔥 {success_rate:.0f}%[/]",
                        "",
                    )
            else:
                # Повний вигляд
                stats_table.add_row(
                    f"[{THEME.success}]✅ Completed: [{THEME.number_success}]{self.metrics.success_count}[/]",
                    f"[{THEME.warning}]⚠️  Duplicates: [{THEME.number_primary}]{self.metrics.duplicate_groups}[/]",
                    f"[{THEME.error}]❌ Errors: [{THEME.number_error}]{self.metrics.error_count}[/]",
                    f"[{THEME.info}]⏳ Pending: [{THEME.number_primary}]{self.total_files - self.files_processed}[/]",
                )

                if self.metrics.llm_requests > 0:
                    stats_table.add_row(
                        f"[{THEME.llm_request}]🤖 LLM Requests: [{THEME.number_primary}]{self.metrics.llm_requests}[/]",
                        f"[{THEME.llm_response}]💬 LLM Responses: [{THEME.number_primary}]{self.metrics.llm_responses}[/]",
                        f"[{THEME.success}]🔥 Success Rate: [{THEME.number_success}]{(self.metrics.success_count / max(self.files_processed, 1) * 100):.0f}%[/]",
                        "",
                    )

            footer_panel = Panel(
                stats_table,
                title=f"[{THEME.header}]📈 СТАТИСТИКА[/]" if terminal_width < 80 else f"[{THEME.header}]📈 СТАТИСТИКА СЕСІЇ[/]",
                border_style=THEME.border,
                padding=(0, 1),
                expand=False,
                width=int(terminal_width * 0.95),  # 95% від ширини терміналу
            )
            components.append(footer_panel)

        return Group(*components)

    def percentage(self) -> float:
        """Розрахунок прогресу лінійно від кількості оброблених файлів."""
        if self.total_files == 0:
            return 0.0
        # Лінійний прогрес: скільки файлів оброблено від загальної кількості
        return min(100.0, max(0.0, (self.files_processed / self.total_files) * 100.0))

    def eta_seconds(self) -> float | None:
        if len(self.history) < 2:
            return None
        (t0, p0), (t1, p1) = self.history[0], self.history[-1]
        delta_p = p1 - p0
        if delta_p <= 0:
            return None
        delta_t = t1 - t0
        remaining = 100.0 - p1
        return (delta_t / delta_p) * remaining if remaining > 0 else 0.0

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        return {
            stage: {
                "completed": sp.completed,
                "total": sp.total,
                "weight": sp.weight,
            }
            for stage, sp in self.stages.items()
        }

    def print_error_report(self) -> None:
        """Надрукувати звіт по помилках в кінці."""
        if not self.error_list:
            return

        from rich.table import Table
        from rich.panel import Panel

        console = Console()
        console.print(f"\n{markup(THEME.error, '═══ ЗВІТ ПО ПОМИЛКАХ ═══')}\n")

        error_table = Table(show_header=True, header_style=THEME.header, show_lines=True, border_style=THEME.error)
        error_table.add_column("Час", style=THEME.dim_text, width=10)
        error_table.add_column("Файл", style=THEME.file_name, max_width=50)
        error_table.add_column("Помилка", style=THEME.error, max_width=60)

        for error in self.error_list:
            error_table.add_row(
                error["time"],
                error["file"],
                error["error"]
            )

        # Отримати ширину терміналу для симетрії з іншими панелями
        terminal_width = console.width

        panel = Panel(
            error_table,
            title=f"[{THEME.error}]❌ Помилки обробки ({len(self.error_list)} файлів)[/]",
            border_style=THEME.error,
            padding=(1, 2),
            width=int(terminal_width * 0.95),  # Та ж ширина як у всіх панелей
        )
        console.print(panel)
        console.print()

