"""Server-rendered dashboard.

No JS charting library and no external requests -- the battery history is an
inline SVG built here. One series per device, so no legend: the tile title names
it (dataviz rule -- a legend box exists only for >= 2 series).
"""

from __future__ import annotations

import html
import time

# --- palette -------------------------------------------------------------
# Roles from the validated reference palette. Dark steps are *selected* for the
# dark surface, not an automatic flip.
CSS = """
:root {
  color-scheme: light;
  --surface-0:#f4f3f0; --surface-1:#fcfcfb; --border:#e2e0da;
  --text-1:#0b0b0b; --text-2:#52514e; --text-3:#7a786f;
  --series-1:#2a78d6; --series-1-fill:#cde2fb;
  --good:#0ca30c; --warning:#fab219; --critical:#d03b3b;
  --panel-k:#000; --panel-w:#fff; --panel-y:#e4be20; --panel-r:#a62a2a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --surface-0:#121211; --surface-1:#1a1a19; --border:#383835;
    --text-1:#fff; --text-2:#c3c2b7; --text-3:#8f8e85;
    --series-1:#3987e5; --series-1-fill:#184f95;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --surface-0:#121211; --surface-1:#1a1a19; --border:#383835;
  --text-1:#fff; --text-2:#c3c2b7; --text-3:#8f8e85;
  --series-1:#3987e5; --series-1-fill:#184f95;
}

*{box-sizing:border-box}
body{margin:0;background:var(--surface-0);color:var(--text-1);
  font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:2rem 1.25rem 4rem}
a{color:var(--series-1)}

header{display:flex;align-items:center;gap:.75rem;margin-bottom:.35rem}
.mark{display:flex;border-radius:4px;overflow:hidden;box-shadow:0 0 0 1px var(--border)}
.mark i{width:11px;height:22px;display:block}
h1{font-size:1.15rem;font-weight:650;margin:0;letter-spacing:-.01em}
.sub{color:var(--text-2);font-size:.875rem;margin:0 0 1.75rem}

.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:.75rem;margin-bottom:1.5rem}
.tile{background:var(--surface-1);border:1px solid var(--border);
  border-radius:10px;padding:.85rem 1rem}
.tile .k{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--text-3);font-weight:600}
.tile .v{font-size:1.6rem;font-weight:650;letter-spacing:-.02em;
  margin-top:.15rem;font-variant-numeric:tabular-nums}
.tile .u{font-size:.8rem;color:var(--text-2);font-weight:500;margin-left:.15rem}

h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.06em;
  color:var(--text-3);font-weight:600;margin:0 0 .7rem}

/* auto-fill, not auto-fit: with a single frame registered, auto-fit would
   stretch its card the full page width and leave the detail list marooned
   beside a 200px thumbnail. */
.cards{display:grid;gap:1rem;grid-template-columns:repeat(auto-fill,minmax(460px,1fr))}
.card{background:var(--surface-1);border:1px solid var(--border);
  border-radius:12px;overflow:hidden;display:flex}
/* flex-start, not center: with the settings panel open the card grows tall
   and a centred thumbnail drifts to the middle, away from the name it labels. */
.shot{width:200px;flex:0 0 200px;background:var(--surface-0);
  border-right:1px solid var(--border);display:flex;align-items:flex-start}
.shot img{width:100%;display:block}
.body{padding:.9rem 1rem;flex:1;min-width:0}
.name{font-weight:650;font-size:.95rem;display:flex;align-items:center;
  gap:.5rem;margin-bottom:.1rem}
.name code{font:inherit}
.meta{color:var(--text-3);font-size:.78rem;margin-bottom:.7rem}

.rows{display:grid;grid-template-columns:auto 1fr;gap:.3rem .7rem;
  font-size:.83rem;align-items:center}
.rows dt{color:var(--text-3)}
.rows dd{margin:0;font-variant-numeric:tabular-nums}

.batt{display:flex;align-items:center;gap:.5rem}
.meter{width:52px;height:7px;border-radius:99px;background:var(--border);
  overflow:hidden;flex:0 0 auto}
.meter i{display:block;height:100%;border-radius:99px}
/* Status reads as dot + word. The colour is a mark, never the text itself:
   status yellow on the light surface is 1.79:1 and would be unreadable. */
.pill{font-size:.7rem;font-weight:600;color:var(--text-2);
  display:inline-flex;align-items:center;gap:.3rem;line-height:1.4;
  text-transform:uppercase;letter-spacing:.04em}
.pill i{width:8px;height:8px;border-radius:99px;flex:0 0 auto;
  box-shadow:0 0 0 1px rgb(0 0 0 / .12)}

.spark{margin-top:.8rem}
.spark figcaption{font-size:.72rem;color:var(--text-3);margin-bottom:.2rem}
.settings{margin-top:.9rem;border-top:1px solid var(--border);padding-top:.6rem}
.settings summary{cursor:pointer;font-size:.78rem;font-weight:600;color:var(--text-2);
  text-transform:uppercase;letter-spacing:.05em;list-style:none}
.settings summary::-webkit-details-marker{display:none}
.settings summary::before{content:"▸ ";color:var(--text-3)}
.settings[open] summary::before{content:"▾ "}
.settings label{display:flex;align-items:center;gap:.5rem;font-size:.83rem;
  margin-top:.55rem;color:var(--text-2)}
.settings label.chk{gap:.4rem}
.settings select,.settings input[type=number]{margin-left:auto;font:inherit;
  font-size:.83rem;padding:.2rem .35rem;border-radius:6px;
  border:1px solid var(--border);background:var(--surface-0);color:var(--text-1)}
.settings input[type=number]{width:4.5rem}
.ovl{padding-left:.85rem;border-left:2px solid var(--border);margin-top:.2rem}
.next{display:flex;gap:.7rem;align-items:center;margin:.6rem 0 .2rem}
.next img{width:96px;border-radius:5px;border:1px solid var(--border);display:block}
.next strong{font-size:.83rem}
.next p{margin:.1rem 0 .4rem;font-size:.76rem;color:var(--text-3)}
.settings button{font:inherit;font-size:.8rem;font-weight:600;cursor:pointer;
  padding:.3rem .7rem;border-radius:6px;border:1px solid var(--border);
  background:var(--surface-0);color:var(--text-1)}
.settings button:hover{border-color:var(--series-1);color:var(--series-1)}
.act{display:flex;align-items:center;gap:.6rem;margin-top:.8rem}
.status{font-size:.78rem;color:var(--text-3)}
.hint{font-size:.74rem;color:var(--text-3);margin:.5rem 0 0;line-height:1.45}
.empty{background:var(--surface-1);border:1px dashed var(--border);
  border-radius:12px;padding:2rem;text-align:center;color:var(--text-2)}
.empty code{background:var(--surface-0);padding:.1rem .35rem;border-radius:4px}
footer{margin-top:2.5rem;color:var(--text-3);font-size:.78rem;
  display:flex;gap:1rem;flex-wrap:wrap}
.err{background:#d03b3b12;border:1px solid var(--critical);color:var(--critical);
  border-radius:8px;padding:.6rem .8rem;font-size:.85rem;margin-bottom:1.25rem}
@media(max-width:560px){.cards{grid-template-columns:1fr}
  .card{flex-direction:column}.shot{width:100%;flex:none;border-right:0;
  border-bottom:1px solid var(--border)}}
"""


def _ago(ts: int | None) -> str:
    if not ts:
        return "never"
    d = max(0, int(time.time()) - ts)
    if d < 60:
        return f"{d}s ago"
    if d < 3600:
        return f"{d // 60}m ago"
    if d < 86400:
        return f"{d // 3600}h ago"
    return f"{d // 86400}d ago"


def _duration(s: int) -> str:
    if s % 86400 == 0 and s >= 86400:
        return f"{s // 86400}d"
    if s % 3600 == 0 and s >= 3600:
        return f"{s // 3600}h"
    if s >= 60:
        return f"{s // 60}m"
    return f"{s}s"


def _battery_status(pct: int | None) -> tuple[str, str]:
    """(css var, label). Status colour never travels alone -- the label ships
    with it, and the numeric percent is always shown beside the meter."""
    if pct is None:
        return "var(--text-3)", "unknown"
    if pct < 15:
        return "var(--critical)", "critical"
    if pct < 40:
        return "var(--warning)", "low"
    return "var(--good)", "good"


def _sparkline(rows, w: int = 240, h: int = 34) -> str:
    """Battery % over time, one series. Needs >= 2 points to mean anything."""
    pts = [(r["ts"], r["battery_pct"]) for r in rows if r["battery_pct"] is not None]
    if len(pts) < 2:
        return (
            '<p style="font-size:.78rem;color:var(--text-3);margin:.2rem 0 0">'
            "Battery history appears after a few wakes.</p>"
        )

    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x0, x1 = min(xs), max(xs)
    span = (x1 - x0) or 1
    # Fixed 0-100 domain: a percentage auto-scaled to its own range turns a
    # 2% drift into a cliff.
    def px(t):
        return round((t - x0) / span * (w - 2) + 1, 1)

    def py(v):
        return round(h - 3 - (v / 100) * (h - 6), 1)

    line = " ".join(f"{px(t)},{py(v)}" for t, v in pts)
    last_x, last_y = px(xs[-1]), py(ys[-1])
    lo, hi = min(ys), max(ys)
    # No area fill: on the fixed 0-100 domain a healthy 93-95% battery would
    # fill almost the whole box, reading as a solid block rather than a trend.
    # The line alone, high in the frame, says "full" at a glance.
    return f"""<figure class="spark" style="margin:0">
<figcaption>Battery &middot; last {len(pts)} wakes &middot; {lo}&ndash;{hi}%</figcaption>
<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" preserveAspectRatio="none"
     aria-label="Battery percent over the last {len(pts)} wakes, {lo} to {hi} percent">
  <title>Battery {lo}&ndash;{hi}% over {len(pts)} wakes (scale 0-100%)</title>
  <line x1="0" y1="{py(100)}" x2="{w}" y2="{py(100)}"
        stroke="var(--border)" stroke-width="1" stroke-dasharray="2 3"/>
  <line x1="0" y1="{py(0)}" x2="{w}" y2="{py(0)}"
        stroke="var(--border)" stroke-width="1"/>
  <polyline points="{line}" fill="none" stroke="var(--series-1)"
            stroke-width="2" stroke-linejoin="round" stroke-linecap="round"
            vector-effect="non-scaling-stroke"/>
  <circle cx="{last_x}" cy="{last_y}" r="3.5" fill="var(--series-1)"
          stroke="var(--surface-1)" stroke-width="2"/>
</svg></figure>"""


_INTERVALS = [
    (300, "5m"), (900, "15m"), (3600, "1h"), (10800, "3h"),
    (21600, "6h"), (43200, "12h"), (86400, "1d"), (259200, "3d"),
]
_POSITIONS = ["bottom-left", "bottom-right", "top-left", "top-right"]


def _due(d, interval_s: int) -> str:
    """When the device will next check in -- i.e. when an edit lands."""
    if not d.last_seen:
        return "on next wake"
    left = d.last_seen + interval_s - int(time.time())
    if left <= 0:
        return "due now"
    if left < 3600:
        return f"in ~{max(1, left // 60)}m"
    return f"in ~{left // 3600}h"


def _opts(items, current) -> str:
    return "".join(
        f'<option value="{v}"{" selected" if v == current else ""}>{lbl}</option>'
        for v, lbl in items
    )


def _device_card(d, history, cfg) -> str:
    pct = d.battery_pct
    color, label = _battery_status(pct)
    meter = f'<i style="width:{max(2, min(100, pct or 0))}%;background:{color}"></i>'
    rssi = f"{d.rssi} dBm" if d.rssi is not None else "&mdash;"
    mv = f"{d.battery_mv} mV" if d.battery_mv else "&mdash;"
    did = html.escape(d.id)
    ov = cfg.overlay
    ck = lambda b: " checked" if b else ""  # noqa: E731

    return f"""<article class="card" data-device="{did}">
  <div class="shot"><img src="/preview.png?id={did}&amp;scale=1"
       alt="Current frame on {did}" loading="lazy"></div>
  <div class="body">
    <div class="name"><code>{did}</code>
      <span class="pill"><i style="background:{color}"></i>{label}</span></div>
    <p class="meta">Last wake {_ago(d.last_seen)} &middot; {d.wakes} total</p>
    <dl class="rows">
      <dt>Battery</dt><dd><div class="batt"><div class="meter">{meter}</div>
        <span>{pct if pct is not None else "&mdash;"}%</span>
        <span style="color:var(--text-3)">{mv}</span></div></dd>
      <dt>Signal</dt><dd>{rssi}</dd>
    </dl>
    {_sparkline(history)}

    <details class="settings">
      <summary>Settings</summary>
      <div class="next">
        <img src="/preview.png?id={did}&amp;scale=1&amp;peek=1" alt="Next photo" loading="lazy">
        <div><strong>Up next</strong>
          <p>Shown at the next wake, {_due(d, cfg.interval_s)}.</p>
          <button type="button" data-act="skip">Skip this one</button></div>
      </div>
      <label>Cadence
        <select name="interval_s">{_opts(_INTERVALS, cfg.interval_s)}</select></label>
      <label class="chk"><input type="checkbox" name="overlay.enabled"{ck(ov.enabled)}>
        Caption on the photo</label>
      <div class="ovl">
        <label class="chk"><input type="checkbox" name="overlay.show_date"{ck(ov.show_date)}>
          Date</label>
        <label class="chk"><input type="checkbox" name="overlay.show_location"{ck(ov.show_location)}>
          Location</label>
        <label>Position <select name="overlay.position">
          {_opts([(p, p.replace("-", " ")) for p in _POSITIONS], ov.position)}</select></label>
        <label>Text size <input type="number" name="overlay.font_size"
          value="{ov.font_size}" min="9" max="40"></label>
        <label>Edge margin <input type="number" name="overlay.margin"
          value="{ov.margin}" min="0" max="60"></label>
      </div>
      <div class="act"><button type="button" data-act="save">Save</button>
        <span class="status" role="status"></span></div>
      <p class="hint">The frame's radio is off until it wakes, so there is no way to
        push. Saved changes land {_due(d, cfg.interval_s)} &mdash; or tap the
        frame's button to apply now.</p>
    </details>
  </div>
</article>"""


JS = """
const post = (url, body) => fetch(url, {method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify(body||{})});

document.addEventListener('click', async (e) => {
  const btn = e.target.closest('button[data-act]');
  if (!btn) return;
  const card = btn.closest('[data-device]');
  const id = encodeURIComponent(card.dataset.device);
  const status = card.querySelector('.status');
  btn.disabled = true;
  try {
    if (btn.dataset.act === 'skip') {
      await post(`/api/devices/${id}/next`);
      // Re-fetch the up-next thumbnail; cache-bust or the browser reuses it.
      const img = card.querySelector('.next img');
      img.src = img.src.split('&t=')[0] + '&t=' + Date.now();
    } else {
      const body = {};
      card.querySelectorAll('.settings [name]').forEach(el => {
        body[el.name] = el.type === 'checkbox' ? el.checked : el.value;
      });
      const r = await post(`/api/devices/${id}/settings`, body);
      const j = await r.json();
      if (status) status.textContent = r.ok
        ? 'Saved — applies on next wake'
        : ('Error: ' + (j.detail || r.status));
      // A cadence change moves the countdown; refresh so it is not stale.
      if (r.ok) setTimeout(() => location.reload(), 1200);
    }
  } catch (err) {
    if (status) status.textContent = 'Error: ' + err;
  } finally { btn.disabled = false; }
});

// Auto-refresh, but never while someone has a settings panel open.
setInterval(() => {
  if (!document.querySelector('.settings[open]')) location.reload();
}, 30000);
"""


def render(*, devices, histories, settings, pool, immich_url, immich_ok, interval_s) -> str:
    tiles = [
        ("Photos", f"{pool['count']:,}", "in pool"),
        ("Frames", str(len(devices)), "registered"),
        ("Cadence", _duration(interval_s), "per photo"),
        ("Immich", "up" if immich_ok else "down", ""),
    ]
    tiles_html = "".join(
        f'<div class="tile"><div class="k">{k}</div>'
        f'<div class="v">{v}<span class="u">{u}</span></div></div>'
        for k, v, u in tiles
    )

    if devices:
        cards = "".join(
            _device_card(d, histories.get(d.id, []), settings[d.id]) for d in devices
        )
        body = f'<h2>Frames</h2><div class="cards">{cards}</div>'
    else:
        body = (
            '<div class="empty"><strong>No frames yet.</strong><br>'
            "A PicPak registers itself the first time it fetches "
            "<code>/api/frame.bin</code>.</div>"
        )

    err = (
        f'<div class="err"><strong>Photo pool error.</strong> {html.escape(str(pool["error"]))}</div>'
        if pool.get("error")
        else ""
    )

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ImmPakt</title><style>{CSS}</style></head><body>
<div class="wrap">
  <header>
    <span class="mark" aria-hidden="true">
      <i style="background:var(--panel-k)"></i><i style="background:var(--panel-w)"></i>
      <i style="background:var(--panel-y)"></i><i style="background:var(--panel-r)"></i>
    </span>
    <h1>ImmPakt</h1>
  </header>
  <p class="sub">Serving Immich photos to e-ink frames &middot;
     <a href="{html.escape(immich_url)}">{html.escape(immich_url)}</a></p>
  {err}
  <div class="tiles">{tiles_html}</div>
  {body}
  <footer>
    <a href="/preview.png?next=true">Preview next photo</a>
    <a href="/api/status">Status JSON</a>
    <span>400&times;300 &middot; 4-colour &middot; 30000-byte frames</span>
  </footer>
</div>
<script>{JS}</script>
</body></html>"""
