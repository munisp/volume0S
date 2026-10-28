FROM python:3.12-slim AS test
WORKDIR /source
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY . ./
RUN pip install --no-cache-dir pytest pytest-asyncio h3 cryptography && python -m pytest && touch /tests-passed

FROM apache/spark:3.5.3-scala2.12-java17-python3-ubuntu
USER root
WORKDIR /opt/volumeos
COPY . ./
RUN pip install --no-cache-dir h3==4.3.1
COPY --from=test /tests-passed /opt/volumeos/.tests-passed
USER 185
