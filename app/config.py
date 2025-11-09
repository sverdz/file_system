"""Configuration management for the File Inventory Tool."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Literal

from pydantic import BaseModel, Field, ConfigDict

CONFIG_VERSION = "1.0"
DEFAULT_TEMPLATE = "{yyyy}-{mm}-{dd}_{category}{ext}"
MAX_FILENAME_LENGTH = 20  # Максимальна довжина назви файлу без розширення
DEFAULT_CATEGORY_MAP = [
    "договір",
    "рахунок",
    "акт",
    "протокол",
    "лист",
    "наказ",
    "звіт",
    "кошторис",
    "тендер",
    "презентація",
    "довідка",
    "ТЗ",
    "специфікація",
    "інше",
]

# Виключення - папки та файли, які ігноруються при скануванні
DEFAULT_EXCLUDE_DIRS = [
    # Системи контролю версій
    ".git", ".svn", ".hg",
    # Залежності
    "node_modules", "vendor",
    # Python
    "venv", ".venv", "env", "virtualenv", "__pycache__", ".pytest_cache",
    # IDE
    ".idea", ".vscode", ".vs",
    # Збірка
    "dist", "build", "target", "out", "bin", "obj",
    # Службові папки програми
    "duplicates", "_duplicates", "_sorted", "_output", "_near_duplicates", "runs",
]

DEFAULT_EXCLUDE_FILES = [
    # Системні файли
    ".DS_Store", "Thumbs.db", "desktop.ini",
    # Python скомпільовані
    "*.pyc", "*.pyo", "*.pyd",
    # Бібліотеки
    "*.dll", "*.so", "*.dylib",
    # Виконувані
    "*.exe", "*.bin", "*.app",
    # Тимчасові
    "*.log", "*.tmp", "*.temp", "*.cache",
    # Git конфігурація
    ".gitignore", ".gitattributes", ".editorconfig",
    # Lock файли
    "package-lock.json", "yarn.lock", "composer.lock", "Pipfile.lock",
    # Мінімізовані
    "*.min.js", "*.min.css",
]

DEFAULT_INCLUDE_EXTENSIONS = [
    # Документи
    ".pdf", ".doc", ".docx", ".odt",
    ".xls", ".xlsx", ".ods", ".csv",
    ".ppt", ".pptx", ".odp",
    ".txt", ".md", ".rtf",
    # Зображення
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp", ".svg", ".ico",
    # Архіви
    ".zip", ".rar", ".7z", ".tar", ".gz",
    # Інші
    ".json", ".xml", ".yaml", ".yml", ".html", ".htm",
]


class DuplicatePolicy(BaseModel):
    exact: str = Field(default="quarantine", pattern=r"^(quarantine|delete_to_trash)$")
    near: str = Field(default="quarantine", pattern=r"^(quarantine)$")


class DedupSettings(BaseModel):
    exact: bool = True
    near: bool = False
    near_threshold: float = Field(0.85, ge=0.0, le=1.0)


class Config(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    version: str = CONFIG_VERSION
    root: Path = Path.cwd()
    rename_template: str = DEFAULT_TEMPLATE
    # Нові параметри для короткого формату перейменування
    use_short_format: bool = True  # Використовувати короткий формат (20 символів)
    use_short_date: bool = False  # Використовувати YYMMDD замість YYYYMMDD
    max_filename_length: int = MAX_FILENAME_LENGTH  # Максимальна довжина без розширення
    category_map: list[str] = Field(default_factory=lambda: list(DEFAULT_CATEGORY_MAP))
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    duplicates_policy: DuplicatePolicy = Field(default_factory=DuplicatePolicy)
    export_mode: Literal["views_only", "physical_sort", "prompt"] = "prompt"
    sorted_targets: list[str] = Field(default_factory=lambda: ["by_category", "by_date", "by_type"])
    sorted_root: str = "_sorted"
    ocr_lang: str = "ukr+eng"
    llm_enabled: bool = False
    llm_provider: Literal["claude", "chatgpt", "none"] = "none"
    llm_api_key_claude: str = ""
    llm_api_key_openai: str = ""
    llm_model: str = ""  # Наприклад: "claude-3-sonnet-20240229" або "gpt-4"
    threads: int = 0

    # Фільтри для сканування
    exclude_dirs: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_DIRS))
    exclude_files: list[str] = Field(default_factory=lambda: list(DEFAULT_EXCLUDE_FILES))
    include_extensions: list[str] = Field(default_factory=lambda: list(DEFAULT_INCLUDE_EXTENSIONS))
    use_extension_filter: bool = True  # Використовувати фільтр розширень (якщо False - обробляти всі)

    @property
    def root_path(self) -> Path:
        return Path(self.root)


def get_project_root() -> Path:
    """Отримати кореневу директорію проекту."""
    return Path(__file__).resolve().parents[1]


def get_runs_dir() -> Path:
    """Отримати директорію для зберігання запусків."""
    return get_project_root() / "runs"


def get_output_dir() -> Path:
    """Отримати директорію для зберігання вихідних файлів (відсортованих, дублікатів тощо)."""
    output_dir = get_project_root() / "_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def config_locations() -> Dict[str, Path]:
    """Отримати шляхи до конфігураційних файлів. Всі конфіги зберігаються в папці проекту."""
    project_root = get_project_root()
    return {
        "project": project_root / "config.yaml",  # Конфігурація в корені проекту
    }


def load_config(explicit: Optional[Path] = None) -> Config:
    import yaml  # type: ignore

    if explicit and explicit.exists():
        with explicit.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if data is None:
            return Config()
        return Config.model_validate(data)
    for location in config_locations().values():
        if location.exists():
            with location.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if data is None:
                return Config()
            return Config.model_validate(data)
    return Config()


def save_config(cfg: Config, run_dir: Optional[Path] = None) -> None:
    import yaml  # type: ignore

    data = cfg.model_dump(mode="json")
    for name, path in config_locations().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
    if run_dir:
        run_cfg = run_dir / "config.yaml"
        run_cfg.parent.mkdir(parents=True, exist_ok=True)
        with run_cfg.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)


def test_llm_connection(provider: str, api_key: str, model: str = "") -> tuple[bool, str]:
    """
    Перевірити підключення до LLM провайдера.

    Returns:
        tuple[bool, str]: (успіх, повідомлення)
    """
    import requests

    if not api_key:
        return False, "API ключ не вказано"

    try:
        if provider == "claude":
            # Перевірка Claude API
            headers = {
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            }
            response = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers,
                json={
                    "model": model or "claude-3-haiku-20240307",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": "test"}]
                },
                timeout=10
            )
            if response.status_code == 200:
                return True, f"✓ Підключення до Claude успішне (модель: {model or 'claude-3-haiku-20240307'})"
            elif response.status_code == 401:
                return False, "✗ Невірний API ключ для Claude"
            else:
                return False, f"✗ Помилка підключення до Claude: {response.status_code}"

        elif provider == "chatgpt":
            # Перевірка OpenAI API
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            response = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers=headers,
                json={
                    "model": model or "gpt-3.5-turbo",
                    "messages": [{"role": "user", "content": "test"}],
                    "max_tokens": 10
                },
                timeout=10
            )
            if response.status_code == 200:
                return True, f"✓ Підключення до ChatGPT успішне (модель: {model or 'gpt-3.5-turbo'})"
            elif response.status_code == 401:
                return False, "✗ Невірний API ключ для OpenAI"
            else:
                return False, f"✗ Помилка підключення до OpenAI: {response.status_code}"
        else:
            return False, f"✗ Невідомий провайдер: {provider}"

    except requests.exceptions.Timeout:
        return False, "✗ Таймаут підключення. Перевірте інтернет з'єднання"
    except requests.exceptions.ConnectionError:
        return False, "✗ Немає інтернет з'єднання"
    except Exception as e:
        return False, f"✗ Помилка: {str(e)}"


__all__ = ["Config", "load_config", "save_config", "test_llm_connection", "get_project_root", "get_runs_dir", "get_output_dir"]

