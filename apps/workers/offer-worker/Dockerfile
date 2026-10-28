FROM python:3.12-slim AS test
WORKDIR /app
COPY pyproject.toml common.py scorecards.py front_desk.py books.py compliance.py funding.py costs.py worker.py ./
COPY tests ./tests
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "boto3>=1.35,<2" "asyncpg>=0.30,<1" "pytest>=8,<9" "pytest-asyncio>=0.25,<1" && python -m pytest && touch /tests-passed
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir "temporalio>=1.18,<2" "httpx>=0.28,<1" "boto3>=1.35,<2" "asyncpg>=0.30,<1" && useradd --uid 10001 --create-home app
COPY common.py scorecards.py front_desk.py books.py compliance.py funding.py costs.py worker.py ./
COPY --from=test /tests-passed /app/.tests-passed
USER 10001
CMD ["python", "worker.py"]
