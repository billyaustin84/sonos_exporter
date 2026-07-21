import threading
from types import SimpleNamespace

import sonos_exporter.main as main_module
from sonos_exporter.config import Config

from conftest import UID, ZONE, FakeSpeaker


def fake_soco(monkeypatch, *, discovered=None, discover_error=None):
    """Replace the soco module used by main with a controllable stand-in."""

    def discover(timeout):
        if discover_error is not None:
            raise discover_error
        return discovered

    stub = SimpleNamespace(SoCo=lambda host: FakeSpeaker(ip_address=host), discover=discover)
    monkeypatch.setattr(main_module, "soco", stub)
    return stub


def test_get_speakers_uses_static_hosts(monkeypatch):
    fake_soco(monkeypatch, discover_error=AssertionError("must not discover"))
    config = Config(hosts=("192.168.1.50", "192.168.1.51"))
    speakers = main_module.get_speakers(config)
    assert [s.ip_address for s in speakers] == ["192.168.1.50", "192.168.1.51"]


def test_get_speakers_discovers_and_sorts_by_ip(monkeypatch):
    fake_soco(
        monkeypatch,
        discovered={FakeSpeaker(ip_address="192.168.1.60"), FakeSpeaker(ip_address="192.168.1.51")},
    )
    speakers = main_module.get_speakers(Config())
    assert [s.ip_address for s in speakers] == ["192.168.1.51", "192.168.1.60"]


def test_get_speakers_handles_no_result(monkeypatch):
    fake_soco(monkeypatch, discovered=None)  # soco.discover returns None on timeout
    assert main_module.get_speakers(Config()) == []


def run_poll_until(metrics, config, predicate, timeout=5.0):
    """Run poll_forever in a thread until predicate() holds, then stop it."""
    stop = threading.Event()
    thread = threading.Thread(
        target=main_module.poll_forever, args=(config, metrics, stop), daemon=True
    )
    thread.start()
    try:
        deadline = threading.Event()
        for _ in range(int(timeout / 0.02)):
            if predicate():
                break
            deadline.wait(0.02)
        else:
            raise AssertionError("poll loop never produced the expected metrics")
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()


def test_poll_forever_polls_discovered_speakers(monkeypatch, metrics, registry):
    fake_soco(monkeypatch, discovered={FakeSpeaker()})
    config = Config(poll_interval=0.01, discovery_interval=3600)

    run_poll_until(
        metrics,
        config,
        lambda: registry.get_sample_value(
            "sonos_volume", {"uid": UID, "zone_name": ZONE}
        )
        == 25.0,
    )
    assert registry.get_sample_value("sonos_speakers_discovered") == 1.0
    assert registry.get_sample_value("sonos_last_discovery_timestamp_seconds") > 0


def test_poll_forever_survives_discovery_failure(monkeypatch, metrics, registry):
    fake_soco(monkeypatch, discover_error=OSError("no multicast here"))
    config = Config(poll_interval=0.01, discovery_interval=0.01)

    run_poll_until(
        metrics,
        config,
        lambda: (registry.get_sample_value("sonos_discovery_errors_total") or 0) >= 2,
    )
