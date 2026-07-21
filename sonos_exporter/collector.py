"""Maps SoCo speaker state onto Prometheus metrics."""

from __future__ import annotations

import logging
import time
from typing import Protocol

from soco.exceptions import NotSupportedException

from .config import Config
from .metrics import SonosMetrics

logger = logging.getLogger(__name__)

PLAYBACK_STATES = ("PLAYING", "PAUSED_PLAYBACK", "STOPPED", "TRANSITIONING")
REPEAT_MODES = ("off", "all", "one")

# The endpoints a poll cycle touches, in order. Counters for all of them are
# created up front so increase() queries see "no errors" rather than "no data".
ENDPOINTS = (
    "speaker_info",
    "audio",
    "eq_modes",
    "transport",
    "track",
    "group",
    "battery",
)


class SpeakerLike(Protocol):
    """The slice of soco.SoCo the collector needs (fakeable in tests)."""

    ip_address: str
    volume: int
    mute: bool
    bass: int
    treble: int
    loudness: bool
    night_mode: bool | None
    dialog_mode: bool | None
    shuffle: bool
    repeat: bool | str
    group: object

    def get_speaker_info(self) -> dict: ...
    def get_current_transport_info(self) -> dict: ...
    def get_current_track_info(self) -> dict: ...
    def get_battery_info(self) -> dict: ...


def parse_track_time(value) -> float | None:
    """Parse a Sonos ``H:MM:SS`` time string to seconds.

    Sonos reports ``NOT_IMPLEMENTED`` for streams without a position, and
    empty strings when nothing is queued; both map to None.
    """
    if not value or not isinstance(value, str):
        return None
    parts = value.split(":")
    if not all(part.strip().isdigit() for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def repeat_mode(value) -> str:
    """Map soco's repeat property (False / True / 'ONE') to a label value."""
    if value == "ONE":
        return "one"
    return "all" if value else "off"


class SpeakerCollector:
    def __init__(self, metrics: SonosMetrics, config: Config) -> None:
        self.metrics = metrics
        self.config = config
        # ip -> (uid, zone_name), so an unreachable speaker keeps its identity
        self._identity: dict[str, tuple[str, str]] = {}
        # uid -> last exported label values, for series whose labels can change
        self._info_series: dict[str, tuple[str, ...]] = {}
        self._track_series: dict[str, tuple[str, ...]] = {}
        # Speakers without a battery raise NotSupportedException; remember them
        # so every later cycle skips the extra HTTP request.
        self._battery_unsupported: set[str] = set()

    def collect(self, speaker: SpeakerLike) -> bool:
        """Poll one speaker.

        Endpoint failures are isolated: one failing call still lets the others
        update. Returns True when the whole cycle was error-free.
        """
        started = time.monotonic()
        m = self.metrics

        try:
            info = speaker.get_speaker_info()
        except Exception:
            # Unreachable. Label with the identity from an earlier poll if we
            # have one, else fall back to the IP so the failure is visible.
            uid, zone_name = self._identity.get(
                speaker.ip_address, (speaker.ip_address, speaker.ip_address)
            )
            logger.warning("Speaker %s (%s) is unreachable", zone_name, speaker.ip_address)
            m.poll_errors.labels(uid=uid, zone_name=zone_name, endpoint="speaker_info").inc()
            m.set_bool(m.speaker_reachable, uid, zone_name, False)
            m.set_bool(m.poll_success, uid, zone_name, False)
            m.poll_duration.labels(uid=uid, zone_name=zone_name).set(
                time.monotonic() - started
            )
            return False

        uid = info.get("uid") or speaker.ip_address
        zone_name = info.get("zone_name") or speaker.ip_address
        self._identity[speaker.ip_address] = (uid, zone_name)
        m.set_bool(m.speaker_reachable, uid, zone_name, True)
        self._replace_series(
            m.speaker_info,
            self._info_series,
            uid,
            (
                uid,
                zone_name,
                info.get("model_name") or "",
                info.get("model_number") or "",
                info.get("software_version") or "",
                info.get("hardware_version") or "",
                info.get("mac_address") or "",
            ),
        )

        for endpoint in ENDPOINTS:
            m.poll_errors.labels(uid=uid, zone_name=zone_name, endpoint=endpoint)

        steps = [
            ("audio", self._collect_audio),
            ("eq_modes", self._collect_eq_modes),
            ("transport", self._collect_transport),
            ("track", self._collect_track),
            ("group", self._collect_group),
            ("battery", self._collect_battery),
        ]
        errors = 0
        for endpoint, step in steps:
            try:
                step(speaker, uid, zone_name)
            except Exception:
                errors += 1
                m.poll_errors.labels(uid=uid, zone_name=zone_name, endpoint=endpoint).inc()
                logger.exception("Failed to fetch %s for %s", endpoint, zone_name)

        success = errors == 0
        m.set_bool(m.poll_success, uid, zone_name, success)
        if success:
            m.last_success_timestamp.labels(uid=uid, zone_name=zone_name).set(time.time())
        m.poll_duration.labels(uid=uid, zone_name=zone_name).set(time.monotonic() - started)
        return success

    def _replace_series(self, gauge, cache: dict, uid: str, labels: tuple[str, ...] | None):
        """Set an info-style series, removing the stale one if its labels changed."""
        old = cache.get(uid)
        if old is not None and old != labels:
            try:
                gauge.remove(*old)
            except KeyError:
                pass
        if labels is None:
            cache.pop(uid, None)
        else:
            cache[uid] = labels
            gauge.labels(*labels).set(1.0)

    def _collect_audio(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        m = self.metrics
        m.volume.labels(uid=uid, zone_name=zone_name).set(speaker.volume)
        m.set_bool(m.mute, uid, zone_name, speaker.mute)
        m.bass.labels(uid=uid, zone_name=zone_name).set(speaker.bass)
        m.treble.labels(uid=uid, zone_name=zone_name).set(speaker.treble)
        m.set_bool(m.loudness, uid, zone_name, speaker.loudness)

    def _collect_eq_modes(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        # These return None on speakers without them (soundbar-only features).
        m = self.metrics
        if speaker.night_mode is not None:
            m.set_bool(m.night_mode, uid, zone_name, speaker.night_mode)
        if speaker.dialog_mode is not None:
            m.set_bool(m.dialog_mode, uid, zone_name, speaker.dialog_mode)

    def _collect_transport(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        m = self.metrics
        state = speaker.get_current_transport_info().get("current_transport_state") or ""
        m.set_one_hot(m.playback_state, uid, zone_name, "state", PLAYBACK_STATES, state)
        m.set_bool(m.shuffle, uid, zone_name, speaker.shuffle)
        m.set_one_hot(
            m.repeat, uid, zone_name, "mode", REPEAT_MODES, repeat_mode(speaker.repeat)
        )

    def _collect_track(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        m = self.metrics
        track = speaker.get_current_track_info()
        position = parse_track_time(track.get("position"))
        duration = parse_track_time(track.get("duration"))
        if position is not None:
            m.track_position.labels(uid=uid, zone_name=zone_name).set(position)
        if duration is not None:
            m.track_duration.labels(uid=uid, zone_name=zone_name).set(duration)

        if not self.config.export_track_info:
            return
        title = track.get("title") or ""
        if title:
            labels = (uid, zone_name, title, track.get("artist") or "", track.get("album") or "")
        else:
            labels = None  # nothing playing: drop the stale series
        self._replace_series(m.track_info, self._track_series, uid, labels)

    def _collect_group(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        group = speaker.group
        if group is None:
            return
        m = self.metrics
        coordinator_uid = getattr(group.coordinator, "uid", None)
        m.set_bool(m.group_coordinator, uid, zone_name, coordinator_uid == uid)
        m.group_size.labels(uid=uid, zone_name=zone_name).set(len(group.members))

    def _collect_battery(self, speaker: SpeakerLike, uid: str, zone_name: str) -> None:
        if uid in self._battery_unsupported:
            return
        try:
            battery = speaker.get_battery_info()
        except NotSupportedException:
            self._battery_unsupported.add(uid)
            logger.debug("Speaker %s has no battery; not asking again", zone_name)
            return
        m = self.metrics
        level = battery.get("Level")
        if level is not None:
            m.battery_percent.labels(uid=uid, zone_name=zone_name).set(float(level))
        power_source = battery.get("PowerSource")
        if power_source:
            m.set_bool(m.battery_charging, uid, zone_name, power_source != "BATTERY")
