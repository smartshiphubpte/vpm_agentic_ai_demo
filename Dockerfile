FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY vpm_agents ./vpm_agents
COPY report_sender ./report_sender
COPY prevoyage_db ./prevoyage_db
COPY scripts ./scripts
COPY templates ./templates

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    VPM_SERVICE=ingest

# Same image, different VPM_SERVICE per container.
CMD ["python3", "scripts/run_service.py"]
