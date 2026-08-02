"""CLI entry point: run the server, or render one photo to a PNG for tuning."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def _client_and_cfg():
    from . import config as config_mod
    from .immich import ImmichClient

    cfg = config_mod.load()
    return cfg, ImmichClient(
        cfg.immich.url, cfg.immich.api_key,
        timeout=cfg.immich.timeout_s, verify_tls=cfg.immich.verify_tls,
    )


def doctor() -> int:
    """Walk the chain from config to photos, stopping at the first break."""
    import httpx

    cfg, client = _client_and_cfg()
    print(f"config      : {os.environ.get('IMMPAKT_CONFIG', 'config.yaml')}")
    print(f"immich url  : {cfg.immich.url}")
    print(f"api key     : {'set (%d chars)' % len(cfg.immich.api_key) if cfg.immich.api_key else 'MISSING'}\n")

    if not cfg.immich.api_key:
        print(f"[{BAD}] no API key. Set IMMICH_API_KEY or immich.api_key in config.yaml.")
        print("        Immich -> Account Settings -> API Keys -> New API Key")
        return 1

    try:
        client._client.get("/server/ping").raise_for_status()
        print(f"[{OK}] reachable")
    except httpx.ConnectError as e:
        print(f"[{BAD}] cannot reach {cfg.immich.url}\n        {e}\n")
        host = cfg.immich.url.split("//")[-1].split(":")[0].split("/")[0]
        print("        Most likely one of:")
        print(f"        - '{host}' does not resolve from inside this container.")
        print("          Join Immich's docker network (see docker-compose.yaml), or")
        print("          point IMMICH_URL at a LAN IP, e.g. http://192.168.1.10:2283")
        print("        - Immich is on a different port (default 2283)")
        return 1
    except httpx.HTTPError as e:
        print(f"[{BAD}] {type(e).__name__}: {e}")
        return 1

    try:
        me = client.whoami()
        print(f"[{OK}] authenticated as {me.get('email') or me.get('name')}")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        print(f"[{BAD}] auth failed (HTTP {code}) -- the API key is wrong or revoked")
        return 1

    src = cfg.source
    filters = [
        f"albums={len(src.albums)}" if src.albums else "",
        f"people={len(src.people)}" if src.people else "",
        "favorites_only" if src.favorites_only else "",
        "memories" if src.memories else "",
        f"min_aspect={src.min_aspect}" if src.min_aspect is not None else "",
        f"after={src.taken_after}" if src.taken_after else "",
        f"before={src.taken_before}" if src.taken_before else "",
    ]
    active = ", ".join(f for f in filters if f) or "none (whole library)"
    print(f"[{OK}] source filters: {active}")

    for album_id in src.albums:
        try:
            n = len(client.album_assets(album_id))
            tag = OK if n else WARN
            print(f"[{tag}] album {album_id}: {n} photos")
        except httpx.HTTPStatusError as e:
            print(f"[{BAD}] album {album_id}: HTTP {e.response.status_code} -- wrong UUID?")
            print("        run 'immpakt albums' to list the real ones")

    from .selector import AssetPool

    pool = AssetPool(client, src)
    assets = pool.ensure_fresh(force=True)
    status = pool.status
    if status["error"]:
        print(f"[{BAD}] pool refresh failed: {status['error']}")
        return 1
    if not assets:
        print(f"[{BAD}] 0 photos matched. Loosen the source filters above.")
        return 1

    portraits = sum(1 for a in assets if 0 < a.aspect < 1)
    fate = {
        "cover": "cropped to fill",
        "contain": "matted",
        "auto": f"matted past {cfg.frame.fit.crop_tolerance_tall}x mismatch",
    }.get(cfg.frame.fit.mode, cfg.frame.fit.mode)
    print(f"[{OK}] {len(assets)} photos in pool ({portraits} portrait -> {fate})")
    print(f"\nAll good. Devices poll /api/frame.bin; open / for the dashboard.")
    return 0


def list_albums() -> int:
    cfg, client = _client_and_cfg()
    try:
        albums = client.albums()
    except Exception as e:  # noqa: BLE001
        print(f"could not list albums: {type(e).__name__}: {e}")
        print("run 'immpakt doctor' for a fuller diagnosis")
        return 1
    if not albums:
        print("no albums found")
        return 0
    print("Paste the ids you want into config.yaml under source.albums:\n")
    for a in sorted(albums, key=lambda x: x.get("albumName", "")):
        print(f"  {a['id']}  {a.get('assetCount', '?'):>5} photos  {a.get('albumName','')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="immpakt")
    sub = p.add_subparsers(dest="cmd")

    serve = sub.add_parser("serve", help="run the HTTP server (default)")
    serve.add_argument("--host"), serve.add_argument("--port", type=int)

    r = sub.add_parser("render", help="render a local image file to preview + .bin")
    r.add_argument("image", type=Path)
    r.add_argument("-o", "--out", type=Path, default=Path("preview.png"))
    r.add_argument("--bin", type=Path, help="also write the raw 30000-byte frame")
    r.add_argument("--scale", type=int, default=2)

    sub.add_parser("doctor", help="check the Immich connection and photo pool")
    sub.add_parser("albums", help="list album UUIDs for config.yaml")

    args = p.parse_args(argv)

    if args.cmd == "doctor":
        return doctor()
    if args.cmd == "albums":
        return list_albums()

    if args.cmd == "render":
        from . import config as config_mod
        from . import render as render_mod

        cfg = config_mod.load()
        opts = cfg.frame.render_options()
        frame, indices = render_mod.render(args.image.read_bytes(), opts)
        args.out.write_bytes(render_mod.preview_png(indices, opts, scale=args.scale))
        print(f"wrote {args.out} ({len(frame)} byte frame)")
        if args.bin:
            args.bin.write_bytes(frame)
            print(f"wrote {args.bin}")
        return 0

    import uvicorn

    from . import config as config_mod

    cfg = config_mod.load()
    uvicorn.run(
        "immpakt.app:app",
        host=getattr(args, "host", None) or cfg.server.bind,
        port=getattr(args, "port", None) or cfg.server.port,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
