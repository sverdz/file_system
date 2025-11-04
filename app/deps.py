"""Dependency management utilities for the File Inventory Tool."""
from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Iterable, List, Tuple

REQUIRED_PACKAGES: Tuple[str, ...] = (
    "pydantic",
    "loguru",
    "chardet",
    "unidecode",
    "colorama",
    "rich",
    "pandas",
    "openpyxl",
    "xlwt",
    "python-docx",
    "pdfminer.six",
    "pillow",
    "pytesseract",
    "requests",
    "send2trash",
)

OPTIONAL_PACKAGES: Tuple[str, ...] = (
    "pypdf",
)


def ensure_ready() -> None:
    """Ensure the process runs in a virtual environment with all deps installed."""
    _ensure_utf8_console()
    if not _in_venv():
        _bootstrap_venv()
    missing = _find_missing(REQUIRED_PACKAGES)
    if missing:
        _install(missing)
    # Optional packages are installed on-demand when users enable features.


def _ensure_utf8_console() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(65001)
        kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    if hasattr(sys.stderr, "reconfigure"):
        try:
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _in_venv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def _bootstrap_venv() -> None:
    """Create a .venv directory and re-exec the current script inside it."""
    project_root = Path(__file__).resolve().parents[1]
    venv_dir = project_root / ".venv"
    if not venv_dir.exists():
        print("[deps] Створюється віртуальне середовище .venv...")
        import venv

        venv.create(venv_dir, with_pip=True)
    python_exe = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python_exe.exists():
        raise SystemExit("Помилка створення .venv: python не знайдено")
    print("[deps] Перезапуск у .venv...")
    args = [str(python_exe), "-m", "app.main", "--reexec", *sys.argv[1:]]
    os.execv(str(python_exe), args)


def _find_missing(packages: Iterable[str]) -> List[str]:
    missing: List[str] = []
    for pkg in packages:
        try:
            __import__(pkg.split("-")[0].replace(".", "_"))
        except ImportError:
            missing.append(pkg)
    return missing


def _install(packages: Iterable[str]) -> None:
    python = Path(sys.executable)
    print("[deps] Встановлення залежностей у .venv...")
    cmd = [str(python), "-m", "pip", "install", "--upgrade", "pip"]
    subprocess.run(cmd, check=False)
    cmd = [str(python), "-m", "pip", "install", "--upgrade", *packages]
    process = subprocess.run(cmd, check=False)
    if process.returncode != 0:
        missing = "\n".join(f"  - {pkg}" for pkg in packages)
        message = textwrap.dedent(
            f"""
            Не вдалося встановити деякі пакети. Будь ласка, встановіть вручну:
{missing}
            """
        ).strip()
        print(message)
        raise SystemExit(1)

