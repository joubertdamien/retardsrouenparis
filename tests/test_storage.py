import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fixtures import TZ, make_snapshot
from rouenparis.storage import (
    Snapshot,
    append_snapshot,
    dedupe_final_observations,
    load_snapshots,
)


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_load(self):
        snapshot = make_snapshot(datetime(2026, 10, 15, 7, 0, tzinfo=TZ))
        path = append_snapshot(self.data_dir, snapshot)
        self.assertTrue(path.name == "2026-10-15.jsonl")

        loaded = list(load_snapshots(self.data_dir))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(len(loaded[0].trains), 3)
        self.assertEqual(loaded[0].trains[0].to_dict(), snapshot.trains[0].to_dict())

    def test_load_filters_by_date_range(self):
        day1 = make_snapshot(datetime(2026, 10, 15, 7, 0, tzinfo=TZ))
        day2 = make_snapshot(datetime(2026, 10, 16, 7, 0, tzinfo=TZ))
        append_snapshot(self.data_dir, day1)
        append_snapshot(self.data_dir, day2)

        from datetime import date

        loaded = list(load_snapshots(self.data_dir, start=date(2026, 10, 16)))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].collected_at.date(), date(2026, 10, 16))

    def test_dedupe_prefers_later_realtime_observation(self):
        early = make_snapshot(datetime(2026, 10, 15, 6, 55, tzinfo=TZ))
        late = make_snapshot(datetime(2026, 10, 15, 9, 5, tzinfo=TZ))
        # Simulate the same train seen early (base schedule only) and late (realtime)
        import dataclasses

        early.trains[1] = dataclasses.replace(
            early.trains[1],
            realtime_departure=None,
            departure_delay_seconds=None,
            departure_state="base_schedule",
            realtime_arrival=None,
            arrival_delay_seconds=None,
            arrival_state="base_schedule",
        )
        snapshots = [early, late]
        final = dedupe_final_observations(snapshots)
        self.assertEqual(len(final), 3)
        by_number = {train.train_number: train for train in final}
        self.assertEqual(by_number["3125"].arrival_delay_seconds, 1140)
        self.assertEqual(by_number["3125"].departure_delay_seconds, 720)

    def test_snapshot_serialization_roundtrip(self):
        snapshot = make_snapshot(datetime(2026, 10, 15, 7, 0, tzinfo=TZ))
        restored = Snapshot.from_dict(snapshot.to_dict())
        self.assertEqual(restored.to_dict(), snapshot.to_dict())


if __name__ == "__main__":
    unittest.main()
