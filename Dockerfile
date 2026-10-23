FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TURNOVER_DB=/tmp/turnover.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY turnover ./turnover
COPY demo ./demo

RUN useradd --system --uid 10001 turnover
USER turnover

EXPOSE 8080
CMD ["uvicorn", "turnover.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
