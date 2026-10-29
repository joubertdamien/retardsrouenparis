from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import Settings
from .positions import append_position_records
from .reports import generate_all
from .sncf_client import SncfClient
from .storage import TZ, Snapshot, append_snapshot
from .trip_storage import append_trip_profiles
from .trip_updates import TripUpdatesClient
from .vehicle_positions import VehiclePositionsClient

logger = logging.getLogger(__name__)


def collect_trip_updates(settings: Settings) -> int:
    """Collect the GTFS-RT TripUpdates feed and store profiles for our route.

    The TripUpdates feed covers trains running in the next ~60 minutes.
    We store profiles of trips that serve *both* Rouen Rive Droite and
    Paris Saint-Lazare, plus trips serving either station (so we can track
    partial trips too).
    """
    if not settings.track_trip_updates:
        logger.info("trip-updates tracking disabled (ROUENPARIS_TRACK_TRIP_UPDATES=0)")
        return 0
    client = TripUpdatesClient(settings)
    origin = settings.origin_uic8
    destination = settings.destination_uic8
    profiles = client.fetch_profiles()
    selected = []
    for profile in profiles:
        uics = {s.uic8 for s in profile.stops if s.uic8}
        if origin in uics or destination in uics:
            selected.append(profile)
    if not selected:
        logger.warning("no TripUpdates profiles matched Rouen/Paris Saint-Lazare")
        return 0
    path = append_trip_profiles(settings.data_dir, selected)
    logger.info("%d trip profiles stored in %s", len(selected), path)
    return len(selected)


def collect_positions(settings: Settings) -> int:
    if not settings.track_positions:
        logger.info("position tracking disabled (ROUENPARIS_TRACK_POSITIONS=0)")
        return 0
    client = VehiclePositionsClient(settings)
    records = client.fetch_vehicles_for_route()
    if not records:
        logger.warning("no vehicle positions matched the configured routes")
        return 0
    path = append_position_records(settings.data_dir, records)
    logger.info("%d vehicle positions stored in %s", len(records), path)
    return len(records)


def cmd_collect(args) -> int:
    try:
        settings = Settings.from_env()
    except RuntimeError:
        logger.warning(
            "SNCF_API_TOKEN not set — collecting GTFS-RT TripUpdates only "
            "(no API SNCF departures). The page will show delayed trains "
            "from the TripUpdates feed."
        )
        settings = Settings.from_env(require_token=False)
        collect_trip_updates(settings)
        try:
            collect_positions(settings)
        except Exception as error:
            logger.warning("position collection failed: %s", error)
        return 0
    client = SncfClient(settings)
    now = datetime.now(tz=TZ)
    if args.at:
        naive = datetime.strptime(args.at, "%Y%m%dT%H%M%S")
        now = naive.replace(tzinfo=TZ)
    for index in range(args.count):
        started = datetime.now(tz=TZ)
        try:
            observations = client.fetch_departures(started)
        except Exception as error:
            logger.warning("SNCF API fetch failed: %s — falling back to TripUpdates only", error)
            observations = []
        if observations:
            snapshot = Snapshot(collected_at=started, trains=observations)
            path = append_snapshot(settings.data_dir, snapshot)
            logger.info(
                "snapshot %d: %d departures stored in %s", index + 1, len(observations), path
            )
        try:
            collect_trip_updates(settings)
        except Exception as error:
            logger.warning("trip-updates collection failed: %s", error)
        try:
            collect_positions(settings)
        except Exception as error:
            logger.warning("position collection failed: %s", error)
        if index + 1 < args.count:
            time.sleep(args.interval_seconds)
    return 0


def cmd_collect_trip_updates(args) -> int:
    settings = Settings.from_env(require_token=False)
    collect_trip_updates(settings)
    return 0


def cmd_collect_positions(args) -> int:
    settings = Settings.from_env(require_token=False)
    collect_positions(settings)
    return 0


def cmd_report(args) -> int:
    settings = Settings.from_env(require_token=False)
    index = generate_all(settings.data_dir, settings.site_dir, settings)
    for period in index["periods"]:
        logger.info(
            "%s: %d reports generated", period["type"], len(period["reports"])
        )
    logger.info("reports written to %s", settings.site_dir)
    return 0


def cmd_all(args) -> int:
    collect = argparse.Namespace(
        at=args.at, count=args.count, interval_seconds=args.interval_seconds
    )
    cmd_collect(collect)
    return cmd_report(argparse.Namespace())


def cmd_resolve_stations(args) -> int:
    from .stations import resolve_stop_area

    settings = Settings.from_env()
    origin = resolve_stop_area(
        settings.api_base, settings.coverage, settings.api_token, settings.origin_uic
    )
    destination = resolve_stop_area(
        settings.api_base, settings.coverage, settings.api_token, settings.destination_uic
    )
    print(f"ROUENPARIS_ORIGIN_STOP_AREA={origin}")
    print(f"ROUENPARIS_DESTINATION_STOP_AREA={destination}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="rouenparis")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="enable debug logging"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="fetch departures and store a snapshot")
    collect.add_argument("--at", help="override collection time, format YYYYMMDDTHHMMSS (local)")
    collect.add_argument("--count", type=int, default=1, help="number of snapshots to take")
    collect.add_argument(
        "--interval-seconds", type=int, default=900, help="delay between snapshots"
    )
    collect.set_defaults(func=cmd_collect)

    report = subparsers.add_parser("report", help="generate JSON reports from stored data")
    report.set_defaults(func=cmd_report)

    all_cmd = subparsers.add_parser("all", help="collect then generate reports")
    all_cmd.add_argument("--at", help="override collection time, format YYYYMMDDTHHMMSS (local)")
    all_cmd.add_argument("--count", type=int, default=1)
    all_cmd.add_argument("--interval-seconds", type=int, default=900)
    all_cmd.set_defaults(func=cmd_all)

    trip_updates = subparsers.add_parser(
        "collect-trip-updates",
        help="fetch GTFS-RT TripUpdates profiles for Rouen/Paris (no API token needed)",
    )
    trip_updates.set_defaults(func=cmd_collect_trip_updates)

    positions = subparsers.add_parser(
        "collect-positions",
        help="fetch GTFS-RT vehicle positions (no API token needed)",
    )
    positions.set_defaults(func=cmd_collect_positions)

    resolve = subparsers.add_parser(
        "resolve-stations", help="resolve stop_area ids from UIC codes"
    )
    resolve.set_defaults(func=cmd_resolve_stations)

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        return args.func(args)
    except Exception as error:
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
