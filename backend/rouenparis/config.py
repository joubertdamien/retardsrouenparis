import os
from dataclasses import dataclass

DEFAULT_API_BASE = "https://api.sncf.com/v1"
DEFAULT_COVERAGE = "sncf"

ROUEN_RIVE_DROITE_UIC = "8711017"
PARIS_ST_LAZARE_UIC = "8727100"

ROUEN_RIVE_DROITE_STOP_AREA = "stop_area:SNCF:87411017"
PARIS_ST_LAZARE_STOP_AREA = "stop_area:SNCF:87271007"

ROUEN_RIVE_DROITE_UIC8 = "87411017"
PARIS_ST_LAZARE_UIC8 = "87271007"

PARIS_ST_LAZARE_UIC8_ALL = (
    "87271007",
    "87384008",
)

ROUEN_PARIS_CORRIDOR_UIC8 = (
    "87411017",
    "87411207",
    "87415877",
    "87415620",
    "87415604",
    "87381509",
    "87317586",
    "87317263",
    "87342014",
)

LOCAL_TZ = "Europe/Paris"

DEFAULT_GTFS_RT_TRIP_UPDATES_URL = (
    "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-trip-updates"
)

DEFAULT_GTFS_RT_VEHICLE_POSITIONS_URL = (
    "https://proxy.transport.data.gouv.fr/resource/sncf-gtfs-rt-vehicle-positions"
)


@dataclass(frozen=True)
class Settings:
    api_base: str = DEFAULT_API_BASE
    coverage: str = DEFAULT_COVERAGE
    api_token: str = ""
    origin_stop_area: str = ROUEN_RIVE_DROITE_STOP_AREA
    destination_stop_area: str = PARIS_ST_LAZARE_STOP_AREA
    origin_uic: str = ROUEN_RIVE_DROITE_UIC
    destination_uic: str = PARIS_ST_LAZARE_UIC
    origin_uic8: str = ROUEN_RIVE_DROITE_UIC8
    destination_uic8: str = PARIS_ST_LAZARE_UIC8
    destination_uic8_all: tuple = PARIS_ST_LAZARE_UIC8_ALL
    corridor_uic8: tuple = ROUEN_PARIS_CORRIDOR_UIC8
    data_dir: str = "data"
    site_dir: str = "site-build"
    lookahead_minutes: int = 240
    poll_interval_minutes: int = 15
    min_delay_minutes: int = 5
    snapshots_per_day: int = 0
    lines: tuple = ()
    gtfs_rt_trip_updates_url: str = DEFAULT_GTFS_RT_TRIP_UPDATES_URL
    gtfs_rt_vehicle_positions_url: str = DEFAULT_GTFS_RT_VEHICLE_POSITIONS_URL
    gtfs_rt_route_ids: tuple = ()
    track_positions: bool = True
    track_trip_updates: bool = True
    stop_radius_m: float = 150.0
    min_stop_duration_seconds: float = 60.0
    station_latitude: float = 48.8809
    station_longitude: float = 2.3206

    @classmethod
    def from_env(cls, env=None, require_token: bool = True):
        env = os.environ if env is None else env
        token = env.get("SNCF_API_TOKEN", "")
        if require_token and not token:
            raise RuntimeError(
                "SNCF_API_TOKEN is not set. Create a free token at "
                "https://numerique.sncf.com/startup/api/ and store it as a "
                "GitHub Actions secret (or export it locally)."
            )
        lines = tuple(
            line.strip()
            for line in env.get("ROUENPARIS_LINES", "").split(",")
            if line.strip()
        )
        return cls(
            api_base=env.get("SNCF_API_BASE", DEFAULT_API_BASE),
            coverage=env.get("SNCF_COVERAGE", DEFAULT_COVERAGE),
            api_token=token,
            origin_stop_area=env.get("ROUENPARIS_ORIGIN_STOP_AREA", ROUEN_RIVE_DROITE_STOP_AREA),
            destination_stop_area=env.get(
                "ROUENPARIS_DESTINATION_STOP_AREA", PARIS_ST_LAZARE_STOP_AREA
            ),
            origin_uic=env.get("ROUENPARIS_ORIGIN_UIC", ROUEN_RIVE_DROITE_UIC),
            destination_uic=env.get("ROUENPARIS_DESTINATION_UIC", PARIS_ST_LAZARE_UIC),
            origin_uic8=env.get("ROUENPARIS_ORIGIN_UIC8", ROUEN_RIVE_DROITE_UIC8),
            destination_uic8=env.get("ROUENPARIS_DESTINATION_UIC8", PARIS_ST_LAZARE_UIC8),
            destination_uic8_all=tuple(
                u.strip()
                for u in env.get(
                    "ROUENPARIS_DESTINATION_UIC8_ALL",
                    ",".join(PARIS_ST_LAZARE_UIC8_ALL),
                ).split(",")
                if u.strip()
            ),
            corridor_uic8=tuple(
                u.strip()
                for u in env.get(
                    "ROUENPARIS_CORRIDOR_UIC8",
                    ",".join(ROUEN_PARIS_CORRIDOR_UIC8),
                ).split(",")
                if u.strip()
            ),
            data_dir=env.get("ROUENPARIS_DATA_DIR", "data"),
            site_dir=env.get("ROUENPARIS_SITE_DIR", "site-build"),
            lookahead_minutes=int(env.get("ROUENPARIS_LOOKAHEAD_MINUTES", "240")),
            poll_interval_minutes=int(env.get("ROUENPARIS_POLL_INTERVAL_MINUTES", "15")),
            min_delay_minutes=int(env.get("ROUENPARIS_MIN_DELAY_MINUTES", "5")),
            snapshots_per_day=int(env.get("ROUENPARIS_SNAPSHOTS_PER_DAY", "0")),
            lines=lines,
            gtfs_rt_trip_updates_url=env.get(
                "ROUENPARIS_GTFS_RT_TRIP_UPDATES_URL",
                DEFAULT_GTFS_RT_TRIP_UPDATES_URL,
            ),
            gtfs_rt_vehicle_positions_url=env.get(
                "ROUENPARIS_GTFS_RT_VEHICLE_POSITIONS_URL",
                DEFAULT_GTFS_RT_VEHICLE_POSITIONS_URL,
            ),
            gtfs_rt_route_ids=tuple(
                route.strip()
                for route in env.get("ROUENPARIS_GTFS_RT_ROUTE_IDS", "").split(",")
                if route.strip()
            ),
            track_positions=env.get("ROUENPARIS_TRACK_POSITIONS", "1") not in ("0", "false", "no"),
            track_trip_updates=env.get("ROUENPARIS_TRACK_TRIP_UPDATES", "1") not in ("0", "false", "no"),
            stop_radius_m=float(env.get("ROUENPARIS_STOP_RADIUS_M", "150")),
            min_stop_duration_seconds=float(env.get("ROUENPARIS_MIN_STOP_DURATION_S", "60")),
            station_latitude=float(env.get("ROUENPARIS_STATION_LATITUDE", "48.8809")),
            station_longitude=float(env.get("ROUENPARIS_STATION_LONGITUDE", "2.3206")),
        )
