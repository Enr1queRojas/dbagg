# The image holds code only: .env, config/ and data/ are mounted at runtime.
FROM python:3.11-slim-bookworm AS build
WORKDIR /build
COPY pyproject.toml requirements.txt ./
COPY src ./src
# requirements.txt locks the versions; its "-e ." line is replaced by the built wheel.
RUN python -m venv /opt/venv \
    && grep -v '^-e ' requirements.txt > locked.txt \
    && /opt/venv/bin/pip install --no-cache-dir -r locked.txt \
    && pip install --no-cache-dir build==1.6.1 \
    && python -m build --wheel \
    && /opt/venv/bin/pip install --no-cache-dir --no-deps dist/*.whl \
    && /opt/venv/bin/pip check

FROM python:3.11-slim-bookworm
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && echo "deb [signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
        > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    DBAGG_HOME=/srv/dbagg \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Fail the build, not the first query, if pyodbc cannot see the driver.
RUN python -c "import pyodbc; assert 'ODBC Driver 18 for SQL Server' in pyodbc.drivers()" \
    && groupadd --system --gid 10001 dbagg \
    && useradd --system --uid 10001 --gid 10001 --home-dir /srv/dbagg --no-create-home dbagg \
    && install -d -o dbagg -g dbagg /srv/dbagg /srv/dbagg/data

WORKDIR /srv/dbagg
USER dbagg
VOLUME /srv/dbagg/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=3)"]
# One worker: the dedupe, history and notice limits live in this process.
# No access log: Meta's verification request carries its token in the query string.
CMD ["python", "-m", "uvicorn", "dbagg.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
