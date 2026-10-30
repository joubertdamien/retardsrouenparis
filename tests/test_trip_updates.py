import struct
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from fixtures import TZ
from rouenparis.config import Settings
from rouenparis.gtfsrt_proto import parse_feed
from rouenparis.trip_storage import (
    append_trip_profiles,
    cross_check_train,
    group_profiles_by_trip,
    load_trip_profiles,
    match_profile_to_train,
)
from rouenparis.trip_updates import (
    HoldEvent,
    StopPassage,
    TripProfile,
    TripUpdatesClient,
    cross_check_arrival_delay,
    detect_hold_events,
    profile_from_update,
    train_number_from_trip_id,
)
from test_gtfsrt_proto import build_feed, ld, tag, vi, varint


def build_stop_time_update(stop_id: str, arrival_delay=None, arrival_time=None,
                           departure_delay=None, departure_time=None, seq=0) -> bytes:
    out = vi(1, seq)
    if arrival_delay is not None or arrival_time is not None:
        event = b""
        if arrival_delay is not None:
            event += vi(1, arrival_delay)
        if arrival_time is not None:
            event += vi(2, arrival_time)
        out += ld(2, event)
    if departure_delay is not None or departure_time is not None:
        event = b""
        if departure_delay is not None:
            event += vi(1, departure_delay)
        if departure_time is not None:
            event += vi(2, departure_time)
        out += ld(3, event)
    if stop_id:
        out += ld(4, stop_id.encode())
    return out


def build_trip_update_entity(entity_id: str, trip_id: str, stops: list[bytes],
                              start_date: str = "20261028", start_time: str = "08:00:00",
                              route_id: str = "OCE:Train:1", timestamp: int = 0) -> bytes:
    trip = ld(1, trip_id.encode()) + ld(2, start_time.encode()) + ld(3, start_date.encode())
    trip += ld(5, route_id.encode())
    update = ld(1, trip)
    for stop in stops:
        update += ld(2, stop)
    if timestamp:
        update += vi(4, timestamp)
    return ld(1, entity_id.encode()) + ld(3, update)


def make_profile(trip_id: str, stops_data: list[dict], collected_at: datetime,
                 route_id: str = "OCE:Train:1") -> TripProfile:
    stops = []
    for i, s in enumerate(stops_data):
        stops.append(
            StopPassage(
                stop_id=s["stop_id"],
                stop_sequence=i,
                arrival_delay=s.get("arr_delay"),
                arrival_time=s.get("arr_time"),
                departure_delay=s.get("dep_delay"),
                departure_time=s.get("dep_time"),
            )
        )
    return TripProfile(
        entity_id=f"entity-{trip_id}",
        trip_id=trip_id,
        route_id=route_id,
        direction_id=0,
        start_date="20261028",
        start_time="08:00:00",
        feed_timestamp=int(collected_at.timestamp()),
        update_delay=None,
        stops=stops,
        collected_at=collected_at.isoformat(),
    )


ROUEN = "StopPoint:OCETrain TER-87411017"
PARIS = "StopPoint:OCETrain TER-87271007"
PARIS_TER = "StopPoint:OCETrain TER-87384008"
VERNON = "StopPoint:OCETrain TER-87317586"
MANTES = "StopPoint:OCETrain TER-87317263"
OISSEL = "StopPoint:OCETrain TER-87411207"
VALDEREUIL = "StopPoint:OCETrain TER-87415877"
POISSY = "StopPoint:OCETrain TER-87342014"


class TrainNumberExtractionTest(unittest.TestCase):
    def test_extracts_commercial_number(self):
        self.assertEqual(train_number_from_trip_id("OCESN7546F1187_F:OUI:FR:Line::abc"), "7546")
        self.assertEqual(train_number_from_trip_id("OCESN3125F"), "3125")
        self.assertEqual(train_number_from_trip_id("OCESN850627R"), "850627")

    def test_returns_none_without_digits(self):
        self.assertIsNone(train_number_from_trip_id("UNKNOWN"))


class StopPassageTest(unittest.TestCase):
    def test_uic8_extraction(self):
        stop = StopPassage(stop_id=ROUEN, stop_sequence=0, arrival_delay=0, arrival_time=100,
                           departure_delay=0, departure_time=120)
        self.assertEqual(stop.uic8, "87411017")

    def test_uic8_none_for_non_uic_stop_id(self):
        stop = StopPassage(stop_id="StopArea:OCE87415604", stop_sequence=0,
                           arrival_delay=0, arrival_time=100, departure_delay=0, departure_time=120)
        self.assertEqual(stop.uic8, "87415604")

    def test_uic8_none_for_empty(self):
        stop = StopPassage(stop_id="", stop_sequence=0, arrival_delay=None, arrival_time=None,
                           departure_delay=None, departure_time=None)
        self.assertIsNone(stop.uic8)


class DetectHoldEventsTest(unittest.TestCase):
    def test_detects_hold_when_delay_grows(self):
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000},
                {"stop_id": VERNON, "arr_delay": 300, "arr_time": 2000, "dep_delay": 300, "dep_time": 2060},
                {"stop_id": MANTES, "arr_delay": 300, "arr_time": 3000, "dep_delay": 300, "dep_time": 3060},
                {"stop_id": PARIS, "arr_delay": 600, "arr_time": 4000},
            ],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        events = detect_hold_events(profile)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].extra_delay_seconds, 300)
        self.assertEqual(events[0].uic8, "87317586")
        self.assertEqual(events[1].extra_delay_seconds, 300)
        self.assertEqual(events[1].uic8, "87271007")

    def test_no_hold_when_delay_constant(self):
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 300, "dep_time": 1000},
                {"stop_id": VERNON, "arr_delay": 300, "arr_time": 2000, "dep_delay": 300, "dep_time": 2060},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": 4000},
            ],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        events = detect_hold_events(profile)
        self.assertEqual(len(events), 0)

    def test_no_hold_when_delay_decreases(self):
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 600, "dep_time": 1000},
                {"stop_id": VERNON, "arr_delay": 300, "arr_time": 2000, "dep_delay": 300, "dep_time": 2060},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": 4000},
            ],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        events = detect_hold_events(profile)
        self.assertEqual(len(events), 0)


class CrossCheckArrivalDelayTest(unittest.TestCase):
    def test_agrees_within_tolerance(self):
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": 4000},
            ],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        result = cross_check_arrival_delay(profile, "87271007", 310)
        self.assertEqual(result["trip_updates_delay_seconds"], 300)
        self.assertEqual(result["announced_delay_seconds"], 310)
        self.assertEqual(result["difference_seconds"], 10)
        self.assertTrue(result["agrees"])

    def test_disagrees_beyond_tolerance(self):
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000},
                {"stop_id": PARIS, "arr_delay": 600, "arr_time": 4000},
            ],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        result = cross_check_arrival_delay(profile, "87271007", 300)
        self.assertFalse(result["agrees"])
        self.assertEqual(result["difference_seconds"], 300)

    def test_none_when_no_stop_data(self):
        profile = make_profile(
            "OCESN3125F",
            [{"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000}],
            datetime(2026, 10, 28, 8, 0, tzinfo=TZ),
        )
        result = cross_check_arrival_delay(profile, "87271007", 300)
        self.assertIsNone(result["trip_updates_delay_seconds"])
        self.assertIsNone(result["agrees"])


class ProtoRoundtripTest(unittest.TestCase):
    def test_parse_trip_update_feed(self):
        stops = [
            build_stop_time_update(ROUEN, departure_delay=0, departure_time=1000, seq=0),
            build_stop_time_update(VERNON, arrival_delay=300, arrival_time=2000,
                                   departure_delay=300, departure_time=2060, seq=1),
            build_stop_time_update(PARIS, arrival_delay=600, arrival_time=4000, seq=2),
        ]
        entity = build_trip_update_entity("e1", "OCESN3125F1187_F:OUI:FR:Line::abc", stops,
                                           timestamp=1793220000)
        payload = build_feed([entity])
        feed = parse_feed(payload)
        self.assertEqual(len(feed.trip_updates), 1)
        self.assertEqual(len(feed.vehicles), 0)
        tu = feed.trip_updates[0]
        self.assertEqual(tu.entity_id, "e1")
        self.assertEqual(tu.trip.trip_id, "OCESN3125F1187_F:OUI:FR:Line::abc")
        self.assertEqual(tu.trip.start_date, "20261028")
        self.assertEqual(tu.trip.start_time, "08:00:00")
        self.assertEqual(tu.timestamp, 1793220000)
        self.assertEqual(len(tu.stop_time_updates), 3)
        self.assertEqual(tu.stop_time_updates[0].stop_id, ROUEN)
        self.assertEqual(tu.stop_time_updates[0].departure.delay, 0)
        self.assertEqual(tu.stop_time_updates[1].arrival.delay, 300)
        self.assertEqual(tu.stop_time_updates[2].arrival.delay, 600)
        self.assertEqual(tu.stop_time_updates[2].arrival.time, 4000)

    def test_roundtrip_with_real_bindings_if_available(self):
        try:
            from google.transit import gtfs_realtime_pb2
        except ImportError:
            self.skipTest("gtfs-realtime-bindings not installed")
        message = gtfs_realtime_pb2.FeedMessage()
        message.header.gtfs_realtime_version = "2.0"
        message.header.timestamp = 1793220000
        entity = message.entity.add()
        entity.id = "entity-tu-1"
        entity.trip_update.trip.trip_id = "OCESN7546F1187_F:OUI:FR:Line::abc"
        entity.trip_update.trip.route_id = "OCE:Train:1"
        entity.trip_update.trip.start_date = "20261028"
        entity.trip_update.trip.start_time = "21:30:00"
        entity.trip_update.timestamp = 1793220000

        stu = entity.trip_update.stop_time_update.add()
        stu.stop_id = ROUEN
        stu.stop_sequence = 0
        stu.departure.delay = 0
        stu.departure.time = 1793220000

        stu2 = entity.trip_update.stop_time_update.add()
        stu2.stop_id = VERNON
        stu2.stop_sequence = 1
        stu2.arrival.delay = 300
        stu2.arrival.time = 1793221000
        stu2.departure.delay = 300
        stu2.departure.time = 1793221060

        stu3 = entity.trip_update.stop_time_update.add()
        stu3.stop_id = PARIS
        stu3.stop_sequence = 2
        stu3.arrival.delay = 600
        stu3.arrival.time = 1793224000

        feed = parse_feed(message.SerializeToString())
        self.assertEqual(len(feed.trip_updates), 1)
        tu = feed.trip_updates[0]
        self.assertEqual(tu.entity_id, "entity-tu-1")
        self.assertEqual(tu.trip.trip_id, "OCESN7546F1187_F:OUI:FR:Line::abc")
        self.assertEqual(tu.trip.start_date, "20261028")
        self.assertEqual(tu.trip.start_time, "21:30:00")
        self.assertEqual(tu.timestamp, 1793220000)
        self.assertEqual(len(tu.stop_time_updates), 3)
        self.assertEqual(tu.stop_time_updates[0].stop_id, ROUEN)
        self.assertEqual(tu.stop_time_updates[1].arrival.delay, 300)
        self.assertEqual(tu.stop_time_updates[2].arrival.delay, 600)

        profile = profile_from_update(tu, datetime(2026, 10, 28, 21, 0, tzinfo=TZ))
        self.assertEqual(profile.train_number, "7546")
        events = detect_hold_events(profile)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].extra_delay_seconds, 300)
        self.assertEqual(events[1].extra_delay_seconds, 300)


class TripUpdatesClientTest(unittest.TestCase):
    def test_fetch_profiles_with_fake_opener(self):
        stops = [
            build_stop_time_update(ROUEN, departure_delay=0, departure_time=1000, seq=0),
            build_stop_time_update(PARIS, arrival_delay=300, arrival_time=4000, seq=1),
        ]
        entity = build_trip_update_entity("e1", "OCESN3125F", stops, timestamp=1793220000)
        payload = build_feed([entity])

        class FakeOpener:
            def open(self, request, timeout=30):
                import io

                return io.BytesIO(payload)

        settings = Settings(api_token="x")
        client = TripUpdatesClient(settings, opener=FakeOpener())
        profiles = client.fetch_profiles(datetime(2026, 10, 28, 8, 0, tzinfo=TZ))
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].trip_id, "OCESN3125F")
        self.assertEqual(profiles[0].train_number, "3125")
        self.assertEqual(len(profiles[0].stops), 2)
        self.assertEqual(profiles[0].stops[1].uic8, "87271007")

    def test_fetch_profiles_for_stations(self):
        stops_rp = [
            build_stop_time_update(ROUEN, departure_delay=0, departure_time=1000, seq=0),
            build_stop_time_update(PARIS, arrival_delay=300, arrival_time=4000, seq=1),
        ]
        stops_other = [
            build_stop_time_update("StopPoint:OCETrain TER-87111017", departure_delay=0, departure_time=1000, seq=0),
            build_stop_time_update("StopPoint:OCETrain TER-87222007", arrival_delay=0, arrival_time=4000, seq=1),
        ]
        payload = build_feed([
            build_trip_update_entity("e1", "OCESN3125F", stops_rp),
            build_trip_update_entity("e2", "OCESN9999F", stops_other),
        ])

        class FakeOpener:
            def open(self, request, timeout=30):
                import io

                return io.BytesIO(payload)

        settings = Settings(api_token="x")
        client = TripUpdatesClient(settings, opener=FakeOpener())
        profiles = client.fetch_profiles_for_stations("87411017", "87271007")
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].trip_id, "OCESN3125F")


class TripStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_load(self):
        day = date(2026, 10, 28)
        collected = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": 4000},
            ],
            collected,
        )
        path = append_trip_profiles(self.data_dir, [profile])
        self.assertEqual(path.name, "2026-10-28.jsonl")
        loaded = list(load_trip_profiles(self.data_dir))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["trip_id"], "OCESN3125F")
        self.assertEqual(loaded[0]["train_number"], "3125")

    def test_load_filters_by_date(self):
        d1 = datetime(2026, 10, 27, 8, 0, tzinfo=TZ)
        d2 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        append_trip_profiles(self.data_dir, [make_profile("OCESN3123F", [{"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000}], d1)])
        append_trip_profiles(self.data_dir, [make_profile("OCESN3125F", [{"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000}], d2)])
        loaded = list(load_trip_profiles(self.data_dir, start=date(2026, 10, 28)))
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["trip_id"], "OCESN3125F")

    def test_group_by_trip(self):
        t1 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        t2 = datetime(2026, 10, 28, 8, 15, tzinfo=TZ)
        append_trip_profiles(self.data_dir, [make_profile("OCESN3125F", [{"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000}], t1)])
        append_trip_profiles(self.data_dir, [make_profile("OCESN3125F", [{"stop_id": ROUEN, "dep_delay": 0, "dep_time": 1000}], t2)])
        groups = group_profiles_by_trip(load_trip_profiles(self.data_dir))
        self.assertEqual(len(groups["OCESN3125F"]), 2)

    def test_match_profile_to_train_by_number(self):
        t1 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(t1.timestamp())},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(t1.timestamp()) + 3000},
            ],
            t1,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        base_dep = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        matched = match_profile_to_train(profiles, "3125", base_dep, "87271007", origin_uic8="87411017")
        self.assertIsNotNone(matched)
        self.assertEqual(matched["trip_id"], "OCESN3125F")

    def test_match_profile_to_train_rejects_wrong_route(self):
        t1 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(t1.timestamp())},
                {"stop_id": "StopPoint:OCETrain TER-87999999", "arr_delay": 300, "arr_time": int(t1.timestamp()) + 3000},
            ],
            t1,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        base_dep = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        matched = match_profile_to_train(profiles, "3125", base_dep, "87271007", origin_uic8="87411017")
        self.assertIsNone(matched)

    def test_match_profile_to_train_no_match(self):
        t1 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(t1.timestamp())},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(t1.timestamp()) + 3000},
            ],
            t1,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        base_dep = datetime(2026, 10, 28, 3, 0, tzinfo=TZ)
        matched = match_profile_to_train(profiles, "9999", base_dep, "87271007")
        self.assertIsNone(matched)

    def test_cross_check_train(self):
        t1 = datetime(2026, 10, 28, 8, 0, tzinfo=TZ)
        profile = make_profile(
            "OCESN3125F",
            [
                {"stop_id": ROUEN, "dep_delay": 0, "dep_time": int(t1.timestamp())},
                {"stop_id": PARIS, "arr_delay": 300, "arr_time": int(t1.timestamp()) + 3000},
            ],
            t1,
        )
        append_trip_profiles(self.data_dir, [profile])
        profiles = list(load_trip_profiles(self.data_dir))
        result = cross_check_train(profiles, "3125", t1, "87271007", 310)
        self.assertIsNotNone(result)
        self.assertEqual(result["trip_updates_delay_seconds"], 300)
        self.assertEqual(result["announced_delay_seconds"], 310)
        self.assertTrue(result["agrees"])

    def test_cross_check_train_no_profile(self):
        profiles = []
        result = cross_check_train(profiles, "9999", datetime(2026, 10, 28, 8, 0, tzinfo=TZ), "87271007", 300)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
