import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fixtures import TZ
from rouenparis.config import Settings
from rouenparis.positions import (
    append_position_records,
    build_odometries,
    load_position_records,
    match_odometry_to_train,
    train_number_from_trip_id,
)


def make_record(trip_id: str, collected_at: datetime, timestamp: int, lat: float, lon: float, speed=0.0, stop_id: str = ""):
    return {
        "collected_at": collected_at.isoformat(),
        "entity_id": f"entity-{trip_id}-{timestamp}",
        "trip_id": trip_id,
        "route_id": "OCE:Train:1",
        "direction_id": 0,
        "stop_id": stop_id,
        "current_stop_sequence": 1,
        "current_status": "STOPPED_AT" if speed == 0 else "IN_TRANSIT_TO",
        "timestamp": timestamp,
        "position": {"latitude": lat, "longitude": lon, "bearing": 90.0, "speed": speed},
        "vehicle_id": "TGV-3125",
        "vehicle_label": "3125",
    }


def make_trip_records(train: str, day: date, start_hour: int, lat: float, lon: float):
    """Simulate ~35 fixes of a train stopping at lat/lon for 3 minutes."""
    trip_id = f"OCESN{train}F"
    base = datetime(day.year, day.month, day.day, start_hour, 0, tzinfo=TZ)
    records = []
    ts = int(base.timestamp())
    for i in range(35):
        records.append(
            make_record(
                trip_id,
                base + timedelta(seconds=30 * i),
                ts + 30 * i,
                lat + (0.0001 if 30 <= i < 33 else 0.0),
                lon,
                speed=0.0 if 30 <= i < 33 else 25.0,
                stop_id="StopArea:OCE87271007" if 30 <= i < 33 else "",
            )
        )
    return records


class TrainNumberExtractionTest(unittest.TestCase):
    def test_extracts_commercial_number(self):
        self.assertEqual(train_number_from_trip_id("OCESN3125F"), "3125")
        self.assertEqual(train_number_from_trip_id("OCE850627R"), "850627")
        self.assertEqual(train_number_from_trip_id("OCESN3401F"), "3401")

    def test_returns_none_without_digits(self):
        self.assertIsNone(train_number_from_trip_id("UNKNOWN"))


class PositionStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_load(self):
        day = date(2026, 10, 15)
        records = make_trip_records("3125", day, 8, 48.8809, 2.3206)
        path = append_position_records(self.data_dir, records)
        self.assertEqual(path.name, "2026-10-15.jsonl")
        loaded = list(load_position_records(self.data_dir))
        self.assertEqual(len(loaded), 35)

    def test_load_filters_by_date(self):
        day1 = date(2026, 10, 14)
        day2 = date(2026, 10, 15)
        append_position_records(self.data_dir, make_trip_records("3123", day1, 7, 49.44, 1.09))
        append_position_records(self.data_dir, make_trip_records("3125", day2, 8, 48.88, 2.32))
        loaded = list(load_position_records(self.data_dir, start=date(2026, 10, 15)))
        self.assertEqual(len(loaded), 35)
        self.assertTrue(all(record["trip_id"] == "OCESN3125F" for record in loaded))

    def test_build_odometries_and_match(self):
        day = date(2026, 10, 15)
        append_position_records(self.data_dir, make_trip_records("3125", day, 8, 48.8809, 2.3206))
        odometries = build_odometries(load_position_records(self.data_dir))
        self.assertIn("OCESN3125F", odometries)
        odometry = odometries["OCESN3125F"]
        self.assertEqual(len(odometry.stop_events), 1)
        self.assertEqual(odometry.stop_events[0].feed_stop_id, "StopArea:OCE87271007")

        base_departure = datetime(2026, 10, 15, 8, 0, tzinfo=TZ)
        matched = match_odometry_to_train(odometries, "3125", base_departure)
        self.assertIs(matched, odometry)

        # A train with no matching number and no trip starting near its
        # scheduled departure must not be matched by the time-based fallback.
        far_away = datetime(2026, 10, 15, 3, 0, tzinfo=TZ)
        unmatched = match_odometry_to_train(odometries, "9999", far_away)
        self.assertIsNone(unmatched)

    def test_vehicle_positions_client_with_fake_opener(self):
        from test_gtfsrt_proto import build_feed, build_vehicle_entity
        from rouenparis.vehicle_positions import VehiclePositionsClient

        payload = build_feed(
            [
                build_vehicle_entity("e1", "OCESN3125F", "OCE:Train:1", 48.88, 2.32, speed=10.0),
                build_vehicle_entity("e2", "OCESN9999F", "OCE:Train:2", 45.0, 1.0, speed=10.0),
            ]
        )

        class FakeOpener:
            def open(self, request, timeout=30):
                import io

                return io.BytesIO(payload)

        settings = Settings(api_token="x", gtfs_rt_route_ids=("OCE:Train:1",))
        client = VehiclePositionsClient(settings, opener=FakeOpener())
        records = client.fetch_vehicles_for_route(datetime(2026, 10, 15, 8, 0, tzinfo=TZ))
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["trip_id"], "OCESN3125F")
        self.assertAlmostEqual(records[0]["position"]["latitude"], 48.88, places=5)


if __name__ == "__main__":
    unittest.main()
