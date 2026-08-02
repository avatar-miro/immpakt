#!/bin/sh
# The app always ends up running unprivileged. There are two ways in, and the
# entrypoint picks based on how the container was started.
#
# 1. Started as root (the default). Root is used only to chown the bind-mounted
#    volumes to PUID:PGID, then dropped with setpriv and never regained. This is
#    the linuxserver.io pattern, and it is why so many public images "just work"
#    with no host preparation: they have root available to fix ownership.
#
#      PUID=1000 PGID=1000 docker compose up -d
#
# 2. Started with an explicit user (`user:` in compose, `--user` on the CLI).
#    Root was never available, so nothing can be chowned; we verify the volumes
#    are usable and say exactly what to fix if they are not.
#
#      user: "1000:1000"
#
# Either way the process that serves traffic is not root.
set -e

PUID="${PUID:-10001}"
PGID="${PGID:-10001}"

# Attempt a real write rather than testing permission bits: `[ -w ]` reports
# what the kernel thinks, and some volume drivers (Docker Desktop's file
# sharing among them) misreport it. Creating a file is what sqlite is about to
# do anyway.
can_write() {
    ( : > "$1/.immpakt-write-test" ) 2>/dev/null || return 1
    rm -f "$1/.immpakt-write-test"
    return 0
}

if [ "$(id -u)" = "0" ]; then
    for d in /data /config; do
        [ -d "$d" ] || mkdir -p "$d"
        # Only chown when it is actually wrong: a recursive chown over a large
        # data directory is slow, and on some network filesystems it fails.
        if [ "$(stat -c '%u:%g' "$d" 2>/dev/null)" != "$PUID:$PGID" ]; then
            chown -R "$PUID:$PGID" "$d" 2>/dev/null || true
        fi
    done
    exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups "$@"
fi

# Explicit user: verify rather than repair.
uid="$(id -u)"
gid="$(id -g)"

if [ -e /data ] && ! can_write /data; then
    owner="$(stat -c '%u:%g' /data 2>/dev/null || echo '?')"
    cat >&2 <<EOF

ImmPakt cannot write to /data

  running as : uid $uid, gid $gid
  /data owned by: $owner

The container was started with an explicit user, so it cannot fix this itself.
A bind mount carries the HOST's ownership, not the image's.

Either chown the directories to that uid, on the host:

    mkdir -p data config
    sudo chown -R $uid:$gid data config

or drop the \`user:\` line from docker-compose.yaml and set PUID/PGID instead,
which lets the container chown them for you at startup.

EOF
    exit 1
fi

# /config is only ever read, so a read-only mount is fine and expected.
if [ -e /config ] && [ ! -r /config ]; then
    echo "ImmPakt cannot read /config (running as uid $uid, gid $gid)" >&2
    exit 1
fi

exec "$@"
