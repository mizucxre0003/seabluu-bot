FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

ENV PORT=8080
CMD ["sh", "-c", "uvicorn app.webhook:app --host 0.0.0.0 --port ${PORT:-8080}"]
