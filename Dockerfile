FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY screener/ screener/

# Cloud Run Jobs just run this as a one-shot container and exit.
ENTRYPOINT ["python", "main.py"]
