FROM python:3.12-slim AS test
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY main.py store.py model.py providers.py worker.py ./
COPY tests ./tests
RUN pip install --no-cache-dir '.[test]' && python -m pytest && touch /tests-passed

FROM python:3.12-slim
# Bold font for thumbnail captions.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY main.py store.py model.py providers.py worker.py ./
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home volumeos
COPY --from=test /tests-passed /app/.tests-passed
USER 10001:10001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
