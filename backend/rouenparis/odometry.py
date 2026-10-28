"""Train odometry: derive movement, distance and stop events from position fixes.

Given a time series of GPS fixes for one train, this module computes:

- the distance travelled between consecutive fixes (haversine),
- the ground speed between fixes (used when the feed omits ``speed``),
- **stop events**: maximal runs of consecutive fixes whose position stays
  within ``stop_radius_meters``. The midpoint time of a stop is used as the
  best estimate of when the train was actually stopped at a station.

The stop detection is what allows cross-checking the arrival time announced
by the API: the moment the train's odometry shows it stopped at (or within
a few hundred meters of) Paris Saint-Lazare is an independent estimate of
the real arrival time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass
class Fix:
    timestamp: datetime
    latitude: float
    longitude: float
    speed: float | None = None
    stop_id: str = ""
    current_status: str = ""

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "latitude": self.latitude,
            "longitude": self.longitude,
            "speed": self.speed,
            "stop_id": self.stop_id,
            "current_status": self.current_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Fix":
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            latitude=data["latitude"],
            longitude=data["longitude"],
            speed=data.get("speed"),
            stop_id=data.get("stop_id", ""),
            current_status=data.get("current_status", ""),
        )


@dataclass
class StopEvent:
    start: datetime
    end: datetime
    latitude: float
    longitude: float
    max_speed_mps: float
    feed_stop_id: str = ""

    @property
    def duration_seconds(self) -> float:
        return (self.end - self.start).total_seconds()

    @property
    def midpoint(self) -> datetime:
        return self.start + (self.end - self.start) / 2

    @classmethod
    def from_dict(cls, data: dict) -> "StopEvent":
        return cls(
            start=datetime.fromisoformat(data["start"]),
            end=datetime.fromisoformat(data["end"]),
            latitude=data["latitude"],
            longitude=data["longitude"],
            max_speed_mps=data.get("max_speed_mps", 0.0),
            feed_stop_id=data.get("feed_stop_id", ""),
        )

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "midpoint": self.midpoint.isoformat(),
            "duration_seconds": self.duration_seconds,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "max_speed_mps": self.max_speed_mps,
            "feed_stop_id": self.feed_stop_id,
        }


@dataclass
class TripOdometry:
    trip_key: str
    fixes: list = field(default_factory=list)
    distance_m: float = 0.0
    moving_time_seconds: float = 0.0
    stop_events: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "trip_key": self.trip_key,
            "fixes": [fix.to_dict() for fix in self.fixes],
            "distance_m": self.distance_m,
            "moving_time_seconds": self.moving_time_seconds,
            "stop_events": [event.to_dict() for event in self.stop_events],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TripOdometry":
        return cls(
            trip_key=data["trip_key"],
            fixes=[Fix.from_dict(fix) for fix in data.get("fixes", [])],
            distance_m=data.get("distance_m", 0.0),
            moving_time_seconds=data.get("moving_time_seconds", 0.0),
            stop_events=[StopEvent.from_dict(event) for event in data.get("stop_events", [])],
        )


def _fixes_close(a: Fix, b: Fix, radius_m: float) -> bool:
    return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude) <= radius_m


def detect_stops(
    fixes: list[Fix],
    stop_radius_m: float = 150.0,
    min_duration_seconds: float = 60.0,
    max_gap_seconds: float = 180.0,
) -> list[StopEvent]:
    """Detect stop events from a time-ordered list of fixes.

    A stop is a maximal run of consecutive fixes that all lie within
    ``stop_radius_m`` of the run's first fix, lasting at least
    ``min_duration_seconds``. Runs separated by more than ``max_gap_seconds``
    are split (a missing fix must not merge two distinct stops).
    """
    events: list[StopEvent] = []
    if not fixes:
        return events
    run_start = 0
    for index in range(1, len(fixes) + 1):
        boundary = index == len(fixes)
        if not boundary:
            gap = (fixes[index].timestamp - fixes[index - 1].timestamp).total_seconds()
            close = _fixes_close(fixes[run_start], fixes[index], stop_radius_m)
            boundary = gap > max_gap_seconds or not close
        if boundary:
            run = fixes[run_start:index]
            duration = (run[-1].timestamp - run[0].timestamp).total_seconds()
            if duration >= min_duration_seconds:
                lat = sum(fix.latitude for fix in run) / len(run)
                lon = sum(fix.longitude for fix in run) / len(run)
                speeds = [fix.speed for fix in run if fix.speed is not None]
                events.append(
                    StopEvent(
                        start=run[0].timestamp,
                        end=run[-1].timestamp,
                        latitude=lat,
                        longitude=lon,
                        max_speed_mps=max(speeds) if speeds else 0.0,
                        feed_stop_id=next((fix.stop_id for fix in run if fix.stop_id), ""),
                    )
                )
            run_start = index
    return events


def compute_odometry(
    trip_key: str,
    fixes: list[Fix],
    stop_radius_m: float = 150.0,
    min_stop_duration_seconds: float = 60.0,
) -> TripOdometry:
    ordered = sorted(fixes, key=lambda fix: fix.timestamp)
    distance = 0.0
    moving_time = 0.0
    for previous, current in zip(ordered, ordered[1:]):
        distance += haversine_m(
            previous.latitude, previous.longitude, current.latitude, current.longitude
        )
        dt = (current.timestamp - previous.timestamp).total_seconds()
        if dt > 0 and not _fixes_close(previous, current, stop_radius_m):
            moving_time += dt
    stops = detect_stops(ordered, stop_radius_m=stop_radius_m, min_duration_seconds=min_stop_duration_seconds)
    return TripOdometry(
        trip_key=trip_key,
        fixes=ordered,
        distance_m=distance,
        moving_time_seconds=moving_time,
        stop_events=stops,
    )


def estimate_arrival_at(
    odometry: TripOdometry,
    latitude: float,
    longitude: float,
    radius_m: float = 400.0,
) -> datetime | None:
    """Best odometry-based estimate of when the train stopped at a station.

    Returns the midpoint of the first stop event within ``radius_m`` of the
    station coordinates, or None if the train never stopped there.
    """
    for event in odometry.stop_events:
        if haversine_m(event.latitude, event.longitude, latitude, longitude) <= radius_m:
            return event.midpoint
    return None
