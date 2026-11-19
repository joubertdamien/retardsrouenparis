"""Tests for trip_storage: append/load roundtrip and history pruning."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from fixtures import TZ
from test_trip_updates import make_profile

from rouenparis.trip_storage import (
    append_trip_profiles,
    load_trip_profiles,
    prune_excluded_profiles,
)


class PruneExcludedProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"

    def tearDown(self):
        self.tmp.cleanup()

    def test_prune_removes_only_excluded_origins(self):
        base = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)
        mantes_tid = (
            "OCESN16354F1187_F:TER:FR:Line::11111111-2222-3333-4444-555555555555"
            "::87317263:87271007:9:100:20261118"
        )
        rouen_tid = (
            "OCESN13100F1187_F:TER:FR:Line::22222222-3333-4444-5555-666666666666"
            "::87411017:87384008:7:100:20261118"
        )
        havre_tid = (
            "OCESN3132F1187_F:TER:FR:Line::33333333-4444-5555-6666-777777777777"
            "::87413013:87384008:6:100:20261118"
        )
        stops = [
            {"stop_id": "StopPoint:OCETrain TER-87411017", "dep_delay": 0,
             "dep_time": int(base.timestamp())},
            {"stop_id": "StopPoint:OCETrain TER-87384008", "arr_delay": 300,
             "arr_time": int(base.timestamp()) + 3600},
        ]
        profiles = [
            make_profile(mantes_tid, stops, base),
            make_profile(rouen_tid, stops, base),
            make_profile(havre_tid, stops, base),
        ]
        append_trip_profiles(self.data_dir, profiles)

        removed = prune_excluded_profiles(self.data_dir, ("87317263", "87381509"))
        self.assertEqual(sum(removed.values()), 1)
        self.assertEqual(len(removed), 1)

        remaining = list(load_trip_profiles(self.data_dir))
        self.assertEqual(len(remaining), 2)
        origins = {p.get("origin_uic8") for p in remaining}
        self.assertEqual(origins, {"87411017", "87413013"})

    def test_prune_is_idempotent(self):
        base = datetime(2026, 11, 18, 8, 0, tzinfo=TZ)
        mantes_tid = (
            "OCESN16354F1187_F:TER:FR:Line::11111111-2222-3333-4444-555555555555"
            "::87317263:87271007:9:100:20261118"
        )
        stops = [
            {"stop_id": "StopPoint:OCETrain TER-87411017", "dep_delay": 0,
             "dep_time": int(base.timestamp())},
        ]
        append_trip_profiles(self.data_dir, [make_profile(mantes_tid, stops, base)])
        first = prune_excluded_profiles(self.data_dir, ("87317263",))
        second = prune_excluded_profiles(self.data_dir, ("87317263",))
        self.assertEqual(sum(first.values()), 1)
        self.assertEqual(second, {})
        self.assertEqual(list(load_trip_profiles(self.data_dir)), [])

    def test_prune_missing_dir_returns_empty(self):
        removed = prune_excluded_profiles(Path(self.tmp.name) / "nope", ("87317263",))
        self.assertEqual(removed, {})


if __name__ == "__main__":
    unittest.main()
