"""stilhawt_cli.std — the GENERIC tools of the CLI: useful anywhere, knowing nothing of a workspace.

    python -m stilhawt_cli.std fs-files [<dir>]          # one row per file: language, kind, lines, vendored
    python -m stilhawt_cli.std fs-search <pattern>       # a typed grep: file, line, text
    python -m stilhawt_cli.std data-read <file>          # JSON / JSON Lines / CSV / TSV / YAML → rows
    python -m stilhawt_cli.std repo-status [<dir>]       # every git repository under a directory
    python -m stilhawt_cli.std http-get <url>            # status, time, type, size, title
    python -m stilhawt_cli.std dsl-read <file> [<path>]  # navigate a YAML / JSON document (JMESPath)
    python -m stilhawt_cli.std dsl-tree <file>           # the document as rows: path, type, value
    python -m stilhawt_cli.std ai-keys | ai-probe        # the model keys the extensions declare — never a value
    python -m stilhawt_cli.std --selftest

Why a brick of its own (2026-09-29: a light toolset to show the CLI — above all its AI pipes — on any
machine). The workspace's commands answer the workspace (its fleet,
its contracts, its repositories); these answer the SAME gestures on any machine. Each returns FACTS
only — filtering, grouping, totalling, asking a model are the pipeline's business. They ship in the
public package and serve the workspace too: its commands are overlays on them (`ws files` adds the
repository owner to `fs files`), never a second implementation.

Read-only everywhere; `http get` is the one `network` verb. No value of a credential is ever read out:
`ai keys` says whether its VARIABLE is set.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from stilhawt_cli.tool import Arg, ToolRefusal, Toolset

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache", ".pytest_cache", "build",
        "dist", ".tox", ".idea", ".vscode"}
LANG = {".py": "Python", ".go": "Go", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
        ".mjs": "JavaScript", ".kt": "Kotlin", ".java": "Java", ".cpp": "C++", ".cc": "C++", ".h": "C/C++ header",
        ".hpp": "C/C++ header", ".c": "C", ".rs": "Rust", ".sh": "Shell", ".ps1": "PowerShell",
        ".yaml": "YAML", ".yml": "YAML", ".md": "Markdown", ".json": "JSON", ".html": "HTML", ".css": "CSS",
        ".lp": "ASP", ".star": "Starlark", ".sql": "SQL", ".toml": "TOML", ".nix": "Nix", ".vue": "Vue"}
# What a language IS in a line count: counting config, data and docs with code answered « how big is
# the code? » 2.6× too high on a real workspace (2026-09-29).
KIND = {"YAML": "config/data", "JSON": "config/data", "TOML": "config/data",
        "Markdown": "doc", "HTML": "markup", "CSS": "markup"}          # every other language: code
# Vendored code by the usual conventions (GitHub linguist's): a closed list, not a guess on a name.
VENDOR_DIRS = {"vendor", "vendored", "third_party", "third-party", "3rdparty", "external", "extern", "node_modules"}
_MINIFIED = (".min.js", ".min.css", ".min.mjs")
MAX_READ = 50 * 2**20


def is_vendored(rel: str) -> bool:
    """A path that is someone else's code: under a vendor directory, or minified. PURE."""
    parts = rel.replace("\\", "/").lower().split("/")
    return any(p in VENDOR_DIRS for p in parts[:-1]) or parts[-1].endswith(_MINIFIED)


def tracked(d: Path) -> list[Path] | None:
    """The files git tracks under `d` (answered by the repository that CONTAINS `d`), or None."""
    try:
        r = subprocess.run(["git", "-C", str(d), "ls-files", "-z", "--", "."], capture_output=True,
                           timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode:
        return None
    return [d / p for p in r.stdout.decode("utf-8", "replace").split("\0") if p]


def walk(d: Path) -> list[Path]:
    out = []
    for dp, dn, fn in os.walk(d):
        dn[:] = [x for x in dn if x not in SKIP and not x.startswith(".")]
        out += [Path(dp) / f for f in fn]
    return out


def count_lines(f: Path) -> int | None:
    try:
        with f.open("rb") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def here(path: str | None) -> Path:
    """A path the USER typed, relative to where they stand (the engine passes STILHAWT_CALLER_CWD: a
    tool runs from the CLI's root, the user does not). An absolute path stays as it is."""
    p = Path(path or ".").expanduser()
    return p if p.is_absolute() else Path(os.environ.get("STILHAWT_CALLER_CWD") or ".") / p


def _dir(d: str | None) -> Path:
    p = here(d)
    if not p.is_dir():
        raise ToolRefusal(f"not a directory: {d}")
    return p


def fs_files(d: str | None = None) -> list[dict]:
    """One row per file with a known language — git-tracked files inside a repository, else a walk."""
    from concurrent.futures import ThreadPoolExecutor
    root = _dir(d)
    files = tracked(root)
    source = "git" if files is not None else "walk"
    files = files if files is not None else walk(root)
    picked = [(f, LANG[f.suffix.lower()]) for f in files if f.suffix.lower() in LANG]
    with ThreadPoolExecutor(16) as ex:
        counts = list(ex.map(lambda p: count_lines(p[0]), picked))
    return [{"path": str(f.relative_to(root)).replace("\\", "/"), "language": lang, "kind": KIND.get(lang, "code"),
             "lines": n, "vendored": is_vendored(str(f.relative_to(root))), "source": source}
            for (f, lang), n in zip(picked, counts) if n is not None]


def fs_search(pattern: str, within: str | None = None, glob: str | None = None, regex: bool = False,
              case: bool = False, limit: int = 200) -> list[dict]:
    import fnmatch
    root = _dir(within)
    try:
        rx = re.compile(pattern if regex else re.escape(pattern), 0 if case else re.I)
    except re.error as e:
        raise ToolRefusal(f"not a regular expression: {e}") from None
    files = tracked(root)
    files = files if files is not None else walk(root)
    out = []
    for f in files:
        if glob and not fnmatch.fnmatch(f.name, glob):
            continue
        try:
            if f.stat().st_size > MAX_READ:
                continue
            raw = f.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:4096]:
            continue                                        # binary
        for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
            if rx.search(line):
                out.append({"file": str(f.relative_to(root)).replace("\\", "/"), "line": i, "text": line.strip()[:240]})
                if len(out) >= limit:
                    return out
    return out


_NUMBER = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?$")


def _number(v):
    """A CSV cell: a clean integer or decimal becomes a number, anything else stays as it is. PURE."""
    if isinstance(v, str) and _NUMBER.match(v.strip()):
        return float(v) if "." in v else int(v)
    return v


def data_read(file: str) -> list[dict]:
    """A data file → rows. A list of objects is the rows; a mapping of mappings gives one row per key
    (`key` added); anything else is ONE row. The format is read from the EXTENSION, never guessed."""
    import csv
    p = here(file)
    if not p.is_file():
        raise ToolRefusal(f"no such file: {file}")
    if p.stat().st_size > MAX_READ:
        raise ToolRefusal(f"{file} is larger than {MAX_READ // 2**20} MB")
    ext = p.suffix.lower()
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if ext in (".csv", ".tsv"):
        # A CSV has no types: a cell that is a clean number BECOMES a number, or `where stars gt 3` would
        # compare a string with a number and keep nothing (found on the README's own example). Anything
        # else stays text — "007" and "1e5" too: a leading zero or an exponent is an identifier's shape.
        return [{k: _number(v) for k, v in r.items()}
                for r in csv.DictReader(text.splitlines(), delimiter="\t" if ext == ".tsv" else ",")]
    if ext in (".jsonl", ".ndjson"):
        rows = []
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    v = json.loads(line)
                except ValueError:
                    raise ToolRefusal(f"{file}:{i} is not JSON") from None
                rows.append(v if isinstance(v, dict) else {"value": v})
        return rows
    if ext == ".json":
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ToolRefusal(f"{file} is not JSON: {e}") from None
    elif ext in (".yaml", ".yml"):
        import yaml
        doc = yaml.safe_load(text)
    else:
        raise ToolRefusal(f"{file}: extension `{ext}` is not a data format read here (json, jsonl, csv, tsv, yaml)")
    if isinstance(doc, list):
        return [x if isinstance(x, dict) else {"value": x} for x in doc]
    if isinstance(doc, dict) and doc and all(isinstance(v, dict) for v in doc.values()):
        return [{"key": k, **v} for k, v in doc.items()]
    return [doc if isinstance(doc, dict) else {"value": doc}]


def _git(r: Path, *args) -> str:
    x = subprocess.run(["git", "-C", str(r), *args], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, creationflags=_NO_WINDOW)
    return x.stdout if x.returncode == 0 else ""


def repo_status(d: str | None = None, depth: int = 2) -> list[dict]:
    """Every git repository under `d` (itself included), up to `depth` levels down."""
    root = _dir(d)
    repos = []
    for dp, dn, _ in os.walk(root):
        p = Path(dp)
        if (p / ".git").exists():
            repos.append(p)
        if len(p.relative_to(root).parts) >= depth:
            dn[:] = []
        dn[:] = [x for x in dn if x not in SKIP and not x.startswith(".")]
    rows = []
    for r in repos:
        branch, ahead, behind, modified, untracked = None, None, None, 0, 0
        for line in _git(r, "status", "--porcelain=v2", "--branch").splitlines():
            if line.startswith("# branch.head "):
                branch = line.split(" ", 2)[2]
            elif line.startswith("# branch.ab "):
                a, b = line.split()[2:4]
                ahead, behind = int(a), -int(b)
            elif line[:2] in ("1 ", "2 ", "u "):
                modified += 1
            elif line.startswith("? "):
                untracked += 1
        rows.append({"repo": str(r.relative_to(root)).replace("\\", "/") or ".", "branch": branch,
                     "modified": modified, "untracked": untracked, "ahead": ahead, "behind": behind,
                     "last_commit": _git(r, "log", "-1", "--format=%cI").strip() or None})
    return rows


def http_get(url: str, timeout: float = 10.0) -> list[dict]:
    """ONE request: status, time, content type, size, the page title. http(s) only; no proxy magic."""
    import time
    import urllib.error
    import urllib.request
    if not re.match(r"^https?://", url or ""):
        raise ToolRefusal("http get takes an http:// or https:// URL")
    req = urllib.request.Request(url, headers={"User-Agent": "stilhawt-cli"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, ctype, body = r.status, r.headers.get("Content-Type", ""), r.read(2**20)
    except urllib.error.HTTPError as e:
        status, ctype, body = e.code, e.headers.get("Content-Type", "") if e.headers else "", b""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return [{"url": url, "status": None, "ms": int((time.perf_counter() - t0) * 1000), "type": None,
                 "bytes": None, "title": None, "error": type(getattr(e, "reason", e)).__name__}]
    m = re.search(rb"<title[^>]*>(.*?)</title>", body, re.I | re.S)
    title = " ".join(m.group(1).decode("utf-8", "replace").split())[:160] if m else None
    return [{"url": url, "status": status, "ms": int((time.perf_counter() - t0) * 1000), "type": ctype.split(";")[0] or None,
             "bytes": len(body), "title": title, "error": None}]


# ── DSL navigation: any YAML / JSON document, by path ───────────────────────────────────────────
def load_doc(file: str):
    p = here(file)
    if not p.is_file():
        raise ToolRefusal(f"no such file: {file}")
    text = p.read_text(encoding="utf-8-sig", errors="replace")
    if p.suffix.lower() == ".json":
        return json.loads(text)
    import yaml
    return yaml.safe_load(text)


def brief(v, n: int = 200):
    if isinstance(v, (dict, list)):
        s = json.dumps(v, ensure_ascii=False, default=str)
        return s if len(s) <= n else s[:n] + "…"
    return v


def navigate(doc, path: str | None) -> list[dict]:
    """A document, or the part a JMESPath expression selects, as rows {key, value, type}. PURE."""
    if path:
        try:
            import jmespath
        except ImportError:
            raise ToolRefusal("JMESPath is not installed (`pip install jmespath`)") from None
        try:
            doc = jmespath.search(path, doc)
        except jmespath.exceptions.JMESPathError as e:
            raise ToolRefusal(f"not a JMESPath expression: {e}") from None
    if isinstance(doc, dict):
        return [{"key": k, "value": brief(v), "type": type(v).__name__} for k, v in doc.items()]
    if isinstance(doc, list):
        return [{"key": i, "value": brief(v), "type": type(v).__name__} for i, v in enumerate(doc)]
    return [{"key": path or "", "value": doc, "type": type(doc).__name__}]


def tree(doc, depth: int = 3, prefix: str = "") -> list[dict]:
    """The document flattened: one row per node down to `depth`, its dotted PATH (the key to give
    `where`, `select`, or an AI pipe's `--on`), its type and a brief value. PURE."""
    rows = []

    def go(v, path, d):
        container = isinstance(v, (dict, list))
        # A container is SUMMARISED (its size), at the depth bound too — never poured out as JSON.
        rows.append({"path": path or ".", "type": type(v).__name__, "value": brief(v) if not container else
                     (f"{len(v)} key(s)" if isinstance(v, dict) else f"{len(v)} item(s)")})
        if not container or d >= depth:
            return
        items = v.items() if isinstance(v, dict) else enumerate(v)
        for k, x in items:
            go(x, f"{path}.{k}" if path else str(k), d + 1)

    go(doc, prefix, 0)
    return rows


# ── model keys: what the extensions declare, never a value ──────────────────────────────────────
def _declared_credentials() -> dict:
    from stilhawt_cli import ext
    from stilhawt_cli import grammar
    ext.load_extensions(grammar.load().get("extensions"))
    return ext.credentials()


def ai_keys() -> list[dict]:
    creds = _declared_credentials()
    return [{"provider": p, "variable": c["variable"], "set": bool(os.environ.get(c["variable"])),
             "how": c.get("how") or ""} for p, c in sorted(creds.items())]


def ai_probe() -> list[dict]:
    rows = []
    for p, c in sorted(_declared_credentials().items()):
        if not os.environ.get(c["variable"]):
            state = "not set"
        elif c.get("probe") is None:
            state = "no probe declared"
        else:
            try:
                state = str(c["probe"]())
            except Exception as e:  # noqa: BLE001 — a probe that crashes is a row, not a crash
                state = f"probe failed: {type(e).__name__}"
        rows.append({"provider": p, "variable": c["variable"], "state": state})
    return rows


# ── the verbs, declared ONCE (cli/tool.py) ──────────────────────────────────────────────────────
tools = Toolset("stilhawt_cli.std")


def _int(v, default, name):
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        raise ToolRefusal(f"{name} takes a number") from None


@tools.verb("fs-files", output=["path", "language", "kind", "lines", "vendored", "source"],
            types={"path": "str", "language": "str", "kind": "str", "lines": "int", "vendored": "bool", "source": "str"},
            args=[Arg("dir", help="a directory (default: the current one)")])
def _fs_files(dir):
    """one row per file with a known language: kind (code, markup, config/data, doc), lines, vendored"""
    return fs_files(dir)


@tools.verb("fs-search", output=["file", "line", "text"], types={"file": "str", "line": "int", "text": "str"},
            args=[Arg("pattern", rest=True, required=True, help="the text to find"),
                  Arg("--in", help="a directory (default: the current one)"), Arg("--glob", help="file name filter, e.g. *.py"),
                  Arg("--regex", flag=True, help="the pattern is a regular expression"),
                  Arg("--case", flag=True, help="case-sensitive"), Arg("--max", help="at most N hits (default 200)")])
def _fs_search(pattern, **kw):
    """a typed grep over text files: file, line, text"""
    return fs_search(pattern, kw.get("in"), kw.get("glob"), kw.get("regex"), kw.get("case"), _int(kw.get("max"), 200, "--max"))


@tools.verb("data-read", output=["row"], types={"row": "int"},
            args=[Arg("file", required=True, help="a .json, .jsonl, .csv, .tsv or .yaml file")],
            does="a data file as rows — JSON, JSON Lines, CSV, TSV, YAML; the file's own keys, plus `row` (its number, from 1)")
def _data_read(file):
    # `row` is the ONE key promised whatever the file holds (R4): where a result came from. A key of
    # the file named `row` is kept as `row_` rather than overwritten.
    return [{"row": i, **({("row_" if k == "row" else k): v for k, v in r.items()})}
            for i, r in enumerate(data_read(file), 1)]


@tools.verb("repo-status", output=["repo", "branch", "modified", "untracked", "ahead", "behind", "last_commit"],
            types={"repo": "str", "branch": "str", "modified": "int", "untracked": "int", "ahead": "int",
                   "behind": "int", "last_commit": "str"},
            args=[Arg("dir", help="a directory (default: the current one)"), Arg("--depth", help="levels down (default 2)")])
def _repo_status(dir, depth):
    """every git repository under a directory: branch, modified, untracked, ahead / behind its upstream"""
    return repo_status(dir, _int(depth, 2, "--depth"))


@tools.verb("http-get", effect="network", output=["url", "status", "ms", "type", "bytes", "title", "error"],
            types={"url": "str", "status": "int", "ms": "int", "type": "str", "bytes": "int", "title": "str", "error": "str"},
            args=[Arg("url", required=True, help="an http(s) URL"), Arg("--timeout", help="seconds (default 10)")])
def _http_get(url, timeout):
    """one HTTP GET: status, time, content type, size, page title"""
    return http_get(url, float(_int(timeout, 10, "--timeout")))


@tools.verb("dsl-read", output=["key", "value", "type"], types={"key": "any", "value": "any", "type": "str"},
            args=[Arg("file", required=True, help="a YAML or JSON document"),
                  Arg("path", rest=True, help="a JMESPath expression, e.g. rules[?effect=='write'].id")])
def _dsl_read(file, path):
    """navigate a YAML / JSON document: the part a JMESPath expression selects, as rows"""
    return navigate(load_doc(file), path)


@tools.verb("dsl-tree", output=["path", "type", "value"], types={"path": "str", "type": "str", "value": "any"},
            args=[Arg("file", required=True, help="a YAML or JSON document"), Arg("--depth", help="levels (default 3)")])
def _dsl_tree(file, depth):
    """a document flattened: one row per node, its dotted path (what `--on` and `where` take), type, value"""
    return tree(load_doc(file), _int(depth, 3, "--depth"))


@tools.verb("ai-keys", output=["provider", "variable", "set", "how"],
            types={"provider": "str", "variable": "str", "set": "bool", "how": "str"})
def _ai_keys():
    """the model keys the extensions declare: which variable, whether it is set — never a value"""
    return ai_keys()


@tools.verb("ai-probe", effect="network", output=["provider", "variable", "state"],
            types={"provider": "str", "variable": "str", "state": "str"})
def _ai_probe():
    """each declared model key tried with one tiny read call: valid, refused, unreachable — never a value"""
    return ai_probe()


@tools.selftest
def _selftest() -> int:
    import shutil
    import tempfile
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"✗ {name}")

    with tempfile.TemporaryDirectory() as t:
        w = Path(t)
        (w / "src").mkdir()
        (w / "src" / "a.py").write_text("1\n2\n3\n", encoding="utf-8")
        (w / "vendor").mkdir()
        (w / "vendor" / "lib.js").write_text("x\n" * 9, encoding="utf-8")
        (w / "conf.yaml").write_text("a: 1\n", encoding="utf-8")
        (w / "node_modules").mkdir()
        (w / "node_modules" / "dep.js").write_text("y\n", encoding="utf-8")
        f = {r["path"]: r for r in fs_files(t)}
        check("fs files: facts per file, kind and vendored flagged",
              (f["src/a.py"]["lines"], f["src/a.py"]["kind"], f["conf.yaml"]["kind"], f["vendor/lib.js"]["vendored"])
              == (3, "code", "config/data", True))
        check("MUST-FAIL a walk skips node_modules (a dependency tree, not the project)", "node_modules/dep.js" not in f)
        hits = fs_search("2", t)
        check("fs search: file and line", [(h["file"], h["line"]) for h in hits] == [("src/a.py", 2)])
        try:
            fs_search("(", t, regex=True)
            check("MUST-FAIL a bad regex is refused", False)
        except ToolRefusal:
            check("MUST-FAIL a bad regex is refused", True)
        (w / "r.csv").write_text("id,text\n1,good\n2,bad\n", encoding="utf-8")
        (w / "r.jsonl").write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")
        (w / "m.yaml").write_text("alpha: {x: 1}\nbeta: {x: 2}\n", encoding="utf-8")
        check("data read: CSV (numbers typed), JSON Lines, a mapping of mappings",
              (data_read(str(w / "r.csv"))[1], [r["id"] for r in data_read(str(w / "r.jsonl"))], data_read(str(w / "m.yaml"))[0])
              == ({"id": 2, "text": "bad"}, [1, 2], {"key": "alpha", "x": 1}))
        check("MUST-FAIL an identifier's shape stays text: a leading zero, an exponent, a version",
              [_number(x) for x in ("007", "1e5", "1.2.3", "4.5", "-3")] == ["007", "1e5", "1.2.3", 4.5, -3])
        (w / "x.txt").write_text("hi", encoding="utf-8")
        try:
            data_read(str(w / "x.txt"))
            check("MUST-FAIL an unknown extension is refused, never guessed", False)
        except ToolRefusal:
            check("MUST-FAIL an unknown extension is refused, never guessed", True)
        doc = {"rules": [{"id": "r1", "effect": "read"}, {"id": "r2", "effect": "write"}], "name": "demo"}
        check("dsl read: a JMESPath selection as rows",
              navigate(doc, "rules[?effect=='write'].id") == [{"key": 0, "value": "r2", "type": "str"}])
        tr = {r["path"]: r for r in tree(doc, depth=3)}
        check("dsl tree: dotted paths down to the leaves", tr["rules.1.effect"]["value"] == "write" and tr["name"]["value"] == "demo")
        check("MUST-FAIL a depth bound stops the tree (a summary, not the subtree)",
              {r["path"] for r in tree(doc, depth=1)} == {".", "rules", "name"} and tree(doc, depth=1)[1]["value"] == "2 item(s)")
        if shutil.which("git"):
            r = w / "proj"
            r.mkdir()
            subprocess.run(["git", "-C", str(r), "init", "-q"], check=True, creationflags=_NO_WINDOW)
            (r / "f.txt").write_text("x", encoding="utf-8")
            st = repo_status(t)
            check("repo status: a repository under the directory, its untracked file counted",
                  [(x["repo"], x["untracked"]) for x in st] == [("proj", 1)])
    import tempfile as _tf
    with _tf.TemporaryDirectory() as caller:
        (Path(caller) / "mine.csv").write_text("a\n1\n", encoding="utf-8")
        old = os.environ.get("STILHAWT_CALLER_CWD")
        os.environ["STILHAWT_CALLER_CWD"] = caller
        try:
            check("MUST-FAIL a relative path the user typed is read from THEIR directory, not the tool's",
                  data_read("mine.csv") == [{"a": 1}])
        finally:
            if old is None:
                os.environ.pop("STILHAWT_CALLER_CWD", None)
            else:
                os.environ["STILHAWT_CALLER_CWD"] = old
    try:
        http_get("file:///etc/passwd")
        check("MUST-FAIL http get refuses anything but http(s)", False)
    except ToolRefusal:
        check("MUST-FAIL http get refuses anything but http(s)", True)
    print(f"{ok}/{total} selftests passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    sys.exit(tools.main())
