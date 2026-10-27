FROM python:3.12-slim AS test
WORKDIR /app
COPY main.py ./
COPY tests ./tests
RUN pip install --no-cache-dir "httpx>=0.28,<1" "prometheus-client>=0.21,<1" "pytest>=8,<9" "pytest-asyncio>=0.25,<1" \
    && python -m pytest && touch /tests-passed

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "httpx>=0.28,<1" "prometheus-client>=0.21,<1" \
    && useradd --uid 10001 --create-home app
COPY main.py ./
COPY --from=test /tests-passed /app/.tests-passed
USER 10001
CMD ["python", "main.py"]
