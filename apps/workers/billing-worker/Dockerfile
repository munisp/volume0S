FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY billing_main.py documents.py models.py store.py ./
COPY migrations ./migrations
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home volumeos
USER 10001:10001
CMD ["python", "billing_main.py"]
