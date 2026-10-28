from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .gtfsrt_proto import GtfsVehiclePosition, parse_feed

logger = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Paris")


class VehiclePositionsClient:
    """Fetches the SNCF GTFS-RT VehiclePositions feed (no API token required).

    The feed is published by SNCF Voyageurs on the national access point
    (transport.data.gouv.fr). It contains the positions of TGV, Intercités and
    TER trains, refreshed roughly every 30–60 seconds.
    """

    def __init__(self, settings: Settings, opener=None, url: str | None = None, max_retries: int = 3):
        self.settings = settings
        self.url = url or settings.gtfs_rt_vehicle_positions_url
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

    def fetch_vehicles(self) -> list[GtfsVehiclePosition]:
        payload = self._fetch()
        feed = parse_feed(payload)
        logger.info(
            "GTFS-RT feed: version=%s timestamp=%s vehicles=%d",
            feed.version, feed.timestamp, len(feed.vehicles),
        )
        return feed.vehicles

    def fetch_vehicles_for_route(self, collected_at: datetime | None = None) -> list[dict]:
        vehicles = self.fetch_vehicles()
        collected_at = collected_at or datetime.now(tz=TZ)
        records = []
        for vehicle in vehicles:
            if not self._matches_route(vehicle):
                continue
            records.append(
                {
                    "collected_at": collected_at.isoformat(),
                    **vehicle.to_dict(),
                }
            )
        return records

    def _matches_route(self, vehicle: GtfsVehiclePosition) -> bool:
        routes = self.settings.gtfs_rt_route_ids
        if not routes:
            return True
        return vehicle.route_id in routes
