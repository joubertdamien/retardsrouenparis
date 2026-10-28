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

        daily_report = json.loads(
            (self.site_dir / "reports" / "daily-2026-10-15.json").read_text()
        )
        self.assertEqual(daily_report["summary"]["scheduled"], 3)
        self.assertEqual(daily_report["summary"]["delayed"], 1)
        self.assertEqual(daily_report["summary"]["cancelled"], 1)
        self.assertEqual(len(daily_report["trains"]), 3)
        train = daily_report["trains"][1]
        self.assertEqual(train["delay_minutes"], 19.0)

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
