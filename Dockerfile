FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vpm_agents ./vpm_agents
COPY inbox_agent ./inbox_agent
COPY noon_agent ./noon_agent
COPY weather_agent ./weather_agent
COPY routeopt_agent ./routeopt_agent
COPY storm_agent ./storm_agent
COPY report_sender ./report_sender
COPY prevoyage_db ./prevoyage_db
COPY port_weather ./port_weather
COPY scripts ./scripts
COPY templates ./templates

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VPM_SERVICE=ingest

# Same image, different VPM_SERVICE per container.
CMD ["python3", "scripts/run_service.py"]
