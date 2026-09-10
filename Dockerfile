FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY simple_sync.py .

# credentials.json and config/custom_sync.json are mounted from Secret Manager at runtime
ENTRYPOINT ["python3", "simple_sync.py"]
CMD []
