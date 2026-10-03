# -*- coding: utf-8 -*-
"""Anonymous usage telemetry of a public StilHawt package — the engine of contract TLM.

Everything this module may send is declared in `telemetry.dsl.yaml` (next to this file in a public
package, `contracts/` in the workspace): a CLOSED vocabulary of events and attributes, the ways to
turn it off, the identity policy. Read that file first; this one only obeys it.

Properties the selftest holds:
  · a command is reported by GRAMMAR words only — its arguments never leave (`<other>` otherwise);
  · nothing is sent when off (env, CI, config, or `enabled: false` in the contract);
  · the run that shows the notice sends nothing;
  · a payload carrying an undeclared or forbidden attribute is refused before it leaves;
  · stdlib only, and no failure of telemetry ever changes the command's result or exit code.

Transport: OTLP/HTTP with JSON encoding (opentelemetry-proto, docs/specification.md: ids as hex,
proto3 JSON mapping). Events go to a local spool first; a background thread sends the spool, and a
command waits at most `transport.exit_wait_s` for it at exit — what is not sent goes next time.

  python -m stilhawt_cli.telemetry status | on | off     # the user's switch (public package)
  python telemetry.py --check                            # the contract's own consistency
  python telemetry.py --collector-config                 # the collector config DERIVED from TLM
  python telemetry.py --selftest
"""
from __future__ import annotations

import json
import os
import platform
import secrets
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path

_HERE = Path(__file__).resolve().parent
OTHER = "<other>"
_thread: threading.Thread | None = None


# ───────────────────────────── the contract ─────────────────────────────

def contract_path() -> Path:
    """Public package: the YAML ships beside the module. Workspace: stilhawt-sdk/contracts/."""
    for p in (_HERE / "telemetry.dsl.yaml", _HERE.parent / "contracts" / "telemetry.dsl.yaml"):
        if p.exists():
            return p
    raise FileNotFoundError("telemetry.dsl.yaml")


_CONTRACT: dict | None = None
_RAW: dict | None = None


def _has_lex_ref(node) -> bool:
    if isinstance(node, dict):
        return ("lex" in node and set(node) <= {"lex", "en"}) or any(_has_lex_ref(v) for v in node.values())
    return isinstance(node, list) and any(_has_lex_ref(v) for v in node)


def contract() -> dict:
    """The contract with its legal references RESOLVED. In a public package the export already
    materialised them (oss `resolve_lex`), so the sibling resolver `lex` is only imported at home — the
    public package does not ship it and never takes this branch."""
    global _CONTRACT, _RAW
    if _CONTRACT is None:
        import yaml
        _RAW = yaml.safe_load(contract_path().read_text(encoding="utf-8"))
        if _has_lex_ref(_RAW):
            if __package__:
                from . import lex
            else:                       # run as a script (the DSL linter): the sibling file is on sys.path
                import lex
            _CONTRACT = lex.resolve(_RAW)
        else:
            _CONTRACT = _RAW
    return _CONTRACT


def check(c: dict, raw: dict | None = None) -> list[str]:
    """Grievances of a contract — what would make it lie about what it sends. With `raw` (the
    workspace source, before resolution), the legal durations must be LEX references, never numbers."""
    g = []
    if raw is not None:
        for path in (("identity", "rotate_days"), ("collector", "retention_days")):
            v = (raw.get(path[0]) or {}).get(path[1])
            if not (isinstance(v, dict) and "lex" in v):
                g.append(f"{'.'.join(path)} = {v!r} written in clear — a legal duration REFERENCES LEX")
    forbidden = set(c.get("forbidden") or [])
    declared = set(c.get("resource") or {})
    for name, ev in (c.get("events") or {}).items():
        declared |= set((ev or {}).get("attributes") or {})
    for k in sorted(declared & forbidden):
        g.append(f"attribute `{k}` is declared AND forbidden")
    ep = (c.get("transport") or {}).get("endpoint", "")
    if not ep.startswith("https://"):
        g.append(f"endpoint `{ep}` is not https")
    else:
        import ipaddress
        from urllib.parse import urlparse
        try:
            ipaddress.ip_address(urlparse(ep).hostname or "")
            g.append(f"endpoint `{ep}` is an IP literal — installed copies could never follow a move: use the domain")
        except ValueError:
            pass
    if (c.get("identity") or {}).get("kind") != "random_uuid4":
        g.append("identity must be a random uuid4 (no hardware identifier)")
    if not isinstance((c.get("identity") or {}).get("rotate_days"), int):
        g.append("identity.rotate_days does not resolve to a number of days")
    if c.get("first_run") != "notice_only":
        g.append("first_run must be notice_only: nothing is sent before the user can read the notice")
    if not c.get("notice"):
        g.append("no notice")
    for k in ("command", "install"):
        if k not in (c.get("events") or {}):
            g.append(f"event `{k}` missing")
    return g


# ───────────────────────────── on / off ─────────────────────────────

def config_dir(env: dict | None = None) -> Path:
    env = os.environ if env is None else env
    if sys.platform == "win32" and env.get("APPDATA"):
        base = Path(env["APPDATA"])
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "stilhawt"


def off_reason(c: dict, env: dict, state: dict) -> str | None:
    """Why telemetry is off — None when it is on. The first reason found is the one said."""
    if not c.get("enabled"):
        return "disabled in this build (contract TLM: enabled: false)"
    for var, values in ((c.get("off_when") or {}).get("env") or {}).items():
        if str(env.get(var, "")).strip().lower() in values:
            return f"{var}={env.get(var)}"
    for var in (c.get("off_when") or {}).get("ci_env") or []:
        if env.get(var):
            return f"continuous integration ({var})"
    if state.get("enabled") is False:
        return "turned off by the user (telemetry.json)"
    return None


def _state_file(env: dict | None = None) -> Path:
    return config_dir(env) / "telemetry.json"


def load_state(env: dict | None = None) -> dict:
    try:
        return json.loads(_state_file(env).read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict, env: dict | None = None) -> None:
    f = _state_file(env)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
    os.replace(tmp, f)


def identity(state: dict, c: dict, now: float) -> tuple[dict, bool]:
    """(state, is_new) — a fresh random id on the first run or once it is older than rotate_days."""
    days = int((c.get("identity") or {}).get("rotate_days", 395))
    if state.get("install_id") and now - float(state.get("id_created", 0)) < days * 86400:
        return state, False
    return {**state, "install_id": uuid.uuid4().hex, "id_created": now, "install_sent": False}, True


# ───────────────────────────── what a command line becomes ─────────────────────────────

def vocabulary(doc: dict, pipes: list[str]) -> dict:
    """The grammar's words: {namespace: [commands]} and the pipe names (engine + AI pipes)."""
    ns = {n: sorted((v or {}).get("commands") or {}) for n, v in ((doc or {}).get("namespaces") or {}).items()}
    return {"namespaces": ns, "pipes": sorted(set(pipes) | set((doc or {}).get("pipes") or {}))}


def describe(argv: list[str], vocab: dict) -> dict:
    """A command line → the command event's attributes, GRAMMAR WORDS ONLY. Arguments never leave."""
    a = [x for x in argv if x not in ("--json", "--text")]
    if "--profile" in a:
        i = a.index("--profile")
        del a[i:i + 2]
    if not a or a[0] == "shell":
        return {"cli.mode": "shell", "cli.namespace": "", "cli.command": "", "cli.pipes": ""}
    if a[0] in ("help", "-h", "--help", "?"):
        return {"cli.mode": "help", "cli.namespace": "", "cli.command": "", "cli.pipes": ""}
    if a[0] in ("--version", "--check"):
        return {"cli.mode": a[0][2:], "cli.namespace": "", "cli.command": "", "cli.pipes": ""}
    segs, cur = [], []
    for x in a:
        if x == "|":
            segs.append(cur); cur = []
        else:
            cur.append(x)
    segs.append(cur)
    ns_words = vocab.get("namespaces") or {}
    first = segs[0]
    pipe_segs = segs[1:]
    namespace = command = ""
    if first and first[0] in ns_words:
        namespace = first[0]
        command = first[1] if len(first) > 1 and first[1] in ns_words[namespace] else OTHER
    elif first and first[0] in (vocab.get("pipes") or []):
        pipe_segs = segs                      # a line that starts with a pipe reads stdin
    else:
        namespace = OTHER
    pipes = [s[0] if s and s[0] in (vocab.get("pipes") or []) else OTHER for s in pipe_segs]
    return {"cli.mode": "line", "cli.namespace": namespace, "cli.command": command,
            "cli.pipes": ",".join(pipes)}


# ───────────────────────────── OTLP/JSON ─────────────────────────────

def _attr(k: str, v) -> dict:
    if isinstance(v, bool):
        return {"key": k, "value": {"boolValue": v}}
    if isinstance(v, int):
        return {"key": k, "value": {"intValue": str(v)}}      # proto3 JSON: int64 as a string
    return {"key": k, "value": {"stringValue": str(v)}}


def resource_attrs(c: dict, install_id: str, version: str) -> dict:
    s = platform.system().lower()
    return {"service.name": c.get("service", "stilhawt"), "service.version": version,
            "os.type": s if s in ("windows", "linux", "darwin") else "other",
            "process.runtime.version": f"{sys.version_info.major}.{sys.version_info.minor}",
            "stilhawt.install_id": install_id}


def validate(c: dict, resource: dict, events: list[dict]) -> list[str]:
    """Every key must be DECLARED and none forbidden — checked on the payload, before it leaves."""
    forbidden = set(c.get("forbidden") or [])
    bad = [f"resource `{k}`" for k in resource if k not in (c.get("resource") or {}) or k in forbidden]
    for e in events:
        decl = ((c.get("events") or {}).get(e["name"]) or {})
        if not decl and e["name"] not in (c.get("events") or {}):
            bad.append(f"event `{e['name']}` undeclared")
            continue
        allowed = set(decl.get("attributes") or {})
        bad += [f"{e['name']}.`{k}`" for k in e["attrs"] if k not in allowed or k in forbidden]
    return bad


def otlp(resource: dict, events: list[dict]) -> dict:
    spans = []
    for e in events:
        end = int(e["t_end"] * 1e9)
        spans.append({"traceId": e["trace_id"], "spanId": e["span_id"], "name": e["name"],
                      "kind": 1, "startTimeUnixNano": str(end - int(e.get("ms", 0) * 1e6)),
                      "endTimeUnixNano": str(end),
                      "attributes": [_attr(k, v) for k, v in e["attrs"].items()]})
    return {"resourceSpans": [{"resource": {"attributes": [_attr(k, v) for k, v in resource.items()]},
                               "scopeSpans": [{"scope": {"name": "stilhawt.telemetry"}, "spans": spans}]}]}


def event(name: str, attrs: dict, ms: float = 0) -> dict:
    return {"name": name, "attrs": attrs, "ms": ms, "t_end": time.time(),
            "trace_id": secrets.token_hex(16), "span_id": secrets.token_hex(8)}


# ───────────────────────────── spool + send ─────────────────────────────

def _spool(env: dict | None = None) -> Path:
    return config_dir(env) / "telemetry.spool.jsonl"


def _append(events: list[dict], c: dict, env: dict | None = None) -> None:
    f = _spool(env)
    f.parent.mkdir(parents=True, exist_ok=True)
    with f.open("a", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
    lines = f.read_text(encoding="utf-8").splitlines()
    cap = int((c.get("transport") or {}).get("spool_max", 200))
    if len(lines) > cap:
        f.write_text("\n".join(lines[-cap:]) + "\n", encoding="utf-8")


def _send(c: dict, resource: dict, env: dict | None = None, post=None) -> None:
    """Take the spool (atomic rename: two concurrent processes never send it twice), post it, put it
    back on failure. Any error is swallowed — telemetry never breaks a command."""
    f = _spool(env)
    taken = f.with_name(f"{f.name}.{os.getpid()}.{secrets.token_hex(3)}")
    try:
        os.replace(f, taken)
    except OSError:
        return
    try:
        events = [json.loads(l) for l in taken.read_text(encoding="utf-8").splitlines() if l.strip()]
        if not events:
            taken.unlink(missing_ok=True)
            return
        if validate(c, resource, events):
            taken.unlink(missing_ok=True)       # never sent, never kept: a bug, not a retry
            return
        body = json.dumps(otlp(resource, events)).encode()
        (post or _post)(c, body)
        taken.unlink(missing_ok=True)
    except Exception:
        try:
            with f.open("a", encoding="utf-8") as fh:
                fh.write(taken.read_text(encoding="utf-8"))
            taken.unlink(missing_ok=True)
        except Exception:
            pass


def _post(c: dict, body: bytes) -> None:
    t = c.get("transport") or {}
    req = urllib.request.Request(t["endpoint"], data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=float(t.get("timeout_s", 3))) as r:
        if r.status >= 300:
            raise RuntimeError(r.status)


# ───────────────────────────── the entry the CLI calls ─────────────────────────────

def record(argv: list[str], exit_code: int, ms: float, vocab, version: str,
           env: dict | None = None, stream=None, post=None) -> str:
    """Called once per CLI process, AFTER the command. Returns what happened (for tests):
    off:<reason> · notice · spooled. `vocab` is a callable (the grammar is loaded only when on)."""
    env = dict(os.environ) if env is None else env
    stream = sys.stderr if stream is None else stream
    try:
        c = contract()
        state = load_state(env)
        why = off_reason(c, env, state)
        if why:
            return f"off:{why}"
        state, new = identity(state, c, time.time())
        if not state.get("notice_shown"):
            print(c["notice"], file=stream)
            state["notice_shown"] = time.time()
            save_state(state, env)
            return "notice"
        events = []
        if not state.get("install_sent"):
            events.append(event("install", {}))
            state["install_sent"] = True
        if new:
            save_state(state, env)
        attrs = describe(argv, vocab() if callable(vocab) else vocab)
        attrs.update({"cli.exit_code": int(exit_code), "cli.duration_ms": int(ms)})
        events.append(event("command", attrs, ms))
        _append(events, c, env)
        save_state(state, env)
        global _thread
        res = resource_attrs(c, state["install_id"], version)
        _thread = threading.Thread(target=_send, args=(c, res, env, post), daemon=True)
        _thread.start()
        _thread.join(float((c.get("transport") or {}).get("exit_wait_s", 0.2)))
        return "spooled"
    except Exception as e:  # telemetry never breaks a command
        return f"error:{type(e).__name__}"


def switch(cmd: str, env: dict | None = None) -> str:
    env = dict(os.environ) if env is None else env
    state = load_state(env)
    if cmd in ("on", "off"):
        state["enabled"] = cmd == "on"
        if cmd == "off":
            _spool(env).unlink(missing_ok=True)   # what was waiting is dropped, not sent later
        save_state(state, env)
    why = off_reason(contract(), env, state)
    return (f"telemetry: OFF — {why}" if why else
            f"telemetry: ON — anonymous id {state.get('install_id', '(drawn on first run)')[:8]}…, "
            f"config {_state_file(env)}")


# ───────────────────────────── collector config (DERIVED) ─────────────────────────────

def collector_config(c: dict) -> str:
    """The OpenTelemetry Collector config, DERIVED from TLM: the same allowlist is re-applied on the
    server (`keep_keys`), so a modified client cannot widen what is stored."""
    res = sorted(c.get("resource") or {})
    span = sorted({k for e in (c.get("events") or {}).values() for k in ((e or {}).get("attributes") or {})})
    col = c.get("collector") or {}
    if not isinstance(col.get("retention_days"), int):
        # No default: a retention period is a LEGAL value (LEX), never a fallback written here.
        raise ValueError("collector.retention_days is not declared — the collector config is generated at home only")
    days = col["retention_days"]
    q = lambda ks: "[" + ", ".join(f'"{k}"' for k in ks) + "]"
    return f"""# GENERATED from telemetry.dsl.yaml (TLM) by `telemetry.py --collector-config` — do not edit.
receivers:
  otlp:
    protocols:
      http:
        endpoint: 0.0.0.0:4318          # inside the container; published on 127.0.0.1 only
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 150
  transform/allowlist:
    error_mode: ignore
    trace_statements:
      - context: resource
        statements:
          - 'keep_keys(attributes, {q(res)})'
      - context: span
        statements:
          - 'keep_keys(attributes, {q(span)})'
  batch: {{}}
exporters:
  file:
    path: /data/spans.jsonl
    rotation:
      max_megabytes: 50
      max_days: {days}
      max_backups: 200
service:
  telemetry:
    logs:
      level: warn
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, transform/allowlist, batch]
      exporters: [file]
"""


# ───────────────────────────── selftest ─────────────────────────────

def selftest() -> int:
    import tempfile
    ko = 0

    def ok(label: str, cond: bool) -> None:
        nonlocal ko
        ko += not cond
        print(f"{'OK ' if cond else 'FAIL'} {label}")

    base = dict(contract())
    on = {**base, "enabled": True}
    home = _has_lex_ref(_RAW)          # the workspace source (LEX references) — not a shipped package
    ok("contract TLM has no grievance", not check(base, _RAW if home else None))
    if home:
        ok("the workspace contract is OFF", off_reason(base, {}, {}) is not None)
        ok("the legal durations resolve through LEX",
           all(isinstance(v, int) for v in (base["identity"]["rotate_days"], base["collector"]["retention_days"])))
        ok("MUST-FAIL a legal duration written in clear is a grievance",
           bool(check(base, {**_RAW, "identity": {**_RAW["identity"], "rotate_days": 395}})))
    else:
        ok("the shipped contract is ON and carries resolved durations",
           base.get("enabled") is True and isinstance(base["identity"]["rotate_days"], int))
    ok("MUST-FAIL a contract declaring host.name is refused",
       bool(check({**on, "resource": {**on["resource"], "host.name": "x"}})))
    ok("MUST-FAIL a hardware identity is refused", bool(check({**on, "identity": {"kind": "mac_sha256"}})))
    ok("MUST-FAIL an IP-literal endpoint is refused (frozen domain)",
       any("IP literal" in g for g in check({**on, "transport": {**on["transport"],
                                                                 "endpoint": "https://203.0.113.9/otel/v1/traces"}})))
    ok("DO_NOT_TRACK=1 → off", off_reason(on, {"DO_NOT_TRACK": "1"}, {}) is not None)
    ok("STILHAWT_TELEMETRY=0 → off", off_reason(on, {"STILHAWT_TELEMETRY": "0"}, {}) is not None)
    ok("CI → off", off_reason(on, {"CI": "true"}, {}) is not None)
    ok("user switch off → off", off_reason(on, {}, {"enabled": False}) is not None)
    ok("nothing set → on", off_reason(on, {}, {}) is None)

    vocab = {"namespaces": {"fs": ["read", "list"], "dsl": ["read"]}, "pipes": ["where", "groq", "head"]}
    secret = ["fs", "read", "D:\\work\\acme\\secret.txt", "|", "groq", "my private prompt", "|", "head", "3"]
    d = describe(secret, vocab)
    ok("grammar words survive", d["cli.namespace"] == "fs" and d["cli.command"] == "read"
       and d["cli.pipes"] == "groq,head")
    ok("MUST-FAIL arguments never leave (acme / private / secret absent)",
       not any(w in json.dumps(d) for w in ("acme", "private", "secret", "3")))
    ok("unknown words → <other>", describe(["rm", "-rf", "/"], vocab)["cli.namespace"] == OTHER
       and describe(["fs", "nuke"], vocab)["cli.command"] == OTHER
       and describe(["fs", "read", "x", "|", "evil"], vocab)["cli.pipes"] == OTHER)
    ok("a leading pipe is a pipe", describe(["where", "a", "eq", "1"], vocab)["cli.pipes"] == "where")
    ok("no args → shell", describe([], vocab)["cli.mode"] == "shell")

    res = resource_attrs(on, "ab" * 16, "stilhawt-cli 0.3.0")
    ev = event("command", {**d, "cli.exit_code": 0, "cli.duration_ms": 12}, 12)
    ok("a declared payload validates", not validate(on, res, [ev]))
    ok("MUST-FAIL a payload with host.name is refused", bool(validate(on, {**res, "host.name": "pc"}, [ev])))
    ok("MUST-FAIL an event with an undeclared attribute is refused",
       bool(validate(on, res, [event("command", {"path": "/srv/acme/notes"})])))
    p = otlp(res, [ev])
    sp = p["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    ok("OTLP ids are hex (16 / 8 bytes)", len(sp["traceId"]) == 32 and len(sp["spanId"]) == 16
       and bytes.fromhex(sp["traceId"]) and bytes.fromhex(sp["spanId"]))

    # The whole flow, in a temporary config dir, with a fake transport.
    with tempfile.TemporaryDirectory() as tmp:
        env = {"APPDATA": tmp, "XDG_CONFIG_HOME": tmp}
        global _CONTRACT
        saved, _CONTRACT = _CONTRACT, on
        sent: list[bytes] = []
        try:
            import io
            out = io.StringIO()
            r1 = record(secret, 0, 5, vocab, "v", env=env, stream=out, post=lambda c, b: sent.append(b))
            ok("first run → notice, nothing sent", r1 == "notice" and "STILHAWT_TELEMETRY=0" in out.getvalue()
               and not sent and not _spool(env).exists())
            r2 = record(secret, 0, 5, vocab, "v", env=env, stream=out, post=lambda c, b: sent.append(b))
            if _thread:
                _thread.join(2)
            body = b"".join(sent).decode()
            ok("second run → install + command sent", r2 == "spooled" and '"install"' in body and '"command"' in body)
            ok("MUST-FAIL the sent body holds no argument", not any(w in body for w in ("acme", "private", "secret.txt")))
            ok("the spool is emptied after a send", not _spool(env).exists())
            r3 = record(secret, 0, 5, vocab, "v", env={**env, "DO_NOT_TRACK": "1"}, stream=out,
                        post=lambda c, b: sent.append(b))
            ok("DO_NOT_TRACK → nothing recorded", r3.startswith("off:"))
            state = load_state(env)
            ok("the identity is a random uuid, not hardware", len(state["install_id"]) == 32
               and state["install_id"] != uuid.getnode())
            old = {**state, "id_created": time.time() - 400 * 86400}
            ok("identity renewed after 395 days", identity(old, on, time.time())[0]["install_id"] != state["install_id"])
            ok("a broken transport keeps the event spooled",
               record(["fs", "list"], 0, 1, vocab, "v", env=env, stream=out,
                      post=lambda c, b: (_ for _ in ()).throw(OSError("offline"))) == "spooled"
               and (_thread.join(2) or True) and _spool(env).exists())
        finally:
            _CONTRACT = saved
    if home:
        cfg = collector_config(base)
        ok("collector re-applies the allowlist", "keep_keys" in cfg and "stilhawt.install_id" in cfg
           and "host.name" not in cfg)
        try:
            collector_config({**base, "collector": {}}); bad = False
        except ValueError:
            bad = True
        ok("MUST-FAIL no retention declared → no collector config (no legal default)", bad)
    print("SELFTEST", "GREEN" if not ko else f"RED ({ko})")
    return 1 if ko else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    arg = sys.argv[1] if len(sys.argv) > 1 else "status"
    if arg == "--selftest":
        sys.exit(selftest())
    if arg == "--check":
        g = check(contract(), _RAW)
        print("\n".join(g) or "TLM: 0 grievance")
        sys.exit(1 if g else 0)
    if arg == "--collector-config":
        print(collector_config(contract()), end="")
        sys.exit(0)
    if arg in ("status", "on", "off"):
        print(switch(arg))
        sys.exit(0)
    print("usage: python -m <package>.telemetry status | on | off", file=sys.stderr)
    sys.exit(2)
