import html
import json
import os
from datetime import datetime, timezone
from typing import Any

import difflib
import docker
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

APP_TITLE = "NPMplus Nginx Config Console"
MAX_VERSIONS = 5

NPMPLUS_CONTAINER = os.getenv("NPMPLUS_CONTAINER", "npmplus")
BASIC_USER = os.getenv("BASIC_AUTH_USER", "").strip()
BASIC_PASS = os.getenv("BASIC_AUTH_PASS", "").strip()
MAX_CHARS = int(os.getenv("MAX_CHARS", "5000000"))

app = FastAPI(title=APP_TITLE)

STATE: dict[str, Any] = {"snapshots": [], "next_id": 1, "last_error": None}


def _check_basic_auth(request: Request) -> None:
    if not (BASIC_USER and BASIC_PASS):
        return
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        raise HTTPException(status_code=401, detail="Auth required", headers={"WWW-Authenticate": "Basic"})

    import base64

    try:
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        user, pwd = raw.split(":", 1)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid auth", headers={"WWW-Authenticate": "Basic"}) from exc
    if user != BASIC_USER or pwd != BASIC_PASS:
        raise HTTPException(status_code=401, detail="Invalid auth", headers={"WWW-Authenticate": "Basic"})


def fetch_nginx_t() -> tuple[str, int]:
    client = docker.from_env()
    try:
        container = client.containers.get(NPMPLUS_CONTAINER)
    except Exception as exc:
        raise RuntimeError(f"Container '{NPMPLUS_CONTAINER}' nicht gefunden: {exc}") from exc

    result = container.exec_run(["nginx", "-T"], stdout=True, stderr=True)
    text = result.output.decode("utf-8", errors="replace")
    code = int(getattr(result, "exit_code", 0) or 0)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[TRUNCATED: output exceeded MAX_CHARS]\n"
    return text, code


def _current_snapshot() -> dict[str, Any] | None:
    snaps = STATE["snapshots"]
    return snaps[-1] if snaps else None


def _push_snapshot(text: str, exit_code: int) -> None:
    snapshot = {
        "id": STATE["next_id"],
        "ts": datetime.now(timezone.utc),
        "text": text,
        "exit_code": exit_code,
    }
    STATE["next_id"] += 1
    STATE["snapshots"].append(snapshot)
    if len(STATE["snapshots"]) > MAX_VERSIONS:
        STATE["snapshots"] = STATE["snapshots"][-MAX_VERSIONS:]


def _find_snapshot(snapshot_id: int) -> dict[str, Any] | None:
    for snap in STATE["snapshots"]:
        if snap["id"] == snapshot_id:
            return snap
    return None


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    _check_basic_auth(request)

    current = _current_snapshot()
    current_text = current["text"] if current else ""
    current_ts_human = current["ts"].strftime("%Y-%m-%d %H:%M:%S UTC") if current else "—"
    current_exit = str(current["exit_code"]) if current else "—"
    err = STATE["last_error"] or ""

    versions = [
        {
            "id": s["id"],
            "ts_human": s["ts"].strftime("%Y-%m-%d %H:%M:%S UTC"),
            "exit_code": s["exit_code"],
        }
        for s in reversed(STATE["snapshots"])
    ]

    page = """
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>__APP_TITLE__</title>
  <style>
    :root { --bg:#0b1020; --panel:#121a2d; --panel2:#17233a; --line:#2a3957; --text:#e8eefc; --muted:#9db1d7; --blue:#4f8cff; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:Inter,system-ui,sans-serif; background:radial-gradient(circle at top,#1a2642 0%,#0b1020 50%); color:var(--text); }
    header { position:sticky; top:0; z-index:5; background:rgba(11,16,32,.85); border-bottom:1px solid var(--line); backdrop-filter: blur(7px); }
    .top { padding:12px 16px; display:flex; justify-content:space-between; gap:12px; flex-wrap:wrap; align-items:center; }
    .title { font-weight:800; }
    .sub { color:var(--muted); font-size:12px; display:flex; gap:8px; flex-wrap:wrap; }
    .chip { border:1px solid var(--line); border-radius:999px; padding:2px 10px; }
    .btn, button { border:0; border-radius:10px; color:white; background:var(--blue); padding:9px 12px; font-weight:700; cursor:pointer; text-decoration:none; }
    .btn.secondary, button.secondary { background:#32466f; }
    .layout { display:grid; grid-template-columns:300px 1fr; gap:12px; padding:12px; }
    @media (max-width:1000px) { .layout { grid-template-columns:1fr; } }
    .card { border:1px solid var(--line); border-radius:14px; background:linear-gradient(180deg,var(--panel2),var(--panel)); overflow:hidden; }
    .hd { border-bottom:1px solid var(--line); padding:10px 12px; font-weight:700; display:flex; justify-content:space-between; }
    .bd { padding:12px; }
    .stack { display:flex; flex-direction:column; gap:10px; }
    input, select { width:100%; border:1px solid var(--line); border-radius:10px; background:#0d1530; color:var(--text); padding:8px 10px; }
    .index { max-height:300px; overflow:auto; display:flex; flex-direction:column; gap:6px; }
    .idx-item { padding:7px 9px; border-radius:9px; border:1px solid transparent; background:#0e1731; color:var(--text); text-decoration:none; font-size:13px; }
    .idx-item:hover { border-color:#5a7fcb; }
    pre { margin:0; max-height:62vh; overflow:auto; background:#090f22; border-radius:10px; padding:12px; font-size:12px; line-height:1.48; border:1px solid var(--line); }
    .mainpre { min-height:240px; }
    .diff-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px; }
    @media (max-width:1000px) { .diff-grid { grid-template-columns:1fr; } }
    .line-del { background:rgba(255,107,107,.14); display:block; }
    .line-add { background:rgba(44,182,125,.14); display:block; }
    .muted { color:var(--muted); font-size:12px; }
    .warn { color:#ffb4b4; margin-bottom:8px; }
    .toolbar { display:flex; gap:8px; flex-wrap:wrap; }
  </style>
</head>
<body>
  <header>
    <div class="top">
      <div>
        <div class="title">__APP_TITLE__</div>
        <div class="sub">
          <span class="chip">Target: __TARGET__</span>
          <span class="chip">Last fetch: __LAST_FETCH__</span>
          <span class="chip">Exit: __EXIT__</span>
        </div>
      </div>
      <div class="toolbar">
        <form method="post" action="/fetch" style="margin:0;"><button type="submit">Fetch nginx -T</button></form>
        <a class="btn secondary" href="/download">Download</a>
        <a class="btn secondary" href="/raw" target="_blank">Raw</a>
      </div>
    </div>
  </header>

  <div class="layout">
    <div class="stack">
      <div class="card">
        <div class="hd">Suche</div>
        <div class="bd stack"><input id="q" placeholder="Suche in der aktuellen Version" /><div class="muted" id="hits">0 Treffer</div></div>
      </div>
      <div class="card">
        <div class="hd">Versionen (Server, max __MAX_VERSIONS__)</div>
        <div class="bd stack">
          <select id="leftVersion"></select>
          <select id="rightVersion"></select>
          <button id="btnCompare" class="secondary">Vergleichen</button>
          <div class="muted">Links/Rechts auswählen für Side-by-Side-Diff.</div>
        </div>
      </div>
      <div class="card">
        <div class="hd">Index <span id="idxMeta" class="muted"></span></div>
        <div class="bd index" id="index"></div>
      </div>
    </div>
    <div class="stack">
      <div class="card">
        <div class="hd">Aktuelle Konfiguration</div>
        <div class="bd">__ERROR__<pre class="mainpre" id="mainCode"></pre></div>
      </div>
      <div class="card">
        <div class="hd">Diff (2 Fenster nebeneinander)</div>
        <div class="bd diff-grid">
          <div><div class="muted" id="leftMeta">Links</div><pre id="leftDiff"></pre></div>
          <div><div class="muted" id="rightMeta">Rechts</div><pre id="rightDiff"></pre></div>
        </div>
      </div>
    </div>
  </div>

<script>
const CONFIG_TEXT = __CONFIG_TEXT__;
const VERSIONS = __VERSIONS__;

const qEl = document.getElementById('q');
const hitsEl = document.getElementById('hits');
const mainCode = document.getElementById('mainCode');
const idxEl = document.getElementById('index');
const idxMeta = document.getElementById('idxMeta');
const leftSel = document.getElementById('leftVersion');
const rightSel = document.getElementById('rightVersion');
const btnCompare = document.getElementById('btnCompare');
const leftMeta = document.getElementById('leftMeta');
const rightMeta = document.getElementById('rightMeta');
const leftDiff = document.getElementById('leftDiff');
const rightDiff = document.getElementById('rightDiff');

function esc(s) { return (s || '').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;'); }
function renderMain(text) { mainCode.textContent = text || 'Noch keine Config geladen. Bitte Fetch ausführen.'; }

function buildIndex(text) {
  const lines = (text || '').split('\\n');
  const items = [];
  let pos = 0;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith('# configuration file ')) items.push({label: line.replace('# configuration file ',''), pos: pos});
    if (/^\\s*server\\s*\\{/.test(line)) items.push({label: `server block (Zeile ${i+1})`, pos: pos});
    pos += line.length + 1;
  }
  idxEl.innerHTML = '';
  idxMeta.textContent = `${items.length} Einträge`;
  if (!items.length) { idxEl.innerHTML = '<div class="muted">Kein Index verfügbar.</div>'; return; }
  for (const it of items) {
    const a = document.createElement('a');
    a.href = '#';
    a.className = 'idx-item';
    a.textContent = it.label;
    a.onclick = (ev) => {
      ev.preventDefault();
      const pre = mainCode.parentElement;
      const ratio = Math.max(0, Math.min(1, it.pos / Math.max(1, text.length)));
      pre.scrollTop = ratio * (pre.scrollHeight - pre.clientHeight);
    };
    idxEl.appendChild(a);
  }
}

function runSearch() {
  const q = qEl.value.trim();
  if (!q) { renderMain(CONFIG_TEXT); hitsEl.textContent = '0 Treffer'; return; }
  const lines = (CONFIG_TEXT || '').split('\\n');
  let hits = 0;
  const marked = lines.map((line) => {
    const i = line.toLowerCase().indexOf(q.toLowerCase());
    if (i < 0) return esc(line);
    hits += 1;
    return esc(line.slice(0, i)) + '<mark>' + esc(line.slice(i, i + q.length)) + '</mark>' + esc(line.slice(i + q.length));
  }).join('\\n');
  mainCode.innerHTML = marked;
  hitsEl.textContent = `${hits} Treffer`;
}

function setupVersions() {
  leftSel.innerHTML = ''; rightSel.innerHTML = '';
  if (!VERSIONS.length) {
    const opt = '<option value="">Keine Versionen</option>';
    leftSel.innerHTML = opt; rightSel.innerHTML = opt;
    return;
  }
  for (const v of VERSIONS) {
    const label = `#${v.id} • ${v.ts_human} • exit=${v.exit_code}`;
    leftSel.insertAdjacentHTML('beforeend', `<option value="${v.id}">${esc(label)}</option>`);
    rightSel.insertAdjacentHTML('beforeend', `<option value="${v.id}">${esc(label)}</option>`);
  }
  leftSel.selectedIndex = Math.min(1, leftSel.options.length - 1);
  rightSel.selectedIndex = 0;
}

function renderSideBySideDiff(leftText, rightText) {
  const left = (leftText || '').split('\\n');
  const right = (rightText || '').split('\\n');
  const max = Math.max(left.length, right.length);
  const lOut = []; const rOut = [];
  for (let i = 0; i < max; i++) {
    const l = left[i] ?? ''; const r = right[i] ?? '';
    const changed = l !== r;
    lOut.push(changed ? `<span class="line-del">${esc(l)}</span>` : esc(l));
    rOut.push(changed ? `<span class="line-add">${esc(r)}</span>` : esc(r));
  }
  leftDiff.innerHTML = lOut.join('\\n');
  rightDiff.innerHTML = rOut.join('\\n');
}

async function compareVersions() {
  const leftId = leftSel.value; const rightId = rightSel.value;
  if (!leftId || !rightId) return;
  const res = await fetch(`/diff-json?left=${encodeURIComponent(leftId)}&right=${encodeURIComponent(rightId)}`, { cache: 'no-store' });
  if (!res.ok) { alert(await res.text()); return; }
  const data = await res.json();
  leftMeta.textContent = `Links: #${data.left.id} • ${data.left.ts_human}`;
  rightMeta.textContent = `Rechts: #${data.right.id} • ${data.right.ts_human}`;
  renderSideBySideDiff(data.left.text, data.right.text);
}

qEl.addEventListener('input', runSearch);
btnCompare.addEventListener('click', compareVersions);
renderMain(CONFIG_TEXT);
buildIndex(CONFIG_TEXT);
setupVersions();
if (VERSIONS.length >= 2) compareVersions();
</script>
</body>
</html>
"""

    page = (
        page.replace("__APP_TITLE__", html.escape(APP_TITLE))
        .replace("__TARGET__", html.escape(NPMPLUS_CONTAINER))
        .replace("__LAST_FETCH__", html.escape(current_ts_human))
        .replace("__EXIT__", html.escape(current_exit))
        .replace("__MAX_VERSIONS__", str(MAX_VERSIONS))
        .replace("__ERROR__", f"<div class='warn'>Fehler: {html.escape(err)}</div>" if err else "")
        .replace("__CONFIG_TEXT__", json.dumps(current_text))
        .replace("__VERSIONS__", json.dumps(versions))
    )

    return HTMLResponse(page)


@app.post("/fetch")
def fetch(request: Request) -> RedirectResponse:
    _check_basic_auth(request)
    try:
        text, code = fetch_nginx_t()
        _push_snapshot(text, code)
        STATE["last_error"] = None
    except Exception as exc:
        STATE["last_error"] = str(exc)
    return RedirectResponse("/", status_code=303)


@app.get("/snapshots", response_class=JSONResponse)
def list_snapshots(request: Request) -> JSONResponse:
    _check_basic_auth(request)
    payload = [
        {"id": s["id"], "ts_human": s["ts"].strftime("%Y-%m-%d %H:%M:%S UTC"), "exit_code": s["exit_code"]}
        for s in reversed(STATE["snapshots"])
    ]
    return JSONResponse(payload)


@app.get("/diff-json", response_class=JSONResponse)
def diff_json(request: Request, left: int, right: int) -> JSONResponse:
    _check_basic_auth(request)
    l = _find_snapshot(left)
    r = _find_snapshot(right)
    if not l or not r:
        return JSONResponse({"error": "Snapshot nicht gefunden."}, status_code=404)
    return JSONResponse(
        {
            "left": {"id": l["id"], "ts_human": l["ts"].strftime("%Y-%m-%d %H:%M:%S UTC"), "text": l["text"]},
            "right": {"id": r["id"], "ts_human": r["ts"].strftime("%Y-%m-%d %H:%M:%S UTC"), "text": r["text"]},
        }
    )


@app.get("/diff", response_class=PlainTextResponse)
def diff(request: Request, left: int | None = None, right: int | None = None) -> PlainTextResponse:
    _check_basic_auth(request)
    snapshots = STATE["snapshots"]
    if len(snapshots) < 2:
        return PlainTextResponse("Nicht genug Versionen für Diff.\n", status_code=404)

    if left is None or right is None:
        l, r = snapshots[-2], snapshots[-1]
    else:
        l = _find_snapshot(left)
        r = _find_snapshot(right)
        if not l or not r:
            return PlainTextResponse("Snapshot nicht gefunden.\n", status_code=404)

    udiff = difflib.unified_diff(l["text"].splitlines(), r["text"].splitlines(), fromfile=f"snapshot-{l['id']}", tofile=f"snapshot-{r['id']}", lineterm="")
    return PlainTextResponse("\n".join(udiff) + "\n")


@app.get("/raw", response_class=PlainTextResponse)
def raw(request: Request) -> PlainTextResponse:
    _check_basic_auth(request)
    snap = _current_snapshot()
    if not snap:
        return PlainTextResponse("No config cached yet. POST /fetch first.\n", status_code=404)
    return PlainTextResponse(snap["text"])


@app.get("/download")
def download(request: Request) -> Response:
    _check_basic_auth(request)
    snap = _current_snapshot()
    if not snap:
        return PlainTextResponse("No config cached yet. POST /fetch first.\n", status_code=404)

    fname = snap["ts"].strftime("npmplus-nginxT-%Y%m%d-%H%M%S.txt")
    return Response(content=snap["text"], media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{fname}"'})
