from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .odometry import Fix, TripOdometry, compute_odometry
from .storage import TZ


def _positions_path(data_dir: Path, day: date) -> Path:
    return data_dir / "positions" / day.strftime("%Y-%m-%d.jsonl")


def append_position_records(data_dir: str | Path, records: list[dict]) -> Path:
    """Append raw GTFS-RT vehicle position records to the day's JSONL file."""
    if not records:
        raise ValueError("no position records to store")
    collected = datetime.fromisoformat(records[0]["collected_at"])
    path = _positions_path(Path(data_dir), collected.astimezone(TZ).date())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return path


def load_position_records(data_dir: str | Path, start: date | None = None, end: date | None = None):
    positions_path = Path(data_dir) / "positions"
    if not positions_path.is_dir():
        return
    for path in sorted(positions_path.glob("*.jsonl")):
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
                    yield json.loads(line)


def group_fixes_by_trip(records) -> dict[str, list[Fix]]:
    trips: dict[str, list[Fix]] = {}
    for record in records:
        position = record.get("position") or {}
        lat = position.get("latitude")
        lon = position.get("longitude")
        if lat is None or lon is None:
            continue
        timestamp = record.get("timestamp")
        fix_time = (
            datetime.fromtimestamp(timestamp, tz=TZ)
            if timestamp
            else datetime.fromisoformat(record["collected_at"])
        )
        trips.setdefault(record.get("trip_id", ""), []).append(
            Fix(
                timestamp=fix_time,
                latitude=lat,
                longitude=lon,
                speed=position.get("speed"),
                stop_id=record.get("stop_id", ""),
                current_status=record.get("current_status", ""),
            )
        )
    return trips


def build_odometries(records, stop_radius_m: float = 150.0, min_stop_duration_seconds: float = 60.0):
    odometries = {}
    for trip_id, fixes in group_fixes_by_trip(records).items():
        if len(fixes) < 2:
            continue
        odometries[trip_id] = compute_odometry(
            trip_id,
            fixes,
            stop_radius_m=stop_radius_m,
            min_stop_duration_seconds=min_stop_duration_seconds,
        )
    return odometries


_TRAIN_NUMBER_RE = re.compile(r"(\d{4,6})")


def train_number_from_trip_id(trip_id: str) -> str | None:
    """Extract the commercial train number from a SNCF GTFS-RT trip_id.

    SNCF trip ids look like ``OCESN3123F`` / ``OCE3125R``: the digits in the
    middle are the commercial train number (same as the Navitia headsign).
    """
    match = _TRAIN_NUMBER_RE.search(trip_id)
    return match.group(1) if match else None


def match_odometry_to_train(odometries: dict[str, TripOdometry], train_number: str, base_departure: datetime):
    """Find the odometry matching a collected train observation.

    Primary key: commercial train number extracted from the trip_id.
    Fallback: the trip whose first fix is closest in time to the scheduled
    departure (within ±45 minutes).
    """
    for trip_id, odometry in odometries.items():
        if train_number_from_trip_id(trip_id) == train_number:
            return odometry
    best = None
    best_delta = None
    for odometry in odometries.values():
        if not odometry.fixes:
            continue
        delta = abs((odometry.fixes[0].timestamp - base_departure).total_seconds())
        if delta <= 45 * 60 and (best_delta is None or delta < best_delta):
            best = odometry
            best_delta = delta
    return best
