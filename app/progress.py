"""Progress tracking utilities with ETA estimation."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, Tuple

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


class ProgressTracker:
    def __init__(self, stages: Dict[str, float]):
        self.stages = {name: StageProgress(weight=weight) for name, weight in stages.items()}
        self.history: list[Tuple[float, float]] = []

    def set_stage_total(self, stage: str, total: int) -> None:
        if stage in self.stages:
            self.stages[stage].total = total

    def increment(self, stage: str, amount: int = 1) -> None:
        if stage not in self.stages:
            return
        sp = self.stages[stage]
        sp.completed += amount
        sp.last_update = time.time()
        self.history.append((time.time(), self.percentage()))
        if len(self.history) > WINDOW:
            self.history = self.history[-WINDOW:]

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

