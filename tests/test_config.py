import unittest

from rouenparis.config import (
    PARIS_ST_LAZARE_STOP_AREA,
    ROUEN_RIVE_DROITE_STOP_AREA,
    Settings,
)


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        settings = Settings(api_token="x")
        self.assertEqual(settings.origin_stop_area, ROUEN_RIVE_DROITE_STOP_AREA)
        self.assertEqual(settings.destination_stop_area, PARIS_ST_LAZARE_STOP_AREA)
        self.assertEqual(settings.min_delay_minutes, 5)

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


if __name__ == "__main__":
    unittest.main()
