"""Storage and analysis of GTFS-RT TripUpdates profiles.

TripUpdates snapshots are stored as JSONL in ``data/trip_updates/YYYY-MM-DD.jsonl``
(one line per trip profile per collection cycle). This module provides:

- append / load of raw profile records,
- grouping of profiles by trip across snapshots (to build a delay timeline),
- matching of TripUpdates profiles to Navitia train observations (by
  commercial train number and scheduled departure time),
- cross-check of the arrival delay at the destination.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from .storage import TZ
from .trip_updates import TripProfile, cross_check_arrival_delay


def _trip_updates_path(data_dir: Path, day: date) -> Path:
    return data_dir / "trip_updates" / day.strftime("%Y-%m-%d.jsonl")


def append_trip_profiles(data_dir: str | Path, profiles: list[TripProfile]) -> Path:
    """Append trip profiles to the day's JSONL file."""
    if not profiles:
        raise ValueError("no trip profiles to store")
    collected = datetime.fromisoformat(profiles[0].collected_at)
    path = _trip_updates_path(Path(data_dir), collected.astimezone(TZ).date())
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for profile in profiles:
            handle.write(json.dumps(profile.to_dict(), ensure_ascii=False) + "\n")
    return path


def load_trip_profiles(data_dir: str | Path, start: date | None = None, end: date | None = None):
    """Yield stored trip profile dicts, optionally filtered by date."""
    base = Path(data_dir) / "trip_updates"
    if not base.is_dir():
        return
    for path in sorted(base.glob("*.jsonl")):
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


def group_profiles_by_trip(records) -> dict[str, list[dict]]:
    """Group stored profile dicts by trip_id, ordered by collected_at."""
    trips: dict[str, list[dict]] = {}
    for record in records:
        trips.setdefault(record.get("trip_id", ""), []).append(record)
    for profiles in trips.values():
        profiles.sort(key=lambda p: p.get("collected_at", ""))
    return trips


def match_profile_to_train(
    profiles: list[dict],
    train_number: str,
    base_departure: datetime,
    destination_uic8: str,
    origin_uic8: str | None = None,
    window_minutes: int = 45,
) -> dict | None:
    """Find the stored TripUpdates profile matching a Navitia observation.

    Primary key: commercial train number **and** service date consistency.
    The fallback (time-window match on the first stop's departure) is only
    used when the train number does not match any profile, to avoid false
    positives when several snapshots cover overlapping trips.
    """
    origin_uic8 = origin_uic8 or ""
    candidates = []
    for profile in profiles:
        if profile.get("train_number") != train_number:
            continue
        stops = profile.get("stops", [])
        if not stops:
            continue
        uics = {s.get("uic8") for s in stops if s.get("uic8")}
        if destination_uic8 not in uics:
            continue
        if origin_uic8 and origin_uic8 not in uics:
            continue
        first_dep = stops[0].get("departure_time")
        if first_dep is not None:
            first_dt = datetime.fromtimestamp(first_dep, tz=TZ)
            if abs((first_dt - base_departure).total_seconds()) > window_minutes * 60:
                continue
        candidates.append(profile)
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.get("collected_at", ""))
    return candidates[-1]


def cross_check_train(
    profiles: list[dict],
    train_number: str,
    base_departure: datetime,
    destination_uic8: str,
    announced_delay_seconds: int | None,
    origin_uic8: str | None = None,
) -> dict | None:
    """Cross-check one train observation against TripUpdates data."""
    profile = match_profile_to_train(
        profiles, train_number, base_departure, destination_uic8, origin_uic8=origin_uic8
    )
    if profile is None:
        return None
    stop = next(
        (s for s in profile.get("stops", []) if s.get("uic8") == destination_uic8), None
    )
    tu_delay = stop.get("arrival_delay") if stop else None
    result = {
        "trip_id": profile.get("trip_id"),
        "train_number": train_number,
        "destination_uic8": destination_uic8,
        "trip_updates_delay_seconds": tu_delay,
        "announced_delay_seconds": announced_delay_seconds,
        "difference_seconds": None,
        "agrees": None,
        "hold_events_count": 0,
    }
    if tu_delay is not None and announced_delay_seconds is not None:
        difference = abs(tu_delay - announced_delay_seconds)
        result["difference_seconds"] = difference
        result["agrees"] = difference <= 60
    return result
