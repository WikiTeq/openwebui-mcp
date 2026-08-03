# openwebui-mcp container image.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY . /app

RUN pip install .

RUN useradd --create-home --uid 1000 owui
USER 1000

ENTRYPOINT ["openwebui-mcp"]
CMD ["--help"]
