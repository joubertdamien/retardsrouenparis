import unittest
from datetime import datetime

from fixtures import DEPARTURES_RESPONSE, FakeOpener, TZ
from rouenparis.config import Settings
from rouenparis.sncf_client import SncfApiError, SncfClient


class SncfClientTest(unittest.TestCase):
    def make_client(self, payload=None):
        settings = Settings(api_token="test-token")
        opener = FakeOpener(payload if payload is not None else DEPARTURES_RESPONSE)
        return SncfClient(settings, opener=opener), opener

    def test_parses_departures(self):
        client, _ = self.make_client()
        trains = client.fetch_departures(datetime(2026, 10, 15, 6, 0, tzinfo=TZ))
        self.assertEqual(len(trains), 3)

        on_time, delayed, cancelled = trains

        self.assertEqual(on_time.train_number, "3123")
        self.assertEqual(on_time.base_departure, datetime(2026, 10, 15, 7, 12, tzinfo=TZ))
        self.assertEqual(on_time.realtime_departure, on_time.base_departure)
        self.assertEqual(on_time.departure_delay_seconds, 0)
        self.assertEqual(on_time.base_arrival, datetime(2026, 10, 15, 8, 30, tzinfo=TZ))
        self.assertEqual(on_time.realtime_arrival, on_time.base_arrival)
        self.assertEqual(on_time.arrival_delay_seconds, 0)
        self.assertEqual(on_time.status, "")

        self.assertEqual(delayed.train_number, "3125")
        self.assertEqual(delayed.departure_delay_seconds, 720)
        self.assertEqual(delayed.arrival_delay_seconds, 1140)

        self.assertEqual(cancelled.train_number, "3401")
        self.assertEqual(cancelled.status, "cancelled")
        self.assertIsNone(cancelled.realtime_departure)

    def test_builds_expected_url(self):
        client, opener = self.make_client()
        client.fetch_departures(datetime(2026, 10, 15, 6, 0, tzinfo=TZ))
        url = opener.requests[0]
        self.assertIn("/coverage/sncf/stop_areas/stop_area:SNCF:87411017/departures?", url)
        self.assertIn("from_datetime=20261015T060000", url)
        self.assertIn("data_freshness=realtime", url)

    def test_sends_authorization_header(self):
        client, opener = self.make_client()
        client.fetch_departures(datetime(2026, 10, 15, 6, 0, tzinfo=TZ))
        headers = {key.lower(): value for key, value in opener.headers[0].items()}
        self.assertEqual(headers["authorization"], "test-token")

    def test_http_error_raises(self):
        import io
        import urllib.error

        class FailingOpener:
            def open(self, request, timeout=30):
                raise urllib.error.HTTPError(
                    request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad token")
                )

        settings = Settings(api_token="bad")
        client = SncfClient(settings, opener=FailingOpener(), max_retries=1)
        with self.assertRaises(SncfApiError) as ctx:
            client.fetch_departures(datetime(2026, 10, 15, 6, 0, tzinfo=TZ))
        self.assertEqual(ctx.exception.status, 401)

    def test_observation_roundtrip(self):
        client, _ = self.make_client()
        trains = client.fetch_departures(datetime(2026, 10, 15, 6, 0, tzinfo=TZ))
        from rouenparis.sncf_client import TrainObservation

        for train in trains:
            restored = TrainObservation.from_dict(train.to_dict())
            self.assertEqual(restored.to_dict(), train.to_dict())


if __name__ == "__main__":
    unittest.main()
