FROM python:3.12-slim AS test
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY main.py models.py store.py whatsapp.py reports.py calculations.py ./
COPY tests ./tests
RUN pip install --no-cache-dir '.[test]' && python -m pytest && touch /tests-passed

FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY main.py models.py store.py whatsapp.py reports.py calculations.py ./
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home volumeos
COPY --from=test /tests-passed /app/.tests-passed
USER 10001:10001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
