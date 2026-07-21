"""Prometheus metric definitions.

Conventions:
- Every per-speaker metric carries ``uid`` and ``zone_name`` labels. The uid
  (Sonos RINCON id) is the stable identity; the zone name is what humans
  recognise, and is what the Grafana dashboard filters on.
- Durations are exported in seconds, levels in the units the speaker reports
  (volume 0-100, bass/treble -10..10).
- The playback state enum is exported one-hot: one series per state with
  value 0/1, mirroring ``node_systemd_unit_state``. This keeps it usable in
  Grafana state timelines without magic-number decoding.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge

SPEAKER_LABELS = ("uid", "zone_name")


class SonosMetrics:
    """All exporter metrics, bound to a registry (injectable for tests)."""

    def __init__(self, registry: CollectorRegistry) -> None:
        self.registry = registry

        def gauge(name: str, doc: str, labels: tuple[str, ...] = SPEAKER_LABELS) -> Gauge:
            return Gauge(name, doc, labels, registry=registry)

        # -- Exporter / poll health --
        self.speaker_info = gauge(
            "sonos_speaker_info",
            "Static speaker metadata (value is always 1)",
            SPEAKER_LABELS
            + (
                "model_name",
                "model_number",
                "software_version",
                "hardware_version",
                "mac_address",
            ),
        )
        self.speakers_discovered = Gauge(
            "sonos_speakers_discovered",
            "Number of speakers found by the most recent discovery",
            registry=registry,
        )
        self.last_discovery_timestamp = Gauge(
            "sonos_last_discovery_timestamp_seconds",
            "Unix timestamp of the last successful speaker discovery",
            registry=registry,
        )
        self.discovery_errors = Counter(
            "sonos_discovery_errors",
            "Errors while discovering speakers on the network",
            registry=registry,
        )
        self.speaker_reachable = gauge(
            "sonos_speaker_reachable",
            "1 if the speaker responded to the most recent poll",
        )
        self.poll_success = gauge(
            "sonos_last_poll_successful",
            "1 if the most recent poll cycle for this speaker completed without errors",
        )
        self.last_success_timestamp = gauge(
            "sonos_last_successful_poll_timestamp_seconds",
            "Unix timestamp of the last error-free poll cycle for this speaker",
        )
        self.poll_duration = gauge(
            "sonos_poll_duration_seconds",
            "Wall-clock duration of the most recent poll cycle for this speaker",
        )
        self.poll_errors = Counter(
            "sonos_poll_errors",
            "Errors while polling a speaker, by endpoint",
            SPEAKER_LABELS + ("endpoint",),
            registry=registry,
        )

        # -- Audio settings --
        self.volume = gauge("sonos_volume", "Speaker volume (0-100)")
        self.mute = gauge("sonos_mute", "1 if the speaker is muted")
        self.bass = gauge("sonos_bass_level", "Bass EQ level (-10 to 10)")
        self.treble = gauge("sonos_treble_level", "Treble EQ level (-10 to 10)")
        self.loudness = gauge("sonos_loudness_enabled", "1 if loudness compensation is on")
        self.night_mode = gauge(
            "sonos_night_mode_enabled",
            "1 if night mode is on (home-theatre products only)",
        )
        self.dialog_mode = gauge(
            "sonos_dialog_mode_enabled",
            "1 if speech enhancement is on (home-theatre products only)",
        )

        # -- Playback --
        self.playback_state = gauge(
            "sonos_playback_state",
            "One-hot playback state; exactly one state label has value 1",
            SPEAKER_LABELS + ("state",),
        )
        self.shuffle = gauge("sonos_shuffle_enabled", "1 if shuffle is on")
        self.repeat = gauge(
            "sonos_repeat_state",
            "One-hot repeat mode (off/all/one); exactly one mode label has value 1",
            SPEAKER_LABELS + ("mode",),
        )
        self.track_position = gauge(
            "sonos_track_position_seconds", "Position in the current track"
        )
        self.track_duration = gauge(
            "sonos_track_duration_seconds", "Duration of the current track"
        )
        self.track_info = gauge(
            "sonos_track_info",
            "Currently playing track (value is always 1). "
            "Series churn with every track change; disable with EXPORT_TRACK_INFO=false",
            SPEAKER_LABELS + ("title", "artist", "album"),
        )

        # -- Grouping --
        self.group_coordinator = gauge(
            "sonos_group_coordinator", "1 if this speaker coordinates its group"
        )
        self.group_size = gauge(
            "sonos_group_size", "Number of speakers in this speaker's group"
        )

        # -- Battery (portable speakers only) --
        self.battery_percent = gauge(
            "sonos_battery_percent", "Battery charge level (portable speakers only)"
        )
        self.battery_charging = gauge(
            "sonos_battery_charging",
            "1 if the speaker is on external power (charging ring/USB), 0 on battery",
        )

    def set_bool(self, gauge: Gauge, uid: str, zone_name: str, value: bool) -> None:
        gauge.labels(uid=uid, zone_name=zone_name).set(1.0 if value else 0.0)

    def set_one_hot(
        self,
        gauge: Gauge,
        uid: str,
        zone_name: str,
        label: str,
        known_states: tuple[str, ...],
        active: str,
    ) -> None:
        """Set one series per state: 1 for the active state, 0 for the rest.

        An active state outside ``known_states`` still gets its own series, so
        firmware surprises show up in the metrics instead of vanishing.
        """
        states = list(known_states)
        if active not in states:
            states.append(active)
        for state in states:
            gauge.labels(**{"uid": uid, "zone_name": zone_name, label: state}).set(
                1.0 if state == active else 0.0
            )
