FROM python:3.12-slim AS test
WORKDIR /app
COPY studio_main.py studio.py metrics.py ./
COPY workflows ./workflows
COPY tests ./tests
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "boto3>=1.35,<2" "Pillow>=11,<13" "prometheus-client>=0.21,<1" "pytest>=8,<9" "pytest-asyncio>=0.25,<1" && python -m pytest

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "boto3>=1.35,<2" "Pillow>=11,<13" "prometheus-client>=0.21,<1" \
    && useradd --uid 10001 --create-home app
COPY studio_main.py studio.py metrics.py ./
COPY workflows ./workflows
USER 10001
CMD ["python", "studio.py"]
