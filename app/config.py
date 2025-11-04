"""Configuration management for the File Inventory Tool."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

CONFIG_VERSION = "1.0"
DEFAULT_TEMPLATE = "{category}_{yyyy}-{mm}-{dd}_{short_title}_v{version:02d}_[{hash8}]{ext}"
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


class DuplicatePolicy(BaseModel):
    exact: str = Field("quarantine", regex="^(quarantine|delete_to_trash)$")
    near: str = Field("quarantine", regex="^(quarantine)$")


class DedupSettings(BaseModel):
    exact: bool = True
    near: bool = False
    near_threshold: float = Field(0.85, ge=0.0, le=1.0)


class Config(BaseModel):
    version: str = CONFIG_VERSION
    root: Path = Path.cwd()
    rename_template: str = DEFAULT_TEMPLATE
    category_map: list[str] = Field(default_factory=lambda: list(DEFAULT_CATEGORY_MAP))
    dedup: DedupSettings = Field(default_factory=DedupSettings)
    duplicates_policy: DuplicatePolicy = Field(default_factory=DuplicatePolicy)
    export_mode: str = Field("prompt", regex="^(views_only|physical_sort|prompt)$")
    sorted_targets: list[str] = Field(default_factory=lambda: ["by_category", "by_date", "by_type"])
    sorted_root: str = "_sorted"
    ocr_lang: str = "ukr+eng"
    llm_enabled: bool = False
    threads: int = 0

    class Config:
        arbitrary_types_allowed = True

    @property
    def root_path(self) -> Path:
        return Path(self.root)


def config_locations() -> Dict[str, Path]:
    project_root = Path(__file__).resolve().parents[1]
    appdata = Path(os.environ.get("APPDATA", project_root / "runs" / "config"))
    return {
        "project": project_root / "runs" / "config.yaml",
        "appdata": appdata / "FileInventoryTool" / "config.yaml",
    }


def load_config(explicit: Optional[Path] = None) -> Config:
    if explicit and explicit.exists():
        return Config.parse_file(explicit)
    for location in config_locations().values():
        if location.exists():
            return Config.parse_file(location)
    return Config()


def save_config(cfg: Config, run_dir: Optional[Path] = None) -> None:
    import yaml  # type: ignore

    data = json.loads(cfg.json())
    for name, path in config_locations().items():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)
    if run_dir:
        run_cfg = run_dir / "config.yaml"
        run_cfg.parent.mkdir(parents=True, exist_ok=True)
        with run_cfg.open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, allow_unicode=True)


__all__ = ["Config", "load_config", "save_config"]

