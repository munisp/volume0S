FROM python:3.12-slim AS test
WORKDIR /app
COPY pyproject.toml main.py auth.py privacy.py redaction.py store.py ./
COPY tests ./tests
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.34,<1" "asyncpg>=0.30,<1" "httpx>=0.28,<1" "PyJWT[crypto]>=2.10,<3" "pytest>=8,<9" "pytest-asyncio>=0.25,<1" \
    && python -m pytest && touch /tests-passed

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn>=0.34,<1" "asyncpg>=0.30,<1" "httpx>=0.28,<1" "PyJWT[crypto]>=2.10,<3" \
    && useradd --uid 10001 --create-home app
COPY main.py auth.py privacy.py redaction.py store.py ./
COPY --from=test /tests-passed /app/.tests-passed
USER 10001
EXPOSE 8080
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
