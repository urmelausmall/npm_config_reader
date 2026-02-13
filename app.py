import os
import html
from datetime import datetime, timezone

import docker
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

APP_TITLE = "NPMplus Nginx Config Console"

NPMPLUS_CONTAINER = os.getenv("NPMPLUS_CONTAINER", "npmplus")
BASIC_USER = os.getenv("BASIC_AUTH_USER", "").strip()
BASIC_PASS = os.getenv("BASIC_AUTH_PASS", "").strip()
MAX_CHARS = int(os.getenv("MAX_CHARS", "5000000"))  # 5MB default

app = FastAPI(title=APP_TITLE)

CACHE = {
    "text": "",
    "ts": None,   # UTC datetime
    "err": None,
    "exit_code": None,
}

PREV = {
    "text": "",
    "ts": None,
}


def _check_basic_auth(request: Request):
    if not (BASIC_USER and BASIC_PASS):
        return  # auth disabled

    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        raise HTTPException(status_code=401, detail="Auth required", headers={"WWW-Authenticate": "Basic"})

    import base64
    try:
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        user, pwd = raw.split(":", 1)
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid auth", headers={"WWW-Authenticate": "Basic"})

    if user != BASIC_USER or pwd != BASIC_PASS:
        raise HTTPException(status_code=401, detail="Invalid auth", headers={"WWW-Authenticate": "Basic"})

def fetch_nginx_T() -> tuple[str, int]:
    client = docker.from_env()
    try:
        c = client.containers.get(NPMPLUS_CONTAINER)
    except Exception as e:
        raise RuntimeError(f"Container '{NPMPLUS_CONTAINER}' nicht gefunden: {e}")

    # nginx -T schreibt häufig auf stderr; wir nehmen beides zusammen
    res = c.exec_run(["nginx", "-T"], stdout=True, stderr=True)
    out = res.output.decode("utf-8", errors="replace")
    code = int(getattr(res, "exit_code", 0) or 0)

    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS] + "\n\n[TRUNCATED: output exceeded MAX_CHARS]\n"
    return out, code

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    _check_basic_auth(request)

    ts_iso = CACHE["ts"].isoformat() if CACHE["ts"] else ""
    ts_human = CACHE["ts"].strftime("%Y-%m-%d %H:%M:%S UTC") if CACHE["ts"] else "—"
    err = CACHE["err"] or ""
    exit_code = CACHE["exit_code"]
    has = bool(CACHE["text"])

    # We embed current config as a JS string (escaped) for client-side indexing/search/diff.
    # Important: HTML-escape for safety, then JSON-ish escaping via repr.
    config_text = CACHE["text"] if has else ""
    # Use Python repr to safely embed as JS string literal; later decode on client side
    config_js_literal = repr(config_text)

    page = f"""<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{html.escape(APP_TITLE)}</title>

  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/styles/github-dark.min.css">
  <script src="https://cdn.jsdelivr.net/npm/highlight.js@11.9.0/lib/highlight.min.js"></script>

  <style>
    :root {{
      --bg: #0b0f14;
      --panel: #0d121a;
      --panel2: #0f1622;
      --border: #1f2a37;
      --text: #e6edf3;
      --muted: rgba(230,237,243,.7);
      --muted2: rgba(230,237,243,.55);
      --blue: #1f6feb;
      --green: #2ea043;
      --orange: #d29922;
      --red: #f85149;
      --chip: #182233;
      --shadow: 0 8px 30px rgba(0,0,0,.35);
      --radius: 16px;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial;
      background: var(--bg);
      color: var(--text);
    }}
    header {{
      padding: 14px 16px;
      border-bottom: 1px solid var(--border);
      background: linear-gradient(180deg, var(--panel), rgba(13,18,26,.85));
      position: sticky; top: 0; z-index: 5;
      backdrop-filter: blur(8px);
    }}
    .topbar {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 12px;
      align-items: center;
    }}
    .title {{
      display:flex; flex-direction: column; gap: 4px;
    }}
    .title .h {{
      font-size: 16px; font-weight: 800; letter-spacing: .2px;
    }}
    .title .sub {{
      font-size: 12.5px; color: var(--muted);
      display:flex; flex-wrap: wrap; gap: 8px; align-items: center;
    }}
    .chip {{
      display:inline-flex;
      align-items:center;
      gap: 8px;
      padding: 4px 10px;
      border: 1px solid var(--border);
      background: var(--chip);
      border-radius: 999px;
      font-size: 12px;
      color: var(--muted);
    }}
    .chip b {{ color: var(--text); font-weight: 700; }}
    .chip.ok {{ border-color: rgba(46,160,67,.35); }}
    .chip.warn {{ border-color: rgba(210,153,34,.35); }}
    .chip.bad {{ border-color: rgba(248,81,73,.35); }}
    .actions {{
      display:flex; flex-wrap: wrap; gap: 10px; justify-content: flex-end;
    }}
    button, a.btn {{
      background: var(--blue);
      color: white;
      border: 0;
      border-radius: 12px;
      padding: 10px 12px;
      cursor: pointer;
      text-decoration: none;
      font-weight: 750;
      box-shadow: var(--shadow);
    }}
    button.secondary, a.btn.secondary {{
      background: #30363d;
      color: var(--text);
      box-shadow: none;
    }}
    button.ghost {{
      background: transparent;
      border: 1px solid var(--border);
      box-shadow: none;
      color: var(--text);
    }}
    button:active {{ transform: translateY(1px); }}

    .layout {{
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 14px;
      padding: 14px;
    }}
    @media (max-width: 980px) {{
      .layout {{ grid-template-columns: 1fr; }}
    }}

    .card {{
      background: linear-gradient(180deg, var(--panel2), rgba(15,22,34,.85));
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      overflow: hidden;
    }}
    .card .hd {{
      padding: 12px 12px;
      border-bottom: 1px solid var(--border);
      display:flex;
      align-items:center;
      justify-content: space-between;
      gap: 10px;
      background: rgba(10,14,20,.35);
    }}
    .card .hd .t {{
      font-weight: 800;
      font-size: 13px;
      color: var(--text);
    }}
    .card .bd {{
      padding: 12px;
    }}

    .err {{
      background: rgba(248,81,73,.09);
      border: 1px solid rgba(248,81,73,.25);
      padding: 10px 12px;
      border-radius: 14px;
      margin-bottom: 10px;
      color: #ffb4b4;
      font-size: 13px;
    }}

    .search {{
      display:flex; flex-direction: column; gap: 10px;
    }}
    .search input {{
      width: 100%;
      padding: 10px 12px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.22);
      color: var(--text);
      outline: none;
      font-size: 14px;
    }}
    .row {{
      display:flex; gap: 8px; flex-wrap: wrap; align-items: center;
    }}
    .k {{
      font-size: 12px;
      color: var(--muted2);
    }}
    .smallbtn {{
      padding: 8px 10px;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: rgba(0,0,0,.2);
      color: var(--text);
      cursor: pointer;
      font-weight: 700;
      font-size: 12.5px;
    }}
    .smallbtn:hover {{ border-color: rgba(31,111,235,.35); }}
    .smallbtn.active {{
      border-color: rgba(31,111,235,.6);
      background: rgba(31,111,235,.12);
    }}
    .split {{
      display:flex; gap: 8px; align-items:center; flex-wrap: wrap;
    }}
    .counter {{
      margin-left: auto;
      font-size: 12px;
      color: var(--muted);
    }}

    .card.indexCard {{
    display: flex; 
    flex-direction: column; 
    }}

    .card.indexCard .bd {{
    flex: 1;
    overflow: auto;
    min-height: 0; /* wichtig für flex scroll */
    padding-right: 6px;
    }}

    .card.searchCard,
    .card.indexCard {{
      max-height: 450px;
      height: 450px;
      display: flex;
      flex-direction: column;
    }}

    .card.searchCard .bd,
    .card.indexCard .bd {{
      flex: 1;
      overflow: auto;
      min-height: 0; /* wichtig */
    }}

    .idx {{
      max-height: none;
      overflow: auto;
    }}
    .idx a {{
      display:block;
      padding: 8px 10px;
      border-radius: 12px;
      color: var(--text);
      text-decoration: none;
      border: 1px solid transparent;
      margin-bottom: 6px;
      background: rgba(0,0,0,.15);
    }}
    .idx a:hover {{
      border-color: rgba(31,111,235,.35);
      background: rgba(31,111,235,.08);
    }}
    .idx .meta {{
      font-size: 12px;
      color: var(--muted);
    }}

    .codeWrap {{
      position: relative;
    }}
    pre {{
      margin: 0;
      border-radius: 0;
      border: 0;
      overflow: auto;
      max-height: calc(100vh - 210px);
      background: #0b1220;
    }}
    pre code {{
      font-size: 12.4px;
      line-height: 1.5;
      white-space: pre;
    }}
    .mark {{
      background: rgba(210,153,34,.25);
      border-radius: 4px;
      outline: 1px solid rgba(210,153,34,.25);
    }}
    .mark.current {{
      background: rgba(46,160,67,.25);
      outline: 1px solid rgba(46,160,67,.35);
    }}

    .footerhint {{
      padding-top: 10px;
      font-size: 12px;
      color: var(--muted2);
    }}
    .pill {{
      display:inline-flex;
      align-items:center;
      gap:6px;
      border:1px solid var(--border);
      background: rgba(0,0,0,.18);
      border-radius:999px;
      padding: 3px 10px;
      color: var(--muted);
    }}
  </style>
</head>
<body>

<header>
  <div class="topbar">
    <div class="title">
      <div class="h">{html.escape(APP_TITLE)}</div>
      <div class="sub">
        <span class="chip"><span>Target</span> <b>{html.escape(NPMPLUS_CONTAINER)}</b></span>
        <span class="chip" id="chip-ts"><span>Last fetch</span> <b>{html.escape(ts_human)}</b></span>
        <span class="chip" id="chip-age"><span>Age</span> <b id="age-val">—</b></span>
        <span class="chip" id="chip-stats"><span>Stats</span> <b id="stats-val">—</b></span>
        <span class="chip {('ok' if exit_code == 0 else 'bad' if exit_code is not None else 'warn')}" id="chip-exit">
          <span>Exit</span> <b id="exit-val">{'' if exit_code is None else str(exit_code)}</b>
        </span>
      </div>
    </div>

    <div class="actions">
      <form method="post" action="/fetch" style="margin:0;">
        <button type="submit">Fetch nginx -T</button>
      </form>
      <a class="btn secondary" href="/download">Download TXT</a>
      <a class="btn secondary" href="/raw" target="_blank">Raw</a>
      <button class="ghost" id="btn-diff" title="Diff gegen letzten Fetch (Browser-local)">Diff</button>
      <button class="ghost" id="btn-reset" title="LocalStorage (Diff/Snapshots) löschen">Reset</button>
    </div>
  </div>
</header>

<div class="layout">
  <div class="card searchCard">
    <div class="hd">
      <div class="t">Suche & Filter</div>
      <div class="pill" title="Shortcuts">⌨️ <span class="meta">Enter / Shift+Enter</span></div>
    </div>
    <div class="bd">
      <div class="search">
        <input id="q" type="text" placeholder="Suche… (z.B. server_name, acme-challenge, crowdsec, proxy_pass)"/>
        <div class="row">
          <button class="smallbtn" data-filter="acme">ACME</button>
          <button class="smallbtn" data-filter="crowdsec">CrowdSec</button>
          <button class="smallbtn" data-filter="geoip">GeoIP</button>
          <button class="smallbtn" data-filter="redirect">Redirect</button>
          <button class="smallbtn" data-filter="proxyhost">Proxy Host</button>
          <button class="smallbtn" data-filter="errors">Errors</button>
          <div class="counter"><span id="hitcount">0/0</span></div>
        </div>
        <div class="row">
          <span class="k">Optionen:</span>
          <label class="k"><input type="checkbox" id="caseSensitive"/> Case-sensitive</label>
          <label class="k"><input type="checkbox" id="wholeWord"/> Whole word</label>
          <label class="k"><input type="checkbox" id="regexMode"/> Regex</label>
        </div>

        <div class="footerhint">
          <div class="k">Tipps:</div>
          <div>• Klick im Index links springt direkt in die Config.</div>
          <div>• “Diff” vergleicht gegen den letzten Snapshot im Browser (localStorage).</div>
        </div>
      </div>
    </div>
  </div>

  <div class="card indexCard">
    <div class="hd">
      <div class="t">Index</div>
      <div class="pill" id="idxinfo">—</div>
    </div>
    <div class="bd idx" id="index">
      <div class="k">Noch kein Index…</div>
    </div>
  </div>

  <div class="card" style="grid-column: 1 / -1;">
    <div class="hd">
      <div class="t">Config Output</div>
      <div class="split">
        <button class="smallbtn" id="btn-prev">◀</button>
        <button class="smallbtn" id="btn-next">▶</button>
        <button class="smallbtn" id="btn-clear">Clear highlights</button>
      </div>
    </div>
    <div class="codeWrap">
      {"<div class='err'><b>Fehler:</b> " + html.escape(err) + "</div>" if err else ""}
      <pre><code id="code" class="language-nginx"></code></pre>
    </div>
  </div>
</div>

<script>
  // ---- Embedded server cache (current snapshot) ----
  const SERVER_TS_ISO = {repr(ts_iso)};
  const CONFIG_TEXT = {config_js_literal};

  // ---- DOM ----
  const codeEl = document.getElementById("code");
  const qEl = document.getElementById("q");
  const hitEl = document.getElementById("hitcount");
  const idxEl = document.getElementById("index");
  const idxInfoEl = document.getElementById("idxinfo");
  const statsEl = document.getElementById("stats-val");
  const ageEl = document.getElementById("age-val");
  const exitValEl = document.getElementById("exit-val");
  const btnPrev = document.getElementById("btn-prev");
  const btnNext = document.getElementById("btn-next");
  const btnClear = document.getElementById("btn-clear");
  const btnDiff = document.getElementById("btn-diff");
  const btnReset = document.getElementById("btn-reset");
  const caseSensitiveEl = document.getElementById("caseSensitive");
  const wholeWordEl = document.getElementById("wholeWord");
  const regexModeEl = document.getElementById("regexMode");

  // ---- State ----
  let rawText = CONFIG_TEXT || "";
  let lastRenderedText = "";
  let matches = [];
  let currentMatch = -1;

  // localStorage keys
  const LS_LAST = "npmcfg_last_text";
  const LS_LAST_TS = "npmcfg_last_ts";

  function bytesHuman(n) {{
    if (!Number.isFinite(n)) return "—";
    const units = ["B","KB","MB","GB"];
    let i=0; let x=n;
    while (x >= 1024 && i < units.length-1) {{ x/=1024; i++; }}
    return x.toFixed(i===0?0:2) + " " + units[i];
  }}

  function linesCount(s) {{
    if (!s) return 0;
    return (s.match(/\\n/g) || []).length + 1;
  }}

  function updateAgeAndStats() {{
    const bytes = rawText.length;
    const lines = linesCount(rawText);
    statsEl.textContent = `${{lines.toLocaleString("de-DE")}} lines • ${{bytesHuman(bytes)}}`;

    if (!SERVER_TS_ISO) {{
      ageEl.textContent = "—";
      return;
    }}
    const ts = new Date(SERVER_TS_ISO);
    if (isNaN(ts.getTime())) {{
      ageEl.textContent = "—";
      return;
    }}
    const sec = Math.max(0, Math.floor((Date.now() - ts.getTime()) / 1000));
    const m = Math.floor(sec/60);
    const h = Math.floor(m/60);
    const d = Math.floor(h/24);
    let s = "";
    if (d>0) s += `${{d}}d `;
    if (h%24>0) s += `${{h%24}}h `;
    if (m%60>0) s += `${{m%60}}m `;
    if (!s) s = `${{sec}}s`;
    ageEl.textContent = s.trim();
  }}

  function escapeHtml(s) {{
    return s
      .replaceAll("&","&amp;")
      .replaceAll("<","&lt;")
      .replaceAll(">","&gt;")
      .replaceAll('"',"&quot;")
      .replaceAll("'","&#039;");
  }}

  function buildRegexFromQuery(q) {{
    if (!q) return null;

    let flags = caseSensitiveEl.checked ? "g" : "gi";

    if (regexModeEl.checked) {{
      try {{
        return new RegExp(q, flags);
      }} catch (e) {{
        return null;
      }}
    }}

    // escape regex
    const escaped = q.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&");
    let pat = escaped;

    if (wholeWordEl.checked) {{
      pat = `\\\\b${{pat}}\\\\b`;
    }}

    return new RegExp(pat, flags);
  }}

  function applyQuickFilter(tag) {{
    // define a few nice presets
    const presets = {{
      acme: "/\\\\.well-known/|acme-challenge|letsencrypt",
      crowdsec: "crowdsec|lua|bouncer|cscli|access_by_lua",
      geoip: "geoip|allowed_country|map \\\\\\$allowed_country|country_code",
      redirect: "return 30[12]|rewrite|server_redirect|301|302",
      proxyhost: "proxy_host|proxy_pass|upstream|include \\/data\\/nginx\\/proxy_host",
      errors: "\\\\[emerg\\\\]|\\\\[warn\\\\]|duplicate location|configuration file .* test failed"
    }};
    const p = presets[tag] || "";
    regexModeEl.checked = true;
    wholeWordEl.checked = false;
    qEl.value = p;
    runSearch();
  }}

  function clearHighlights() {{
    matches = [];
    currentMatch = -1;
    hitEl.textContent = "0/0";
    renderCode(rawText);  // plain
  }}

  function renderCode(text, re=null) {{
    // If no regex -> raw
    if (!re) {{
      codeEl.innerHTML = escapeHtml(text || "Noch keine Config geladen. Klick auf “Fetch nginx -T”.");
      if (window.hljs) {{
        if (typeof hljs.highlightElement === "function") {{
          hljs.highlightElement(codeEl);
        }} else if (typeof hljs.highlightBlock === "function") {{
          hljs.highlightBlock(codeEl);
        }}
      }}
      lastRenderedText = text;
      return;
    }}

    // highlight matches by wrapping in spans
    // caution: we highlight in HTML space, so we work on raw string but insert markers safely
    const src = text || "";
    let out = "";
    let lastIndex = 0;
    matches = [];
    currentMatch = -1;

    // we need to iterate all matches
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(src)) !== null) {{
      const start = m.index;
      const end = start + (m[0] || "").length;
      if (end <= start) {{
        // avoid infinite loops
        re.lastIndex = start + 1;
        continue;
      }}
      matches.push([start, end]);
      out += escapeHtml(src.slice(lastIndex, start));
      out += `<span class="mark" data-midx="${{matches.length-1}}">${{escapeHtml(src.slice(start, end))}}</span>`;
      lastIndex = end;
      // prevent catastrophic backtracking lock on empty matches
      if (matches.length > 20000) break;
    }}
    out += escapeHtml(src.slice(lastIndex));

    codeEl.innerHTML = out || escapeHtml("—");
    // No hljs after we injected spans: still ok to run; it may wrap; so we skip to keep our spans stable.
    // hljs.highlightElement(codeEl);

    // update counter
    if (matches.length > 0) {{
      currentMatch = 0;
      updateCurrentMark();
    }}
    hitEl.textContent = matches.length ? `${{currentMatch+1}}/${{matches.length}}` : "0/0";
    lastRenderedText = text;

    // rebuild index based on raw (not highlighted html)
    buildIndex(src);
  }}

  function scrollToMatch(idx) {{
    const el = codeEl.querySelector(`span.mark[data-midx="${{idx}}"]`);
    if (!el) return;
    el.scrollIntoView({{ block: "center", inline: "nearest" }});
  }}

  function updateCurrentMark() {{
    codeEl.querySelectorAll("span.mark.current").forEach(x => x.classList.remove("current"));
    const el = codeEl.querySelector(`span.mark[data-midx="${{currentMatch}}"]`);
    if (el) {{
      el.classList.add("current");
      scrollToMatch(currentMatch);
    }}
    hitEl.textContent = matches.length ? `${{currentMatch+1}}/${{matches.length}}` : "0/0";
  }}

  function nextMatch(step=1) {{
    if (!matches.length) return;
    currentMatch = (currentMatch + step + matches.length) % matches.length;
    updateCurrentMark();
  }}

  function runSearch() {{
    const q = qEl.value.trim();
    if (!q) {{
      clearHighlights();
      buildIndex(rawText);
      return;
    }}
    const re = buildRegexFromQuery(q);
    if (!re) {{
      // invalid regex
      hitEl.textContent = "0/0";
      renderCode(rawText); // show without highlights
      buildIndex(rawText);
      return;
    }}
    renderCode(rawText, re);
  }}

  function buildIndex(text) {{
    // Best effort parsing of nginx -T output
    // Index items: configuration file lines, http/stream blocks, and server blocks with server_name
    const lines = (text || "").split("\\n");
    const items = [];
    let offset = 0;

    function addItem(label, meta, pos) {{
      items.push({{ label, meta, pos }});
    }}

    // pre-scan offsets for line->char position
    const lineOffsets = [];
    let acc = 0;
    for (let i=0; i<lines.length; i++) {{
      lineOffsets.push(acc);
      acc += lines[i].length + 1;
    }}

    for (let i=0; i<lines.length; i++) {{
      const line = lines[i];

      if (line.startsWith("# configuration file ")) {{
        addItem(line.replace("# configuration file ","").trim(), "file", lineOffsets[i]);
        continue;
      }}
      if (/^http\\s*\\{{/.test(line.trim())) {{
        addItem("http { ... }", "block", lineOffsets[i]);
        continue;
      }}
      if (/^stream\\s*\\{{/.test(line.trim())) {{
        addItem("stream { ... }", "block", lineOffsets[i]);
        continue;
      }}

      // server block start (heuristic)
      if (/^server\\s*\\{{/.test(line.trim())) {{
        // look ahead for server_name
        let name = "";
        for (let j=i+1; j<Math.min(i+40, lines.length); j++) {{
          const m = lines[j].match(/^\\s*server_name\\s+([^;]+);/);
          if (m) {{ name = m[1].trim(); break; }}
        }}
        const label = name ? `server {{ ${{name}} }}` : "server {{ ... }}";
        addItem(label, "server", lineOffsets[i]);
      }}
    }}

    idxEl.innerHTML = "";
    if (!items.length) {{
      idxEl.innerHTML = '<div class="k">Kein Index möglich (noch keine Config?)</div>';
      idxInfoEl.textContent = "0 items";
      return;
    }}
    idxInfoEl.textContent = `${{items.length}} items`;

    for (const it of items) {{
      const a = document.createElement("a");
      a.href = "#";
      a.innerHTML = `<div style="font-weight:800; font-size:12.8px;">${{escapeHtml(it.label)}}</div>
                     <div class="meta">${{escapeHtml(it.meta)}}</div>`;
      a.addEventListener("click", (ev) => {{
        ev.preventDefault();
        scrollToCharPos(it.pos);
      }});
      idxEl.appendChild(a);
    }}
  }}

  function scrollToCharPos(pos) {{
    // Convert char position to an approximate scroll location in the PRE.
    // We'll search in the *rendered HTML* by walking text nodes would be heavy.
    // Instead: we use a trick - find nearest span mark if present; otherwise we do a plain search slice.
    const pre = codeEl.parentElement; // code -> pre
    // If highlighted, we try to find nearest mark by data-midx (pos mapping not stored).
    // We'll fallback to a simple scroll ratio.
    const total = rawText.length || 1;
    const ratio = Math.min(1, Math.max(0, pos / total));
    pre.scrollTop = Math.floor((pre.scrollHeight - pre.clientHeight) * ratio);
  }}

  function snapshotToLocalStorage() {{
    // store the last snapshot (previous)
    if (!rawText) return;
    localStorage.setItem(LS_LAST, rawText);
    localStorage.setItem(LS_LAST_TS, SERVER_TS_ISO || new Date().toISOString());
  }}

  function computeSimpleDiff(oldText, newText) {{
    // lightweight line-based diff for UI: show first ~500 changed lines around changes
    const oldLines = (oldText || "").split("\\n");
    const newLines = (newText || "").split("\\n");

    // Use a naive LCS window is heavy; we'll do a simple "set compare" with context:
    // Find lines that differ by index and render unified-style block
    const max = Math.max(oldLines.length, newLines.length);
    const hunks = [];
    let inHunk = false;
    let start = 0;

    function pushHunk(s,e) {{
      const from = Math.max(0, s-3);
      const to = Math.min(max, e+3);
      hunks.push({{from,to}});
    }}

    for (let i=0; i<max; i++) {{
      const a = oldLines[i] ?? "";
      const b = newLines[i] ?? "";
      const diff = a !== b;
      if (diff && !inHunk) {{
        inHunk = true;
        start = i;
      }}
      if (!diff && inHunk) {{
        inHunk = false;
        pushHunk(start, i);
      }}
    }}
    if (inHunk) pushHunk(start, max);

    // Merge overlapping hunks
    const merged = [];
    for (const h of hunks) {{
      const last = merged[merged.length-1];
      if (!last || h.from > last.to) merged.push({{...h}});
      else last.to = Math.max(last.to, h.to);
    }}

    // Render
    const parts = [];
    let shownLines = 0;
    for (const h of merged) {{
      parts.push(`@@ lines ${{h.from+1}}-${{h.to}} @@`);
      for (let i=h.from; i<h.to; i++) {{
        const a = oldLines[i] ?? "";
        const b = newLines[i] ?? "";
        if (a === b) {{
          parts.push(" " + a);
        }} else {{
          if (a) parts.push("-" + a);
          if (b) parts.push("+" + b);
        }}
        shownLines++;
        if (shownLines > 1200) {{
          parts.push("\\n[Diff truncated]\\n");
          return parts.join("\\n");
        }}
      }}
      parts.push("");
    }}
    if (!parts.length) {{
      return "No changes detected (line-by-line index comparison).";
    }}
    return parts.join("\\n");
  }}

async function showDiff() {{
  try {{
    const r = await fetch("/diff", {{ cache: "no-store" }});
    if (!r.ok) {{
      const t = await r.text();
      alert((t && t.trim()) ? t.trim() : "Kein Diff vorhanden. Bitte mindestens 2× Fetch.");
      return;
    }}
    const diff = await r.text();
    codeEl.innerHTML = escapeHtml(diff || "No changes.");
    hitEl.textContent = "0/0";
    matches = [];
    currentMatch = -1;

    idxEl.innerHTML = `<div class="k">Diff-Ansicht (kein Index).</div>`;
    idxInfoEl.textContent = "diff";
  }} catch (e) {{
    alert("Diff fetch failed: " + e);
  }}
}}


  // ---- Wire buttons ----
  btnPrev.addEventListener("click", () => nextMatch(-1));
  btnNext.addEventListener("click", () => nextMatch(+1));
  btnClear.addEventListener("click", () => clearHighlights());
  btnDiff.addEventListener("click", () => showDiff());
  btnReset.addEventListener("click", () => {{
    localStorage.removeItem(LS_LAST);
    localStorage.removeItem(LS_LAST_TS);
    alert("LocalStorage Snapshots gelöscht.");
  }});

  // quick filters
  document.querySelectorAll("button[data-filter]").forEach(btn => {{
    btn.addEventListener("click", () => {{
      document.querySelectorAll("button[data-filter]").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      applyQuickFilter(btn.dataset.filter);
    }});
  }});

  // Search handlers
  qEl.addEventListener("input", () => runSearch());
  [caseSensitiveEl, wholeWordEl, regexModeEl].forEach(x => x.addEventListener("change", () => runSearch()));

  qEl.addEventListener("keydown", (ev) => {{
    if (ev.key === "Enter") {{
      ev.preventDefault();
      if (matches.length) {{
        nextMatch(ev.shiftKey ? -1 : +1);
      }} else {{
        runSearch();
      }}
    }}
  }});

  // ---- Initial render ----
  function init() {{
    // Always render raw text first
    renderCode(rawText || "Noch keine Config geladen. Klick auf “Fetch nginx -T”.");
    buildIndex(rawText);

    updateAgeAndStats();
    setInterval(updateAgeAndStats, 1000);

    // Store a snapshot baseline *only if* we have content and no baseline yet
    if (rawText && !localStorage.getItem(LS_LAST)) {{
      snapshotToLocalStorage();
    }}
  }}
  init();
</script>

</body>
</html>
"""
    return HTMLResponse(page)

@app.post("/fetch")
def fetch(request: Request):
    _check_basic_auth(request)
    try:
        text, code = fetch_nginx_T()

        # rotate: aktueller Snapshot wird "previous"
        if CACHE["text"]:
            PREV["text"] = CACHE["text"]
            PREV["ts"] = CACHE["ts"]

        CACHE["text"] = text
        CACHE["ts"] = datetime.now(timezone.utc)
        CACHE["err"] = None
        CACHE["exit_code"] = code
    except Exception as e:
        CACHE["err"] = str(e)
        CACHE["exit_code"] = None
    return RedirectResponse("/", status_code=303)

@app.get("/diff", response_class=PlainTextResponse)
def diff(request: Request):
    _check_basic_auth(request)

    if not PREV["text"]:
        return PlainTextResponse("No previous snapshot. Click Fetch at least twice.\n", status_code=404)

    import difflib

    old = PREV["text"].splitlines()
    new = (CACHE["text"] or "").splitlines()

    old_ts = PREV["ts"].isoformat() if PREV["ts"] else "previous"
    new_ts = CACHE["ts"].isoformat() if CACHE["ts"] else "current"

    udiff = difflib.unified_diff(
        old, new,
        fromfile=f"prev ({old_ts})",
        tofile=f"curr ({new_ts})",
        lineterm=""
    )
    return PlainTextResponse("\n".join(udiff) + "\n")

@app.get("/raw", response_class=PlainTextResponse)
def raw(request: Request):
    _check_basic_auth(request)
    if not CACHE["text"]:
        return PlainTextResponse("No config cached yet. POST /fetch first.\n", status_code=404)
    return PlainTextResponse(CACHE["text"])

@app.get("/download")
def download(request: Request):
    _check_basic_auth(request)
    if not CACHE["text"]:
        return PlainTextResponse("No config cached yet. POST /fetch first.\n", status_code=404)

    ts = CACHE["ts"] or datetime.now(timezone.utc)
    fname = ts.strftime("npmplus-nginxT-%Y%m%d-%H%M%S.txt")
    return Response(
        content=CACHE["text"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'}
    )
