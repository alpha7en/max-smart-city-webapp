FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data \
    TZ=Europe/Moscow

WORKDIR /app
RUN useradd --system --uid 10001 --no-create-home app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app/ app/
RUN mkdir -p /app/data && chown app:app /app/data

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

# Том ./data с хоста может принадлежать root: отдаём его пользователю app и сразу
# сбрасываем права (setpriv) — само приложение всегда работает не от root.
ENTRYPOINT ["/bin/sh", "-c", "if [ \"$(id -u)\" = 0 ]; then chown -R app:app /app/data && exec setpriv --reuid=app --regid=app --init-groups \"$@\"; fi; exec \"$@\"", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
