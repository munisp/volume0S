FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY contracts.py dispatcher.py rollup_job.py ./
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home volumeos
USER 10001:10001
CMD ["python", "dispatcher.py"]
