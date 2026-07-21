"""Shared fixtures.

Tests exercise the collector against a FakeSpeaker that mirrors the slice of
soco.SoCo the exporter touches, including soco's real NotSupportedException
for the battery endpoint, so error handling matches the live library.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from prometheus_client import CollectorRegistry

from sonos_exporter.config import Config
from sonos_exporter.metrics import SonosMetrics

UID = "RINCON_TEST0000000001400"
ZONE = "Living Room"
IP = "192.168.1.50"


class FakeSpeaker:
    """Stands in for soco.SoCo; per-endpoint values or exceptions.

    Pass ``errors={"volume": SomeError()}`` to make a property or method
    raise; pass keyword overrides to change the reported values.
    """

    def __init__(
        self,
        ip_address: str = IP,
        uid: str = UID,
        zone_name: str = ZONE,
        errors: dict | None = None,
        **overrides,
    ):
        self.ip_address = ip_address
        self._uid = uid
        self._zone_name = zone_name
        self._errors = errors or {}
        self._battery_calls = 0
        self._service_list_calls = 0
        self._values = {
            "volume": 25,
            "mute": False,
            "bass": 2,
            "treble": -1,
            "loudness": True,
            "night_mode": None,
            "dialog_mode": None,
            "shuffle": False,
            "repeat": False,
            "transport_state": "PLAYING",
            "title": "Sultans of Swing",
            "artist": "Dire Straits",
            "album": "Dire Straits",
            "position": "0:01:30",
            "duration": "0:05:47",
            "uri": "x-sonosapi-stream:s200662?sid=254",  # classified as radio
            "battery": {"Level": 80, "PowerSource": "BATTERY", "Health": "GREEN"},
            "group_members": 1,
            "group_coordinator_uid": uid,
        }
        self._values.update(overrides)

    def _get(self, name: str):
        if name in self._errors:
            raise self._errors[name]
        return self._values[name]

    # -- identity ---------------------------------------------------------

    def get_speaker_info(self):
        if "speaker_info" in self._errors:
            raise self._errors["speaker_info"]
        return {
            "uid": self._uid,
            "zone_name": self._zone_name,
            "model_name": "Sonos One",
            "model_number": "S13",
            "software_version": "83.1-61240",
            "hardware_version": "1.26.1.5-1.1",
            "mac_address": "00:0E:58:AA:BB:CC",
            "serial_number": "00-0E-58-AA-BB-CC:8",
            "display_version": "16.1",
            "player_icon": "",
        }

    # -- audio settings ---------------------------------------------------

    volume = property(lambda self: self._get("volume"))
    mute = property(lambda self: self._get("mute"))
    bass = property(lambda self: self._get("bass"))
    treble = property(lambda self: self._get("treble"))
    loudness = property(lambda self: self._get("loudness"))
    night_mode = property(lambda self: self._get("night_mode"))
    dialog_mode = property(lambda self: self._get("dialog_mode"))
    shuffle = property(lambda self: self._get("shuffle"))
    repeat = property(lambda self: self._get("repeat"))

    # -- playback ---------------------------------------------------------

    def get_current_transport_info(self):
        return {
            "current_transport_state": self._get("transport_state"),
            "current_transport_status": "OK",
            "current_transport_speed": "1",
        }

    def get_current_track_info(self):
        if "track" in self._errors:
            raise self._errors["track"]
        return {
            "title": self._get("title"),
            "artist": self._get("artist"),
            "album": self._get("album"),
            "position": self._get("position"),
            "duration": self._get("duration"),
            "uri": self._get("uri"),
            "album_art": "",
        }

    # -- music services ---------------------------------------------------

    @property
    def musicServices(self):  # noqa: N802 — matches soco's attribute name
        if "music_services" in self._errors:
            raise self._errors["music_services"]
        return _FakeMusicServices(self)

    # -- grouping ---------------------------------------------------------

    @property
    def group(self):
        if "group" in self._errors:
            raise self._errors["group"]
        members = [self] + [object() for _ in range(self._get("group_members") - 1)]
        return SimpleNamespace(
            uid=f"{self._uid}:5",
            coordinator=SimpleNamespace(uid=self._get("group_coordinator_uid")),
            members=set(members),
        )

    # -- battery ----------------------------------------------------------

    def get_battery_info(self, timeout=3.0):
        self._battery_calls += 1
        if "battery" in self._errors:
            raise self._errors["battery"]
        return self._get("battery")


class _FakeMusicServices:
    """Mimics soco's MusicServices UPnP service wrapper."""

    SERVICES = {"254": "TuneIn", "204": "Apple Music", "284": "YouTube Music"}

    def __init__(self, speaker: FakeSpeaker):
        self._speaker = speaker

    def ListAvailableServices(self):  # noqa: N802 — matches soco's method name
        self._speaker._service_list_calls += 1
        if "service_list" in self._speaker._errors:
            raise self._speaker._errors["service_list"]
        services = "".join(
            f'<Service Id="{sid}" Name="{name}" Version="1.1"/>'
            for sid, name in self.SERVICES.items()
        )
        return {"AvailableServiceDescriptorList": f"<Services>{services}</Services>"}


@pytest.fixture
def registry() -> CollectorRegistry:
    return CollectorRegistry()


@pytest.fixture
def metrics(registry: CollectorRegistry) -> SonosMetrics:
    return SonosMetrics(registry)


@pytest.fixture
def config() -> Config:
    return Config()
