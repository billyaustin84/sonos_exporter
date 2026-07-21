# sonos-exporter

[![CI](https://github.com/billyaustin84/sonos_exporter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/billyaustin84/sonos_exporter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A [Prometheus](https://prometheus.io/) exporter for Sonos speakers, built on
[SoCo](https://github.com/SoCo/SoCo). It discovers the speakers on your
network and polls them directly over the LAN — no Sonos account, no cloud
API, no credentials — exposing playback state, the current track, volume and
EQ settings, group topology, and battery level (for portable speakers) as
Prometheus metrics. An example Grafana dashboard is included in
[`grafana/sonos-dashboard.json`](grafana/sonos-dashboard.json).

Everything happens on your local network: the exporter speaks the same
UPnP/SOAP protocol the Sonos app uses, read-only. It never sends playback
commands and nothing leaves your LAN.

## Requirements

- Python 3.12+
- One or more Sonos speakers reachable on the network
- Prometheus (and optionally Grafana) to do anything useful with the output

## Installation

```bash
pip install .
# or for development
pip install -e .[dev]
```

## Running

```bash
sonos-exporter
```

That's it — speakers are found automatically via SSDP multicast discovery,
and metrics are served at `http://localhost:9805/metrics`.

If discovery finds nothing (different VLAN/subnet, Docker's default network,
WSL — anywhere multicast can't reach the speakers), list the speakers
explicitly instead:

```bash
export SONOS_HOSTS=192.168.1.50,192.168.1.51
sonos-exporter
```

### Configuration (environment variables)

| Variable | Default | Description |
| --- | --- | --- |
| `SONOS_HOSTS` | *(unset)* | Comma-separated speaker IPs. When set, exactly these are polled and discovery is skipped |
| `EXPORTER_PORT` | `9805` | Port to serve `/metrics` on |
| `EXPORTER_ADDRESS` | `0.0.0.0` | Bind address |
| `POLL_INTERVAL_SECONDS` | `30` | How often to poll each speaker (min 5) |
| `DISCOVERY_INTERVAL_SECONDS` | `300` | How often to re-run discovery (picks up new/renamed speakers) |
| `DISCOVERY_TIMEOUT_SECONDS` | `5` | How long each discovery waits for speakers to answer |
| `EXPORT_TRACK_INFO` | `true` | Export the current track as a labelled series (see cardinality note below) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

### A note on `sonos_track_info` cardinality

The currently playing track is exported as
`sonos_track_info{title=...,artist=...,album=...} 1`, which is handy for a
"now playing" dashboard panel but creates a new time series for every track
that plays. The exporter drops each speaker's previous track series as soon
as the track changes, so the *active* series count stays at one per speaker,
but Prometheus still remembers every played track for its retention window.
If that churn bothers you, set `EXPORT_TRACK_INFO=false` — track position and
duration remain available either way.

## Docker

```bash
docker build -t sonos-exporter .
# host networking so SSDP multicast discovery works:
docker run --network host sonos-exporter
# or on the default bridge network, with explicit speaker IPs:
docker run -p 9805:9805 -e SONOS_HOSTS=192.168.1.50,192.168.1.51 sonos-exporter
```

## systemd

A hardened unit file and an example environment file are in
[`contrib/`](contrib/); installation instructions are in the comments at the
top of each.

## Prometheus

```yaml
scrape_configs:
  - job_name: sonos
    static_configs:
      - targets: ["localhost:9805"]
```

The exporter polls the speakers on its own schedule and serves the most
recent values, so the scrape interval and poll interval are independent.

## Grafana

Import [`grafana/sonos-dashboard.json`](grafana/sonos-dashboard.json)
(Dashboards → New → Import) and point it at your Prometheus data source. It
includes a per-speaker filter and panels for playback state, now playing,
track progress, volume, mute, battery, groups, EQ settings, and exporter
health.

## Metrics

All per-speaker metrics carry `uid` (the stable Sonos RINCON id) and
`zone_name` (the room name) labels.

### Playback

| Metric | Description |
| --- | --- |
| `sonos_playback_state` | One-hot playback state (`state` = `PLAYING`, `PAUSED_PLAYBACK`, `STOPPED`, `TRANSITIONING`) |
| `sonos_music_source` | One-hot audio source (`source` = `tv`, `radio`, `music_service`, `spotify`, `line_in`, `airplay`, `group`, ...) |
| `sonos_music_service` | Streaming service currently playing (`service` = `Apple Music`, `YouTube Music`, ...; resolved from the track URI's `sid=` against the speaker's service list) |
| `sonos_track_info` | Currently playing track (`title`, `artist`, `album` labels; value always 1) |
| `sonos_track_position_seconds` | Position in the current track |
| `sonos_track_duration_seconds` | Duration of the current track |
| `sonos_shuffle_enabled` | 1 if shuffle is on |
| `sonos_repeat_state` | One-hot repeat mode (`mode` = `off`, `all`, `one`) |

### Audio settings

| Metric | Description |
| --- | --- |
| `sonos_volume` | Volume (0–100) |
| `sonos_mute` | 1 if muted |
| `sonos_bass_level` / `sonos_treble_level` | EQ levels (−10 to 10) |
| `sonos_loudness_enabled` | 1 if loudness compensation is on |
| `sonos_night_mode_enabled` | 1 if night mode is on (home-theatre products only) |
| `sonos_dialog_mode_enabled` | 1 if speech enhancement is on (home-theatre products only) |

### Grouping

| Metric | Description |
| --- | --- |
| `sonos_group_coordinator` | 1 if this speaker coordinates its group |
| `sonos_group_size` | Number of speakers in this speaker's group |

### Battery (portable speakers: Move, Roam)

| Metric | Description |
| --- | --- |
| `sonos_battery_percent` | Battery charge level |
| `sonos_battery_charging` | 1 if on external power (charging ring / USB) |

### Speaker and exporter health

| Metric | Description |
| --- | --- |
| `sonos_speaker_info` | Static metadata (`model_name`, `model_number`, `software_version`, `hardware_version`, `mac_address`; value always 1) |
| `sonos_speaker_reachable` | 1 if the speaker answered the most recent poll |
| `sonos_speakers_discovered` | Speakers found by the most recent discovery |
| `sonos_last_discovery_timestamp_seconds` | When discovery last succeeded |
| `sonos_discovery_errors_total` | Discovery failures |
| `sonos_last_poll_successful` | 1 if the last poll cycle for this speaker was error-free |
| `sonos_last_successful_poll_timestamp_seconds` | When this speaker last polled cleanly |
| `sonos_poll_duration_seconds` | Duration of the last poll cycle for this speaker |
| `sonos_poll_errors_total` | Poll errors, by `endpoint` (`speaker_info`, `audio`, `eq_modes`, `transport`, `track`, `group`, `battery`) |

## Development

```bash
pip install -e .[dev]
pytest        # unit + integration tests (no speakers needed)
ruff check sonos_exporter tests
```

The test suite fakes the SoCo layer, so it runs anywhere — including CI —
without Sonos hardware.

## License

[MIT](LICENSE)
