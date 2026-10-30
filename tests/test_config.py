import unittest

from rouenparis.config import (
    DEFAULT_GTFS_RT_TRIP_UPDATES_URL,
    PARIS_ST_LAZARE_STOP_AREA,
    PARIS_ST_LAZARE_UIC8,
    ROUEN_RIVE_DROITE_STOP_AREA,
    ROUEN_RIVE_DROITE_UIC8,
    Settings,
)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        settings = Settings(api_token="x")
        self.assertEqual(settings.origin_stop_area, ROUEN_RIVE_DROITE_STOP_AREA)
        self.assertEqual(settings.destination_stop_area, PARIS_ST_LAZARE_STOP_AREA)
        self.assertEqual(settings.min_delay_minutes, 5)

    def test_uic8_defaults(self):
        settings = Settings(api_token="x")
        self.assertEqual(settings.origin_uic8, ROUEN_RIVE_DROITE_UIC8)
        self.assertEqual(settings.destination_uic8, PARIS_ST_LAZARE_UIC8)
        self.assertEqual(settings.origin_uic8, "87411017")
        self.assertEqual(settings.destination_uic8, "87271007")

    def test_corridor_uic8_defaults(self):
        from rouenparis.config import ROUEN_PARIS_CORRIDOR_UIC8
        settings = Settings(api_token="x")
        self.assertEqual(settings.corridor_uic8, ROUEN_PARIS_CORRIDOR_UIC8)
        self.assertIn("87411017", settings.corridor_uic8)
        self.assertIn("87342014", settings.corridor_uic8)
        self.assertNotIn("87271007", settings.corridor_uic8)

    def test_destination_uic8_all_defaults(self):
        from rouenparis.config import PARIS_ST_LAZARE_UIC8_ALL
        settings = Settings(api_token="x")
        self.assertEqual(settings.destination_uic8_all, PARIS_ST_LAZARE_UIC8_ALL)
        self.assertIn("87271007", settings.destination_uic8_all)
        self.assertIn("87384008", settings.destination_uic8_all)

    def test_trip_updates_url_default(self):
        settings = Settings(api_token="x")
        self.assertEqual(settings.gtfs_rt_trip_updates_url, DEFAULT_GTFS_RT_TRIP_UPDATES_URL)
        self.assertIn("sncf-gtfs-rt-trip-updates", settings.gtfs_rt_trip_updates_url)

    def test_track_trip_updates_default(self):
        settings = Settings(api_token="x")
        self.assertTrue(settings.track_trip_updates)

    def test_from_env_requires_token(self):
        with self.assertRaises(RuntimeError):
            Settings.from_env(env={})

    def test_from_env_ok_without_token_when_not_required(self):
        settings = Settings.from_env(env={}, require_token=False)
        self.assertEqual(settings.api_token, "")

    def test_from_env_parses_overrides(self):
        settings = Settings.from_env(
            env={
                "SNCF_API_TOKEN": "abc",
                "ROUENPARIS_MIN_DELAY_MINUTES": "10",
                "ROUENPARIS_LINES": "line:SNCF:A, line:SNCF:B",
            }
        )
        self.assertEqual(settings.min_delay_minutes, 10)
        self.assertEqual(settings.lines, ("line:SNCF:A", "line:SNCF:B"))

    def test_from_env_trip_updates_overrides(self):
        settings = Settings.from_env(
            env={
                "SNCF_API_TOKEN": "abc",
                "ROUENPARIS_TRACK_TRIP_UPDATES": "0",
                "ROUENPARIS_GTFS_RT_TRIP_UPDATES_URL": "https://example.com/tu",
                "ROUENPARIS_ORIGIN_UIC8": "87411017",
                "ROUENPARIS_DESTINATION_UIC8": "87271007",
            }
        )
        self.assertFalse(settings.track_trip_updates)
        self.assertEqual(settings.gtfs_rt_trip_updates_url, "https://example.com/tu")
        self.assertEqual(settings.origin_uic8, "87411017")
        self.assertEqual(settings.destination_uic8, "87271007")


if __name__ == "__main__":
    unittest.main()
