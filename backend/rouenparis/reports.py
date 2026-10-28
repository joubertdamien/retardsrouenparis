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
        }


def build_train_records(
    snapshots,
    min_delay_seconds: int = 300,
    odometries: dict | None = None,
    settings: Settings | None = None,
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


def generate_all(data_dir: str | Path, site_dir: str | Path, settings: Settings) -> dict:
    snapshots = list(load_snapshots(data_dir))
    odometries = build_odometries(
        load_position_records(data_dir),
        stop_radius_m=settings.stop_radius_m,
        min_stop_duration_seconds=settings.min_stop_duration_seconds,
    )
    records = build_train_records(
        snapshots, settings.min_delay_minutes * 60, odometries=odometries, settings=settings
    )
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

    index["days_tracked"] = len(series)
    index["odometry"] = {
        "trips_tracked": len(odometries),
        "trains_matched": matched,
        "arrivals_cross_checked": cross_checked,
    }
    (site_path / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return index
