from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Paris")


def _stop_time(day: date, base_time: str, actual_time: str, delay: int, freshness: str = "realtime", stop_area: str = "stop_area:SNCF:87411017"):
    return {
        "base_arrival_date_time": base_time,
        "arrival_date_time": actual_time,
        "arrival_delay": delay,
        "base_departure_date_time": base_time,
        "departure_date_time": actual_time,
        "departure_delay": delay,
        "data_freshness": freshness,
        "stop_point": {
            "stop_area": {"id": stop_area},
            "codes": [{"type": "UIC", "value": "8727100"} if "87271007" in stop_area else {"type": "UIC", "value": "8711017"}],
        },
    }


def _shifted(day: date, time: str, delay_seconds: int) -> tuple[str, str]:
    base = datetime.strptime(f"{day.strftime('%Y%m%d')}T{time}", "%Y%m%dT%H%M%S")
    actual = base + timedelta(seconds=delay_seconds)
    return (
        f"{day.strftime('%Y%m%d')}T{time}",
        actual.strftime("%Y%m%dT%H%M%S"),
    )


def departures_response_for(day: date) -> dict:
    def departure(train: str, dep_time: str, dep_delay: int, arr_time: str, arr_delay: int, status: str = "", line_code: str = "K22", line_label: str = "TER"):
        base_dep, real_dep = _shifted(day, dep_time, dep_delay)
        base_arr, real_arr = _shifted(day, arr_time, arr_delay)
        origin = _stop_time(day, base_dep, real_dep, dep_delay)
        destination = _stop_time(day, base_arr, real_arr, arr_delay, stop_area="stop_area:SNCF:87271007")
        if status == "supprimé":
            destination = {}
        return {
            "display_informations": {
                "direction": "Paris Saint-Lazare",
                "code": line_code,
                "label": f"{line_label} {train}",
                "headsign": train,
                "status": status,
            },
            "route": {
                "line": {"code": line_code, "name": "Rouen - Paris"},
                "direction": {"name": "Paris Saint-Lazare"},
            },
            "stop_date_time": {
                "base_departure_date_time": base_dep,
                "departure_date_time": real_dep if not status else None,
                "departure_delay": dep_delay,
                "data_freshness": "realtime",
            },
            "vehicle_journey": {
                "name": train,
                "stop_times": [origin, destination],
            },
        }

    return {
        "departures": [
            departure("3123", "071200", 0, "083000", 0),
            departure("3125", "073000", 720, "084800", 1140),
            departure("3401", "080500", 0, "091000", 0, status="supprimé", line_code="K24", line_label="IC"),
        ],
        "pagination": {"total_result": 3, "start_page": 0, "items_per_page": 1000},
    }


DEPARTURES_RESPONSE = departures_response_for(date(2026, 10, 15))


class FakeOpener:
    def __init__(self, payload: dict):
        self.payload = payload
        self.requests = []
        self.headers = []

    def open(self, request, timeout=30):
        import io
        import json as jsonlib

        self.requests.append(request.full_url)
        self.headers.append(dict(request.header_items()))
        body = jsonlib.dumps(self.payload).encode("utf-8")
        return io.BytesIO(body)


def make_snapshot(collected_at: datetime, day: date | None = None, response: dict | None = None):
    from rouenparis.config import Settings
    from rouenparis.sncf_client import SncfClient
    from rouenparis.storage import Snapshot

    settings = Settings(api_token="test-token")
    payload = response if response is not None else departures_response_for(day or collected_at.date())
    client = SncfClient(settings, opener=FakeOpener(payload))
    trains = client.fetch_departures(collected_at)
    return Snapshot(collected_at=collected_at, trains=trains)
