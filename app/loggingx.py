"""Logging helpers for human-readable and structured logs."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

READABLE_LOG = "log_readable.txt"
JSON_LOG = "log_events.jsonl"


def setup_logging(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    readable_path = run_dir / READABLE_LOG
    json_path = run_dir / JSON_LOG
    logger.add(
        readable_path,
        format="{time:YYYY-MM-DD HH:mm:ss} | {message}",
        encoding="utf-8",
        enqueue=True,
    )
    logger.add(
        json_path,
        format="{message}",
        encoding="utf-8",
        enqueue=True,
        level="INFO",
    )


def mask_sensitive(text: str) -> str:
    return text


def log_event(run_id: str, category: str, payload: Dict[str, Any]) -> None:
    entry = {
        "run_id": run_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "category": category,
    }
    entry.update(payload)
    logger.log("INFO", json.dumps(entry, ensure_ascii=False))


def log_readable(message: str) -> None:
    logger.info(mask_sensitive(message))

