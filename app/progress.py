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
        self.file_log: List[FileLogEntry] = []  # Історія оброблених файлів
        self.file_queue: List[QueuedFile] = []  # Черга файлів (ТІЛЬКИ наступні 5!)
        self.all_files: List[str] = []  # ВСІ файли для обробки
        self.current_file_index: int = 0  # Поточний індекс в all_files
        self.hex_counter = 0x7F8A  # Лічильник для генерації hex адрес
        self.files_processed: int = 0  # Скільки файлів оброблено
        self.total_files: int = 0  # Загальна кількість файлів
        self.last_update_time: float = 0  # Час останнього оновлення дисплею

    def _should_update_display(self) -> bool:
        """Перевірити чи потрібно оновлювати дисплей (throttling)."""
        current_time = time.time()
        # Оновлювати максимум раз на 0.5 секунди
        if current_time - self.last_update_time >= 0.5:
            self.last_update_time = current_time
            return True
        return False

    def _update_display_if_needed(self) -> None:
        """Оновити дисплей якщо пройшло достатньо часу."""
        if self.live and self.use_compact_view and self._should_update_display():
            self.live.update(self._render_display())

    def start_visual(self) -> None:
        """Запустити візуальний прогрес-бар з Live display"""
        if self.use_compact_view:
            # Запустити Live display (БЕЗ прогрес-бару - він буде в _render_display)
            self.live = Live(
                self._render_display(),
                console=self.console,
                refresh_per_second=2,  # Зменшено для продуктивності (було 10)
                transient=False
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
                    f"[cyan]{stage_name_ua}",
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
                    self._update_display_if_needed()
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
        self._update_display_if_needed()

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
                self._update_display_if_needed()
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
            self._update_display_if_needed()

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

        # Збільшити лічильник оброблених файлів
        self.files_processed += 1

        # Оновити метрики успішності
        if status == "success":
            self.metrics.success_count += 1
        elif status == "error":
            self.metrics.error_count += 1

        # Оновити Live display
        if self.live and self.use_compact_view:
            self._update_display_if_needed()

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
            self._update_display_if_needed()

    def remove_from_queue(self, filename: str) -> None:
        """Видалити файл з черги - просто переходимо до наступного."""
        # Збільшити індекс поточного файлу
        self.current_file_index += 1
        # Оновити чергу (показати наступні 5)
        self._update_queue()

    def _render_display(self) -> Group:
        """Відрендерити хакерський дисплей з файлами."""
        components = []

        # ═══════════════════════════════════════════════════════════
        # HEADER: ASCII LOGO + СТАТУС
        # ═══════════════════════════════════════════════════════════
        logo = render_ascii_logo(self.scan_dir or "/")
        components.append(logo)

        # Статистика в хедері
        elapsed = time.time() - self.start_time
        elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))

        # Використовуємо files_processed замість суми stages
        files_progress = f"{self.files_processed}/{self.total_files}" if self.total_files > 0 else "0/0"

        header_table = Table.grid(padding=(0, 2))
        header_table.add_row(
            f"[{THEME.info}]📊 PROCESSED: [{THEME.number_primary}]{files_progress}[/]",
            f"[{THEME.info}]⏱️  [{THEME.number_primary}]{elapsed_str}[/]",
            f"[{THEME.success}]✅ [{THEME.number_success}]{self.metrics.success_count}[/]",
            f"[{THEME.warning}]⚠️  [{THEME.number_primary}]{self.metrics.duplicate_groups}[/]",
            f"[{THEME.error}]❌ [{THEME.number_error}]{self.metrics.error_count}[/]",
            f"[{THEME.dim_text}]⏳ [{THEME.number_primary}]{len(self.file_queue)}[/]",
        )

        llm_stats = ""
        if self.metrics.llm_requests > 0:
            llm_stats = f"  │  [{THEME.llm_request}]🤖 LLM: [{THEME.number_primary}]{self.metrics.llm_requests}/{self.metrics.llm_responses}[/]"

        header_panel = Panel(
            Group(header_table, Text(llm_stats, overflow="ignore")),
            border_style=THEME.border,
            padding=(0, 1),
        )
        components.append(header_panel)

        # ═══════════════════════════════════════════════════════════
        # PROCESSING LOG: Останні 10 файлів
        # ═══════════════════════════════════════════════════════════
        if self.file_log:
            log_lines = []
            # Показати останні 10 файлів
            for entry in self.file_log[-10:]:
                entry_lines = render_file_log_entry(entry, show_details=True)
                for line in entry_lines:
                    log_lines.append(Text.from_markup(line))
                log_lines.append(Text(""))  # Порожній рядок між файлами

            log_panel = Panel(
                Group(*log_lines) if log_lines else Text("Очікування файлів...", style="dim"),
                title=f"[{THEME.header}]📜 PROCESSING LOG[/]",
                border_style=THEME.decoration,
                padding=(0, 1),
            )
            components.append(log_panel)

        # ═══════════════════════════════════════════════════════════
        # CURRENTLY PROCESSING: Поточний файл
        # ═══════════════════════════════════════════════════════════
        if self.current_file.name:
            # Зібрати прогрес по етапах
            stages_progress = {}
            for stage_name, sp in self.stages.items():
                stages_progress[stage_name] = (sp.completed, sp.total)

            current_lines = render_current_file(self.current_file, stages_progress)
            current_texts = [Text.from_markup(line) for line in current_lines]

            current_panel = Panel(
                Group(*current_texts),
                title=f"[{THEME.processing}]⚙️  CURRENTLY PROCESSING[/]",
                border_style=THEME.processing,
                padding=(0, 1),
            )
            components.append(current_panel)

        # ═══════════════════════════════════════════════════════════
        # QUEUE: Наступні 5 файлів
        # ═══════════════════════════════════════════════════════════
        if self.file_queue:
            queue_lines = render_queue(self.file_queue)
            queue_texts = [Text.from_markup(line) for line in queue_lines]

            queue_panel = Panel(
                Group(*queue_texts) if queue_texts else Text("Черга порожня", style="dim"),
                title=f"[{THEME.dim_text}]⏳ QUEUE (next 5 files)[/]",
                border_style=THEME.separator,
                padding=(0, 1),
            )
            components.append(queue_panel)

        # ═══════════════════════════════════════════════════════════
        # FOOTER: Детальна статистика
        # ═══════════════════════════════════════════════════════════
        stats_table = Table.grid(padding=(0, 2))
        stats_table.add_row(
            f"[{THEME.success}]✅ Completed: [{THEME.number_success}]{self.metrics.success_count}[/]",
            f"[{THEME.warning}]⚠️  Duplicates: [{THEME.number_primary}]{self.metrics.duplicate_groups}[/]",
            f"[{THEME.error}]❌ Errors: [{THEME.number_error}]{self.metrics.error_count}[/]",
            f"[{THEME.info}]⏳ Pending: [{THEME.number_primary}]{len(self.file_queue)}[/]",
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
            title=f"[{THEME.header}]📈 SESSION STATISTICS[/]",
            border_style=THEME.border,
            padding=(0, 1),
        )
        components.append(footer_panel)

        return Group(*components)

    def percentage(self) -> float:
        total_weight = sum(sp.weight for sp in self.stages.values())
        if not total_weight:
            return 0.0
        acc = 0.0
        for sp in self.stages.values():
            if sp.total:
                acc += sp.weight * min(sp.completed / sp.total, 1.0)
        return min(100.0, max(0.0, (acc / total_weight) * 100.0))

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

