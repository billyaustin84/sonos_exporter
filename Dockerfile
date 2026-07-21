FROM python:3.14-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY sonos_exporter ./sonos_exporter

RUN pip install --no-cache-dir .

RUN useradd --create-home exporter
USER exporter

ENV EXPORTER_PORT=9805
EXPOSE 9805

# SSDP multicast discovery does not work on Docker's default bridge network.
# Either run with --network host, or set SONOS_HOSTS to your speakers' IPs.
CMD ["sonos-exporter"]
