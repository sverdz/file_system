"""Хакерський інтерфейс для відображення прогресу обробки файлів."""
from __future__ import annotations

import time
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .theme import THEME, markup


def generate_hex_id(counter: int) -> str:
    """Генерувати hex адресу для файлу."""
    return f"0x{counter:04X}"


def calculate_sha256(file_path: str) -> str:
    """Обчислити SHA-256 хеш файлу (перші 6 символів)."""
    try:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()[:6]
    except Exception:
        return "------"


def format_file_size(size_bytes: int) -> str:
    """Форматувати розмір файлу."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_date(timestamp: float) -> str:
    """Форматувати дату у формат DD.MM.YYYY HH:MM."""
    if timestamp == 0:
        return "N/A"
    dt = datetime.fromtimestamp(timestamp)
    return dt.strftime("%d.%m.%Y %H:%M")


def render_ascii_logo(scan_dir: str) -> Text:
    """Створити ASCII логотип з поточною папкою."""
    logo = f"""  _____ ___ _     _____   ____  ____   ___   ____
 |  ___|_ _| |   | ____| |  _ \\|  _ \\ / _ \\ / ___|
 | |_   | || |   |  _|   | |_) | |_) | | | | |
 |  _|  | || |___| |___  |  __/|  _ <| |_| | |___
 |_|   |___|_____|_____| |_|   |_| \\_\\\\___/ \\____|

 📁 Scanning: {scan_dir}"""

    text = Text()
    for line in logo.split("\n"):
        if "📁 Scanning:" in line:
            text.append("   📁 Scanning: ", style=THEME.header)
            text.append(scan_dir, style=f"bold {THEME.file_path}")
        else:
            text.append(line, style=THEME.warning)
        text.append("\n")

    return text


def render_progress_bar(percentage: float, width: int = 20) -> str:
    """Створити прогрес-бар заданої ширини."""
    filled = int(width * percentage / 100)
    empty = width - filled
    return f"[{THEME.progress_bar}]{'█' * filled}[/][dim white]{'░' * empty}[/]"


def render_file_log_entry(entry, show_details: bool = True) -> List[str]:
    """Відрендерити запис у лозі файлу."""
    lines = []

    # Статус іконка
    status_icons = {
        "success": "✅",
        "error": "❌",
        "duplicate": "⚠️",
        "skipped": "⏭️"
    }
    status_colors = {
        "success": THEME.success,
        "error": THEME.error,
        "duplicate": THEME.warning,
        "skipped": THEME.info
    }

    icon = status_icons.get(entry.status, "📄")
    color = status_colors.get(entry.status, THEME.info)

    # Заголовок файлу
    header = f"[{color}]{icon}[/][{THEME.hex_address}][{entry.hex_id}][/][{THEME.dim_text}][{entry.timestamp}][/] [{THEME.file_name}]{entry.filename}[/]"
    if entry.status == "duplicate":
        header += f" [{THEME.duplicate}][DUPLICATE!][/]"

    lines.append(header)

    if show_details:
        # Деталі файлу
        size_str = format_file_size(entry.size)
        details = f"├─ 📏 {size_str}  │  📅 {entry.modified_date}  │  🔒 SHA-256: [{THEME.sha_hash}]{entry.sha_hash}...[/]"
        lines.append(details)

        # Прогрес обробки
        if "dedup" in entry.processing_time:
            dedup_time = entry.processing_time["dedup"]
            prog = render_progress_bar(100, 20)
            lines.append(f"├─ 🔍 Duplicate scan {prog} 100% [{THEME.dim_text}][{dedup_time:.2f}s][/]")

            if entry.duplicate_info:
                lines.append(f"│    └─ [{THEME.warning}]⚠️  {entry.duplicate_info}[/]")

        if entry.status != "duplicate" and "extract" in entry.processing_time:
            extract_time = entry.processing_time["extract"]
            prog = render_progress_bar(100, 20)
            chars_info = f" → {entry.text_length:,} chars" if entry.text_length > 0 else ""
            lines.append(f"├─ 📝 Text extract   {prog} 100% [{THEME.dim_text}][{extract_time:.2f}s][/]{chars_info}")

        if entry.status != "duplicate" and "classify" in entry.processing_time:
            classify_time = entry.processing_time["classify"]
            prog = render_progress_bar(100, 20)
            lines.append(f"├─ 🤖 LLM classify   {prog} 100% [{THEME.dim_text}][{classify_time:.2f}s][/]")

            if entry.llm_response:
                lines.append(f"│    └─ [{THEME.llm_response}]💬 \"{entry.llm_response}\"[/]")

        if entry.category and entry.status == "success":
            dest = f" → {entry.destination}" if entry.destination else ""
            lines.append(f"└─ 🏷️  [{THEME.category}]CATEGORY: {entry.category}[/]{dest}")
        elif entry.status == "duplicate":
            lines.append(f"└─ [{THEME.warning}]⏭️  SKIPPED: Duplicate detected[/]")
        elif entry.status == "error":
            lines.append(f"└─ [{THEME.error}]❌ ERROR: Processing failed[/]")

    return lines


def render_current_file(current_file, stages_progress: Dict[str, tuple[int, int]]) -> List[str]:
    """Відрендерити поточний файл що обробляється."""
    lines = []

    if not current_file.name:
        return lines

    # Заголовок
    icon = "⚙️"
    header = f"[{THEME.processing}]{icon}[/][{THEME.hex_address}][{current_file.hex_id}][/][{THEME.dim_text}][{time.strftime('%H:%M:%S')}][/] [{THEME.file_name}]{current_file.name}[/] [{THEME.processing}][PROCESSING...][/]"
    lines.append(header)

    # Деталі
    size_str = format_file_size(current_file.size)
    modified = format_date(current_file.modified_time)
    details = f"├─ 📏 {size_str}  │  📅 {modified}  │  🔒 SHA-256: [{THEME.sha_hash}]{current_file.sha_hash}...[/]"
    lines.append(details)

    # Прогрес по етапах
    stage_names = {
        "dedup": "🔍 Duplicate scan",
        "extract": "📝 Text extract  ",
        "classify": "🤖 LLM classify  "
    }

    for stage_key, stage_name in stage_names.items():
        if stage_key in stages_progress:
            completed, total = stages_progress[stage_key]
            if total > 0:
                percentage = (completed / total) * 100
                prog = render_progress_bar(percentage, 20)
                status = f"{percentage:>3.0f}%"
                if percentage >= 100:
                    status += f" [{THEME.dim_text}][done][/]"
                else:
                    status += f" [{THEME.processing}][processing...][/]"
                lines.append(f"├─ {stage_name} {prog} {status}")

    # Статус або помилка
    if current_file.error_msg:
        lines.append(f"└─ [{THEME.error}]⚠️  ERROR: {current_file.error_msg}[/]")
    elif current_file.category:
        lines.append(f"└─ 🏷️  [{THEME.category}]Classified as: {current_file.category}[/]")
    else:
        lines.append(f"└─ [{THEME.processing}]⏳ Processing...[/]")

    return lines


def render_queue(queue_files: List) -> List[str]:
    """Відрендерити чергу файлів."""
    lines = []

    for qf in queue_files[:5]:  # Показати тільки перші 5
        size_str = format_file_size(qf.size).ljust(8)
        line = f"[{THEME.dim_text}]⏳[/][{THEME.hex_address}][{qf.hex_id}][/] [{THEME.file_name}]{qf.filename:40s}[/] │ [{THEME.number_primary}]{size_str}[/] │  {qf.modified_date}"
        lines.append(line)

    return lines
