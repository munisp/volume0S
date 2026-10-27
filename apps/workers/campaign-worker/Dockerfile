FROM python:3.12-slim AS test
WORKDIR /app
COPY pyproject.toml ./
COPY main.py worker.py messaging.py onboarding.py metrics.py ./
COPY tests ./tests
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "prometheus-client>=0.21,<1" "pytest>=8,<9" "pytest-asyncio>=0.25,<1" \
    && python -m pytest && touch /tests-passed

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "prometheus-client>=0.21,<1"
COPY main.py worker.py messaging.py onboarding.py metrics.py ./
COPY --from=test /tests-passed /app/.tests-passed
RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER 10001
CMD ["python", "worker.py"]
