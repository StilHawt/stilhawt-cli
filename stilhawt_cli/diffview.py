"""stilhawt_cli.diffview — a unified diff as a stream of HUNKS, and a fold/unfold page for them.

Three bricks, each usable on its own:

  · `parse_unified_diff(text)` — PURE. unified-diff text → one row per hunk (file, change, the
    line counts, the raw hunk body, the language). The generic brick behind the `git hunks` command.
  · `render_diff_body(objects, esc)` — PURE. Those rows → an HTML body of nested <details> (one per
    file, one per hunk): the fold/unfold is NATIVE (no script), so the page keeps the strict CSP of
    `view` (`default-src 'none'`). If a row carries an explanation field (groq / claude / …), it is
    shown as that hunk's comment.
  · `serve_live(diff_fn, explain_fn, …)` — a stdlib HTTP server on localhost that RE-RUNS `diff_fn`
    on a poll and re-renders, re-explaining only the hunks whose content changed. `diff_fn` and
    `explain_fn` are INJECTED: this module never imports git nor a model — it is given how to read a
    diff and how to explain a hunk, so it stays pure, testable, and least-privilege (anti-OpenClaw:
    the server runs nothing of its own; the page's CSP is `connect-src 'self'` — localhost only).

`git hunks` emits the rows; `… | groq "Explique ce changement" --on hunk --pan code | view diff`
adds the comments through the egress-guarded AI pipe; `view diff --live` holds the server open.

  python -m stilhawt_cli.diffview --selftest
"""
from __future__ import annotations

import html as _html
import json
import re
import subprocess
import sys

from stilhawt_cli.tool import Arg, Toolset

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Language by extension — a CLOSED map we control (never a guess on the name): the viewer only uses
# it as a label, so an unknown extension is "" not a wrong guess (réflexe identification fragile).
_LANG = {"py": "Python", "js": "JavaScript", "ts": "TypeScript", "tsx": "TypeScript", "jsx": "JavaScript",
         "md": "Markdown", "yaml": "YAML", "yml": "YAML", "json": "JSON", "html": "HTML", "css": "CSS",
         "sh": "Shell", "sql": "SQL", "go": "Go", "rs": "Rust", "java": "Java", "c": "C", "h": "C",
         "cpp": "C++", "toml": "TOML", "cfg": "Config", "ini": "Config", "txt": "Text", "dsl": "DSL"}

_FIELDS = ["file", "change", "old_start", "new_start", "added", "removed", "header", "hunk", "lang"]
# Where an explanation of a hunk may live — the AI pipes add their own key; `view diff` shows the
# first present. A CLOSED set (not « any text field »): the diff text itself is not an explanation.
_EXPLAIN_KEYS = ("explanation", "comment", "groq", "claude", "codex")


def _lang_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    return _LANG.get(ext, "")


def _change_of(header_lines: list[str]) -> str:
    """new / deleted / renamed / modified — read from the git file header, never guessed."""
    joined = "\n".join(header_lines)
    if "new file mode" in joined:
        return "added"
    if "deleted file mode" in joined:
        return "deleted"
    if "rename from" in joined or "rename to" in joined:
        return "renamed"
    return "modified"


def _path_of(diff_line: str, header_lines: list[str]) -> str:
    """The file a block is about: the `+++ b/…` side (or `--- a/…` for a deletion), else the path
    out of `diff --git a/… b/…`. Quoted paths (spaces, unicode) handled by taking the b/ side."""
    for ln in header_lines:
        if ln.startswith("+++ b/"):
            return ln[6:].strip()
        if ln.startswith("rename to "):
            return ln[len("rename to "):].strip()
    for ln in header_lines:
        if ln.startswith("--- a/"):
            return ln[6:].strip()
    m = re.match(r"diff --git a/(.*) b/(.*)", diff_line)
    if m:
        return m.group(2).strip()
    return "?"


def parse_unified_diff(text: str) -> list[dict]:
    """unified-diff text (what the git program prints for a diff) → a list of hunk rows (the order of
    the diff). PURE.

    One row per hunk header; a file changed without a textual hunk (new empty file, pure mode change,
    binary) still yields ONE row (hunk body empty or `(binary)`), so the file is never invisible.
    """
    if not text or not text.strip():
        return []
    rows: list[dict] = []
    # Split into file blocks on the `diff --git` lines, keeping the line that starts each block.
    blocks: list[list[str]] = []
    cur: list[str] | None = None
    for ln in text.splitlines():
        if ln.startswith("diff --git "):
            if cur is not None:
                blocks.append(cur)
            cur = [ln]
        elif cur is not None:
            cur.append(ln)
    if cur is not None:
        blocks.append(cur)

    for block in blocks:
        diff_line = block[0]
        # header = everything before the first @@ (or the whole block if there is none)
        first_hunk = next((i for i, ln in enumerate(block) if ln.startswith("@@")), len(block))
        header_lines = block[:first_hunk]
        change = _change_of(header_lines)
        path = _path_of(diff_line, header_lines)
        lang = _lang_of(path)
        binary = any(ln.startswith("Binary files ") or ln.startswith("GIT binary patch") for ln in header_lines)

        hunk_starts = [i for i, ln in enumerate(block) if ln.startswith("@@")]
        if not hunk_starts:
            rows.append({"file": path, "change": change, "old_start": None, "new_start": None,
                         "added": 0, "removed": 0, "header": "", "lang": lang,
                         "hunk": "(binary)" if binary else "(no textual change)"})
            continue
        for k, start in enumerate(hunk_starts):
            end = hunk_starts[k + 1] if k + 1 < len(hunk_starts) else len(block)
            head = block[start]
            body = block[start + 1:end]
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", head)
            old_start = int(m.group(1)) if m else None
            new_start = int(m.group(3)) if m else None
            added = sum(1 for ln in body if ln.startswith("+") and not ln.startswith("+++"))
            removed = sum(1 for ln in body if ln.startswith("-") and not ln.startswith("---"))
            rows.append({"file": path, "change": change, "old_start": old_start, "new_start": new_start,
                         "added": added, "removed": removed, "header": head, "lang": lang,
                         "hunk": "\n".join(body)})
    return rows


# ── rendering (PURE, no script) ───────────────────────────────────────────────────────────────
DIFF_CSS = """
.dv-file{border:1px solid var(--line);border-radius:6px;margin:0 0 10px;background:var(--bg)}
.dv-file>summary{padding:8px 12px;font-weight:600;cursor:pointer;list-style:none;display:flex;
gap:10px;align-items:baseline;flex-wrap:wrap;background:var(--head);border-radius:6px}
.dv-file[open]>summary{border-radius:6px 6px 0 0;border-bottom:1px solid var(--line)}
.dv-path{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}
.dv-tag{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.04em}
.dv-add{color:#2e7d32}.dv-del{color:#c62828}
@media (prefers-color-scheme:dark){.dv-add{color:#7bc47f}.dv-del{color:#e57373}}
.dv-hunk{border-top:1px solid var(--line)}.dv-hunk:first-child{border-top:none}
.dv-hunk>summary{padding:5px 12px;cursor:pointer;list-style:none;font-family:ui-monospace,Consolas,monospace;
font-size:12px;color:var(--mute);background:var(--bg)}
.dv-why{display:block;margin:4px 12px;padding:6px 10px;border-left:3px solid var(--bar);
background:var(--head);border-radius:0 4px 4px 0;font-size:13px;line-height:1.4}
.dv-code{margin:0;overflow-x:auto;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;
font-size:12.5px;line-height:1.5}
.dv-code .ln{display:block;padding:0 12px;white-space:pre}
.dv-code .ln.a{background:rgba(46,125,50,.12)}.dv-code .ln.d{background:rgba(198,40,40,.12)}
.dv-code .ln.h{color:var(--bar);background:var(--head)}
.dv-empty{padding:16px;color:var(--mute)}
summary::-webkit-details-marker{display:none}
/* AFFORDANCE pli/dépli : un chevron qui tourne (▶ fermé → ▼ ouvert) + retour au survol. Sans lui,
   rien ne dit que l'en-tête est cliquable (le marqueur natif était masqué). */
.dv-file>summary::before,.dv-hunk>summary::before{content:"\\25B6";font-size:9px;color:var(--mute);
margin-right:8px;display:inline-block;transition:transform .12s;flex:none}
.dv-file[open]>summary::before,.dv-hunk[open]>summary::before{transform:rotate(90deg)}
.dv-file>summary:hover,.dv-hunk>summary:hover{background:var(--line)}
.dv-bar{display:flex;gap:8px;align-items:center;margin:0 0 12px;flex-wrap:wrap}
.dv-bar button{font:inherit;font-size:12px;padding:4px 11px;border:1px solid var(--line);border-radius:5px;
background:var(--head);color:var(--fg);cursor:pointer}
.dv-bar button:hover{background:var(--line)}
.dv-hint{color:var(--mute);font-size:12px}
"""


def _explanation(o: dict) -> str:
    for k in _EXPLAIN_KEYS:
        v = o.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def _code_lines(hunk: str, esc) -> str:
    out = []
    for ln in hunk.splitlines():
        if ln.startswith("@@"):
            cls = "h"
        elif ln.startswith("+") and not ln.startswith("+++"):
            cls = "a"
        elif ln.startswith("-") and not ln.startswith("---"):
            cls = "d"
        else:
            cls = ""
        out.append(f'<span class="ln {cls}">{esc(ln) or "&nbsp;"}</span>')
    return "".join(out)


def render_diff_body(objects: list[dict], esc=None) -> str:
    """Hunk rows → an HTML body: one <details> per file, one per hunk, native fold/unfold, no script.
    `esc` defaults to html.escape; the engine passes its own so the whole page escapes uniformly."""
    esc = esc or _html.escape
    if not objects:
        return '<div class="dv-empty">No change — nothing to show.</div>'
    files: list[str] = []
    by_file: dict[str, list[dict]] = {}
    for o in objects:
        f = str(o.get("file", "?"))
        if f not in by_file:
            by_file[f] = []
            files.append(f)
        by_file[f].append(o)
    parts: list[str] = []
    for f in files:
        hunks = by_file[f]
        added = sum(int(h.get("added") or 0) for h in hunks)
        removed = sum(int(h.get("removed") or 0) for h in hunks)
        change = next((str(h.get("change")) for h in hunks if h.get("change")), "modified")
        secs = []
        for h in hunks:
            why = _explanation(h)
            why_html = f'<span class="dv-why">{esc(why)}</span>' if why else ""
            header = esc(str(h.get("header") or ""))
            code = _code_lines(str(h.get("hunk") or ""), esc)
            secs.append(f'<details class="dv-hunk" open><summary>{header or "(no hunk header)"}</summary>'
                        f'{why_html}<pre class="dv-code">{code}</pre></details>')
        parts.append(
            f'<details class="dv-file" open><summary><span class="dv-path">{esc(f)}</span>'
            f'<span class="dv-tag">{esc(change)}</span>'
            f'<span class="dv-add">+{added}</span><span class="dv-del">−{removed}</span>'
            f'</summary>{"".join(secs)}</details>')
    # Barre pli/dépli (le JS ne touche QUE l'attribut `open` des <details> de CETTE page — rien d'autre).
    bar = ('<div class="dv-bar"><button type="button" data-all="open">Tout déplier</button>'
           '<button type="button" data-all="close">Tout replier</button>'
           '<span class="dv-hint">▶ clique un en-tête pour replier / déplier</span></div>')
    script = ('<script>document.querySelectorAll(".dv-bar button").forEach(function(b){'
              'b.addEventListener("click",function(){var o=b.dataset.all==="open";'
              'document.querySelectorAll("details.dv-file,details.dv-hunk").forEach(function(d){d.open=o;});'
              '});});</script>')
    return bar + "".join(parts) + script


# ── the live server (stdlib, localhost, injected diff_fn + explain_fn) ──────────────────────────
import hashlib  # noqa: E402


def hunks_payload(diff_text: str, explain_fn=None, cache: dict | None = None) -> dict:
    """{hunks:[…], explained:N} from a diff text. PURE but for `explain_fn` (which it is GIVEN).

    An explanation is attached per hunk and MEMOISED by the hunk's content hash: on a live refresh,
    only a hunk whose text changed is explained again — a bound on model calls, by construction.
    """
    cache = cache if cache is not None else {}
    rows = parse_unified_diff(diff_text)
    explained = 0
    if explain_fn is not None:
        for h in rows:
            body = h.get("hunk") or ""
            if not body or body.startswith("("):
                continue
            key = hashlib.sha1(body.encode("utf-8", "replace")).hexdigest()
            if key not in cache:
                try:
                    cache[key] = str(explain_fn(h) or "")
                except Exception as e:  # noqa: BLE001 — a failed explanation never breaks the view
                    cache[key] = ""
                    h["explanation_error"] = f"{type(e).__name__}: {str(e)[:80]}"
            if cache[key]:
                h["explanation"] = cache[key]
                explained += 1
    return {"hunks": rows, "explained": explained, "files": len({h["file"] for h in rows})}


_LIVE_SCRIPT = """
const POLL=%(poll)d;
function esc(s){const d=document.createElement('div');d.textContent=s==null?'':s;return d.innerHTML;}
function codeLines(h){return (h||'').split('\\n').map(function(l){
  var c=l.startsWith('@@')?'h':((l.startsWith('+')&&!l.startsWith('+++'))?'a':((l.startsWith('-')&&!l.startsWith('---'))?'d':''));
  return '<span class="ln '+c+'">'+(esc(l)||'&nbsp;')+'</span>';}).join('');}
function render(data){
  const open=new Set();document.querySelectorAll('details[data-k]').forEach(function(d){if(d.open)open.add(d.dataset.k);});
  const files={},order=[];
  (data.hunks||[]).forEach(function(h){if(!(h.file in files)){files[h.file]=[];order.push(h.file);}files[h.file].push(h);});
  let html='';
  if(!order.length)html='<div class="dv-empty">No change \\u2014 working tree clean for this range.</div>';
  order.forEach(function(f){
    const hs=files[f];let a=0,d=0;hs.forEach(function(h){a+=h.added||0;d+=h.removed||0;});
    const fk='f:'+f;const fo=open.has(fk)||!open.size?' open':'';
    let secs='';hs.forEach(function(h,i){const hk='h:'+f+':'+i;const ho=open.has(hk)||!open.size?' open':'';
      const why=h.explanation?('<span class="dv-why">'+esc(h.explanation)+'</span>'):'';
      secs+='<details class="dv-hunk" data-k="'+esc(hk)+'"'+ho+'><summary>'+esc(h.header||'(no hunk header)')+'</summary>'+why+'<pre class="dv-code">'+codeLines(h.hunk)+'</pre></details>';});
    html+='<details class="dv-file" data-k="'+esc(fk)+'"'+fo+'><summary><span class="dv-path">'+esc(f)+'</span><span class="dv-tag">'+esc(hs[0].change||'')+'</span><span class="dv-add">+'+a+'</span><span class="dv-del">\\u2212'+d+'</span></summary>'+secs+'</details>';
  });
  document.getElementById('body').innerHTML=html;
  document.getElementById('meta').textContent=(data.files||0)+' file(s) \\u00b7 '+((data.hunks||[]).length)+' hunk(s) \\u00b7 '+(data.explained||0)+' explained \\u00b7 '+new Date().toLocaleTimeString();
}
function tick(){fetch('hunks.json',{cache:'no-store'}).then(function(r){return r.json();}).then(render).catch(function(e){document.getElementById('meta').textContent='poll error: '+e;});}
tick();setInterval(tick,POLL*1000);
"""


def live_page(title: str, poll: int, esc=None) -> str:
    """The served page: strict CSP but `connect-src 'self'` so it may poll THIS localhost server
    (and nowhere else). The only script is inline and talks to its own origin."""
    esc = esc or _html.escape
    script = _LIVE_SCRIPT % {"poll": poll}
    csp = "default-src 'none'; connect-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'"
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<meta http-equiv=\"Content-Security-Policy\" content=\"{csp}\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{esc(title)}</title><style>{_VIEW_BASE_CSS}{DIFF_CSS}</style></head><body>"
            f"<h1>{esc(title)}</h1><div class=\"meta\" id=\"meta\">connecting…</div>"
            f"<div id=\"body\"></div><script>{script}</script></body></html>")


# A minimal base CSS (the engine's _VIEW_CSS tokens) so the live page stands alone without importing
# the engine (SBR: diffview must not drag the 3 500-line engine in just to serve a page).
_VIEW_BASE_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1c1a;--mute:#6b6760;--line:#e3dfd6;--bar:#3d6b8c;--head:#f1eee7}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe7;--mute:#a09c93;--line:#34322e;--bar:#7fb0d3;--head:#201f1d}}
*{box-sizing:border-box}body{margin:0;padding:24px 16px;background:var(--bg);color:var(--fg);
font:14px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}h1{font-size:18px;margin:0 0 4px}
.meta{color:var(--mute);font-size:12px;margin-bottom:16px}
"""


def serve_live(diff_fn, explain_fn=None, *, port: int = 0, host: str = "127.0.0.1",
               title: str = "stilhawt diff (live)", poll: int = 2, open_browser: bool = True):
    """Start the live server. Returns (httpd, url). `diff_fn()` -> diff text (re-run each poll);
    `explain_fn(hunk_row)` -> str (optional). Caller runs `httpd.serve_forever()` and closes it.

    The server runs NOTHING of its own: it calls the two functions it was given. No OS tuning, no
    firewall (localhost only) — a bind on an ephemeral port, torn down on close."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    cache: dict = {}
    page = live_page(title, poll).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # silence — the CLI says where it listens, the server stays quiet
            pass

        def _send(self, code, ctype, body: bytes):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", page)
            elif path == "/hunks.json":
                try:
                    payload = hunks_payload(diff_fn() or "", explain_fn, cache)
                except Exception as e:  # noqa: BLE001
                    payload = {"hunks": [], "explained": 0, "files": 0, "error": f"{type(e).__name__}: {e}"}
                self._send(200, "application/json; charset=utf-8",
                           json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"))
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")

    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{httpd.server_address[1]}/"
    if open_browser:
        import webbrowser
        webbrowser.open_new_tab(url)
    return httpd, url


# ── git access (the only impure brick; injected into serve_live, bypassed in tests) ─────────────
def git_diff_text(rev: str | None = None, staged: bool = False, dir_: str | None = None) -> str:
    # The tool runs from the CLI's ROOT, but the user stands elsewhere: resolve the repository
    # against STILHAWT_CALLER_CWD (the engine's convention, `cli.std.here`), default = where they are.
    import shutil
    from stilhawt_cli.std import here
    from stilhawt_cli.tool import ToolRefusal
    if not shutil.which("git"):
        # Same refusal as `git status` (cli.std) — the blank tester got a raw FileNotFoundError here.
        raise ToolRefusal("git is not installed (or not on the PATH) — `git hunks` asks git for the diff")
    cwd = str(here(dir_))
    args = ["git", "diff", "--no-color"]
    if staged:
        args.append("--cached")
    if rev:
        args += rev.split()
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, creationflags=_NO_WINDOW, cwd=cwd)
    if r.returncode != 0 and not r.stdout:
        from stilhawt_cli.tool import ToolRefusal
        raise ToolRefusal(f"git diff failed: {(r.stderr or '').strip()[:200]}")
    return r.stdout


# ── the conforming tool: `git hunks` ────────────────────────────────────────────────────────────
tools = Toolset("stilhawt_cli.diffview")


@tools.verb("hunks", output=_FIELDS, effect="read",
            types={"file": "str", "change": "str", "old_start": "int", "new_start": "int",
                   "added": "int", "removed": "int", "header": "str", "hunk": "str", "lang": "str"},
            args=[Arg("rev", rest=True, help="a git diff range, e.g. main...HEAD or HEAD~1 (default: the working tree)"),
                  Arg("--staged", flag=True, help="the staged changes (git diff --cached)"),
                  Arg("--dir", help="the repository directory (default: where you stand)")])
def _hunks(rev, staged, dir):  # noqa: A002 — `dir` is the declared option name
    """one row per hunk of the repository's unified diff here: file, change, line counts, the raw hunk, the language"""
    return parse_unified_diff(git_diff_text(rev, staged, dir))


# ── selftest (golden + must-fail) ────────────────────────────────────────────────────────────────
_SAMPLE = """diff --git a/poc/portal_mdm_poc/.gitignore b/poc/portal_mdm_poc/.gitignore
new file mode 100644
index 0000000..ac481ac
--- /dev/null
+++ b/poc/portal_mdm_poc/.gitignore
@@ -0,0 +1,3 @@
+data/
+__pycache__/
+*.pyc
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -10,7 +10,7 @@ def handler():
 context line
-    old = 1
+    new = 2
 tail line
@@ -40,2 +40,3 @@ def other():
 keep
+added tail
diff --git a/old/name.txt b/new/name.txt
similarity index 100%
rename from old/name.txt
rename to new/name.txt
diff --git a/img.png b/img.png
index 3333333..4444444 100644
Binary files a/img.png and b/img.png differ
"""


def _selftest() -> int:
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"  ✗ {name}")

    # 0.3.2 — the blank tester (no git in the image): `git hunks` leaked a Python traceback where
    # `git status` refuses cleanly. Same namespace, same quality of refusal.
    import shutil as _sh
    from stilhawt_cli.tool import ToolRefusal as _TR
    saved_which = _sh.which
    try:
        _sh.which = lambda name, *a, **k: None if name == "git" else saved_which(name, *a, **k)
        try:
            git_diff_text()
            refused = ""
        except _TR as e:
            refused = str(e)
        except Exception as e:  # the bug: a raw FileNotFoundError
            refused = f"RAW {type(e).__name__}"
    finally:
        _sh.which = saved_which
    check("MUST-FAIL without git, `git hunks` REFUSES with the reason, never a traceback",
          "git is not installed" in refused)

    rows = parse_unified_diff(_SAMPLE)
    by_file: dict[str, list[dict]] = {}
    for r in rows:
        by_file.setdefault(r["file"], []).append(r)

    check("one block with two @@ → two hunks; distinct files kept apart",
          len(by_file.get("src/app.py", [])) == 2 and set(by_file) ==
          {"poc/portal_mdm_poc/.gitignore", "src/app.py", "new/name.txt", "img.png"})
    check("a new file is change=added, counts its + lines",
          by_file["poc/portal_mdm_poc/.gitignore"][0]["change"] == "added"
          and by_file["poc/portal_mdm_poc/.gitignore"][0]["added"] == 3)
    app0 = by_file["src/app.py"][0]
    check("a modified hunk: + and - counted apart, not the +++/--- headers",
          app0["added"] == 1 and app0["removed"] == 1 and app0["old_start"] == 10 and app0["new_start"] == 10)
    check("the hunk @@ with no ,count parses (old_start/new_start still read)",
          by_file["src/app.py"][1]["new_start"] == 40)
    check("a rename with no textual hunk still yields ONE row, change=renamed",
          len(by_file["new/name.txt"]) == 1 and by_file["new/name.txt"][0]["change"] == "renamed")
    check("a binary file yields ONE row marked (binary), not silence",
          by_file["img.png"][0]["hunk"] == "(binary)" and by_file["img.png"][0]["added"] == 0)
    check("language read from the extension (closed map), unknown → ''",
          app0["lang"] == "Python" and by_file["img.png"][0]["lang"] == "")

    # MUST-FAIL: an empty / blank diff is NOT a hunk (no row), never a fabricated one.
    check("MUST-FAIL empty diff → no rows (not one blank hunk)", parse_unified_diff("") == [] and parse_unified_diff("   \n") == [])
    # MUST-FAIL: text that is not a git diff produces nothing, never a guessed row.
    check("MUST-FAIL non-diff text → no rows", parse_unified_diff("hello\nworld\n") == [])

    # rendering: escapes, folds, shows an explanation when present, marks +/- lines
    body = render_diff_body([{**app0, "groq": "remplace old=1 par new=2"}])
    check("render: <details> per file and per hunk + a fold/unfold toolbar whose script toggles only `open`",
          body.count("<details") >= 2 and 'class="dv-bar"' in body and "d.open=o" in body)
    check("render: + line gets the add class, - line the del class",
          'class="ln a"' in body and 'class="ln d"' in body)
    check("render: the explanation is shown as the hunk comment", "remplace old=1 par new=2" in body)
    evil = render_diff_body([{"file": "x", "change": "modified", "header": "@@ x @@",
                              "hunk": "+<script>alert(1)</script>", "added": 1, "removed": 0,
                              "groq": "<img src=x onerror=alert(2)>"}])
    check("render: a value that looks like HTML is ESCAPED, never live (Rule no. 1)",
          "<script>alert(1)" not in evil and "&lt;script&gt;" in evil and "<img src=x onerror" not in evil and "&lt;img" in evil)
    check("render: empty stream says so, no crash", "No change" in render_diff_body([]))

    # hunks_payload: explanation memoised by content hash → a model call bound to CHANGED hunks
    calls = {"n": 0}

    def fake_explain(h):
        calls["n"] += 1
        return f"explains {h['added']}+/{h['removed']}-"

    cache: dict = {}
    p1 = hunks_payload(_SAMPLE, fake_explain, cache)
    n_after_first = calls["n"]
    p2 = hunks_payload(_SAMPLE, fake_explain, cache)      # same diff again
    # 3 textual hunks (gitignore + app.py×2); the rename and the binary carry no body → skipped.
    check("payload: hunks explained, binary/rename skipped", p1["explained"] == 3 and n_after_first == 3)
    check("payload: re-run on the SAME diff makes NO new model call (memoised)", calls["n"] == n_after_first)
    check("payload: the explanation lands on the hunk row",
          any(h.get("explanation") == "explains 1+/1-" for h in p2["hunks"]))

    def boom(h):
        raise RuntimeError("provider down")
    pe = hunks_payload(_SAMPLE, boom, {})
    check("payload: an explanation that throws never breaks the view (error noted, hunks flow)",
          pe["explained"] == 0 and len(pe["hunks"]) == len(rows))

    # the live server, end to end, over a real socket on an ephemeral port — no browser, stub diff
    import http.client
    import threading
    state = {"diff": _SAMPLE}
    httpd, url = serve_live(lambda: state["diff"], fake_explain, port=0, open_browser=False, poll=1)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        _, _, portnum = url.rstrip("/").rpartition(":")
        conn = http.client.HTTPConnection("127.0.0.1", int(portnum), timeout=5)
        conn.request("GET", "/")
        page = conn.getresponse()
        page_body = page.read().decode("utf-8")
        check("live: / serves the page with connect-src 'self' (localhost only) and no other origin",
              page.status == 200 and "connect-src 'self'" in page_body and "default-src 'none'" in page_body)
        conn.request("GET", "/hunks.json")
        jr = conn.getresponse()
        data = json.loads(jr.read().decode("utf-8"))
        check("live: /hunks.json re-runs the diff and returns the hunks as JSON",
              jr.status == 200 and len(data["hunks"]) == len(rows) and data["explained"] == 3)
        # change the working tree → next poll reflects it
        state["diff"] = _SAMPLE.replace("new = 2", "new = 999")
        conn.request("GET", "/hunks.json")
        data2 = json.loads(conn.getresponse().read().decode("utf-8"))
        check("live: a changed diff is reflected on the next request (the server re-reads, live)",
              any("new = 999" in (h.get("hunk") or "") for h in data2["hunks"]))
        conn.request("GET", "/nope")
        check("live: an unknown path is 404, not the page", conn.getresponse().status == 404)
    finally:
        httpd.shutdown()
        httpd.server_close()

    print(f"\n{ok}/{total} selftests passed")
    return 0 if ok == total else 1


tools.selftest(_selftest)

if __name__ == "__main__":
    raise SystemExit(tools.main())
