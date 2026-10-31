import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from fixtures import TZ, make_snapshot
from rouenparis.config import Settings
from rouenparis.reports import (
    build_report,
    build_train_records,
    daily_series,
    generate_all,
    list_periods,
    summarize,
)
from rouenparis.storage import append_snapshot, load_snapshots


class ReportsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.site_dir = Path(self.tmp.name) / "site-build"
        self.settings = Settings(api_token="test", min_delay_minutes=5)

    def tearDown(self):
        self.tmp.cleanup()

    def populate(self):
        # Wednesday 2026-10-14 and Thursday 2026-10-15 (ISO week 42), month 2026-10
        append_snapshot(self.data_dir, make_snapshot(datetime(2026, 10, 14, 21, 30, tzinfo=TZ)))
        append_snapshot(self.data_dir, make_snapshot(datetime(2026, 10, 15, 7, 0, tzinfo=TZ)))
        append_snapshot(self.data_dir, make_snapshot(datetime(2026, 10, 15, 21, 30, tzinfo=TZ)))

    def test_build_train_records(self):
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        # 3 trains per snapshot, deduplicated across snapshots of the same day
        self.assertEqual(len(records), 6)
        by_number = {}
        for record in records:
            by_number.setdefault(record.train_number, []).append(record)
        self.assertEqual(len(by_number["3123"]), 2)
        delayed = [r for r in records if r.delay_seconds is not None and r.delay_seconds >= 300]
        self.assertEqual(len(delayed), 2)
        cancelled = [r for r in records if r.cancelled]
        self.assertEqual(len(cancelled), 2)

    def test_summarize(self):
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        summary = summarize(records, 300)
        self.assertEqual(summary["scheduled"], 6)
        self.assertEqual(summary["cancelled"], 2)
        self.assertEqual(summary["circulated"], 4)
        self.assertEqual(summary["measured"], 4)
        self.assertEqual(summary["delayed"], 2)
        self.assertAlmostEqual(summary["punctuality_rate"], 50.0)
        self.assertAlmostEqual(summary["avg_delay_minutes"], (0 + 19 + 0 + 19) / 4, places=1)
        self.assertEqual(summary["max_delay_minutes"], 19.0)

    def test_period_bounds(self):
        records = build_train_records([], 300)
        self.assertEqual(list_periods(records, "daily"), [])
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        self.assertEqual(list_periods(records, "daily"), [date(2026, 10, 14), date(2026, 10, 15)])
        self.assertEqual(list_periods(records, "weekly"), [date(2026, 10, 12)])
        self.assertEqual(list_periods(records, "monthly"), [date(2026, 10, 1)])

    def test_weekly_report_spans_seven_days(self):
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        report = build_report(records, "weekly", date(2026, 10, 12), self.settings)
        self.assertEqual(report["period"]["start"], "2026-10-12")
        self.assertEqual(report["period"]["end"], "2026-10-18")
        self.assertEqual(report["summary"]["scheduled"], 6)
        self.assertEqual(len(report["trains"]), 6)

    def test_monthly_report_end_of_month(self):
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        report = build_report(records, "monthly", date(2026, 10, 1), self.settings)
        self.assertEqual(report["period"]["end"], "2026-10-31")

    def test_generate_all_writes_site_files(self):
        self.populate()
        index = generate_all(self.data_dir, self.site_dir, self.settings)
        self.assertTrue((self.site_dir / "index.json").exists())
        self.assertTrue((self.site_dir / "series.json").exists())
        self.assertTrue((self.site_dir / "latest.json").exists())
        self.assertTrue((self.site_dir / "reports" / "daily-2026-10-15.json").exists())
        self.assertTrue((self.site_dir / "reports" / "weekly-2026-10-12.json").exists())
        self.assertTrue((self.site_dir / "reports" / "monthly-2026-10-01.json").exists())

        index_data = json.loads((self.site_dir / "index.json").read_text())
        self.assertEqual(index_data["days_tracked"], 2)
        self.assertEqual(len(index_data["periods"]), 3)

        series = json.loads((self.site_dir / "series.json").read_text())
        self.assertEqual(len(series["days"]), 2)
        self.assertEqual(series["days"][0]["date"], "2026-10-14")
        self.assertEqual(series["days"][0]["scheduled"], 3)

        latest = json.loads((self.site_dir / "latest.json").read_text())
        self.assertEqual(latest["latest_day"], "2026-10-15")

        crosscheck = json.loads((self.site_dir / "crosscheck.json").read_text())
        self.assertIn("trains_cross_checked", crosscheck)
        self.assertIn("delays_agreeing", crosscheck)
        self.assertIn("source", crosscheck)
        self.assertIn("TripUpdates", crosscheck["source"])

        index_data = json.loads((self.site_dir / "index.json").read_text())
        self.assertIn("cross_check", index_data)

        daily_report = json.loads(
            (self.site_dir / "reports" / "daily-2026-10-15.json").read_text()
        )
        self.assertEqual(daily_report["summary"]["scheduled"], 3)
        self.assertEqual(daily_report["summary"]["delayed"], 1)
        self.assertEqual(daily_report["summary"]["cancelled"], 1)
        self.assertEqual(len(daily_report["trains"]), 3)
        train = daily_report["trains"][1]
        self.assertEqual(train["delay_minutes"], 19.0)

    def test_generate_all_with_trip_profiles_cross_check(self):
        from test_trip_updates import PARIS, ROUEN, make_profile
        from rouenparis.trip_storage import append_trip_profiles

        self.populate()
        dep_ts = int(datetime(2026, 10, 15, 7, 12, tzinfo=TZ).timestamp())
        collected = datetime(2026, 10, 15, 7, 12, tzinfo=TZ)
        profile = make_profile(
            "OCESN3123F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": dep_ts},
                {"stop_id": PARIS, "arr_delay": 0, "arr_time": dep_ts + 4000},
            ],
            collected,
        )
        append_trip_profiles(self.data_dir, [profile])

        index = generate_all(self.data_dir, self.site_dir, self.settings)
        crosscheck = json.loads((self.site_dir / "crosscheck.json").read_text())
        self.assertGreaterEqual(crosscheck["trains_cross_checked"], 1)
        self.assertIn("trains", crosscheck)
        numbers = [t["train_number"] for t in crosscheck["trains"]]
        self.assertIn("3123", numbers)

        daily_report = json.loads(
            (self.site_dir / "reports" / "daily-2026-10-15.json").read_text()
        )
        train_3123 = next(t for t in daily_report["trains"] if t["train_number"] == "3123")
        self.assertIsNotNone(train_3123["trip_update_delay_seconds"])
        self.assertIsNotNone(train_3123["delay_cross_check_agrees"])

        index_data = json.loads((self.site_dir / "index.json").read_text())
        self.assertGreaterEqual(index_data["cross_check"]["trains_cross_checked"], 1)

    def test_daily_series_aggregates_per_day(self):
        self.populate()
        records = build_train_records(load_snapshots(self.data_dir), 300)
        series = daily_series(records, 300)
        self.assertEqual([entry["date"] for entry in series], ["2026-10-14", "2026-10-15"])
        for entry in series:
            self.assertEqual(entry["scheduled"], 3)
            self.assertEqual(entry["punctuality_rate"], 50.0)


if __name__ == "__main__":
    unittest.main()


class TripUpdatesOnlyRecordsTest(unittest.TestCase):
    """Tests for the GTFS-RT TripUpdates-only fallback (no SNCF API token)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name) / "data"
        self.site_dir = Path(self.tmp.name) / "site-build"
        self.settings = Settings(api_token="", min_delay_minutes=5)

    def tearDown(self):
        self.tmp.cleanup()

    def _add_profiles(self):
        from test_trip_updates import OISSEL, PARIS, POISSY, ROUEN, VALDEREUIL, VERNON, make_profile
        from rouenparis.trip_storage import append_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profiles = [
            make_profile(
                "OCESN3123F",
                [
                    {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(base.timestamp())},
                    {"stop_id": OISSEL, "arr_delay": 0, "arr_time": int(base.timestamp()) + 600,
                     "dep_delay": 0, "dep_time": int(base.timestamp()) + 660},
                    {"stop_id": VALDEREUIL, "arr_delay": 0, "arr_time": int(base.timestamp()) + 1200,
                     "dep_delay": 0, "dep_time": int(base.timestamp()) + 1260},
                    {"stop_id": VERNON, "arr_delay": 0, "arr_time": int(base.timestamp()) + 1800,
                     "dep_delay": 0, "dep_time": int(base.timestamp()) + 1860},
                    {"stop_id": PARIS, "arr_delay": 0, "arr_time": int(base.timestamp()) + 4000},
                ],
                base,
            ),
            make_profile(
                "OCESN3125F",
                [
                    {"stop_id": ROUEN, "dep_delay": 720, "dep_time": int(base.timestamp()) + 1080},
                    {"stop_id": OISSEL, "arr_delay": 780, "arr_time": int(base.timestamp()) + 1680,
                     "dep_delay": 780, "dep_time": int(base.timestamp()) + 1740},
                    {"stop_id": VERNON, "arr_delay": 900, "arr_time": int(base.timestamp()) + 2880,
                     "dep_delay": 900, "dep_time": int(base.timestamp()) + 2940},
                    {"stop_id": PARIS, "arr_delay": 1140, "arr_time": int(base.timestamp()) + 5080},
                ],
                base,
            ),
        ]
        append_trip_profiles(self.data_dir, profiles)

    def test_build_records_from_trip_profiles(self):
        from rouenparis.reports import build_records_from_trip_profiles
        from rouenparis.trip_storage import load_trip_profiles

        self._add_profiles()
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 2)
        by_number = {r.train_number: r for r in records}
        r3123 = by_number["3123"]
        self.assertEqual(r3123.delay_seconds, 0)
        self.assertEqual(r3123.data_source, "gtfs_rt_trip_updates")
        self.assertEqual(r3123.line_name, "GTFS-RT")
        r3125 = by_number["3125"]
        self.assertEqual(r3125.delay_seconds, 1140)
        self.assertEqual(r3125.data_source, "gtfs_rt_trip_updates")

    def test_build_records_corridor_matching_partial_profile(self):
        """A profile without Rouen but with corridor stops before Paris
        should still produce a record (feed truncates long trips)."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, POISSY, VALDEREUIL, VERNON, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN13110F",
            [
                {"stop_id": VALDEREUIL, "dep_delay": 300, "dep_time": int(base.timestamp())},
                {"stop_id": VERNON, "arr_delay": 300, "arr_time": int(base.timestamp()) + 1200,
                 "dep_delay": 300, "dep_time": int(base.timestamp()) + 1260},
                {"stop_id": POISSY, "arr_delay": 300, "arr_time": int(base.timestamp()) + 2400,
                 "dep_delay": 300, "dep_time": int(base.timestamp()) + 2460},
                {"stop_id": PARIS, "arr_delay": 600, "arr_time": int(base.timestamp()) + 3600},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r.train_number, "13110")
        self.assertEqual(r.delay_seconds, 600)
        self.assertEqual(r.data_source, "gtfs_rt_trip_updates")
        self.assertIsNotNone(r.scheduled_arrival)
        self.assertIsNotNone(r.actual_arrival)

    def test_build_records_skips_paris_arrival_without_corridor(self):
        """A Paris arrival with no corridor station before it is not a
        Rouen→Paris train (e.g. a Transilien from Cergy)."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN9999F",
            [
                {"stop_id": "StopPoint:OCETrain TER-87276006", "dep_delay": 0,
                 "dep_time": int(base.timestamp())},
                {"stop_id": "StopPoint:OCETrain TER-87276113", "arr_delay": 0,
                 "arr_time": int(base.timestamp()) + 300,
                 "dep_delay": 0, "dep_time": int(base.timestamp()) + 360},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(base.timestamp()) + 600},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 0)

    def test_build_records_skips_poissy_only_lille_train(self):
        """A train from Lille/Dunkerque that only shares Poissy with the
        Rouen line must NOT be shown (Poissy is not Rouen-specific)."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, POISSY, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        # trip_id origin = Lille (87286005), stops: Lille → Poissy → Paris
        profile = make_profile(
            "OCESN7312F1187_F:TER:FR:Line::AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE::87286005:87271007:5:100:20261028",
            [
                {"stop_id": "StopPoint:OCETrain TER-87286005", "dep_delay": 0,
                 "dep_time": int(base.timestamp())},
                {"stop_id": POISSY, "arr_delay": 0, "arr_time": int(base.timestamp()) + 2400,
                 "dep_delay": 0, "dep_time": int(base.timestamp()) + 2460},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(base.timestamp()) + 3600},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 0)

    def test_build_records_accepts_truncated_rouen_axis_origin(self):
        """A Le Havre→Paris train whose feed-truncated stop list only shows
        Poissy before Paris is still accepted because its trip_id origin
        (Le Havre, 87413013) is on the Rouen axis."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, POISSY, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN3106F1187_F:TER:FR:Line::11111111-2222-3333-4444-555555555555::87413013:87384008:6:100:20261028",
            [
                {"stop_id": POISSY, "dep_delay": 0, "dep_time": int(base.timestamp())},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(base.timestamp()) + 1200},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].origin_station, "Le Havre")
        self.assertEqual(records[0].delay_seconds, 300)

    def test_build_records_skips_non_rouen_axis_origin_poissy_only(self):
        """A Mantes-origin train stopping only at Poissy before Paris is
        NOT a Rouen-line train (Mantes is not on the Rouen axis) — it is
        excluded even though Mantes is a corridor station."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, POISSY, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN16382F1187_F:TER:FR:Line::11111111-2222-3333-4444-555555555555::87317263:87271007:9:100:20261028",
            [
                {"stop_id": POISSY, "dep_delay": 0, "dep_time": int(base.timestamp())},
                {"stop_id": PARIS, "arr_delay": 0, "arr_time": int(base.timestamp()) + 2400},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 0)

    def test_generate_all_without_snapshots_uses_trip_updates(self):
        from rouenparis.reports import generate_all

        self._add_profiles()
        index = generate_all(self.data_dir, self.site_dir, self.settings)
        self.assertEqual(index["data_source"], "gtfs_rt_trip_updates")
        self.assertTrue(index["trip_updates_only"]["active"])
        self.assertEqual(index["trip_updates_only"]["trains_from_trip_updates"], 2)
        self.assertEqual(index["trip_updates_only"]["delayed_from_trip_updates"], 1)
        self.assertEqual(index["days_tracked"], 1)

        daily = json.loads(
            (self.site_dir / "reports" / "daily-2026-10-28.json").read_text()
        )
        self.assertEqual(daily["data_source"], "gtfs_rt_trip_updates")
        self.assertEqual(daily["summary"]["scheduled"], 2)
        self.assertEqual(daily["summary"]["delayed"], 1)
        self.assertEqual(daily["summary"]["measured"], 2)

        crosscheck = json.loads((self.site_dir / "crosscheck.json").read_text())
        self.assertEqual(len(crosscheck["trip_updates_only_trains"]), 2)

    def test_generate_all_prefers_snapshots_when_available(self):
        from rouenparis.reports import generate_all

        append_snapshot(self.data_dir, make_snapshot(datetime(2026, 10, 15, 7, 0, tzinfo=TZ)))
        self._add_profiles()
        index = generate_all(self.data_dir, self.site_dir, self.settings)
        self.assertEqual(index["data_source"], "sncf_api")
        self.assertFalse(index["trip_updates_only"]["active"])

    def test_build_records_skips_profiles_without_route(self):
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import PARIS, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN9999F",
            [
                {"stop_id": "StopPoint:OCETrain TER-87111017", "dep_delay": 0, "dep_time": int(base.timestamp())},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(base.timestamp()) + 4000},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 0)

    def test_build_records_matches_paris_ter_uic8(self):
        """Paris Saint-Lazare has two UIC8 codes in GTFS-RT: 87271007 (TGV)
        and 87384008 (TER). A Rouen→Paris TER train ends at 87384008."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import OISSEL, PARIS_TER, ROUEN, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN13148F",
            [
                {"stop_id": ROUEN, "dep_delay": 600, "dep_time": int(base.timestamp())},
                {"stop_id": OISSEL, "arr_delay": 600, "arr_time": int(base.timestamp()) + 600,
                 "dep_delay": 600, "dep_time": int(base.timestamp()) + 660},
                {"stop_id": PARIS_TER, "arr_delay": 900, "arr_time": int(base.timestamp()) + 5880},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 1)
        r = records[0]
        self.assertEqual(r.train_number, "13148")
        self.assertEqual(r.delay_seconds, 900)
        self.assertEqual(r.data_source, "gtfs_rt_trip_updates")
        self.assertIsNotNone(r.actual_arrival)

    def test_build_records_extracts_origin_station(self):
        """The origin station name is extracted from the trip_id's origin UIC8."""
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import OISSEL, PARIS_TER, ROUEN, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        # trip_id with origin=87411017 (Rouen) and dest=87384008 (Paris TER)
        profile = make_profile(
            "OCESN13148F1187_F:TER:FR:Line::97E911FA-C8F2-4861-AD3C-C36CF4750A49::87411017:87384008:7:1849:20261028",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(base.timestamp())},
                {"stop_id": OISSEL, "arr_delay": 0, "arr_time": int(base.timestamp()) + 600,
                 "dep_delay": 0, "dep_time": int(base.timestamp()) + 660},
                {"stop_id": PARIS_TER, "arr_delay": 300, "arr_time": int(base.timestamp()) + 5880},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].origin_station, "Rouen Rive Droite")
        self.assertEqual(records[0].to_dict()["origin_station"], "Rouen Rive Droite")

    def test_build_records_deduplicates_same_trip(self):
        from rouenparis.reports import build_records_from_trip_profiles
        from test_trip_updates import OISSEL, PARIS, ROUEN, make_profile
        from rouenparis.trip_storage import append_trip_profiles, load_trip_profiles

        base = datetime(2026, 10, 28, 7, 30, tzinfo=TZ)
        profile = make_profile(
            "OCESN3123F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(base.timestamp())},
                {"stop_id": OISSEL, "arr_delay": 0, "arr_time": int(base.timestamp()) + 600,
                 "dep_delay": 0, "dep_time": int(base.timestamp()) + 660},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(base.timestamp()) + 4000},
            ],
            base,
        )
        append_trip_profiles(self.data_dir, [profile, profile])
        profiles = list(load_trip_profiles(self.data_dir))
        records = build_records_from_trip_profiles(profiles, self.settings, 300)
        self.assertEqual(len(records), 1)
