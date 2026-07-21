"""End-to-end smoke test: run() with faked speakers, scraped over real HTTP."""

from __future__ import annotations

import threading
import urllib.request
from types import SimpleNamespace

from prometheus_client import CollectorRegistry

import sonos_exporter.main as main_module
from sonos_exporter.config import Config

from conftest import UID, ZONE, FakeSpeaker


def test_run_serves_metrics_over_http(monkeypatch):
    monkeypatch.setattr(
        main_module,
        "soco",
        SimpleNamespace(discover=lambda timeout: {FakeSpeaker()}, SoCo=None),
    )
    registry = CollectorRegistry()
    config = Config(port=0, address="127.0.0.1", poll_interval=0.05)

    stop = threading.Event()
    servers = []
    thread = threading.Thread(
        target=main_module.run,
        args=(config, registry),
        kwargs={"stop": stop, "on_server_started": servers.append},
        daemon=True,
    )
    thread.start()
    try:
        ready = threading.Event()
        for _ in range(100):
            if registry.get_sample_value(
                "sonos_volume", {"uid": UID, "zone_name": ZONE}
            ):
                break
            ready.wait(0.05)
        else:
            raise AssertionError("first poll cycle never completed")

        port = servers[0].server_port
        body = (
            urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5)
            .read()
            .decode()
        )
        assert "sonos_volume" in body
        assert f'uid="{UID}"' in body
        assert f'zone_name="{ZONE}"' in body
        assert "sonos_speakers_discovered 1.0" in body
        assert 'sonos_playback_state{state="PLAYING"' in body or "sonos_playback_state{" in body
        assert "sonos_last_poll_successful" in body
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
