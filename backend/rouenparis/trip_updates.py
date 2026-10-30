"""GTFS-RT TripUpdates client and schedule-based odometry for SNCF trains.

SNCF does **not** publish GPS positions for its trains on any public feed.
The working alternative is the GTFS-RT **TripUpdates** feed (published on
transport.data.gouv.fr, no API token required), which carries per-stop
real-time arrival/departure times and delays for every train currently
running.

From this feed we derive a *schedule-based odometry*:

- For each trip we collect the sequence of stops with their predicted
  arrival/departure delay (``StopTimeEvent.delay``).
- A **hold event** is detected when the arrival delay at stop *k* is greater
  than the departure delay at stop *k-1*: the train accumulated extra delay
  while approaching stop *k*, which means it was held (stopped) somewhere
  between the two stations.
- The **arrival delay at Paris Saint-Lazare** extracted from TripUpdates is
  an independent second source that can be cross-checked against the delay
  announced by the Navitia API (api.sncf.com).

This replaces the earlier GPS-based odometry (VehiclePositions), whose feed
turned out not to exist on the SNCF data.gouv.fr dataset.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .gtfsrt_proto import GtfsStopTimeUpdate, GtfsTripUpdate, parse_feed

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Paris")


@dataclass
class StopPassage:
    """One stop of a trip as reported by TripUpdates."""

    stop_id: str
    stop_sequence: int | None
    arrival_delay: int | None
    arrival_time: int | None
    departure_delay: int | None
    departure_time: int | None

    @property
    def uic8(self) -> str | None:
        """8-digit UIC code embedded in the SNCF stop_id, if present."""
        match = re.search(r"(\d{8})$", self.stop_id or "")
        return match.group(1) if match else None

    def to_dict(self) -> dict:
        return {
            "stop_id": self.stop_id,
            "stop_sequence": self.stop_sequence,
            "arrival_delay": self.arrival_delay,
            "arrival_time": self.arrival_time,
            "departure_delay": self.departure_delay,
            "departure_time": self.departure_time,
            "uic8": self.uic8,
        }


@dataclass
class HoldEvent:
    """A detected hold: delay grew between the previous stop's departure and
    this stop's arrival, meaning the train was stopped/held en route."""

    stop_id: str
    uic8: str | None
    previous_stop_id: str
    previous_stop_uic8: str | None
    delay_before: int
    delay_at: int
    extra_delay_seconds: int
    arrival_time: int | None

    def to_dict(self) -> dict:
        return {
            "stop_id": self.stop_id,
            "uic8": self.uic8,
            "previous_stop_id": self.previous_stop_id,
            "previous_stop_uic8": self.previous_stop_uic8,
            "delay_before": self.delay_before,
            "delay_at": self.delay_at,
            "extra_delay_seconds": self.extra_delay_seconds,
            "arrival_time": self.arrival_time,
        }


@dataclass
class TripProfile:
    """A single TripUpdates entity: one trip with its stop-by-stop delays."""

    entity_id: str
    trip_id: str
    route_id: str
    direction_id: int | None
    start_date: str
    start_time: str
    feed_timestamp: int | None
    update_delay: int | None
    stops: list = field(default_factory=list)
    collected_at: str = ""

    @property
    def train_number(self) -> str | None:
        return train_number_from_trip_id(self.trip_id)

    def stop_by_uic(self, uic8: str) -> StopPassage | None:
        for stop in self.stops:
            if stop.uic8 == uic8:
                return stop
        return None

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "trip_id": self.trip_id,
            "route_id": self.route_id,
            "direction_id": self.direction_id,
            "start_date": self.start_date,
            "start_time": self.start_time,
            "feed_timestamp": self.feed_timestamp,
            "update_delay": self.update_delay,
            "train_number": self.train_number,
            "stops": [s.to_dict() for s in self.stops],
            "collected_at": self.collected_at,
        }


def _stop_from_update(update: GtfsStopTimeUpdate) -> StopPassage:
    return StopPassage(
        stop_id=update.stop_id,
        stop_sequence=update.stop_sequence,
        arrival_delay=update.arrival.delay,
        arrival_time=update.arrival.time,
        departure_delay=update.departure.delay,
        departure_time=update.departure.time,
    )


def profile_from_update(update: GtfsTripUpdate, collected_at: datetime) -> TripProfile:
    stops = [_stop_from_update(s) for s in update.stop_time_updates]
    stops.sort(key=lambda s: (s.arrival_time or s.departure_time or 0))
    return TripProfile(
        entity_id=update.entity_id,
        trip_id=update.trip.trip_id,
        route_id=update.trip.route_id,
        direction_id=update.trip.direction_id,
        start_date=update.trip.start_date,
        start_time=update.trip.start_time,
        feed_timestamp=update.timestamp,
        update_delay=update.delay,
        stops=stops,
        collected_at=collected_at.isoformat(),
    )


def detect_hold_events(profile: TripProfile) -> list[HoldEvent]:
    """Detect holds from the delay pattern of a trip.

    When ``arrival_delay[k] > departure_delay[k-1]`` the train accumulated
    extra delay while travelling from stop k-1 to stop k. This is the
    signature of a hold (the train was stopped somewhere between the two
    stations, or arrived late because of a preceding disruption).
    """
    events: list[HoldEvent] = []
    ordered = [s for s in profile.stops if s.arrival_delay is not None or s.departure_delay is not None]
    previous: StopPassage | None = None
    for stop in ordered:
        if previous is not None and stop.arrival_delay is not None and previous.departure_delay is not None:
            extra = stop.arrival_delay - previous.departure_delay
            if extra > 0:
                events.append(
                    HoldEvent(
                        stop_id=stop.stop_id,
                        uic8=stop.uic8,
                        previous_stop_id=previous.stop_id,
                        previous_stop_uic8=previous.uic8,
                        delay_before=previous.departure_delay,
                        delay_at=stop.arrival_delay,
                        extra_delay_seconds=extra,
                        arrival_time=stop.arrival_time,
                    )
                )
        previous = stop
    return events


_TRAIN_NUMBER_RE = re.compile(r"(\d{4,6})")


def train_number_from_trip_id(trip_id: str) -> str | None:
    """Extract the commercial train number from a SNCF GTFS-RT trip_id.

    SNCF trip ids embed the commercial number, e.g. ``OCESN7546F`` -> ``7546``.
    """
    match = _TRAIN_NUMBER_RE.search(trip_id)
    return match.group(1) if match else None


class TripUpdatesClient:
    """Fetches the SNCF GTFS-RT TripUpdates feed (no API token required).

    The feed is published by SNCF Voyageurs on the national access point
    (transport.data.gouv.fr) under the dataset "HORAIRES SNCF". It contains
    per-stop real-time predictions for trains running in the next ~60 minutes.
    """

    def __init__(self, settings: Settings, opener=None, url: str | None = None, max_retries: int = 3):
        self.settings = settings
        self.url = url or settings.gtfs_rt_trip_updates_url
        self._opener = opener or urllib.request.build_opener()
        self.max_retries = max_retries

    def _fetch(self) -> bytes:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            request = urllib.request.Request(self.url)
            request.add_header("Accept", "application/x-protobuf")
            try:
                with self._opener.open(request, timeout=60) as response:
                    return response.read()
            except urllib.error.HTTPError as error:
                last_error = RuntimeError(f"GTFS-RT HTTP {error.code}: {error.reason}")
                if error.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise last_error from error
            except urllib.error.URLError as error:
                last_error = RuntimeError(f"GTFS-RT fetch failed: {error.reason}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise last_error from error
        raise last_error or RuntimeError("unknown GTFS-RT error")

    def fetch_trip_updates(self) -> list[GtfsTripUpdate]:
        payload = self._fetch()
        feed = parse_feed(payload)
        logger.info(
            "GTFS-RT TripUpdates feed: version=%s timestamp=%s entities=%d vehicles=%d",
            feed.version, feed.timestamp, len(feed.trip_updates), len(feed.vehicles),
        )
        return feed.trip_updates

    def fetch_profiles(self, collected_at: datetime | None = None) -> list[TripProfile]:
        updates = self.fetch_trip_updates()
        collected_at = collected_at or datetime.now(tz=TZ)
        return [profile_from_update(update, collected_at) for update in updates]

    def fetch_profiles_for_stations(
        self,
        origin_uic8: str,
        destination_uic8: str,
        collected_at: datetime | None = None,
    ) -> list[TripProfile]:
        """Fetch profiles of trips that serve both origin and destination UICs."""
        profiles = self.fetch_profiles(collected_at)
        selected = []
        for profile in profiles:
            uics = {s.uic8 for s in profile.stops if s.uic8}
            if origin_uic8 in uics and destination_uic8 in uics:
                selected.append(profile)
        return selected


def cross_check_arrival_delay(
    profile: TripProfile,
    destination_uic8: str,
    announced_delay_seconds: int | None,
) -> dict:
    """Cross-check the TripUpdates arrival delay at the destination against
    the delay announced by the Navitia API.

    Returns a dict with the TripUpdates delay, the announced delay, the
    absolute difference, and a boolean ``agrees`` flag (within 60 s tolerance).
    """
    stop = profile.stop_by_uic(destination_uic8)
    tu_delay = stop.arrival_delay if stop else None
    if tu_delay is None or announced_delay_seconds is None:
        return {
            "trip_id": profile.trip_id,
            "train_number": profile.train_number,
            "destination_uic8": destination_uic8,
            "trip_updates_delay_seconds": tu_delay,
            "announced_delay_seconds": announced_delay_seconds,
            "difference_seconds": None,
            "agrees": None,
        }
    difference = abs(tu_delay - announced_delay_seconds)
    return {
        "trip_id": profile.trip_id,
        "train_number": profile.train_number,
        "destination_uic8": destination_uic8,
        "trip_updates_delay_seconds": tu_delay,
        "announced_delay_seconds": announced_delay_seconds,
        "difference_seconds": difference,
        "agrees": difference <= 60,
    }
