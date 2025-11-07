"""Динамічний TUI інтерфейс з реальним прогресом та статистикою."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text

from app.theme import THEME


@dataclass
class FileProcessingInfo:
    """Інформація про обробку поточного файлу."""
    filename: str = ""
    duplicates_status: str = "..."
    classification: str = "..."
    llm_requests: int = 0
    llm_responses: int = 0
    llm_error: bool = False


@dataclass
class SessionStats:
    """Статистика сесії обробки."""
    total_files: int = 0
    processed_files: int = 0
    duplicate_groups: int = 0
    duplicate_files: int = 0
    llm_total_requests: int = 0
    llm_total_responses: int = 0
    llm_tokens_sent: int = 0
    llm_tokens_received: int = 0
    current_stage: str = "Ініціалізація"


class LiveTUI:
    """Живий TUI інтерфейс з динамічним оновленням."""

    def __init__(self, console: Optional[Console] = None):
        self.console = console or Console()
        self.stats = SessionStats()
        self.current_file = FileProcessingInfo()
        self.live: Optional[Live] = None
        self.progress: Optional[Progress] = None
        self.progress_task = None
        self._lock = threading.Lock()
        self._running = False

    def start(self, total_files: int) -> None:
        """Запустити живий інтерфейс."""
        with self._lock:
            self.stats.total_files = total_files
            self.stats.processed_files = 0
            self._running = True

            # Створити прогрес-бар
            self.progress = Progress(
                TextColumn(f"[bold {THEME.progress_text}]{{task.description}}"),
                BarColumn(bar_width=50, complete_style=THEME.success, finished_style=THEME.success),
                TextColumn(f"[bold {THEME.number_primary}]{{task.completed}}/{{task.total}}"),
                TextColumn(f"[bold {THEME.progress_percent}]{{task.percentage:>3.0f}}%"),
                TimeElapsedColumn(),
                console=self.console,
            )
            self.progress_task = self.progress.add_task(
                "Обробка файлів...",
                total=total_files,
                completed=0,
            )

            # Запустити Live Display
            self.live = Live(
                self._render(),
                console=self.console,
                refresh_per_second=4,
                transient=False,
            )
            self.live.start()

    def stop(self) -> None:
        """Зупинити живий інтерфейс."""
        with self._lock:
            self._running = False
            if self.live:
                self.live.stop()
                self.live = None
            if self.progress:
                self.progress.stop()
                self.progress = None

    def update_stage(self, stage: str) -> None:
        """Оновити поточний етап."""
        with self._lock:
            self.stats.current_stage = stage
            self._refresh()

    def start_file(self, filename: str) -> None:
        """Почати обробку нового файлу."""
        with self._lock:
            # Очистити дані попереднього файлу
            self.current_file = FileProcessingInfo(filename=filename)
            self._refresh()

    def update_duplicates(self, status: str) -> None:
        """Оновити статус дублікатів."""
        with self._lock:
            self.current_file.duplicates_status = status
            self._refresh()

    def update_classification(self, category: str) -> None:
        """Оновити класифікацію."""
        with self._lock:
            self.current_file.classification = category
            self._refresh()

    def update_llm(self, requests: int = 0, responses: int = 0, error: bool = False) -> None:
        """Оновити LLM статистику для поточного файлу."""
        with self._lock:
            if requests > 0:
                self.current_file.llm_requests += requests
                self.stats.llm_total_requests += requests
            if responses > 0:
                self.current_file.llm_responses += responses
                self.stats.llm_total_responses += responses
            if error:
                self.current_file.llm_error = True
            self._refresh()

    def update_llm_tokens(self, sent: int = 0, received: int = 0) -> None:
        """Оновити токени LLM."""
        with self._lock:
            self.stats.llm_tokens_sent += sent
            self.stats.llm_tokens_received += received
            self._refresh()

    def finish_file(self) -> None:
        """Завершити обробку файлу."""
        with self._lock:
            self.stats.processed_files += 1
            if self.progress and self.progress_task is not None:
                self.progress.update(self.progress_task, completed=self.stats.processed_files)
            # Очистити поточний файл після завершення
            self.current_file = FileProcessingInfo()
            self._refresh()

    def add_duplicate_group(self, files_count: int = 0) -> None:
        """Додати групу дублікатів."""
        with self._lock:
            self.stats.duplicate_groups += 1
            if files_count > 0:
                self.stats.duplicate_files += files_count
            self._refresh()

    def _refresh(self) -> None:
        """Оновити відображення."""
        if self.live and self._running:
            self.live.update(self._render())

    def _render(self) -> Group:
        """Відрендерити інтерфейс."""
        # Статус-бар
        status_table = Table.grid(padding=(0, 2))
        status_table.add_column(style=f"bold {THEME.progress_percent}")
        status_table.add_column(style=f"bold {THEME.info}")
        status_table.add_column(style=f"bold {THEME.duplicate}")

        progress_text = f"{self.stats.processed_files}/{self.stats.total_files}"
        percentage = (
            int(100 * self.stats.processed_files / self.stats.total_files)
            if self.stats.total_files > 0
            else 0
        )
        status_table.add_row(
            f"📊 Прогрес: {progress_text} ({percentage}%)",
            f"📍 Етап: {self.stats.current_stage}",
            f"🔍 Дублікатів: {self.stats.duplicate_groups} груп",
        )

        status_panel = Panel(
            status_table,
            title=f"[bold {THEME.title}]СТАТУС ОБРОБКИ",
            border_style=THEME.border,
            padding=(0, 1),
        )

        # Панель поточного файлу
        if self.current_file.filename:
            file_table = Table.grid(padding=(0, 1))
            file_table.add_column("Параметр", style=f"dim {THEME.info}")
            file_table.add_column("Значення", style="bold")

            # Назва файлу
            file_table.add_row(
                "📄 Файл:",
                Text(self.current_file.filename, style=f"bold {THEME.file_name}"),
            )

            # Дублікати
            dup_color = THEME.success if "немає" in self.current_file.duplicates_status.lower() else THEME.warning
            file_table.add_row(
                "🔎 Дублікати:",
                Text(self.current_file.duplicates_status, style=dup_color),
            )

            # Класифікація
            file_table.add_row(
                "🏷️  Категорія:",
                Text(self.current_file.classification, style=f"bold {THEME.category}"),
            )

            # LLM статус
            llm_status = f"Запитів: {self.current_file.llm_requests} | Відповідей: {self.current_file.llm_responses}"
            if self.current_file.llm_error:
                llm_status += " | ❌ Помилка"
            llm_color = THEME.error if self.current_file.llm_error else THEME.llm_request
            file_table.add_row(
                "🤖 LLM:",
                Text(llm_status, style=llm_color),
            )

            current_file_panel = Panel(
                file_table,
                title=f"[bold {THEME.progress_percent}]ПОТОЧНИЙ ФАЙЛ",
                border_style=THEME.progress_percent,
                padding=(0, 1),
            )
        else:
            current_file_panel = Panel(
                Text("Очікування файлу...", style="dim"),
                title=f"[bold {THEME.progress_percent}]ПОТОЧНИЙ ФАЙЛ",
                border_style=f"dim {THEME.progress_percent}",
                padding=(0, 1),
            )

        # Прогрес-бар
        if self.progress:
            progress_panel = Panel(
                self.progress,
                title=f"[bold {THEME.progress_bar}]ПРОГРЕС",
                border_style=THEME.progress_bar,
                padding=(0, 1),
            )
        else:
            progress_panel = Panel("", border_style="dim")

        # Об'єднати всі компоненти
        return Group(
            status_panel,
            current_file_panel,
            progress_panel,
        )

    def show_final_stats(self) -> None:
        """Показати фінальну статистику."""
        self.stop()

        # Створити таблицю статистики
        stats_table = Table(title=f"[bold {THEME.success}]ПІДСУМКОВА СТАТИСТИКА СЕСІЇ", show_header=False)
        stats_table.add_column("Параметр", style=f"bold {THEME.header}", width=40)
        stats_table.add_column("Значення", style=f"bold {THEME.number_primary}", justify="right")

        stats_table.add_row("📊 Загальна кількість файлів", str(self.stats.total_files))
        stats_table.add_row("✅ Оброблено файлів", str(self.stats.processed_files))
        stats_table.add_row("🔍 Знайдено груп дублікатів", str(self.stats.duplicate_groups))
        stats_table.add_row("📁 Файлів-дублікатів", str(self.stats.duplicate_files))
        stats_table.add_row("🤖 LLM запитів (всього)", str(self.stats.llm_total_requests))
        stats_table.add_row("✅ LLM відповідей (всього)", str(self.stats.llm_total_responses))
        stats_table.add_row("📤 Токенів надіслано", f"{self.stats.llm_tokens_sent:,}")
        stats_table.add_row("📥 Токенів отримано", f"{self.stats.llm_tokens_received:,}")

        total_tokens = self.stats.llm_tokens_sent + self.stats.llm_tokens_received
        stats_table.add_row("💬 Всього токенів", f"[bold {THEME.progress_percent}]{total_tokens:,}")

        self.console.print("\n")
        self.console.print(stats_table)
        self.console.print("\n")
