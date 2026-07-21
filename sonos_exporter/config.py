"""Environment-based configuration for the exporter."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_PORT = 9805
DEFAULT_POLL_INTERVAL = 30.0
# Polling is all local-network SOAP/HTTP calls to the speakers themselves, so
# it can be frequent — but each cycle makes several requests per speaker, and
# hammering the speakers' tiny embedded web servers helps nobody.
MIN_POLL_INTERVAL = 5.0
DEFAULT_DISCOVERY_INTERVAL = 300.0
DEFAULT_DISCOVERY_TIMEOUT = 5.0


class ConfigError(Exception):
    """Raised when the exporter is misconfigured."""


def _read_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw == "":
        return default
    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be a boolean (got {raw!r})")


def _read_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        return float(env.get(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc


@dataclass(frozen=True)
class Config:
    port: int = DEFAULT_PORT
    address: str = "0.0.0.0"
    poll_interval: float = DEFAULT_POLL_INTERVAL
    discovery_interval: float = DEFAULT_DISCOVERY_INTERVAL
    discovery_timeout: float = DEFAULT_DISCOVERY_TIMEOUT
    hosts: tuple[str, ...] = field(default_factory=tuple)
    export_track_info: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        try:
            port = int(env.get("EXPORTER_PORT", DEFAULT_PORT))
        except ValueError as exc:
            raise ConfigError("EXPORTER_PORT must be an integer") from exc

        poll_interval = _read_float(env, "POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL)
        if poll_interval < MIN_POLL_INTERVAL:
            logger.warning(
                "POLL_INTERVAL_SECONDS=%s is below the minimum of %s; clamping",
                poll_interval,
                MIN_POLL_INTERVAL,
            )
            poll_interval = MIN_POLL_INTERVAL

        hosts = tuple(
            host.strip()
            for host in env.get("SONOS_HOSTS", "").split(",")
            if host.strip()
        )

        return cls(
            port=port,
            address=env.get("EXPORTER_ADDRESS", "0.0.0.0"),
            poll_interval=poll_interval,
            discovery_interval=_read_float(
                env, "DISCOVERY_INTERVAL_SECONDS", DEFAULT_DISCOVERY_INTERVAL
            ),
            discovery_timeout=_read_float(
                env, "DISCOVERY_TIMEOUT_SECONDS", DEFAULT_DISCOVERY_TIMEOUT
            ),
            hosts=hosts,
            export_track_info=_read_bool(env, "EXPORT_TRACK_INFO", True),
        )
