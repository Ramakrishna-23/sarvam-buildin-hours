FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml ./
COPY basha_bridge ./basha_bridge

RUN pip install --upgrade pip && pip install .

# Default command runs the token web service. For the relay worker, override
# with: sh -c 'basha-livekit-agent --room "${LIVEKIT_ROOM:-basha-demo}"'
CMD ["basha-token-server", "--host", "0.0.0.0"]
