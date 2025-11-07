"""Компактний Live TUI для відображення процесу обробки файлів."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from app.hacker_ui import format_file_size
from app.theme import THEME, format_number, format_percent


# ════════════════════════════════════════════════════════════════════════
# ДАНІ ДЛЯ ДИСПЛЕЯ
# ════════════════════════════════════════════════════════════════════════


@dataclass
class PipelineStage:
    """Стан окремого етапу обробки."""

    label: str
    total: int = 0
    completed: int = 0

    @property
    def percent(self) -> float:
        if self.total <= 0:
            return 0.0
        return max(0.0, min(100.0, (self.completed / self.total) * 100.0))


@dataclass
class CurrentFileState:
    """Інформація про файл, що обробляється зараз."""

    filename: str = ""
    hex_id: str = ""
    size_bytes: int = 0
    modified_time: float = 0.0
    sha256: str = ""
    category: str = ""
    note: str = ""
    stage_progress: Dict[str, float] = field(default_factory=dict)

    def reset(self) -> None:
        self.filename = ""
        self.hex_id = ""
        self.size_bytes = 0
        self.modified_time = 0.0
        self.sha256 = ""
        self.category = ""
        self.note = ""
        self.stage_progress.clear()


@dataclass
class DashboardMetrics:
    """Агреговані метрики для дашборду."""

    total_files: int = 0
    success_count: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    error_count: int = 0
    skipped_count: int = 0
    llm_requests: int = 0
    llm_responses: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    speed: float = 0.0
    total_size_bytes: int = 0
    output_size_bytes: int = 0
    shrinkage: float = 0.0
    avg_time: float = 0.0
    ocr_files: int = 0
    low_confidence: int = 0
    long_names_fixed: int = 0
    inventory_written: bool = False
    quarantined: int = 0
    anomalies: int = 0
    no_text_files: int = 0
    categories: Dict[str, int] = field(
        default_factory=lambda: {"finance": 0, "legal": 0, "tech": 0, "unknown": 0}
    )

    @property
    def llm_tokens_total(self) -> int:
        return self.llm_tokens_in + self.llm_tokens_out


@dataclass
class LogEntry:
    """Запис в журналі обробки."""

    lines: List[Text]


# ════════════════════════════════════════════════════════════════════════
# ДОПОМІЖНІ ФУНКЦІЇ
# ════════════════════════════════════════════════════════════════════════


def _render_ascii_logo(width: int) -> str:
    """Повернути ASCII-логотип, адаптований до ширини."""

    banner = [
        " ███████╗██╗██╗     ███████╗    INVENTORY & CLASSIFICATION PIPELINE",
        " ██╔════╝██║██║     ██╔════╝    RUN ID: --:--",
        " █████╗  ██║██║     █████╗      ROOT: ./",
        " ██╔══╝  ██║██║     ██╔══╝      TERMINAL: 120x40  MODE: RICH+EMOJI",
        " ██║     ██║███████╗███████╗    USER: OPERATOR",
    ]

    padded = []
    border_width = max(min(width, max(len(line) for line in banner) + 4), 40)
    border_line = "╔" + "═" * (border_width - 2) + "╗"
    padded.append(border_line)
    for line in banner:
        clipped = line[: border_width - 4]
        padded.append("║ " + clipped.ljust(border_width - 4) + " ║")
    padded.append("╚" + "═" * (border_width - 2) + "╝")
    return "\n".join(padded)


def _format_timestamp(seconds: float) -> str:
    if seconds <= 0:
        return "--:--:--"
    return time.strftime("%H:%M:%S", time.gmtime(seconds))


def _build_stage_bar(percent: float) -> str:
    percent = max(0.0, min(100.0, percent))
    width = 4
    filled = int(round(width * percent / 100.0))
    filled = min(width, max(0, filled))
    empty = width - filled
    return (
        f"[{THEME.bar_complete}]" + "█" * filled + f"[/{THEME.bar_complete}]"
        + f"[{THEME.bar_incomplete}]" + "░" * empty + f"[/{THEME.bar_incomplete}]"
    )


# ════════════════════════════════════════════════════════════════════════
# ОСНОВНИЙ КЛАС
# ════════════════════════════════════════════════════════════════════════


class LiveTUI:
    """Живий TUI-дешборд з компактним макетом на одному екрані."""

    DEFAULT_STAGES = (
        ("scan", "SCAN"),
        ("dedup", "DEDUP"),
        ("extract", "EXTRACT"),
        ("classify", "CLASSIFY"),
        ("rename", "RENAME"),
        ("inventory", "INVENTORY"),
    )

    def __init__(self, console: Optional[Console] = None) -> None:
        self.console = console or Console()
        self.live: Optional[Live] = None
        self._lock = threading.Lock()
        self._running = False

        self.metrics = DashboardMetrics()
        self.stages: Dict[str, PipelineStage] = {
            name: PipelineStage(label=label) for name, label in self.DEFAULT_STAGES
        }
        self.current_file = CurrentFileState()
        self.file_log: List[LogEntry] = []
        self.files_processed = 0
        self.start_time: float | None = None
        self._eta_seconds: float = 0.0
        self._hex_counter = 0x7F8A

    # ──────────────────────────── КЕРУВАННЯ ────────────────────────────
    def start(self, total_files: int) -> None:
        """Запустити Live-інтерфейс."""

        with self._lock:
            self.metrics.total_files = total_files
            self.files_processed = 0
            self.start_time = time.time()
            self._running = True

            self.live = Live(
                self._render_display(),
                console=self.console,
                refresh_per_second=4,
                transient=False,
            )
            self.live.start()

    def stop(self) -> None:
        """Зупинити Live-інтерфейс."""

        with self._lock:
            self._running = False
            if self.live:
                self.live.stop()
                self.live = None

    # ──────────────────────────── ОНОВЛЕННЯ ────────────────────────────
    def update_metrics(self, **values: float | int | bool | Dict[str, int]) -> None:
        """Оновити будь-які метрики дашборду."""

        with self._lock:
            for key, value in values.items():
                if key == "categories" and isinstance(value, dict):
                    self.metrics.categories.update(value)
                    continue
                if hasattr(self.metrics, key):
                    setattr(self.metrics, key, value)
            self._refresh()

    def update_eta(self, seconds: float) -> None:
        with self._lock:
            self._eta_seconds = max(0.0, seconds)
            self._refresh()

    def update_speed(self, files_per_second: float) -> None:
        self.update_metrics(speed=files_per_second)

    def set_stage_totals(self, totals: Dict[str, int]) -> None:
        with self._lock:
            for name, total in totals.items():
                if name in self.stages:
                    self.stages[name].total = max(0, int(total))
            self._refresh()

    def update_stage_progress(
        self, stage: str, completed: Optional[int] = None, total: Optional[int] = None
    ) -> None:
        with self._lock:
            if stage not in self.stages:
                return
            if total is not None:
                self.stages[stage].total = max(0, int(total))
            if completed is not None:
                self.stages[stage].completed = max(0, int(completed))
            self._refresh()

    def start_file(
        self,
        filename: str,
        *,
        size_bytes: int = 0,
        modified_time: float = 0.0,
        sha256: str = "",
        hex_id: Optional[str] = None,
    ) -> None:
        """Позначити початок обробки нового файлу."""

        with self._lock:
            self.current_file = CurrentFileState(
                filename=filename,
                size_bytes=size_bytes,
                modified_time=modified_time,
                sha256=sha256,
                hex_id=hex_id or self._next_hex_id(),
            )
            self._refresh()

    def update_current_file_stage(self, stage: str, percent: float) -> None:
        with self._lock:
            self.current_file.stage_progress[stage] = max(0.0, min(100.0, percent))
            self._refresh()

    def update_current_file_category(self, category: str) -> None:
        with self._lock:
            self.current_file.category = category
            self._refresh()

    def update_current_file_note(self, note: str) -> None:
        with self._lock:
            self.current_file.note = note
            self._refresh()

    def finish_file(self, status_lines: Optional[Iterable[str]] = None) -> None:
        """Завершити обробку поточного файлу та додати запис у журнал."""

        with self._lock:
            if status_lines:
                log_lines = [Text.from_markup(line) for line in status_lines]
                self.file_log.append(LogEntry(lines=log_lines))
            if self.file_log:
                # Обмежити історію останніми 50 записами
                self.file_log = self.file_log[-50:]

            self.files_processed += 1
            self.current_file.reset()
            self._refresh()

    def add_log_entry(self, lines: Iterable[str]) -> None:
        with self._lock:
            self.file_log.append(LogEntry(lines=[Text.from_markup(line) for line in lines]))
            self.file_log = self.file_log[-50:]
            self._refresh()

    # ──────────────────────────── ДОПОМОЖНІ ─────────────────────────────
    def estimated_time_remaining(self) -> float:
        return self._eta_seconds

    def _next_hex_id(self) -> str:
        value = f"0x{self._hex_counter:04X}"
        self._hex_counter += 1
        return value

    def _mini_bar(self, percent: float, width: int = 22) -> str:
        """Компактний однорядковий прогрес-бар."""

        percent = max(0.0, min(100.0, percent))
        filled = int(width * (percent / 100.0))
        empty = width - filled
        return (
            f"[{THEME.bar_complete}]" + "█" * filled + f"[/{THEME.bar_complete}]"
            + f"[{THEME.bar_incomplete}]" + "░" * empty + f"[/{THEME.bar_incomplete}]"
        )

    def _refresh(self) -> None:
        if self.live and self._running:
            self.live.update(self._render_display())

    # ──────────────────────────── РЕНДЕР ────────────────────────────────
    def _render_display(self) -> Group:
        width = self.console.size.width
        now = time.time()

        total = max(self.metrics.total_files or 0, 1)
        done = min(self.files_processed, total)
        pending = max(total - done, 0)
        global_pct = (done / total) * 100.0

        elapsed = int(now - self.start_time) if self.start_time else 0
        elapsed_str = _format_timestamp(elapsed)

        eta_sec = int(self.estimated_time_remaining())
        eta_str = _format_timestamp(eta_sec)

        logo_text = Text(_render_ascii_logo(width=width - 4), style=THEME.logo)

        header_lines: List[str] = [
            " PROCESSED: "
            f"[{THEME.number_primary}]{done}[/]/[{THEME.number_primary}]{total}[/]  "
            f"SUCCESS: [{THEME.number_success}]{self.metrics.success_count}[/]  "
            f"WARN: [{THEME.warning}]{self.metrics.duplicate_groups}[/]  "
            f"ERR: [{THEME.error}]{self.metrics.error_count}[/]  "
            f"PENDING: [{THEME.number_primary}]{pending}[/]"
        ]

        if self.metrics.llm_requests or self.metrics.llm_responses:
            header_lines.append(
                f" 🤖 LLM {self.metrics.llm_requests}/{self.metrics.llm_responses}   "
                f"TOKENS ~{format_number(self.metrics.llm_tokens_in)}→{format_number(self.metrics.llm_tokens_out)}"
            )

        header_lines.append(f"⏱ {elapsed_str}   ETA {eta_str}")
        header_lines.append(
            f" GLOBAL: {self._mini_bar(global_pct, width=32)}  {format_percent(global_pct)}   "
            f"SPEED: {format_number(self.metrics.speed or 0.0)} files/s   "
            f"SIZE: {format_file_size(self.metrics.total_size_bytes or 0)}"
        )

        header_info = Text.from_markup("\n".join(header_lines))

        header_panel = Panel(
            Group(logo_text, header_info),
            border_style=THEME.border,
            padding=(0, 1),
        )

        pipe_table = Table.grid(padding=(0, 1))
        pipe_table.expand = True
        pipe_table.add_row(f"[{THEME.title}]PIPELINE[/]", "", "", "")

        for stage_name, label in self.DEFAULT_STAGES:
            stage = self.stages[stage_name]
            pct = stage.percent
            extra = ""
            if stage_name == "dedup" and self.metrics.duplicate_groups:
                extra = f"{self.metrics.duplicate_groups} groups"
            elif stage_name == "extract" and self.metrics.ocr_files:
                extra = f"OCR {self.metrics.ocr_files}"
            elif stage_name == "classify" and self.metrics.low_confidence:
                extra = f"low-conf {self.metrics.low_confidence}"
            elif stage_name == "rename" and self.metrics.long_names_fixed:
                extra = f"long-names {self.metrics.long_names_fixed}"
            elif stage_name == "inventory" and self.metrics.inventory_written:
                extra = "inventory.xlsx"

            pipe_table.add_row(
                f"[{THEME.label}]{label}[/]",
                f"[{THEME.number_primary}]{stage.completed}/{stage.total}[/]",
                self._mini_bar(pct, width=18),
                f"[{THEME.dim_text}]{extra}[/]",
            )

        pipeline_panel = Panel(pipe_table, padding=(0, 1), border_style=THEME.border_soft)

        log_lines: List[Text] = []
        for entry in self.file_log[-6:]:
            log_lines.extend(entry.lines)

        log_panel = Panel(
            Group(*log_lines) if log_lines else Text("Очікування подій...", style=THEME.dim_text),
            title="PROCESSING LOG",
            border_style=THEME.border_soft,
            padding=(0, 1),
        )

        if self.current_file.filename:
            file_table = Table.grid(padding=(0, 1))
            file_table.add_row(
                f"[{THEME.processing}]⚙️[{THEME.dim_text}][{self.current_file.hex_id or '--'}][/][/{THEME.dim_text}]"
                f" [{THEME.file_name}]{self.current_file.filename}[/]"
            )
            file_table.add_row(
                f"SIZE: {format_file_size(self.current_file.size_bytes)}   "
                f"SHA: [{THEME.dim_text}]{(self.current_file.sha256 or '--')[:8]}...[/]"
            )

            if self.current_file.stage_progress:
                pipeline_parts = []
                for stage_name, label in self.DEFAULT_STAGES:
                    percent = self.current_file.stage_progress.get(stage_name, 0.0)
                    pipeline_parts.append(f" {label} {_build_stage_bar(percent)}")
                file_table.add_row("PIPELINE:" + "".join(pipeline_parts))

            category_line = ""
            if self.current_file.category:
                category_line = f" → [{THEME.category}]{self.current_file.category}[/]"
            if self.current_file.note:
                category_line += f"   [{THEME.dim_text}]{self.current_file.note}[/]"
            if category_line:
                file_table.add_row(category_line.strip())

            current_panel = Panel(
                file_table,
                title="CURRENTLY PROCESSING",
                border_style=THEME.border_soft,
                padding=(0, 1),
            )
        else:
            current_panel = Panel(
                Text("Немає активного файлу", style=THEME.dim_text),
                title="CURRENTLY PROCESSING",
                border_style=THEME.border_soft,
            )

        snap_table = Table.grid(padding=(0, 0))
        snap_table.add_row(f"[{THEME.title}]DATA SNAPSHOT[/]")
        categories = self.metrics.categories
        snap_table.add_row(
            f"[{THEME.label}]FINANCE[/]: {categories.get('finance', 0)}   "
            f"[{THEME.label}]LEGAL[/]: {categories.get('legal', 0)}   "
            f"[{THEME.label}]TECH[/]: {categories.get('tech', 0)}"
        )
        snap_table.add_row(
            f"[{THEME.label}]UNKNOWN[/]: {categories.get('unknown', 0)}   "
            f"DUP GROUPS: {self.metrics.duplicate_groups}   "
            f"QUARANTINED: {self.metrics.quarantined}"
        )
        snap_table.add_row(
            f"ANOMALIES: {self.metrics.anomalies}   "
            f"OCR: {self.metrics.ocr_files}   "
            f"NO-TEXT: {self.metrics.no_text_files}"
        )
        snapshot_panel = Panel(snap_table, border_style=THEME.border_soft)

        middle_row = Table.grid(expand=True)
        middle_row.add_column(ratio=3)
        middle_row.add_column(ratio=2)
        middle_row.add_row(current_panel, snapshot_panel)

        stats = Table.grid(padding=(0, 2))
        success_rate = (self.metrics.success_count / total * 100.0) if total else 0.0
        stats.add_row(
            f"SUCCESS {self.metrics.success_count} ({format_percent(success_rate)})",
            f"WARN {self.metrics.duplicate_groups}",
            f"ERR {self.metrics.error_count}",
            f"OCR {self.metrics.ocr_files}",
            f"LLM USED {self.metrics.llm_requests}",
        )
        stats.add_row(
            f"AVG/file {format_number(self.metrics.avg_time or 0.0)} s",
            f"INPUT {format_file_size(self.metrics.total_size_bytes or 0)}",
            f"OUTPUT {format_file_size(self.metrics.output_size_bytes or 0)}",
            f"SHRINK {format_percent(self.metrics.shrinkage or 0.0)}",
            "",
        )

        stats_panel = Panel(stats, border_style=THEME.border_soft, title="SESSION STATISTICS")

        return Group(
            header_panel,
            pipeline_panel,
            log_panel,
            middle_row,
            stats_panel,
        )

