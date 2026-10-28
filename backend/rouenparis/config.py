import os
from dataclasses import dataclass, field

DEFAULT_API_BASE = "https://api.sncf.com/v1"
DEFAULT_COVERAGE = "sncf"

ROUEN_RIVE_DROITE_UIC = "8711017"
PARIS_ST_LAZARE_UIC = "8727100"

ROUEN_RIVE_DROITE_STOP_AREA = "stop_area:SNCF:87411017"
PARIS_ST_LAZARE_STOP_AREA = "stop_area:SNCF:87271007"

LOCAL_TZ = "Europe/Paris"


@dataclass(frozen=True)
class Settings:
    api_base: str = DEFAULT_API_BASE
    coverage: str = DEFAULT_COVERAGE
    api_token: str = ""
    origin_stop_area: str = ROUEN_RIVE_DROITE_STOP_AREA
    destination_stop_area: str = PARIS_ST_LAZARE_STOP_AREA
    origin_uic: str = ROUEN_RIVE_DROITE_UIC
    destination_uic: str = PARIS_ST_LAZARE_UIC
    data_dir: str = "data"
    site_dir: str = "site-build"
    lookahead_minutes: int = 240
    poll_interval_minutes: int = 15
    min_delay_minutes: int = 5
    snapshots_per_day: int = 0
    lines: tuple = ()

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
            data_dir=env.get("ROUENPARIS_DATA_DIR", "data"),
            site_dir=env.get("ROUENPARIS_SITE_DIR", "site-build"),
            lookahead_minutes=int(env.get("ROUENPARIS_LOOKAHEAD_MINUTES", "240")),
            poll_interval_minutes=int(env.get("ROUENPARIS_POLL_INTERVAL_MINUTES", "15")),
            min_delay_minutes=int(env.get("ROUENPARIS_MIN_DELAY_MINUTES", "5")),
            snapshots_per_day=int(env.get("ROUENPARIS_SNAPSHOTS_PER_DAY", "0")),
            lines=lines,
        )
