"""Exporter entry point: HTTP server plus the speaker poll loop."""

from __future__ import annotations

import logging
import os
import threading
import time

import soco
from prometheus_client import REGISTRY, CollectorRegistry, start_http_server

from .collector import SpeakerCollector
from .config import Config, ConfigError
from .metrics import SonosMetrics

logger = logging.getLogger(__name__)


def get_speakers(config: Config) -> list:
    """Find the speakers to poll.

    With SONOS_HOSTS configured, exactly those speakers are polled — the way
    to go when SSDP multicast can't reach the speakers (Docker's default
    network, VLANs, WSL). Otherwise the network is searched via SoCo's
    multicast discovery.
    """
    if config.hosts:
        return [soco.SoCo(host) for host in config.hosts]
    zones = soco.discover(timeout=config.discovery_timeout) or set()
    return sorted(zones, key=lambda zone: zone.ip_address)


def poll_forever(config: Config, metrics: SonosMetrics, stop: threading.Event) -> None:
    """Discover speakers periodically and poll them on an interval.

    Discovery failures don't kill the loop: the HTTP endpoint stays up and
    keeps serving the last good values, and discovery is retried on the next
    cycle. Individual speaker failures are isolated inside the collector.
    """
    collector = SpeakerCollector(metrics, config)
    speakers: list = []
    next_discovery = 0.0  # monotonic deadline; 0 forces discovery on the first cycle

    while not stop.is_set():
        cycle_start = time.monotonic()

        if cycle_start >= next_discovery or not speakers:
            try:
                speakers = get_speakers(config)
                metrics.speakers_discovered.set(len(speakers))
                metrics.last_discovery_timestamp.set(time.time())
                if speakers:
                    logger.info(
                        "Polling %d speaker(s) every %.0fs: %s",
                        len(speakers),
                        config.poll_interval,
                        ", ".join(s.ip_address for s in speakers),
                    )
                else:
                    logger.warning(
                        "No Sonos speakers found; retrying discovery in %.0fs "
                        "(set SONOS_HOSTS if multicast discovery can't work here)",
                        config.poll_interval,
                    )
            except Exception:
                metrics.discovery_errors.inc()
                logger.exception("Speaker discovery failed")
            next_discovery = cycle_start + config.discovery_interval

        for speaker in speakers:
            if stop.is_set():
                return
            collector.collect(speaker)

        elapsed = time.monotonic() - cycle_start
        stop.wait(max(0.0, config.poll_interval - elapsed))


def run(
    config: Config,
    registry: CollectorRegistry,
    stop: threading.Event | None = None,
    on_server_started=None,
) -> None:
    metrics = SonosMetrics(registry)
    server, server_thread = start_http_server(
        config.port, addr=config.address, registry=registry
    )
    if on_server_started is not None:
        on_server_started(server)
    logger.info(
        "Serving metrics on http://%s:%d/metrics", config.address, server.server_port
    )
    try:
        poll_forever(config, metrics, stop or threading.Event())
    finally:
        server.shutdown()
        server_thread.join()


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = Config.from_env(os.environ)
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    try:
        run(config, REGISTRY)
    except KeyboardInterrupt:
        logger.info("Shutting down")


if __name__ == "__main__":
    main()
