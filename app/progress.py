"""Progress tracking utilities with ETA estimation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple, Optional

from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.layout import Layout
from rich.live import Live
from rich.text import Text

from .theme import THEME, markup, format_number, format_percent, format_status

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


class ProgressTracker:
    def __init__(self, stages: Dict[str, float]):
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

    def start_visual(self) -> None:
        """Запустити візуальний прогрес-бар з Live display"""
        if self.use_compact_view:
            # Створити прогрес-бар
            self.progress = Progress(
                SpinnerColumn(style=THEME.processing),
                TextColumn(f"[bold {THEME.title}]{{task.description}}"),
                BarColumn(
                    bar_width=40,
                    complete_style=THEME.progress_bar,
                    finished_style=THEME.success
                ),
                TextColumn(f"[{THEME.progress_percent}]{{task.percentage:>3.0f}}%"),
                TextColumn(f"[{THEME.number_primary}]{{task.completed}}/{{task.total}}"),
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=self.console,
            )

            # Один глобальний прогрес
            task_id = self.progress.add_task(
                "⚙️  Обробка файлів",
                total=100,
                completed=0
            )
            self.task_ids["global"] = task_id

            # Запустити Live display
            self.live = Live(
                self._render_display(),
                console=self.console,
                refresh_per_second=4,
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
                    self.live.update(self._render_display())
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

        # Оновити Live display
        if self.live and self.use_compact_view:
            self.live.update(self._render_display())

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
        self.current_file.name = name
        self.current_file.path = path
        self.current_file.category = category
        self.current_file.stage = stage
        self.current_file.status = status
        self.current_file.error_msg = error_msg

        # Оновити Live display
        if self.live and self.use_compact_view:
            self.live.update(self._render_display())

    def _render_display(self) -> Group:
        """Відрендерити живий дисплей з панелями."""
        components = []

        # Панель статусу з загальною інформацією
        status_table = Table.grid(padding=(0, 3))
        status_table.add_column(style=f"bold {THEME.info}")
        status_table.add_column(style=f"bold {THEME.success}")
        status_table.add_column(style=f"bold {THEME.duplicate}")

        # Розрахувати час сесії
        elapsed = time.time() - self.start_time
        elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))

        # Загальний прогрес
        total_completed = sum(sp.completed for sp in self.stages.values())
        total_total = sum(sp.total for sp in self.stages.values())

        status_table.add_row(
            f"⏱️  Час: {elapsed_str}",
            f"📊 Прогрес: {self.percentage():.1f}%",
            f"🔍 Дублікати: {self.metrics.duplicate_groups} груп"
        )

        status_panel = Panel(
            status_table,
            title=f"[bold {THEME.title}]╔═══ СТАТУС ОБРОБКИ ═══╗",
            border_style=THEME.border,
            padding=(0, 1),
        )
        components.append(status_panel)

        # Панель метрик
        if (self.metrics.success_count > 0 or self.metrics.error_count > 0 or
            self.metrics.llm_requests > 0):
            metrics_table = Table.grid(padding=(0, 2))
            metrics_table.add_column(style=THEME.info, width=25)
            metrics_table.add_column(style="bold", justify="right")

            if self.metrics.success_count > 0:
                metrics_table.add_row(
                    "✅ Успішно оброблено:",
                    markup(THEME.success, str(self.metrics.success_count))
                )
            if self.metrics.error_count > 0:
                metrics_table.add_row(
                    "❌ Помилок:",
                    markup(THEME.error, str(self.metrics.error_count))
                )
            if self.metrics.skipped_count > 0:
                metrics_table.add_row(
                    "⏭️  Пропущено:",
                    markup(THEME.warning, str(self.metrics.skipped_count))
                )
            if self.metrics.llm_requests > 0:
                metrics_table.add_row(
                    "🤖 LLM запитів:",
                    markup(THEME.llm_request, str(self.metrics.llm_requests))
                )
            if self.metrics.llm_responses > 0:
                metrics_table.add_row(
                    "💬 LLM відповідей:",
                    markup(THEME.llm_response, str(self.metrics.llm_responses))
                )

            metrics_panel = Panel(
                metrics_table,
                title=f"[bold {THEME.header}]📈 МЕТРИКИ",
                border_style=THEME.processing,
                padding=(0, 1),
            )
            components.append(metrics_panel)

        # Панель поточного файлу
        if self.current_file.name:
            file_table = Table.grid(padding=(0, 1))
            file_table.add_column("Параметр", style=THEME.info, width=15)
            file_table.add_column("Значення", style="bold")

            # Статус з іконкою
            status_icon = {
                "processing": "⚙️",
                "success": "✅",
                "error": "❌",
            }.get(self.current_file.status, "📄")

            status_color = {
                "processing": THEME.processing,
                "success": THEME.success,
                "error": THEME.error,
            }.get(self.current_file.status, THEME.info)

            file_table.add_row(
                "📄 Файл:",
                Text(f"{status_icon} {self.current_file.name}", style=f"bold {THEME.file_name}")
            )

            if self.current_file.stage:
                file_table.add_row(
                    "🔄 Етап:",
                    Text(self._translate_stage(self.current_file.stage), style=status_color)
                )

            if self.current_file.category:
                file_table.add_row(
                    "🏷️  Категорія:",
                    Text(self.current_file.category, style=f"bold {THEME.category}")
                )

            if self.current_file.error_msg:
                file_table.add_row(
                    "⚠️  Помилка:",
                    Text(self.current_file.error_msg, style=THEME.error)
                )

            current_file_panel = Panel(
                file_table,
                title=f"[bold {THEME.progress_percent}]🔍 ПОТОЧНИЙ ФАЙЛ",
                border_style=THEME.progress_bar,
                padding=(0, 1),
            )
            components.append(current_file_panel)

        # Прогрес-бар
        if self.progress:
            progress_panel = Panel(
                self.progress,
                title=f"[bold {THEME.success}]⏳ ПРОГРЕС ВИКОНАННЯ",
                border_style=THEME.success,
                padding=(0, 1),
            )
            components.append(progress_panel)

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

