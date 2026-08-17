FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FEEDVERDICT_HOME=/data

WORKDIR /app

RUN useradd --create-home --uid 10001 feedverdict \
    && mkdir --parents /data \
    && chown feedverdict:feedverdict /data

COPY pyproject.toml README.md LICENSE ./
COPY src ./src

RUN python -m pip install --no-cache-dir --disable-pip-version-check .

USER feedverdict
VOLUME ["/data"]

ENTRYPOINT ["feedverdict"]
CMD ["--help"]
