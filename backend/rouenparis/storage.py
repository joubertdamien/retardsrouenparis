from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .sncf_client import TrainObservation

TZ = ZoneInfo("Europe/Paris")


@dataclass
class Snapshot:
    collected_at: datetime
    trains: list[TrainObservation]

    def to_dict(self) -> dict:
        return {
            "collected_at": self.collected_at.isoformat(),
            "trains": [train.to_dict() for train in self.trains],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Snapshot":
        return cls(
            collected_at=datetime.fromisoformat(data["collected_at"]),
            trains=[TrainObservation.from_dict(train) for train in data.get("trains", [])],
        )


def _snapshot_path(data_dir: Path, day: date) -> Path:
    return data_dir / "snapshots" / day.strftime("%Y-%m-%d.jsonl")


def append_snapshot(data_dir: str | Path, snapshot: Snapshot) -> Path:
    path = _snapshot_path(Path(data_dir), snapshot.collected_at.astimezone(TZ).date())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_snapshots(data_dir: str | Path, start: date | None = None, end: date | None = None):
    data_path = Path(data_dir) / "snapshots"
    if not data_path.is_dir():
        return
    for path in sorted(data_path.glob("*.jsonl")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    yield Snapshot.from_dict(json.loads(line))


def iter_observations(snapshots):
    for snapshot in snapshots:
        for train in snapshot.trains:
            yield snapshot.collected_at, train


def dedupe_final_observations(snapshots):
    best: dict[tuple, tuple] = {}
    for collected_at, train in iter_observations(snapshots):
        key = (train.base_departure, train.train_number, train.line_code)
        current = best.get(key)
        if current is None:
            best[key] = (collected_at, train)
            continue
        prev_collected, prev_train = current
        prev_score = _finality_score(prev_collected, prev_train)
        new_score = _finality_score(collected_at, train)
        if new_score > prev_score:
            best[key] = (collected_at, train)
    return [train for _, train in sorted(best.values(), key=lambda item: item[1].base_departure)]


def _finality_score(collected_at: datetime, train: TrainObservation) -> tuple:
    arrived = 1 if train.realtime_arrival is not None else 0
    delay_known = 1 if train.arrival_delay_seconds is not None else 0
    minutes_after_arrival = (
        (collected_at - train.realtime_arrival).total_seconds() / 60
        if train.realtime_arrival is not None
        else -1
    )
    return (arrived, delay_known, minutes_after_arrival)
