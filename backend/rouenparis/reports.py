from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings
from .odometry import estimate_arrival_at
from .positions import build_odometries, load_position_records, match_odometry_to_train
from .storage import TZ, dedupe_final_observations, load_snapshots
from .trip_storage import cross_check_train, load_trip_profiles


@dataclass
class TrainRecord:
    train_number: str
    line_code: str
    line_name: str
    direction: str
    service_date: date
    scheduled_departure: str
    scheduled_arrival: str | None
    actual_departure: str | None
    actual_arrival: str | None
    delay_seconds: int | None
    status: str
    threshold: int = 300
    odometry: object = None
    odometry_arrival: datetime | None = None
    odometry_delay_seconds: int | None = None
    trip_update_cross_check: dict | None = None
    data_source: str = "sncf_api"

    @property
    def cancelled(self) -> bool:
        return self.status == "cancelled"

    def to_dict(self) -> dict:
        return {
            "train_number": self.train_number,
            "line_code": self.line_code,
            "line_name": self.line_name,
            "direction": self.direction,
            "service_date": self.service_date.isoformat(),
            "scheduled_departure": self.scheduled_departure,
            "scheduled_arrival": self.scheduled_arrival,
            "actual_departure": self.actual_departure,
            "actual_arrival": self.actual_arrival,
            "delay_seconds": self.delay_seconds,
            "delay_minutes": round(self.delay_seconds / 60, 1) if self.delay_seconds is not None else None,
            "status": self.status,
            "odometry_arrival": self.odometry_arrival.astimezone(TZ).isoformat()
            if self.odometry_arrival
            else None,
            "odometry_delay_seconds": self.odometry_delay_seconds,
            "odometry_delay_minutes": round(self.odometry_delay_seconds / 60, 1)
            if self.odometry_delay_seconds is not None
            else None,
            "odometry_distance_m": round(self.odometry.distance_m, 1) if self.odometry else None,
            "odometry_stop_events": len(self.odometry.stop_events) if self.odometry else None,
            "trip_update_delay_seconds": self.trip_update_cross_check.get("trip_updates_delay_seconds")
            if self.trip_update_cross_check
            else None,
            "trip_update_delay_minutes": round(
                self.trip_update_cross_check["trip_updates_delay_seconds"] / 60, 1
            )
            if self.trip_update_cross_check
            and self.trip_update_cross_check.get("trip_updates_delay_seconds") is not None
            else None,
            "delay_cross_check_agrees": self.trip_update_cross_check.get("agrees")
            if self.trip_update_cross_check
            else None,
            "delay_cross_check_difference_seconds": self.trip_update_cross_check.get(
                "difference_seconds"
            )
            if self.trip_update_cross_check
            else None,
            "data_source": self.data_source,
        }


def build_train_records(
    snapshots,
    min_delay_seconds: int = 300,
    odometries: dict | None = None,
    settings: Settings | None = None,
    trip_profiles: list | None = None,
) -> list[TrainRecord]:
    records = []
    for train in dedupe_final_observations(snapshots):
        delay = train.arrival_delay_seconds
        if delay is None:
            delay = train.departure_delay_seconds
        odometry = None
        odometry_arrival = None
        odometry_delay = None
        if odometries:
            odometry = match_odometry_to_train(odometries, train.train_number, train.base_departure)
            if odometry is not None and settings is not None:
                odometry_arrival = estimate_arrival_at(
                    odometry, settings.station_latitude, settings.station_longitude
                )
                if odometry_arrival is not None and train.base_arrival is not None:
                    odometry_delay = int((odometry_arrival - train.base_arrival).total_seconds())
        cross_check = None
        if trip_profiles and settings is not None:
            cross_check = cross_check_train(
                trip_profiles,
                train.train_number,
                train.base_departure,
                settings.destination_uic8,
                delay,
                origin_uic8=settings.origin_uic8,
            )
        record = TrainRecord(
            train_number=train.train_number,
            line_code=train.line_code,
            line_name=train.line_name,
            direction=train.direction,
            service_date=train.base_departure.astimezone(TZ).date(),
            scheduled_departure=train.base_departure.astimezone(TZ).isoformat(),
            scheduled_arrival=(
                train.base_arrival.astimezone(TZ).isoformat() if train.base_arrival else None
            ),
            actual_departure=(
                train.realtime_departure.astimezone(TZ).isoformat()
                if train.realtime_departure
                else None
            ),
            actual_arrival=(
                train.realtime_arrival.astimezone(TZ).isoformat()
                if train.realtime_arrival
                else None
            ),
            delay_seconds=delay,
            status=train.status or "scheduled",
            threshold=min_delay_seconds,
            odometry=odometry,
            odometry_arrival=odometry_arrival,
            odometry_delay_seconds=odometry_delay,
            trip_update_cross_check=cross_check,
        )
        records.append(record)
    records.sort(key=lambda record: record.scheduled_departure)
    return records


def _period_bounds(record: TrainRecord, period: str) -> date:
    day = record.service_date
    if period == "daily":
        return day
    if period == "weekly":
        return day - timedelta(days=day.weekday())
    if period == "monthly":
        return day.replace(day=1)
    raise ValueError(f"unknown period: {period}")


def summarize(records: list[TrainRecord], min_delay_seconds: int) -> dict:
    circulated = [record for record in records if not record.cancelled]
    cancelled = [record for record in records if record.cancelled]
    measured = [record for record in circulated if record.delay_seconds is not None]
    delayed = [record for record in measured if record.delay_seconds >= min_delay_seconds]
    delays = [record.delay_seconds for record in measured]
    cross_checked = [r for r in records if r.trip_update_cross_check is not None]
    cross_agreeing = [r for r in cross_checked if r.trip_update_cross_check.get("agrees")]
    return {
        "scheduled": len(records),
        "circulated": len(circulated),
        "cancelled": len(cancelled),
        "measured": len(measured),
        "delayed": len(delayed),
        "punctuality_rate": round((len(measured) - len(delayed)) / len(measured) * 100, 1)
        if measured
        else None,
        "avg_delay_minutes": round(sum(delays) / len(delays) / 60, 1) if delays else None,
        "max_delay_minutes": round(max(delays) / 60, 1) if delays else None,
        "min_delay_threshold_minutes": min_delay_seconds // 60,
        "cross_checked": len(cross_checked),
        "cross_check_agreeing": len(cross_agreeing),
    }


def build_report(records: list[TrainRecord], period: str, start: date, settings: Settings) -> dict:
    end = start
    if period == "weekly":
        end = start + timedelta(days=6)
    elif period == "monthly":
        if start.month == 12:
            end = start.replace(year=start.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = start.replace(month=start.month + 1, day=1) - timedelta(days=1)
    period_records = [record for record in records if start <= record.service_date <= end]
    return {
        "generated_at": datetime.now(tz=TZ).isoformat(),
        "period": {"type": period, "start": start.isoformat(), "end": end.isoformat()},
        "summary": summarize(period_records, settings.min_delay_minutes * 60),
        "trains": [record.to_dict() for record in period_records],
        "data_source": period_records[0].data_source if period_records else "sncf_api",
    }


def daily_series(records: list[TrainRecord], min_delay_seconds: int) -> list[dict]:
    by_day: dict[date, list[TrainRecord]] = {}
    for record in records:
        by_day.setdefault(record.service_date, []).append(record)
    series = []
    for day in sorted(by_day):
        summary = summarize(by_day[day], min_delay_seconds)
        series.append({"date": day.isoformat(), **summary})
    return series


def list_periods(records: list[TrainRecord], period: str) -> list[date]:
    starts = {_period_bounds(record, period) for record in records}
    return sorted(starts)


def build_records_from_trip_profiles(
    trip_profiles: list[dict],
    settings: Settings,
    min_delay_seconds: int = 300,
) -> list[TrainRecord]:
    """Build TrainRecords from GTFS-RT TripUpdates profiles alone.

    Used as a fallback when the SNCF API token is not available: the
    TripUpdates feed (no token required) still provides per-stop delays
    for every train currently running.

    The GTFS-RT feed truncates stop lists for long trips — a Rouen→Paris
    train (~90 min) will not show both Rouen and Paris in a single
    snapshot because the feed only covers the next ~60 minutes.  We use
    *corridor matching* instead: a train arriving at Paris Saint-Lazare
    is a Rouen→Paris train if its stop list contains any station on the
    Rouen–Paris corridor (Oissel, Val-de-Reuil, Vernon, Mantes-la-Jolie,
    Poissy, …) before Paris.

    For each matching profile we build a record whose primary delay is
    the **arrival delay at Paris** (most meaningful for passengers).
    When the profile also includes Rouen we use its departure time as
    the scheduled departure; otherwise we fall back to the first
    corridor stop's departure time.
    """
    destinations = set(settings.destination_uic8_all)
    corridor = set(settings.corridor_uic8)
    records = []
    seen: set[tuple] = set()
    for profile in trip_profiles:
        train_number = profile.get("train_number") or ""
        stops = profile.get("stops", [])
        if not stops:
            continue
        destination = next(
            (s for s in stops if s.get("uic8") in destinations), None
        )
        if destination is None:
            continue
        arr_time = destination.get("arrival_time")
        arr_delay = destination.get("arrival_delay")
        if arr_time is None and arr_delay is None:
            continue
        uics = [s.get("uic8") for s in stops]
        dest_uic = destination.get("uic8")
        paris_idx = next(
            (i for i, u in enumerate(uics) if u == dest_uic), -1
        )
        if paris_idx <= 0:
            continue
        corridor_stops_before = [
            (i, s) for i, s in enumerate(stops[:paris_idx])
            if s.get("uic8") in corridor
        ]
        if not corridor_stops_before:
            continue
        origin_stop = next(
            (s for s in stops if s.get("uic8") == settings.origin_uic8), None
        )
        if origin_stop is not None:
            dep_time = origin_stop.get("departure_time")
            dep_delay = origin_stop.get("departure_delay")
        else:
            first_corridor_idx, first_corridor = corridor_stops_before[0]
            dep_time = first_corridor.get("departure_time")
            dep_delay = first_corridor.get("departure_delay")
        if dep_time is None and arr_time is None:
            continue
        service_date = datetime.fromtimestamp(
            arr_time or dep_time, tz=TZ
        ).date()
        key = (train_number, service_date, arr_time or dep_time)
        if key in seen:
            continue
        seen.add(key)
        scheduled_arrival_dt = (
            datetime.fromtimestamp(arr_time - (arr_delay or 0), tz=TZ)
            if arr_time
            else None
        )
        actual_arrival_dt = (
            datetime.fromtimestamp(arr_time, tz=TZ) if arr_time else None
        )
        if dep_time is not None:
            scheduled_departure_dt = datetime.fromtimestamp(
                dep_time - (dep_delay or 0), tz=TZ
            )
            actual_departure_dt = datetime.fromtimestamp(dep_time, tz=TZ)
            scheduled_departure_str = scheduled_departure_dt.isoformat()
        else:
            scheduled_departure_dt = scheduled_arrival_dt
            actual_departure_dt = None
            scheduled_departure_str = (
                scheduled_arrival_dt.isoformat() if scheduled_arrival_dt else ""
            )
        delay = arr_delay if arr_delay is not None else dep_delay
        records.append(
            TrainRecord(
                train_number=train_number,
                line_code="",
                line_name="GTFS-RT",
                direction="Paris Saint-Lazare",
                service_date=service_date,
                scheduled_departure=scheduled_departure_str,
                scheduled_arrival=(
                    scheduled_arrival_dt.isoformat() if scheduled_arrival_dt else None
                ),
                actual_departure=(
                    actual_departure_dt.isoformat() if actual_departure_dt else None
                ),
                actual_arrival=(
                    actual_arrival_dt.isoformat() if actual_arrival_dt else None
                ),
                delay_seconds=delay,
                status="scheduled",
                threshold=min_delay_seconds,
                data_source="gtfs_rt_trip_updates",
            )
        )
    records.sort(key=lambda record: record.scheduled_departure)
    return records


def generate_all(data_dir: str | Path, site_dir: str | Path, settings: Settings) -> dict:
    snapshots = list(load_snapshots(data_dir))
    odometries = build_odometries(
        load_position_records(data_dir),
        stop_radius_m=settings.stop_radius_m,
        min_stop_duration_seconds=settings.min_stop_duration_seconds,
    )
    trip_profiles = list(load_trip_profiles(data_dir))
    if snapshots:
        records = build_train_records(
            snapshots,
            settings.min_delay_minutes * 60,
            odometries=odometries,
            settings=settings,
            trip_profiles=trip_profiles,
        )
        data_source = "sncf_api"
    else:
        records = build_records_from_trip_profiles(
            trip_profiles, settings, settings.min_delay_minutes * 60
        )
        data_source = "gtfs_rt_trip_updates"
    site_path = Path(site_dir)
    reports_dir = site_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    generated_at = datetime.now(tz=TZ).isoformat()
    index = {
        "generated_at": generated_at,
        "route": {
            "origin": "Rouen Rive Droite",
            "destination": "Paris Saint-Lazare",
        },
        "data_source": data_source,
        "periods": [],
    }

    for period in ("daily", "weekly", "monthly"):
        periods = []
        for start in list_periods(records, period):
            report = build_report(records, period, start, settings)
            slug = f"{period}/{start.isoformat()}"
            path = reports_dir / f"{period}-{start.isoformat()}.json"
            path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            periods.append(
                {
                    "start": start.isoformat(),
                    "end": report["period"]["end"],
                    "summary": report["summary"],
                    "url": f"reports/{path.name}",
                }
            )
        index["periods"].append({"type": period, "reports": periods})

    series = daily_series(records, settings.min_delay_minutes * 60)
    (site_path / "series.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "min_delay_threshold_minutes": settings.min_delay_minutes,
                "days": series,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    latest_day = series[-1]["date"] if series else None
    (site_path / "latest.json").write_text(
        json.dumps({"latest_day": latest_day, "generated_at": generated_at}, indent=2) + "\n",
        encoding="utf-8",
    )

    matched = sum(1 for record in records if record.odometry is not None)
    cross_checked = sum(1 for record in records if record.odometry_arrival is not None)
    tu_checked = sum(1 for record in records if record.trip_update_cross_check is not None)
    tu_agreeing = sum(
        1
        for record in records
        if record.trip_update_cross_check and record.trip_update_cross_check.get("agrees")
    )
    (site_path / "odometry.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "trips_tracked": len(odometries),
                "trains_matched": matched,
                "arrivals_cross_checked": cross_checked,
                "station": {
                    "latitude": settings.station_latitude,
                    "longitude": settings.station_longitude,
                },
                "trips": {key: odometry.to_dict() for key, odometry in sorted(odometries.items())},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    (site_path / "crosscheck.json").write_text(
        json.dumps(
            {
                "generated_at": generated_at,
                "source": "GTFS-RT TripUpdates (transport.data.gouv.fr)",
                "destination_uic8": settings.destination_uic8,
                "trips_in_feed": len({p.get("trip_id") for p in trip_profiles}),
                "trains_cross_checked": tu_checked,
                "delays_agreeing": tu_agreeing,
                "tolerance_seconds": 60,
                "trains": [
                    {
                        "train_number": record.train_number,
                        "service_date": record.service_date.isoformat(),
                        "scheduled_departure": record.scheduled_departure,
                        "announced_delay_seconds": record.delay_seconds,
                        **record.trip_update_cross_check,
                    }
                    for record in records
                    if record.trip_update_cross_check is not None
                ],
                "trip_updates_only_trains": [
                    record.to_dict()
                    for record in records
                    if record.data_source == "gtfs_rt_trip_updates"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    tu_source_trains = sum(1 for r in records if r.data_source == "gtfs_rt_trip_updates")
    tu_delayed = sum(
        1
        for r in records
        if r.data_source == "gtfs_rt_trip_updates"
        and r.delay_seconds is not None
        and r.delay_seconds >= settings.min_delay_minutes * 60
    )
    index["days_tracked"] = len(series)
    index["odometry"] = {
        "trips_tracked": len(odometries),
        "trains_matched": matched,
        "arrivals_cross_checked": cross_checked,
    }
    index["cross_check"] = {
        "trips_in_feed": len({p.get("trip_id") for p in trip_profiles}),
        "trains_cross_checked": tu_checked,
        "delays_agreeing": tu_agreeing,
    }
    index["trip_updates_only"] = {
        "active": data_source == "gtfs_rt_trip_updates",
        "trains_from_trip_updates": tu_source_trains,
        "delayed_from_trip_updates": tu_delayed,
        "trips_in_feed": len({p.get("trip_id") for p in trip_profiles}),
    }
    (site_path / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index
