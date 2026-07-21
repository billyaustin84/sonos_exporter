import pytest

from sonos_exporter.config import (
    DEFAULT_DISCOVERY_INTERVAL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    MIN_POLL_INTERVAL,
    Config,
    ConfigError,
)


def test_defaults_from_empty_env():
    config = Config.from_env({})
    assert config.port == DEFAULT_PORT
    assert config.address == "0.0.0.0"
    assert config.poll_interval == DEFAULT_POLL_INTERVAL
    assert config.discovery_interval == DEFAULT_DISCOVERY_INTERVAL
    assert config.hosts == ()
    assert config.export_track_info is True


def test_hosts_parsed_and_stripped():
    config = Config.from_env({"SONOS_HOSTS": " 192.168.1.50, 192.168.1.51 ,,"})
    assert config.hosts == ("192.168.1.50", "192.168.1.51")


def test_port_must_be_integer():
    with pytest.raises(ConfigError, match="EXPORTER_PORT"):
        Config.from_env({"EXPORTER_PORT": "not-a-port"})


def test_poll_interval_clamped_to_minimum():
    config = Config.from_env({"POLL_INTERVAL_SECONDS": "0.5"})
    assert config.poll_interval == MIN_POLL_INTERVAL


def test_poll_interval_must_be_number():
    with pytest.raises(ConfigError, match="POLL_INTERVAL_SECONDS"):
        Config.from_env({"POLL_INTERVAL_SECONDS": "fast"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", True), ("true", True), ("YES", True), ("0", False), ("off", False)],
)
def test_export_track_info_boolean_forms(raw, expected):
    assert Config.from_env({"EXPORT_TRACK_INFO": raw}).export_track_info is expected


def test_invalid_boolean_raises():
    with pytest.raises(ConfigError, match="EXPORT_TRACK_INFO"):
        Config.from_env({"EXPORT_TRACK_INFO": "maybe"})
