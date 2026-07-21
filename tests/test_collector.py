import pytest
from soco.exceptions import NotSupportedException

from sonos_exporter.collector import (
    SpeakerCollector,
    classify_music_source,
    parse_track_time,
    repeat_mode,
    service_id_from_uri,
)

from conftest import IP, UID, ZONE, FakeSpeaker

LABELS = {"uid": UID, "zone_name": ZONE}


@pytest.fixture
def collector(metrics, config):
    return SpeakerCollector(metrics, config)


def sample(registry, name, extra=None):
    return registry.get_sample_value(name, {**LABELS, **(extra or {})})


# -- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0:03:45", 225.0),
        ("1:02:03", 3723.0),
        ("0:00:00", 0.0),
        ("NOT_IMPLEMENTED", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_track_time(raw, expected):
    assert parse_track_time(raw) == expected


def test_repeat_mode_mapping():
    assert repeat_mode(False) == "off"
    assert repeat_mode(True) == "all"
    assert repeat_mode("ONE") == "one"


# -- happy path ------------------------------------------------------------


def test_full_collect_sets_all_metrics(collector, registry):
    assert collector.collect(FakeSpeaker()) is True

    assert sample(registry, "sonos_speaker_reachable") == 1.0
    assert sample(registry, "sonos_last_poll_successful") == 1.0
    assert sample(registry, "sonos_last_successful_poll_timestamp_seconds") > 0
    assert sample(registry, "sonos_poll_duration_seconds") is not None

    assert sample(registry, "sonos_volume") == 25.0
    assert sample(registry, "sonos_mute") == 0.0
    assert sample(registry, "sonos_bass_level") == 2.0
    assert sample(registry, "sonos_treble_level") == -1.0
    assert sample(registry, "sonos_loudness_enabled") == 1.0

    assert sample(registry, "sonos_playback_state", {"state": "PLAYING"}) == 1.0
    assert sample(registry, "sonos_playback_state", {"state": "STOPPED"}) == 0.0
    assert sample(registry, "sonos_shuffle_enabled") == 0.0
    assert sample(registry, "sonos_repeat_state", {"mode": "off"}) == 1.0
    assert sample(registry, "sonos_track_position_seconds") == 90.0
    assert sample(registry, "sonos_track_duration_seconds") == 347.0
    assert (
        sample(
            registry,
            "sonos_track_info",
            {"title": "Sultans of Swing", "artist": "Dire Straits", "album": "Dire Straits"},
        )
        == 1.0
    )

    assert sample(registry, "sonos_group_coordinator") == 1.0
    assert sample(registry, "sonos_group_size") == 1.0
    assert sample(registry, "sonos_battery_percent") == 80.0
    assert sample(registry, "sonos_battery_charging") == 0.0

    info = registry.get_sample_value(
        "sonos_speaker_info",
        {
            **LABELS,
            "model_name": "Sonos One",
            "model_number": "S13",
            "software_version": "83.1-61240",
            "hardware_version": "1.26.1.5-1.1",
            "mac_address": "00:0E:58:AA:BB:CC",
        },
    )
    assert info == 1.0


def test_poll_error_counters_exist_at_zero_after_clean_poll(collector, registry):
    collector.collect(FakeSpeaker())
    for endpoint in ("audio", "transport", "track", "group", "battery"):
        assert (
            sample(registry, "sonos_poll_errors_total", {"endpoint": endpoint}) == 0.0
        )


# -- eq modes --------------------------------------------------------------


def test_night_and_dialog_mode_absent_by_default(collector, registry):
    collector.collect(FakeSpeaker())
    assert sample(registry, "sonos_night_mode_enabled") is None
    assert sample(registry, "sonos_dialog_mode_enabled") is None


def test_night_and_dialog_mode_on_soundbars(collector, registry):
    collector.collect(FakeSpeaker(night_mode=True, dialog_mode=False))
    assert sample(registry, "sonos_night_mode_enabled") == 1.0
    assert sample(registry, "sonos_dialog_mode_enabled") == 0.0


# -- playback --------------------------------------------------------------


def test_unknown_transport_state_gets_own_series(collector, registry):
    collector.collect(FakeSpeaker(transport_state="VIBING"))
    assert sample(registry, "sonos_playback_state", {"state": "VIBING"}) == 1.0
    assert sample(registry, "sonos_playback_state", {"state": "PLAYING"}) == 0.0


def test_track_change_replaces_track_info_series(collector, registry):
    collector.collect(FakeSpeaker())
    collector.collect(FakeSpeaker(title="Money for Nothing", album="Brothers in Arms"))
    assert (
        sample(
            registry,
            "sonos_track_info",
            {"title": "Money for Nothing", "artist": "Dire Straits", "album": "Brothers in Arms"},
        )
        == 1.0
    )
    assert (
        sample(
            registry,
            "sonos_track_info",
            {"title": "Sultans of Swing", "artist": "Dire Straits", "album": "Dire Straits"},
        )
        is None
    )


def test_idle_speaker_drops_track_info_series(collector, registry):
    collector.collect(FakeSpeaker())
    collector.collect(FakeSpeaker(title="", position="", duration=""))
    assert (
        sample(
            registry,
            "sonos_track_info",
            {"title": "Sultans of Swing", "artist": "Dire Straits", "album": "Dire Straits"},
        )
        is None
    )


def test_track_info_disabled_by_config(metrics, registry):
    from sonos_exporter.config import Config

    collector = SpeakerCollector(metrics, Config(export_track_info=False))
    collector.collect(FakeSpeaker())
    # position still exported, only the labelled info series is suppressed
    assert sample(registry, "sonos_track_position_seconds") == 90.0
    assert (
        sample(
            registry,
            "sonos_track_info",
            {"title": "Sultans of Swing", "artist": "Dire Straits", "album": "Dire Straits"},
        )
        is None
    )


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("x-sonos-htastream:RINCON_TEST:spdif", "tv"),
        ("x-sonosapi-stream:s200662?sid=254", "radio"),
        # soco 0.31's ^x-sonosapi-hls: pattern misses the -static variant
        ("x-sonosapi-hls-static:ALkSOiFF?sid=204", "music_service"),
        ("x-sonos-spotify:spotify%3atrack%3a123?sid=9", "spotify"),
        ("x-rincon:RINCON_COORDINATOR01400", "group"),
        ("x-rincon-stream:RINCON_TEST", "line_in"),
        ("x-file-cifs://nas/music/track.flac", "library"),
        ("", "none"),
        ("gibberish://what", "unknown"),
    ],
)
def test_classify_music_source(uri, expected):
    assert classify_music_source(uri) == expected


def test_music_source_classified_from_uri(collector, registry):
    collector.collect(FakeSpeaker())
    assert sample(registry, "sonos_music_source", {"source": "radio"}) == 1.0
    assert sample(registry, "sonos_music_source", {"source": "tv"}) == 0.0


def test_tv_playback_exposes_source_but_no_track_metadata(collector, registry):
    """TV audio has no title/artist/position; the source is the only signal."""
    collector.collect(
        FakeSpeaker(
            title="",
            artist="",
            album="",
            position="NOT_IMPLEMENTED",
            duration="NOT_IMPLEMENTED",
            uri="x-sonos-htastream:RINCON_TEST:spdif",
        )
    )
    assert sample(registry, "sonos_music_source", {"source": "tv"}) == 1.0
    assert sample(registry, "sonos_track_position_seconds") is None
    assert sample(registry, "sonos_track_duration_seconds") is None
    # no track_info series at all — nothing about the TV content is exported
    samples = [
        s
        for metric in registry.collect()
        if metric.name == "sonos_track_info"
        for s in metric.samples
    ]
    assert samples == []


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("x-sonosapi-hls-static:ALkSOiFF?sid=284&flags=8&sn=2", "284"),
        ("x-sonosapi-stream:s200662?sid=254", "254"),
        ("x-sonos-spotify:track?a=1&sid=9&b=2", "9"),
        ("x-sonos-htastream:RINCON_TEST:spdif", None),
        ("", None),
    ],
)
def test_service_id_from_uri(uri, expected):
    assert service_id_from_uri(uri) == expected


def test_music_service_resolved_from_sid(collector, registry):
    collector.collect(FakeSpeaker(uri="x-sonosapi-hls-static:ALkSOiFF?sid=284&sn=2"))
    assert sample(registry, "sonos_music_service", {"service": "YouTube Music"}) == 1.0


def test_music_service_list_fetched_once(collector, registry):
    speaker = FakeSpeaker()  # default uri has sid=254
    collector.collect(speaker)
    collector.collect(speaker)
    assert speaker._service_list_calls == 1
    assert sample(registry, "sonos_music_service", {"service": "TuneIn"}) == 1.0


def test_unknown_sid_refreshes_list_once_then_gives_up(collector, registry):
    speaker = FakeSpeaker(uri="x-sonosapi-stream:s1?sid=999")
    collector.collect(speaker)  # initial fetch + one refresh for the new sid
    collector.collect(speaker)  # remembered as unknown; no further fetches
    assert speaker._service_list_calls == 2
    samples = [
        s
        for metric in registry.collect()
        if metric.name == "sonos_music_service"
        for s in metric.samples
    ]
    assert samples == []


def test_service_change_replaces_series(collector, registry):
    collector.collect(FakeSpeaker(uri="x-sonosapi-stream:s1?sid=204"))
    collector.collect(FakeSpeaker(uri="x-sonosapi-stream:s1?sid=284"))
    assert sample(registry, "sonos_music_service", {"service": "YouTube Music"}) == 1.0
    assert sample(registry, "sonos_music_service", {"service": "Apple Music"}) is None


def test_no_sid_drops_service_series(collector, registry):
    collector.collect(FakeSpeaker())
    collector.collect(FakeSpeaker(uri="x-sonos-htastream:RINCON_TEST:spdif"))
    assert sample(registry, "sonos_music_service", {"service": "TuneIn"}) is None


def test_service_lookup_failure_does_not_fail_the_poll(collector, registry):
    speaker = FakeSpeaker(errors={"music_services": RuntimeError("SOAP fault")})
    assert collector.collect(speaker) is True
    assert sample(registry, "sonos_track_position_seconds") == 90.0
    assert sample(registry, "sonos_music_source", {"source": "radio"}) == 1.0


# -- grouping --------------------------------------------------------------


def test_grouped_speaker_reports_size_and_non_coordinator(collector, registry):
    collector.collect(
        FakeSpeaker(group_members=3, group_coordinator_uid="RINCON_OTHER")
    )
    assert sample(registry, "sonos_group_coordinator") == 0.0
    assert sample(registry, "sonos_group_size") == 3.0


# -- battery ---------------------------------------------------------------


def test_battery_charging_when_on_external_power(collector, registry):
    collector.collect(
        FakeSpeaker(battery={"Level": 55, "PowerSource": "SONOS_CHARGING_RING"})
    )
    assert sample(registry, "sonos_battery_percent") == 55.0
    assert sample(registry, "sonos_battery_charging") == 1.0


def test_battery_not_supported_is_silent_and_not_retried(collector, registry):
    speaker = FakeSpeaker(errors={"battery": NotSupportedException()})
    assert collector.collect(speaker) is True  # not an error
    assert collector.collect(speaker) is True
    assert speaker._battery_calls == 1  # remembered, not retried
    assert sample(registry, "sonos_battery_percent") is None
    assert sample(registry, "sonos_poll_errors_total", {"endpoint": "battery"}) == 0.0


def test_battery_network_error_counts_as_error(collector, registry):
    speaker = FakeSpeaker(errors={"battery": ConnectionError("timeout")})
    assert collector.collect(speaker) is False
    assert sample(registry, "sonos_poll_errors_total", {"endpoint": "battery"}) == 1.0


# -- failure isolation -----------------------------------------------------


def test_endpoint_failure_is_isolated(collector, registry):
    speaker = FakeSpeaker(errors={"volume": RuntimeError("SOAP fault")})
    assert collector.collect(speaker) is False

    assert sample(registry, "sonos_last_poll_successful") == 0.0
    assert sample(registry, "sonos_poll_errors_total", {"endpoint": "audio"}) == 1.0
    # everything else still updated
    assert sample(registry, "sonos_playback_state", {"state": "PLAYING"}) == 1.0
    assert sample(registry, "sonos_group_size") == 1.0
    assert sample(registry, "sonos_speaker_reachable") == 1.0


def test_unreachable_speaker_keeps_identity_from_earlier_poll(collector, registry):
    collector.collect(FakeSpeaker())
    assert sample(registry, "sonos_speaker_reachable") == 1.0

    dead = FakeSpeaker(errors={"speaker_info": ConnectionError("refused")})
    assert collector.collect(dead) is False
    assert sample(registry, "sonos_speaker_reachable") == 0.0
    assert sample(registry, "sonos_last_poll_successful") == 0.0
    assert (
        sample(registry, "sonos_poll_errors_total", {"endpoint": "speaker_info"}) == 1.0
    )


def test_never_seen_unreachable_speaker_falls_back_to_ip_labels(collector, registry):
    dead = FakeSpeaker(errors={"speaker_info": ConnectionError("refused")})
    assert collector.collect(dead) is False
    assert (
        registry.get_sample_value(
            "sonos_speaker_reachable", {"uid": IP, "zone_name": IP}
        )
        == 0.0
    )
