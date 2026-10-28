import unittest
from datetime import datetime, timedelta

from fixtures import TZ
from rouenparis.odometry import (
    Fix,
    compute_odometry,
    detect_stops,
    estimate_arrival_at,
    haversine_m,
)

ROUEN = (49.4431, 1.0993)
PARIS_ST_LAZARE = (48.8809, 2.3206)


def fix(at: datetime, lat: float, lon: float, speed=None, stop_id: str = "") -> Fix:
    return Fix(timestamp=at, latitude=lat, longitude=lon, speed=speed, stop_id=stop_id)


class HaversineTest(unittest.TestCase):
    def test_rouen_paris_distance(self):
        distance = haversine_m(ROUEN[0], ROUEN[1], PARIS_ST_LAZARE[0], PARIS_ST_LAZARE[1])
        # Rouen - Paris Saint-Lazare is about 110 km as the crow flies
        self.assertAlmostEqual(distance / 1000, 110, delta=10)

    def test_zero_distance(self):
        self.assertEqual(haversine_m(48.0, 2.0, 48.0, 2.0), 0.0)


class StopDetectionTest(unittest.TestCase):
    def test_detects_single_stop(self):
        start = datetime(2026, 10, 15, 8, 25, tzinfo=TZ)
        fixes = []
        # Train approaches, stops for 3 minutes, then leaves
        fixes.append(fix(start - timedelta(minutes=2), 48.8830, 2.3200, speed=20.0))
        for minute in range(3):
            fixes.append(fix(start + timedelta(minutes=minute), 48.8809, 2.3206, speed=0.0, stop_id="StopArea:OCE87271007"))
        fixes.append(fix(start + timedelta(minutes=4), 48.8790, 2.3210, speed=15.0))
        stops = detect_stops(fixes, stop_radius_m=150, min_duration_seconds=60)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].start, start)
        self.assertEqual(stops[0].end, start + timedelta(minutes=2))
        self.assertEqual(stops[0].feed_stop_id, "StopArea:OCE87271007")
        self.assertAlmostEqual(stops[0].latitude, 48.8809, places=5)

    def test_ignores_brief_slowdown(self):
        start = datetime(2026, 10, 15, 8, 25, tzinfo=TZ)
        fixes = [
            fix(start, 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(seconds=30), 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(minutes=2), 48.8790, 2.3210, speed=15.0),
        ]
        stops = detect_stops(fixes, min_duration_seconds=60)
        self.assertEqual(stops, [])

    def test_gap_splits_runs(self):
        start = datetime(2026, 10, 15, 8, 25, tzinfo=TZ)
        # Two genuine 2-minute stops separated by a 10-minute feed gap.
        fixes = [
            fix(start, 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(minutes=1), 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(seconds=90), 48.8850, 2.3250, speed=25.0),
            # 10-minute gap in the feed: must not merge with the next stop
            fix(start + timedelta(minutes=11), 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(minutes=12), 48.8809, 2.3206, speed=0.0),
            fix(start + timedelta(seconds=810), 48.8850, 2.3250, speed=25.0),
        ]
        stops = detect_stops(fixes, min_duration_seconds=60, max_gap_seconds=180)
        self.assertEqual(len(stops), 2)
        self.assertEqual(stops[1].start, start + timedelta(minutes=11))

    def test_empty(self):
        self.assertEqual(detect_stops([]), [])


class OdometryTest(unittest.TestCase):
    def make_trip(self):
        start = datetime(2026, 10, 15, 7, 12, tzinfo=TZ)
        fixes = []
        # Rouen departure
        fixes.append(fix(start, ROUEN[0], ROUEN[1], speed=0.0))
        # Journey: fixes every 2 minutes along a rough line
        for step in range(1, 30):
            lat = ROUEN[0] + (PARIS_ST_LAZARE[0] - ROUEN[0]) * step / 30
            lon = ROUEN[1] + (PARIS_ST_LAZARE[1] - ROUEN[1]) * step / 30
            fixes.append(fix(start + timedelta(minutes=2 * step), lat, lon, speed=30.0))
        # Stop at Paris Saint-Lazare for 4 minutes
        arrival = start + timedelta(minutes=60)
        for minute in range(4):
            fixes.append(fix(arrival + timedelta(minutes=minute), PARIS_ST_LAZARE[0], PARIS_ST_LAZARE[1], speed=0.0, stop_id="StopArea:OCE87271007"))
        return compute_odometry("OCESN3125F", fixes, stop_radius_m=150, min_stop_duration_seconds=60)

    def test_compute_odometry(self):
        odometry = self.make_trip()
        self.assertEqual(odometry.trip_key, "OCESN3125F")
        self.assertGreater(odometry.distance_m, 100_000)
        self.assertLess(odometry.distance_m, 180_000)
        self.assertGreater(odometry.moving_time_seconds, 0)
        # Only the Paris Saint-Lazare arrival stop is long enough: the single
        # Rouen departure fix is a 0-second "stop" and is filtered out.
        self.assertEqual(len(odometry.stop_events), 1)
        self.assertEqual(odometry.stop_events[0].feed_stop_id, "StopArea:OCE87271007")

    def test_estimate_arrival_at_station(self):
        odometry = self.make_trip()
        arrival = estimate_arrival_at(odometry, PARIS_ST_LAZARE[0], PARIS_ST_LAZARE[1])
        self.assertIsNotNone(arrival)
        expected_midpoint = datetime(2026, 10, 15, 8, 12, tzinfo=TZ) + timedelta(minutes=1, seconds=30)
        self.assertEqual(arrival, expected_midpoint)

    def test_estimate_arrival_uses_first_stop_near_station(self):
        odometry = self.make_trip()
        # Rouen is ~110 km away: no stop should match there
        self.assertIsNone(estimate_arrival_at(odometry, ROUEN[0], ROUEN[1], radius_m=400))

    def test_estimate_arrival_unknown_station(self):
        odometry = self.make_trip()
        self.assertIsNone(estimate_arrival_at(odometry, 45.0, -1.0))

    def test_serialization_roundtrip(self):
        odometry = self.make_trip()
        from rouenparis.odometry import TripOdometry

        restored = TripOdometry.from_dict(odometry.to_dict())
        self.assertEqual(restored.to_dict(), odometry.to_dict())


if __name__ == "__main__":
    unittest.main()
