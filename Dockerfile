FROM python:3.12-slim

# fonts-dejavu-core is only needed for the optional caption overlay, but it is
# ~1 MB and saves a confusing fallback to PIL's bitmap font.
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV IMMPAKT_DATA_DIR=/data \
    IMMPAKT_CONFIG=/config/config.yaml \
    IMMPAKT_PORT=8080
# No USER here on purpose: the entrypoint needs root just long enough to chown
# the bind-mounted volumes to PUID:PGID, then drops with setpriv. Pinning USER
# would remove that ability and force every host to chown ./data by hand.
# Setting `user:` in compose still works and skips the chown path entirely.
RUN useradd --system --uid 10001 --user-group --create-home immpakt \
 && mkdir -p /data /config && chown -R immpakt:immpakt /data /config
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh
VOLUME ["/data", "/config"]
EXPOSE 8080

# Reads IMMPAKT_PORT so an overridden internal port still gets health-checked.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('IMMPAKT_PORT','8080')).read()"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["immpakt", "serve"]
