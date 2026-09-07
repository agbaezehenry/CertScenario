# Northstar backend. Default: mock lab provider (works anywhere).
# For the real provider, run the container with LAB_PROVIDER=containerlab,
# the docker socket mounted, host networking and privileges (see docker-compose.yml
# profile "real"). containerlab itself is installed here so the image can drive labs.
FROM python:3.12-slim

ARG CONTAINERLAB_VERSION=0.62.2
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates iproute2 \
    && curl -fsSL https://download.docker.com/linux/static/stable/$(uname -m)/docker-27.3.1.tgz | tar -xz -C /tmp \
    && mv /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker \
    && curl -fsSL "https://github.com/srl-labs/containerlab/releases/download/v${CONTAINERLAB_VERSION}/containerlab_${CONTAINERLAB_VERSION}_linux_$(dpkg --print-architecture).tar.gz" \
       | tar -xz -C /usr/local/bin containerlab \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY pyproject.toml README.md ./
COPY backend ./backend
COPY scenarios ./scenarios
RUN pip install --no-cache-dir -e . && mkdir -p /srv/.northstar

ENV NORTHSTAR_STATE_DIR=/srv/.northstar \
    NORTHSTAR_SCENARIOS_DIR=/srv/scenarios \
    LAB_PROVIDER=mock \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
