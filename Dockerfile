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
# Drop root: nothing here needs it, and a container escape from an
# image-parsing bug should not land as uid 0 on the host mounts.
RUN useradd --system --uid 10001 --create-home immpakt \
 && mkdir -p /data /config && chown -R immpakt:immpakt /data /config
USER immpakt
VOLUME ["/data", "/config"]
EXPOSE 8080

# Reads IMMPAKT_PORT so an overridden internal port still gets health-checked.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/status' % os.environ.get('IMMPAKT_PORT','8080')).read()"

CMD ["immpakt", "serve"]
