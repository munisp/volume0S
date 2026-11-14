FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir "fastapi==0.141.1" "starlette==1.7.0" "uvicorn>=0.34" "python-multipart>=0.0.20"
WORKDIR /srv
COPY server.py .
RUN mkdir -p /work
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8895"]
