FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO \
    DATA_DIR=/app/data \
    APP_HOST=0.0.0.0 \
    APP_PORT=8000 \
    TZ=America/Sao_Paulo

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY main.py .
COPY app/ app/
COPY metrics/ metrics/
COPY templates/ templates/
COPY static/ static/
COPY estoque/ estoque/
COPY finance/ finance/
COPY images/ images/
COPY integrations/ integrations/
COPY deploy/ deploy/
COPY tools/ tools/

RUN mkdir -p data data/images

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD python -c "import socket; socket.create_connection(('localhost',8000), timeout=2).close()" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
