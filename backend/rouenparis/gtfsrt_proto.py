"""Minimal protobuf wire-format reader for GTFS-Realtime feeds.

This module decodes a GTFS-Realtime ``FeedMessage`` without requiring the
``gtfs-realtime-bindings`` / ``protobuf`` packages, so the backend stays
stdlib-only. Only the fields needed for VehiclePositions and TripUpdates are
decoded:

    FeedMessage  { header(1), entity(2, repeated) }
    FeedEntity   { id(1), is_deleted(2), trip_update(3), vehicle(4), alert(5) }
    FeedHeader   { gtfs_realtime_version(1), incrementality(2), timestamp(3) }

    VehiclePosition { trip(1), position(2), current_stop_sequence(3),
                      current_status(4), timestamp(5), congestion_level(6),
                      stop_id(7), vehicle(8), occupancy_status(9) }
    Position     { latitude(1), longitude(2), bearing(3), speed(4) }
    VehicleDescriptor { id(1), label(2) }

    TripUpdate   { trip(1), stop_time_update(2, repeated), vehicle(3),
                   timestamp(4), delay(5) }
    TripDescriptor { trip_id(1), start_time(2), start_date(3),
                     schedule_relationship(4), route_id(5), direction_id(6) }
    StopTimeUpdate { stop_sequence(1), arrival(2), departure(3),
                     stop_id(4), schedule_relationship(5) }
    StopTimeEvent  { delay(1), time(2), uncertainty(3) }

Unknown fields are skipped according to their wire type, so the decoder is
forward-compatible with feed extensions.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

WIRE_VARINT = 0
WIRE_FIXED64 = 1
WIRE_LENGTH_DELIMITED = 2
WIRE_FIXED32 = 5


class ProtobufError(ValueError):
    pass


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ProtobufError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 70:
            raise ProtobufError("varint too long")


def _read_tag(buf: bytes, pos: int) -> tuple[int, int, int]:
    key, pos = _read_varint(buf, pos)
    return key >> 3, key & 0x07, pos


def _iter_fields(buf: bytes):
    pos = 0
    while pos < len(buf):
        field_number, wire_type, pos = _read_tag(buf, pos)
        if wire_type == WIRE_VARINT:
            value, pos = _read_varint(buf, pos)
        elif wire_type == WIRE_FIXED64:
            value = buf[pos : pos + 8]
            pos += 8
        elif wire_type == WIRE_LENGTH_DELIMITED:
            length, pos = _read_varint(buf, pos)
            value = buf[pos : pos + length]
            pos += length
        elif wire_type == WIRE_FIXED32:
            value = buf[pos : pos + 4]
            pos += 4
        else:
            raise ProtobufError(f"unsupported wire type {wire_type}")
        yield field_number, wire_type, value


def _to_float(raw: bytes) -> float:
    return struct.unpack("<f", raw)[0]


def _decode_str(value: bytes) -> str:
    return value.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------

@dataclass
class GtfsPosition:
    latitude: float | None = None
    longitude: float | None = None
    bearing: float | None = None
    speed: float | None = None

    def to_dict(self) -> dict:
        return {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "bearing": self.bearing,
            "speed": self.speed,
        }


@dataclass
class GtfsStopTimeEvent:
    delay: int | None = None
    time: int | None = None
    uncertainty: int | None = None

    def to_dict(self) -> dict:
        return {"delay": self.delay, "time": self.time, "uncertainty": self.uncertainty}


@dataclass
class GtfsStopTimeUpdate:
    stop_sequence: int | None = None
    stop_id: str = ""
    arrival: GtfsStopTimeEvent = field(default_factory=GtfsStopTimeEvent)
    departure: GtfsStopTimeEvent = field(default_factory=GtfsStopTimeEvent)
    schedule_relationship: int | None = None

    def to_dict(self) -> dict:
        return {
            "stop_sequence": self.stop_sequence,
            "stop_id": self.stop_id,
            "arrival": self.arrival.to_dict(),
            "departure": self.departure.to_dict(),
            "schedule_relationship": self.schedule_relationship,
        }


@dataclass
class GtfsTripDescriptor:
    trip_id: str = ""
    route_id: str = ""
    direction_id: int | None = None
    start_time: str = ""
    start_date: str = ""

    def to_dict(self) -> dict:
        return {
            "trip_id": self.trip_id,
            "route_id": self.route_id,
            "direction_id": self.direction_id,
            "start_time": self.start_time,
            "start_date": self.start_date,
        }


@dataclass
class GtfsTripUpdate:
    entity_id: str
    trip: GtfsTripDescriptor = field(default_factory=GtfsTripDescriptor)
    stop_time_updates: list = field(default_factory=list)
    timestamp: int | None = None
    delay: int | None = None
    vehicle_id: str = ""
    vehicle_label: str = ""

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "trip": self.trip.to_dict(),
            "stop_time_updates": [s.to_dict() for s in self.stop_time_updates],
            "timestamp": self.timestamp,
            "delay": self.delay,
            "vehicle_id": self.vehicle_id,
            "vehicle_label": self.vehicle_label,
        }


@dataclass
class GtfsVehiclePosition:
    entity_id: str
    trip_id: str = ""
    route_id: str = ""
    direction_id: int | None = None
    stop_id: str = ""
    current_stop_sequence: int | None = None
    current_status: str = ""
    timestamp: int | None = None
    position: GtfsPosition = field(default_factory=GtfsPosition)
    vehicle_id: str = ""
    vehicle_label: str = ""

    def to_dict(self) -> dict:
        return {
            "entity_id": self.entity_id,
            "trip_id": self.trip_id,
            "route_id": self.route_id,
            "direction_id": self.direction_id,
            "stop_id": self.stop_id,
            "current_stop_sequence": self.current_stop_sequence,
            "current_status": self.current_status,
            "timestamp": self.timestamp,
            "position": self.position.to_dict(),
            "vehicle_id": self.vehicle_id,
            "vehicle_label": self.vehicle_label,
        }


@dataclass
class GtfsFeed:
    version: str
    timestamp: int | None
    vehicles: list = field(default_factory=list)
    trip_updates: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Sub-message parsers
# ---------------------------------------------------------------------------

_VEHICLE_STATUS = {
    0: "IN_TRANSIT_TO",
    1: "STOPPED_AT",
    2: "INCOMING_AT",
}


def _parse_position(buf: bytes) -> GtfsPosition:
    position = GtfsPosition()
    for field_number, wire_type, value in _iter_fields(buf):
        if wire_type != WIRE_FIXED32:
            continue
        if field_number == 1:
            position.latitude = _to_float(value)
        elif field_number == 2:
            position.longitude = _to_float(value)
        elif field_number == 3:
            position.bearing = _to_float(value)
        elif field_number == 4:
            position.speed = _to_float(value)
    return position


def _parse_trip_descriptor(buf: bytes) -> GtfsTripDescriptor:
    trip = GtfsTripDescriptor()
    for field_number, wire_type, value in _iter_fields(buf):
        if wire_type == WIRE_LENGTH_DELIMITED:
            if field_number == 1:
                trip.trip_id = _decode_str(value)
            elif field_number == 2:
                trip.start_time = _decode_str(value)
            elif field_number == 3:
                trip.start_date = _decode_str(value)
            elif field_number == 5:
                trip.route_id = _decode_str(value)
        elif wire_type == WIRE_VARINT:
            if field_number == 6:
                trip.direction_id = value
    return trip


def _parse_vehicle_descriptor(buf: bytes) -> dict:
    descriptor = {"id": "", "label": ""}
    for field_number, wire_type, value in _iter_fields(buf):
        if wire_type != WIRE_LENGTH_DELIMITED:
            continue
        if field_number == 1:
            descriptor["id"] = _decode_str(value)
        elif field_number == 2:
            descriptor["label"] = _decode_str(value)
    return descriptor


def _parse_stop_time_event(buf: bytes) -> GtfsStopTimeEvent:
    event = GtfsStopTimeEvent()
    for field_number, wire_type, value in _iter_fields(buf):
        if wire_type != WIRE_VARINT:
            continue
        if field_number == 1:
            event.delay = value
        elif field_number == 2:
            event.time = value
        elif field_number == 3:
            event.uncertainty = value
    return event


def _parse_stop_time_update(buf: bytes) -> GtfsStopTimeUpdate:
    stop = GtfsStopTimeUpdate()
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == WIRE_VARINT:
            stop.stop_sequence = value
        elif field_number == 2 and wire_type == WIRE_LENGTH_DELIMITED:
            stop.arrival = _parse_stop_time_event(value)
        elif field_number == 3 and wire_type == WIRE_LENGTH_DELIMITED:
            stop.departure = _parse_stop_time_event(value)
        elif field_number == 4 and wire_type == WIRE_LENGTH_DELIMITED:
            stop.stop_id = _decode_str(value)
        elif field_number == 5 and wire_type == WIRE_VARINT:
            stop.schedule_relationship = value
    return stop


def _parse_trip_update(buf: bytes) -> GtfsTripUpdate:
    update = GtfsTripUpdate(entity_id="")
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == WIRE_LENGTH_DELIMITED:
            update.trip = _parse_trip_descriptor(value)
        elif field_number == 2 and wire_type == WIRE_LENGTH_DELIMITED:
            update.stop_time_updates.append(_parse_stop_time_update(value))
        elif field_number == 3 and wire_type == WIRE_LENGTH_DELIMITED:
            desc = _parse_vehicle_descriptor(value)
            update.vehicle_id = desc["id"]
            update.vehicle_label = desc["label"]
        elif field_number == 4 and wire_type == WIRE_VARINT:
            update.timestamp = value
        elif field_number == 5 and wire_type == WIRE_VARINT:
            update.delay = value
    return update


def _parse_vehicle_position(buf: bytes) -> GtfsVehiclePosition:
    vehicle = GtfsVehiclePosition(entity_id="")
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == WIRE_LENGTH_DELIMITED:
            descriptor = _parse_trip_descriptor(value)
            vehicle.trip_id = descriptor.trip_id
            vehicle.route_id = descriptor.route_id
            vehicle.direction_id = descriptor.direction_id
        elif field_number == 2 and wire_type == WIRE_LENGTH_DELIMITED:
            vehicle.position = _parse_position(value)
        elif field_number == 3 and wire_type == WIRE_VARINT:
            vehicle.current_stop_sequence = value
        elif field_number == 4 and wire_type == WIRE_VARINT:
            vehicle.current_status = _VEHICLE_STATUS.get(value, str(value))
        elif field_number == 5 and wire_type == WIRE_VARINT:
            vehicle.timestamp = value
        elif field_number == 7 and wire_type == WIRE_LENGTH_DELIMITED:
            vehicle.stop_id = _decode_str(value)
        elif field_number == 8 and wire_type == WIRE_LENGTH_DELIMITED:
            desc = _parse_vehicle_descriptor(value)
            vehicle.vehicle_id = desc["id"]
            vehicle.vehicle_label = desc["label"]
    return vehicle


# ---------------------------------------------------------------------------
# Top-level feed parser
# ---------------------------------------------------------------------------

def _parse_entity(buf: bytes) -> tuple[GtfsVehiclePosition | None, GtfsTripUpdate | None]:
    entity_id = ""
    vehicle = None
    trip_update = None
    for field_number, wire_type, value in _iter_fields(buf):
        if field_number == 1 and wire_type == WIRE_LENGTH_DELIMITED:
            entity_id = _decode_str(value)
        elif field_number == 3 and wire_type == WIRE_LENGTH_DELIMITED:
            trip_update = _parse_trip_update(value)
        elif field_number == 4 and wire_type == WIRE_LENGTH_DELIMITED:
            vehicle = _parse_vehicle_position(value)
    if vehicle is not None:
        vehicle.entity_id = entity_id
    if trip_update is not None:
        trip_update.entity_id = entity_id
    return vehicle, trip_update


def parse_feed(payload: bytes) -> GtfsFeed:
    version = ""
    timestamp = None
    vehicles = []
    trip_updates = []
    for field_number, wire_type, value in _iter_fields(payload):
        if field_number == 1 and wire_type == WIRE_LENGTH_DELIMITED:
            for sub_number, sub_wire, sub_value in _iter_fields(value):
                if sub_number == 1 and sub_wire == WIRE_LENGTH_DELIMITED:
                    version = _decode_str(sub_value)
                elif sub_number == 3 and sub_wire == WIRE_VARINT:
                    timestamp = sub_value
        elif field_number == 2 and wire_type == WIRE_LENGTH_DELIMITED:
            vehicle, trip_update = _parse_entity(value)
            if vehicle is not None:
                vehicles.append(vehicle)
            if trip_update is not None:
                trip_updates.append(trip_update)
    return GtfsFeed(
        version=version, timestamp=timestamp, vehicles=vehicles, trip_updates=trip_updates
    )
