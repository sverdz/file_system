"""Централізована конфігурація кольорів та стилів для темного фону."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColorTheme:
    """Кольорова схема для темного фону з високим контрастом."""

    # ═══════════════════════════════════════════════════════════
    # ОСНОВНІ КОЛЬОРИ (ВИСОКИЙ КОНТРАСТ ДЛЯ ТЕМНОГО ФОНУ)
    # ═══════════════════════════════════════════════════════════

    # Текст та інформація
    primary_text: str = "white"           # Основний текст
    secondary_text: str = "bright_white"  # Виділений текст
    dim_text: str = "cyan"                # Другорядна інформація

    # Статуси та стани
    success: str = "bright_green"         # Успішні операції
    error: str = "bright_red"             # Помилки
    warning: str = "bright_yellow"        # Попередження
    info: str = "yellow"                  # Інформація
    processing: str = "bright_magenta"    # В процесі

    # Числові показники
    number_primary: str = "bright_yellow" # Основні числа (N/k, відсотки)
    number_total: str = "bright_white"    # Загальні суми
    number_success: str = "bright_green"  # Успішні підрахунки
    number_error: str = "bright_red"      # Кількість помилок

    # ═══════════════════════════════════════════════════════════
    # КОМПОНЕНТИ ІНТЕРФЕЙСУ
    # ═══════════════════════════════════════════════════════════

    # Прогрес-бар
    progress_bar: str = "bright_green"    # Заповнення прогрес-бару
    progress_text: str = "bright_white"   # Текст прогресу
    progress_percent: str = "bright_yellow" # Відсоток виконання

    # Заголовки та рамки
    header: str = "bright_yellow"         # Заголовки секцій
    border: str = "yellow"                # Рамки та розділювачі
    title: str = "bold bright_white"      # Головні заголовки

    # Категорії файлів
    category: str = "bright_magenta"      # Назва категорії
    file_name: str = "bright_white"       # Ім'я файлу
    file_path: str = "green"              # Шлях до файлу

    # Дублікати
    duplicate: str = "bright_yellow"      # Виявлені дублікати
    duplicate_count: str = "bright_red"   # Кількість дублікатів

    # LLM та класифікація
    llm_request: str = "bright_magenta"   # LLM запити
    llm_response: str = "bright_green"    # LLM відповіді
    classification: str = "bright_green"  # Класифікація документів

    # ═══════════════════════════════════════════════════════════
    # ДЕКОРАТИВНІ ЕЛЕМЕНТИ
    # ═══════════════════════════════════════════════════════════

    decoration: str = "yellow"            # Декоративні лінії
    separator: str = "yellow"             # Роздільники
    background_accent: str = "green"      # Фонові акценти

    # Хакерські елементи
    hex_address: str = "bright_yellow"    # Hex адреси (0x7F8A)
    sha_hash: str = "green"               # SHA хеші


# Глобальна тема за замовчуванням
THEME = ColorTheme()


# ═══════════════════════════════════════════════════════════
# RICH MARKUP ШАБЛОНИ (для зручності використання)
# ═══════════════════════════════════════════════════════════

def markup(color: str, text: str) -> str:
    """Обернути текст у Rich markup з кольором."""
    return f"[{color}]{text}[/{color}]"


def bold(text: str) -> str:
    """Зробити текст жирним."""
    return f"[bold]{text}[/bold]"


def format_number(value: int | float, color: str = THEME.number_primary) -> str:
    """Форматувати число з кольором."""
    if isinstance(value, float):
        return markup(color, f"{value:,.2f}")
    return markup(color, f"{value:,}")


def format_percent(value: float, color: str = THEME.progress_percent) -> str:
    """Форматувати відсоток."""
    return markup(color, f"{value:.1f}%")


def format_file_name(name: str) -> str:
    """Форматувати ім'я файлу."""
    return markup(THEME.file_name, name)


def format_category(category: str) -> str:
    """Форматувати категорію."""
    return markup(THEME.category, category)


def format_status(status: str, is_error: bool = False) -> str:
    """Форматувати статус з відповідним кольором."""
    if is_error:
        return markup(THEME.error, f"✗ {status}")
    return markup(THEME.success, f"✓ {status}")


def format_error(message: str) -> str:
    """Форматувати повідомлення про помилку."""
    return markup(THEME.error, f"⚠ {message}")


def format_info(message: str) -> str:
    """Форматувати інформаційне повідомлення."""
    return markup(THEME.info, f"ℹ {message}")


# ═══════════════════════════════════════════════════════════
# ШАБЛОНИ РЯДКІВ
# ═══════════════════════════════════════════════════════════

def header_line(text: str, width: int = 60) -> str:
    """Створити рядок-заголовок."""
    return f"\n{markup(THEME.header, '═' * width)}\n{markup(THEME.title, text.center(width))}\n{markup(THEME.header, '═' * width)}\n"


def section_line(text: str) -> str:
    """Створити розділювач секції."""
    return markup(THEME.border, f"─── {text} ───")


__all__ = [
    "ColorTheme",
    "THEME",
    "markup",
    "bold",
    "format_number",
    "format_percent",
    "format_file_name",
    "format_category",
    "format_status",
    "format_error",
    "format_info",
    "header_line",
    "section_line",
]
