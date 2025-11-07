"""Керування сесіями інвентаризації з окремими директоріями для кожної операції."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class SessionInfo:
    """Інформація про сесію інвентаризації."""
    session_id: str  # YYYY-MM-DD_HH-mm-ss_OPERATION
    operation_type: str  # SCAN, RENAME, SORT, etc.
    timestamp: datetime
    session_dir: Path

    def __str__(self) -> str:
        return f"{self.session_id} ({self.operation_type})"


class SessionManager:
    """Менеджер сесій для інвентаризації."""

    # Типи операцій
    OPERATION_SCAN = "SCAN"
    OPERATION_RENAME = "RENAME"
    OPERATION_SORT = "SORT"
    OPERATION_DEDUP = "DEDUP"
    OPERATION_ANALYZE = "ANALYZE"

    def __init__(self, base_dir: Path = Path("runs")):
        """
        Ініціалізувати менеджер сесій.

        Args:
            base_dir: Базова директорія для збереження всіх сесій
        """
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_session(self, operation_type: str) -> SessionInfo:
        """
        Створити нову сесію з відповідною структурою директорій.

        Args:
            operation_type: Тип операції (SCAN, RENAME, SORT, тощо)

        Returns:
            SessionInfo з інформацією про створену сесію
        """
        now = datetime.now(timezone.utc)

        # Формат: YYYY-MM-DD_HH-mm-ss_OPERATION
        session_id = now.strftime(f"%Y-%m-%d_%H-%M-%S_{operation_type}")

        session_dir = self.base_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        session_info = SessionInfo(
            session_id=session_id,
            operation_type=operation_type,
            timestamp=now,
            session_dir=session_dir,
        )

        # Зберегти метадані сесії
        self._save_session_metadata(session_info)

        return session_info

    def _save_session_metadata(self, session: SessionInfo) -> None:
        """Зберегти метадані сесії в JSON файл."""
        metadata = {
            "session_id": session.session_id,
            "operation_type": session.operation_type,
            "timestamp": session.timestamp.isoformat(),
            "session_dir": str(session.session_dir),
        }

        metadata_path = session.session_dir / "session_metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    def list_sessions(self, operation_type: Optional[str] = None) -> list[SessionInfo]:
        """
        Отримати список всіх сесій.

        Args:
            operation_type: Фільтр по типу операції (опціонально)

        Returns:
            Список SessionInfo, відсортований за датою (від новіших до старіших)
        """
        sessions = []

        if not self.base_dir.exists():
            return sessions

        for session_dir in self.base_dir.iterdir():
            if not session_dir.is_dir():
                continue

            # Спробувати прочитати метадані
            metadata_path = session_dir / "session_metadata.json"
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    session = SessionInfo(
                        session_id=metadata["session_id"],
                        operation_type=metadata["operation_type"],
                        timestamp=datetime.fromisoformat(metadata["timestamp"]),
                        session_dir=Path(metadata["session_dir"]),
                    )

                    # Фільтр за типом операції
                    if operation_type is None or session.operation_type == operation_type:
                        sessions.append(session)
                except Exception:
                    # Якщо не вдалося прочитати метадані, спробувати розпарсити назву
                    parts = session_dir.name.split("_")
                    if len(parts) >= 4:  # YYYY-MM-DD_HH-mm-ss_OPERATION
                        op_type = "_".join(parts[3:])  # Підтримка складених назв операцій

                        if operation_type is None or op_type == operation_type:
                            # Спробувати відновити дату
                            try:
                                date_str = f"{parts[0]}_{parts[1]}"
                                ts = datetime.strptime(date_str, "%Y-%m-%d_%H-%M-%S")
                                ts = ts.replace(tzinfo=timezone.utc)
                            except Exception:
                                ts = datetime.fromtimestamp(session_dir.stat().st_ctime, tz=timezone.utc)

                            session = SessionInfo(
                                session_id=session_dir.name,
                                operation_type=op_type,
                                timestamp=ts,
                                session_dir=session_dir,
                            )
                            sessions.append(session)

        # Сортувати за датою (новіші спочатку)
        sessions.sort(key=lambda s: s.timestamp, reverse=True)

        return sessions

    def get_latest_session(self, operation_type: Optional[str] = None) -> Optional[SessionInfo]:
        """
        Отримати останню сесію.

        Args:
            operation_type: Фільтр по типу операції (опціонально)

        Returns:
            SessionInfo або None якщо сесій немає
        """
        sessions = self.list_sessions(operation_type)
        return sessions[0] if sessions else None

    def get_session_by_id(self, session_id: str) -> Optional[SessionInfo]:
        """
        Отримати сесію за ID.

        Args:
            session_id: ID сесії

        Returns:
            SessionInfo або None якщо не знайдено
        """
        session_dir = self.base_dir / session_id
        if not session_dir.exists():
            return None

        metadata_path = session_dir / "session_metadata.json"
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                return SessionInfo(
                    session_id=metadata["session_id"],
                    operation_type=metadata["operation_type"],
                    timestamp=datetime.fromisoformat(metadata["timestamp"]),
                    session_dir=Path(metadata["session_dir"]),
                )
            except Exception:
                pass

        return None

    def create_session_report(self, session: SessionInfo, report_name: str, content: str) -> Path:
        """
        Створити додатковий звіт в директорії сесії.

        Args:
            session: SessionInfo сесії
            report_name: Назва файлу звіту (з розширенням)
            content: Вміст звіту

        Returns:
            Path до створеного файлу
        """
        report_path = session.session_dir / report_name
        report_path.write_text(content, encoding="utf-8")
        return report_path


__all__ = ["SessionInfo", "SessionManager"]
