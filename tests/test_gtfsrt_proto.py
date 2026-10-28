import struct
import unittest

from rouenparis.gtfsrt_proto import ProtobufError, parse_feed


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if value:
            out.append(bits | 0x80)
        else:
            out.append(bits)
            return bytes(out)


def tag(field: int, wire: int) -> bytes:
    return varint((field << 3) | wire)


def ld(field: int, payload: bytes) -> bytes:
    return tag(field, 2) + varint(len(payload)) + payload


def vi(field: int, value: int) -> bytes:
    return tag(field, 0) + varint(value)


def f32(field: int, value: float) -> bytes:
    return tag(field, 5) + struct.pack("<f", value)


def build_feed(entities: list[bytes], version: str = "2.0", timestamp: int = 1760000000) -> bytes:
    # FeedHeader.timestamp is field 3 (field 2 is incrementality)
    header = ld(1, version.encode()) + vi(3, timestamp)
    out = ld(1, header)
    for entity in entities:
        out += ld(2, entity)
    return out


def build_vehicle_entity(entity_id: str, trip_id: str, route_id: str, lat: float, lon: float,
                         speed: float = 0.0, status: int = 1, stop_id: str = "", timestamp: int = 0,
                         vehicle_id: str = "TGV-1234") -> bytes:
    # Field numbers follow the official gtfs-realtime.proto:
    # VehiclePosition { trip=1, position=2, current_status=4, timestamp=5,
    #                   stop_id=7, vehicle=8 }
    trip = ld(1, trip_id.encode()) + ld(5, route_id.encode())
    position = f32(1, lat) + f32(2, lon) + f32(4, speed)
    vehicle_descriptor = ld(1, vehicle_id.encode())
    vehicle = ld(1, trip) + ld(2, position) + vi(4, status) + ld(8, vehicle_descriptor)
    if stop_id:
        vehicle += ld(7, stop_id.encode())
    if timestamp:
        vehicle += vi(5, timestamp)
    # FeedEntity.vehicle is field 4 in the official gtfs-realtime proto
    return ld(1, entity_id.encode()) + ld(4, vehicle)


class GtfsRtProtoTest(unittest.TestCase):
    def test_parse_vehicle_position(self):
        payload = build_feed(
            [build_vehicle_entity("entity-1", "OCESN3125F", "OCE:Train:1", 48.88, 2.32, speed=12.5, status=1, stop_id="StopArea:OCE87271007", timestamp=1760000100, vehicle_id="TGV-3125")]
        )
        feed = parse_feed(payload)
        self.assertEqual(feed.version, "2.0")
        self.assertEqual(feed.timestamp, 1760000000)
        self.assertEqual(len(feed.vehicles), 1)
        vehicle = feed.vehicles[0]
        self.assertEqual(vehicle.entity_id, "entity-1")
        self.assertEqual(vehicle.trip_id, "OCESN3125F")
        self.assertEqual(vehicle.route_id, "OCE:Train:1")
        self.assertAlmostEqual(vehicle.position.latitude, 48.88, places=5)
        self.assertAlmostEqual(vehicle.position.longitude, 2.32, places=5)
        self.assertAlmostEqual(vehicle.position.speed, 12.5, places=3)
        self.assertEqual(vehicle.current_status, "STOPPED_AT")
        self.assertEqual(vehicle.stop_id, "StopArea:OCE87271007")
        self.assertEqual(vehicle.timestamp, 1760000100)
        self.assertEqual(vehicle.vehicle_id, "TGV-3125")

    def test_skips_non_vehicle_entities(self):
        # FeedEntity.alert is field 5 (vehicle is field 4)
        alert_entity = ld(1, b"alert-1") + ld(5, b"some alert payload")
        payload = build_feed([alert_entity, build_vehicle_entity("v1", "TRIP1", "R1", 49.0, 2.0)])
        feed = parse_feed(payload)
        self.assertEqual(len(feed.vehicles), 1)

    def test_unknown_fields_are_skipped(self):
        vehicle = ld(1, ld(1, b"TRIP9")) + ld(2, f32(1, 49.4) + f32(2, 1.6)) + ld(20, b"unknown extension field")
        entity = ld(1, b"e1") + ld(4, vehicle) + ld(99, varint(12345))
        feed = parse_feed(build_feed([entity]))
        self.assertEqual(len(feed.vehicles), 1)
        self.assertEqual(feed.vehicles[0].trip_id, "TRIP9")

    def test_status_mapping(self):
        for raw, expected in [(0, "IN_TRANSIT_TO"), (1, "STOPPED_AT"), (2, "INCOMING_AT")]:
            payload = build_feed([build_vehicle_entity("e", "T", "R", 49.0, 2.0, status=raw)])
            self.assertEqual(parse_feed(payload).vehicles[0].current_status, expected)

    def test_truncated_payload_raises(self):
        with self.assertRaises(ProtobufError):
            parse_feed(b"\x0a\xff\xff")

    def test_roundtrip_with_real_bindings_if_available(self):
        try:
            from google.transit import gtfs_realtime_pb2
        except ImportError:
            self.skipTest("gtfs-realtime-bindings not installed")
        message = gtfs_realtime_pb2.FeedMessage()
        message.header.gtfs_realtime_version = "2.0"
        message.header.timestamp = 1760000000
        entity = message.entity.add()
        entity.id = "entity-42"
        entity.vehicle.trip.trip_id = "OCESN3401F"
        entity.vehicle.trip.route_id = "OCE:Train:9"
        entity.vehicle.position.latitude = 48.8809
        entity.vehicle.position.longitude = 2.3206
        entity.vehicle.position.speed = 0.0
        entity.vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.STOPPED_AT
        entity.vehicle.stop_id = "StopArea:OCE87271007"
        entity.vehicle.timestamp = 1760000200
        entity.vehicle.vehicle.id = "TGV-3401"

        feed = parse_feed(message.SerializeToString())
        self.assertEqual(feed.version, "2.0")
        self.assertEqual(feed.timestamp, 1760000000)
        self.assertEqual(len(feed.vehicles), 1)
        vehicle = feed.vehicles[0]
        self.assertEqual(vehicle.entity_id, "entity-42")
        self.assertEqual(vehicle.trip_id, "OCESN3401F")
        self.assertEqual(vehicle.route_id, "OCE:Train:9")
        self.assertAlmostEqual(vehicle.position.latitude, 48.8809, places=5)
        self.assertAlmostEqual(vehicle.position.longitude, 2.3206, places=5)
        self.assertEqual(vehicle.current_status, "STOPPED_AT")
        self.assertEqual(vehicle.stop_id, "StopArea:OCE87271007")
        self.assertEqual(vehicle.timestamp, 1760000200)
        self.assertEqual(vehicle.vehicle_id, "TGV-3401")


if __name__ == "__main__":
    unittest.main()
