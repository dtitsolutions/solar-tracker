#dan
# Solar Monitor collector + API (Python) — polls the inverter, writes MongoDB,
# publishes live state to Redis, and exposes the config/auth API used by the
# Node web tier.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY lib ./lib
COPY config_store.py data_store.py logsetup.py workers.py solar_monitor.py dashboard.html cli.py ./
COPY scripts ./scripts

# Commit currently built in, so the app can tell if GitHub has something newer.
ARG GIT_SHA=""
ENV SM_DEPLOYED_SHA=${GIT_SHA}

# Ensure the self-update trigger directory exists even without the bind mount.
RUN mkdir -p /app/.update

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/login',timeout=3).status==200 else 1)"

CMD ["python", "solar_monitor.py", "--port", "8080"]
