from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings

logger = logging.getLogger(__name__)


class SncfApiError(RuntimeError):
    def __init__(self, status: int, message: str):
        super().__init__(f"SNCF API error {status}: {message}")
        self.status = status


@dataclass
class TrainObservation:
    train_number: str
    line_code: str
    line_name: str
    direction: str
    base_departure: datetime
    realtime_departure: datetime | None
    departure_delay_seconds: int | None
    departure_state: str
    base_arrival: datetime | None
    realtime_arrival: datetime | None
    arrival_delay_seconds: int | None
    arrival_state: str
    status: str
    data_freshness: str
    collected_at: datetime

    def to_dict(self) -> dict:
        def iso(value):
            return value.isoformat() if value is not None else None

        return {
            "train_number": self.train_number,
            "line_code": self.line_code,
            "line_name": self.line_name,
            "direction": self.direction,
            "base_departure": iso(self.base_departure),
            "realtime_departure": iso(self.realtime_departure),
            "departure_delay_seconds": self.departure_delay_seconds,
            "departure_state": self.departure_state,
            "base_arrival": iso(self.base_arrival),
            "realtime_arrival": iso(self.realtime_arrival),
            "arrival_delay_seconds": self.arrival_delay_seconds,
            "arrival_state": self.arrival_state,
            "status": self.status,
            "data_freshness": self.data_freshness,
            "collected_at": iso(self.collected_at),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TrainObservation":
        def parse(value):
            return datetime.fromisoformat(value) if value else None

        return cls(
            train_number=data["train_number"],
            line_code=data.get("line_code", ""),
            line_name=data.get("line_name", ""),
            direction=data.get("direction", ""),
            base_departure=parse(data["base_departure"]),
            realtime_departure=parse(data.get("realtime_departure")),
            departure_delay_seconds=data.get("departure_delay_seconds"),
            departure_state=data.get("departure_state", ""),
            base_arrival=parse(data.get("base_arrival")),
            realtime_arrival=parse(data.get("realtime_arrival")),
            arrival_delay_seconds=data.get("arrival_delay_seconds"),
            arrival_state=data.get("arrival_state", ""),
            status=data.get("status", ""),
            data_freshness=data.get("data_freshness", ""),
            collected_at=parse(data["collected_at"]),
        )


def _navitia_datetime(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S")


def _parse_navitia_datetime(value: str, tz: ZoneInfo) -> datetime:
    parsed = datetime.strptime(value, "%Y%m%dT%H%M%S")
    return parsed.replace(tzinfo=tz)


class SncfClient:
    def __init__(self, settings: Settings, opener=None, max_retries: int = 3):
        self.settings = settings
        self._opener = opener or urllib.request.build_opener()
        self.max_retries = max_retries
        self.tz = ZoneInfo("Europe/Paris")

    def _get_json(self, url: str) -> dict:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            request = urllib.request.Request(url)
            request.add_header("Authorization", self.settings.api_token)
            request.add_header("Accept", "application/json")
            try:
                with self._opener.open(request, timeout=30) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                body = error.read().decode("utf-8", errors="replace")[:500]
                if error.code in (429, 500, 502, 503, 504) and attempt < self.max_retries - 1:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "SNCF API HTTP %s, retrying in %ss (%s)", error.code, wait, body
                    )
                    time.sleep(wait)
                    last_error = SncfApiError(error.code, body)
                    continue
                raise SncfApiError(error.code, body) from error
            except urllib.error.URLError as error:
                last_error = SncfApiError(0, str(error.reason))
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise last_error from error
        raise last_error or SncfApiError(0, "unknown error")

    def fetch_departures(self, from_datetime: datetime) -> list[TrainObservation]:
        collected_at = datetime.now(tz=self.tz)
        params = {
            "from_datetime": _navitia_datetime(from_datetime),
            "until_datetime": _navitia_datetime(
                from_datetime + timedelta(minutes=self.settings.lookahead_minutes)
            ),
            "data_freshness": "realtime",
            "count": 1000,
            "disable_geojson": "true",
        }
        if self.settings.lines:
            params["filter"] = " OR ".join(
                f'line.uri="{line}"' for line in self.settings.lines
            )
        query = urllib.parse.urlencode(params)
        url = (
            f"{self.settings.api_base}/coverage/{self.settings.coverage}"
            f"/stop_areas/{self.settings.origin_stop_area}/departures?{query}"
        )
        payload = self._get_json(url)
        observations = []
        for departure in payload.get("departures", []):
            observation = self._parse_departure(departure, collected_at)
            if observation is not None:
                observations.append(observation)
        observations.sort(key=lambda obs: obs.base_departure)
        return observations

    def _parse_departure(self, departure: dict, collected_at: datetime) -> TrainObservation | None:
        route = departure.get("route", {})
        line = route.get("line", {})
        display = departure.get("display_informations", {})
        stop_date_time = departure.get("stop_date_time", {})

        line_code = display.get("code", "") or line.get("code", "")
        line_name = display.get("label", "") or line.get("name", "")

        base_departure = stop_date_time.get("base_departure_date_time")
        if not base_departure:
            return None
        base_departure_dt = _parse_navitia_datetime(base_departure, self.tz)
        realtime_departure = stop_date_time.get("departure_date_time")
        realtime_departure_dt = (
            _parse_navitia_datetime(realtime_departure, self.tz)
            if realtime_departure
            else None
        )
        departure_delay = stop_date_time.get("departure_delay")
        departure_state = stop_date_time.get("data_freshness", "base_schedule")

        vj = departure.get("vehicle_journey", {})
        stops = vj.get("stop_times", [])
        arrival_info = self._find_arrival_at_destination(stops)

        status = display.get("status", "")
        if status == "supprimé":
            status = "cancelled"
        elif status in ("modifié",):
            status = "modified"

        return TrainObservation(
            train_number=display.get("headsign", "") or vj.get("name", ""),
            line_code=line_code,
            line_name=line_name,
            direction=display.get("direction", "") or route.get("direction", {}).get("name", ""),
            base_departure=base_departure_dt,
            realtime_departure=realtime_departure_dt,
            departure_delay_seconds=departure_delay,
            departure_state=departure_state,
            base_arrival=arrival_info[0],
            realtime_arrival=arrival_info[1],
            arrival_delay_seconds=arrival_info[2],
            arrival_state=arrival_info[3],
            status=status,
            data_freshness=departure_state,
            collected_at=collected_at,
        )

    def _find_arrival_at_destination(self, stop_times: list[dict]):
        dest = self.settings.destination_stop_area
        uic = self.settings.destination_uic
        for stop_time in stop_times:
            stop_point = stop_time.get("stop_point", {})
            if stop_point.get("stop_area", {}).get("id") == dest:
                return self._arrival_tuple(stop_time, "arrival")
        for stop_time in stop_times:
            stop_point = stop_time.get("stop_point", {})
            codes = stop_point.get("codes", []) or stop_point.get("stop_area", {}).get("codes", [])
            for code in codes:
                if code.get("type") == "UIC" and str(code.get("value", "")).lstrip("0") == uic.lstrip("0"):
                    return self._arrival_tuple(stop_time, "arrival")
        return (None, None, None, "")

    def _arrival_tuple(self, stop_time: dict, kind: str):
        base = stop_time.get(f"base_{kind}_date_time")
        realtime = stop_time.get(f"{kind}_date_time")
        base_dt = _parse_navitia_datetime(base, self.tz) if base else None
        realtime_dt = _parse_navitia_datetime(realtime, self.tz) if realtime else None
        delay = stop_time.get(f"{kind}_delay")
        state = stop_time.get("data_freshness", "base_schedule")
        return (base_dt, realtime_dt, delay, state)

    def health_check(self) -> bool:
        try:
            self._get_json(f"{self.settings.api_base}/coverage/{self.settings.coverage}/")
            return True
        except SncfApiError as error:
            logger.error("SNCF API health check failed: %s", error)
            return False
