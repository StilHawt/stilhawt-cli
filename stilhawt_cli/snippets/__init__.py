"""The snippet library of the agents (contract SNP, `snippets.dsl.yaml` in this package).

A snippet is a NAMED stilhawt CLI line for a recurring use case. Claude runs `stilhawt @<id>` (the
CLI expands it — see `expand`), every run is recorded in `data/snippets/usage.jsonl` with its
conversation (`CLAUDE_CODE_SESSION_ID`), and the judge hook (`delegues_juge`) lets Jev SUGGEST one
per prompt among the active ones (`options`). Nothing here executes on a suggestion.

    python -m stilhawt_cli.snippets --check      # lint the library (bounds of SNP)
    python -m stilhawt_cli.snippets --list       # JSON Lines: id, intent, line, uses (for `stilhawt snip list`)
    python -m stilhawt_cli.snippets --uses [--session S] [--since ISO]   # JSON Lines of recorded uses
    python -m stilhawt_cli.snippets --harvest    # candidates from the transcripts (lines run often, not yet named)
    python -m stilhawt_cli.snippets --selftest
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
LIBRARY = HERE / "snippets.dsl.yaml"
ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
MAX_ACTIVE = 20                          # they are the closed options Jev chooses among
NONE_OPTION = "aucun — aucun snippet ne correspond à ce prompt"
STATUSES = {"active", "retired"}
FIELDS = {"intent", "line", "origin", "status", "params"}
PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")


def check_value(name: str, v: str) -> str:
    """A parameter value is DATA: never a flag, never a control character, never empty. PURE."""
    if not v or v.startswith("-") or any(c in v for c in "\r\n\0") or len(v) > 300:
        raise Refusal(f"parameter `{name}`: value {v[:40]!r} refused (empty, starts with `-`, control character or > 300 chars)")
    return v


class Refusal(ValueError):
    """The library, or a use of it, is not acceptable."""


def load(path: Path | None = None) -> dict:
    import yaml
    return yaml.safe_load((path or LIBRARY).read_text(encoding="utf-8")) or {}


def active(doc: dict) -> dict:
    return {k: v for k, v in (doc.get("snippets") or {}).items() if (v or {}).get("status") == "active"}


# ── the bounds ───────────────────────────────────────────────────────────────────────────────────

def grievances(doc: dict, cli_doc: dict | None = None) -> list[str]:
    """Everything that makes the library unacceptable. PURE given `cli_doc`."""
    from stilhawt_cli import grammar as cli   # the line language alone, never the engine (SBR)
    cd = cli_doc if cli_doc is not None else cli.load()
    spaces = cd.get("namespaces") or {}
    g: list[str] = []
    snippets = doc.get("snippets") or {}
    if len(active(doc)) > MAX_ACTIVE:
        g.append(f"{len(active(doc))} active snippets > {MAX_ACTIVE}: they are Jev's closed options — retire some")
    for sid, s in snippets.items():
        at = f"snippet '{sid}'"
        s = s or {}
        if not ID.match(sid):
            g.append(f"{at}: id must be lowercase letters, digits and `-` (2-31 chars)")
        if sid in spaces or cli.is_pipe(cd, sid):
            g.append(f"{at}: id shadows a namespace or a pipe of the CLI")
        if set(s) - FIELDS:
            g.append(f"{at}: unknown field(s) {sorted(set(s) - FIELDS)}")
        if s.get("status") not in STATUSES:
            g.append(f"{at}: status '{s.get('status')}' not in {sorted(STATUSES)}")
        if not str(s.get("intent") or "").strip():
            g.append(f"{at}: no `intent` — Jev chooses on it")
        if not (s.get("origin") or {}).get("source"):
            g.append(f"{at}: no `origin.source` — a snippet traces back to real usage")
        line = str(s.get("line") or "")
        params = s.get("params") or []
        used = set(PLACEHOLDER.findall(line))
        if not isinstance(params, list) or any(not isinstance(p, str) or not re.match(r"^[a-z][a-z0-9_]*$", p) for p in params):
            g.append(f"{at}: `params` is a list of lowercase names")
        elif used != set(params):
            g.append(f"{at}: placeholders {sorted(used)} and declared params {sorted(params)} differ — each `{{name}}` is declared, each param is used")
        if "@" in line.split("|")[0] or re.search(r"(^|[\s|(])@", line):
            g.append(f"{at}: a snippet cannot call a snippet (the expansion stays one step)")
            continue
        try:
            tree = cli.parse_line(cli.split_line(line))
        except cli.Refusal as e:
            g.append(f"{at}: its line does not parse — {e}")
            continue
        # The parser does not know the grammar: it labels any first stage `command`. Whether it IS
        # one (and not a pipe like `count`) is the grammar's call.
        if not tree or tree[0]["op"] != "command" or cli.is_pipe(cd, tree[0]["tokens"][0]):
            g.append(f"{at}: a snippet starts with a command (it fetches its own objects)")
            continue
        g += [f"{at}: {x}" for x in cli.tree_grievances(cd, tree)]
        head = tree[0]["tokens"]
        c = ((spaces.get(head[0]) or {}).get("commands") or {}).get(head[1] if len(head) > 1 else "") or {}
        if c.get("effect") not in ("read", "network"):
            g.append(f"{at}: its command `{' '.join(head[:2])}` has effect `{c.get('effect')}` — a snippet READS")
        for seg in cli.tree_segments(tree)[1:]:
            name = cli.pipe_aliases(cd).get(seg[0], seg[0])
            if cli.ai_spec(cd, name):
                g.append(f"{at}: model pipe `{name}` — a snippet never sends data to a model")
            elif ((cd.get("pipes") or {}).get(name) or {}).get("effect") not in (None, "read"):
                g.append(f"{at}: pipe `{name}` is not read-only — a snippet never displays nor records")
    return g


# ── what Jev chooses among, and what the CLI expands ────────────────────────────────────────────

def options(doc: dict) -> dict[str, str]:
    """The closed options of the judge's 3rd question: `aucun` + one per ACTIVE snippet. PURE."""
    def label(sid, s):
        params = "".join(f" <{p}>" for p in (s.get("params") or []))
        return f"@{sid}{params} — {s['intent']}"
    return {"aucun": NONE_OPTION, **{sid: label(sid, s) for sid, s in active(doc).items()}}


def expand(tokens: list[str], doc: dict) -> tuple[list[str], str | None]:
    """`@loc | head 3` → (the snippet's line tokens + the rest, 'loc'). ONE step. PURE.
    A token that does not start with `@` leaves the line untouched: (tokens, None)."""
    from stilhawt_cli.grammar import split_line
    if not tokens or not tokens[0].startswith("@"):
        return tokens, None
    sid = tokens[0][1:]
    snippets = active(doc)
    if sid not in snippets:
        raise Refusal(f"unknown or retired snippet `@{sid}` — active: {', '.join('@' + k for k in sorted(snippets))}")
    params = snippets[sid].get("params") or []
    # The values are the words between `@id` and the first `|`: EXACTLY one per parameter —
    # a missing value never falls back to "anything", an extra one is never silently dropped.
    cut = tokens.index("|") if "|" in tokens else len(tokens)
    values, rest = tokens[1:cut], tokens[cut:]
    if len(values) != len(params):
        want = " ".join(f"<{p}>" for p in params) or "no value"
        raise Refusal(f"`@{sid}` takes {len(params)} value(s): @{sid} {want} — got {len(values)}")
    bound = {p: check_value(p, v) for p, v in zip(params, values)}
    line = [PLACEHOLDER.sub(lambda m: bound[m.group(1)], t) for t in split_line(snippets[sid]["line"])]
    return line + rest, sid


# ── the record of uses ──────────────────────────────────────────────────────────────────────────

def ledger() -> Path:
    """Where uses are recorded: the CLI's data dir (an extension point, cli.ext). The extensions the
    grammar declares are loaded FIRST — a hook reading this without them would fall back on the
    default place and lose the workspace's record."""
    from stilhawt_cli import ext
    from stilhawt_cli import grammar as cli
    ext.load_extensions(cli.load().get("extensions"))
    return ext.data_path("snippets") / "usage.jsonl"


def record_use(sid: str, rows: int, ok: bool, session: str | None = None, path: Path | None = None) -> dict:
    """Append one use. The conversation is read from `CLAUDE_CODE_SESSION_ID` (set by Claude Code
    in every Bash call): the use is joined to its conversation by KEY, never guessed from a time."""
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "id": sid, "rows": rows, "ok": ok,
             "session": session or os.environ.get("CLAUDE_CODE_SESSION_ID")}
    p = path or ledger()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def uses(session: str | None = None, since: str | None = None, path: Path | None = None) -> list[dict]:
    p = path or ledger()
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if session and e.get("session") != session:
            continue
        if since and str(e.get("ts")) <= since:
            continue
        out.append(e)
    return out


def listing(doc: dict, path: Path | None = None) -> list[dict]:
    counts = Counter(e["id"] for e in uses(path=path))
    return [{"id": sid, "status": s.get("status"), "intent": s.get("intent"), "line": s.get("line"),
             "uses": counts.get(sid, 0), "source": (s.get("origin") or {}).get("source")}
            for sid, s in (doc.get("snippets") or {}).items()]


# ── harvest: candidates from the history of the conversations ───────────────────────────────────

_SEGMENTS = re.compile(r";|&&|\|\||\n")
# The CLI's module name as IMPORTED here (the workspace's package, or the public one): derived, never
# typed — a public build must recognise `python -m <its own module>`.
import stilhawt_cli.grammar as _grammar  # noqa: E402
_CLI_MODULE = re.escape(_grammar.__name__.rpartition(".")[0])
_CLI_HEAD = re.compile(r"^\s*(?:cd\s+\S+\s*)?(?:stilhawt|python(?:\.exe)?\s+(?:-X\s+\S+\s+)?-m\s+" + _CLI_MODULE
                       + r")\s+(?!--selftest|--check)(.+)$")
_TAIL_NOISE = re.compile(r"\s--json\b|\s--text\b|\s2>&1.*$|\s>\s*\S.*$"
                         r"|\s\|\s*(?:tail|head\s+-|grep|python|wc|cut|sed|sort\s+-|xargs)\b.*$")


_SHELL_SCRIPT = re.compile(r"""printf\s+(['"])(?P<body>.*?)\1\s*\|\s*(?:stilhawt|python(?:\.exe)?\s+-m\s+"""
                           + _CLI_MODULE + r""")\s+shell""", re.S)


def cli_lines_from_command(command: str) -> list[str]:
    """The stilhawt CLI lines inside ONE Bash command, normalised (quotes around `|` dropped,
    `--json` removed, spaces collapsed). PURE. Two forms: a direct call, and a SCRIPT fed to the
    shell (`printf 'l1\\nl2\\n' | … shell`) — the second is where most real lines were run."""
    out = []
    for m in _SHELL_SCRIPT.finditer(command):
        for line in m.group("body").split("\\n"):
            line = " ".join(line.split())
            if line and not line.startswith(("-", "@", "help", "explain", "json", "profile", "quit")):
                out.append(line)
    # Direct calls: one per SEGMENT of the shell command (`;`, `&&`, `||`, newline), so a chained
    # `; echo …` never sticks to the line. A segment counts only if it STARTS with the CLI.
    for seg in _SEGMENTS.split(_SHELL_SCRIPT.sub("", command)):
        hit = _CLI_HEAD.match(seg)
        if not hit:
            continue
        line = hit.group(1).replace("'|'", "|").replace('"|"', "|")
        line = re.sub(r"\|\s*stilhawt\s+", "| ", line)          # an OS pipe into the CLI = the same line
        line = _TAIL_NOISE.sub("", line)
        line = " ".join(line.split())
        if line and not line.startswith(("-", "@", "help", "shell")):
            out.append(line)
    return out


def is_valid_line(line: str, cli_doc: dict) -> bool:
    """A harvested line is kept only if it is a line of TODAY's grammar: it opens with a real
    `<namespace> <verb>` and passes the static gate. This is what drops prose that merely contains
    the word `stilhawt` and the French names of the v0 grammar (`statut`, `contrats`…). PURE."""
    from stilhawt_cli import grammar as cli
    try:
        tokens = cli.split_line(line)
        tree = cli.parse_line(tokens)
    except (cli.Refusal, ValueError):
        return False
    spaces = cli_doc.get("namespaces") or {}
    head = tree[0]["tokens"] if tree and tree[0]["op"] == "command" else []
    if len(head) < 2 or head[1] not in ((spaces.get(head[0]) or {}).get("commands") or {}):
        return False
    return not cli.tree_grievances(cli_doc, tree)


def harvest(transcripts: Path, minimum: int = 3, doc: dict | None = None) -> list[dict]:
    """Lines Claude ran through the CLI at least `minimum` times, in how many conversations, and
    whether a snippet already names them. The history decides what deserves a name — not a guess."""
    from stilhawt_cli import grammar as cli
    cd = cli.load()
    named ={" ".join(str(s.get("line")).split()) for s in ((doc or {}).get("snippets") or {}).values()}
    count: Counter = Counter()
    convs: dict[str, set] = defaultdict(set)
    for f in transcripts.glob("*.jsonl"):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "stilhawt" not in text:
            continue
        for raw in text.splitlines():
            if '"tool_use"' not in raw or "stilhawt" not in raw:
                continue
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            for item in (d.get("message") or {}).get("content") or []:
                if isinstance(item, dict) and item.get("type") == "tool_use" and item.get("name") in ("Bash", "PowerShell"):
                    for line in cli_lines_from_command(str((item.get("input") or {}).get("command") or "")):
                        if not is_valid_line(line, cd):
                            continue
                        count[line] += 1
                        convs[line].add(f.stem)
    return [{"line": l, "runs": n, "conversations": len(convs[l]), "named": l in named}
            for l, n in count.most_common() if n >= minimum]


# ── selftest ────────────────────────────────────────────────────────────────────────────────────

def _selftest() -> int:
    import copy
    import tempfile
    from stilhawt_cli import grammar as cli
    failures, cases = [], 0

    def check(label, got, want=True):
        nonlocal cases
        cases += 1
        if got != want:
            failures.append(f"  [FAIL] {label}: {got!r} != {want!r}")

    # The unit cases run against the grammar being RUN plus two FIXTURE namespaces (`git status`,
    # `gov who`), so they hold in the workspace and in a public install alike. The real library is
    # checked against the real grammar at the end (positive control).
    real_cd = cli.load()
    cd = copy.deepcopy(real_cd)
    fx = {"description": "fixture", "home": "fixture.py", "export": "local"}
    cd.setdefault("namespaces", {}).setdefault("git", {**fx, "commands": {}})["commands"].setdefault(
        "status", {"description": "fixture", "effect": "read", "pan": "texte", "kind": "command",
                   "command": ["fixture"], "output": ["repo", "modified"]})
    cd["namespaces"].setdefault("gov", {**fx, "commands": {}})["commands"].setdefault(
        "who", {"description": "fixture", "effect": "read", "pan": "texte", "kind": "command",
                "command": ["fixture"], "output": ["file", "conv"]})
    good = {"snippets": {"dirty": {"intent": "dépôts modifiés", "line": "git status | where modified gt 0",
                                   "origin": {"source": "conversations"}, "status": "active"}}}
    check("a well-formed library raises no grievance", grievances(good, cd), [])

    def with_(**patch):
        d = copy.deepcopy(good)
        d["snippets"]["dirty"].update(patch)
        return d
    check("MUST-FAIL a model pipe in a snippet",
          any("model pipe" in x for x in grievances(with_(line='git status | groq "why?"'), cd)))
    check("MUST-FAIL a display stage in a snippet",
          any("not read-only" in x for x in grievances(with_(line="git status | view"), cd)))
    check("MUST-FAIL a snippet calling a snippet", any("cannot call a snippet" in x for x in grievances(with_(line="@dirty | count"), cd)))
    check("MUST-FAIL a line that does not parse", any("does not parse" in x for x in grievances(with_(line="git status | tee (count"), cd)))
    check("MUST-FAIL a snippet that starts with a pipe", any("starts with a command" in x for x in grievances(with_(line="count"), cd)))
    check("MUST-FAIL no intent (Jev chooses on it)", any("no `intent`" in x for x in grievances(with_(intent=""), cd)))
    check("MUST-FAIL no origin (traces back to usage)", any("origin.source" in x for x in grievances(with_(origin={}), cd)))
    shadow = {"snippets": {"git": good["snippets"]["dirty"]}}
    check("MUST-FAIL an id that shadows a namespace", any("shadows" in x for x in grievances(shadow, cd)))
    many = {"snippets": {f"s{i:02d}x": good["snippets"]["dirty"] for i in range(MAX_ACTIVE + 1)}}
    check("MUST-FAIL more active snippets than Jev's option bound", any("active snippets" in x for x in grievances(many, cd)))

    check("options: `aucun` first, then one per active snippet",
          list(options(good)), ["aucun", "dirty"])
    retired = with_(status="retired")
    check("a retired snippet is no longer an option", list(options(retired)), ["aucun"])
    check("expand: the line, then the rest of the tokens",
          expand(["@dirty", "|", "count"], good),
          (["git", "status", "|", "where", "modified", "gt", "0", "|", "count"], "dirty"))
    check("a line without @ is untouched", expand(["git", "status"], good), (["git", "status"], None))
    try:
        expand(["@ghost"], good)
        check("MUST-FAIL an unknown snippet is refused", False)
    except Refusal as e:
        check("MUST-FAIL an unknown snippet is refused, with the active ids", "@dirty" in str(e))

    # --- parameters ---
    who = {"snippets": {"who": {"intent": "qui a modifié ce fichier", "line": "gov who {file}", "params": ["file"],
                                "origin": {"source": "lcl"}, "status": "active"}}}
    check("a parameterised snippet passes its lint", grievances(who, cd), [])
    check("its value takes the placeholder's place, then the rest of the line",
          expand(["@who", "a/b.py", "|", "count"], who), (["gov", "who", "a/b.py", "|", "count"], "who"))
    check("the option tells Jev the parameter", options(who)["who"].startswith("@who <file> — "))
    for label_, toks, frag in (("MUST-FAIL a missing value", ["@who"], "takes 1 value"),
                               ("MUST-FAIL an extra value", ["@who", "a", "b"], "takes 1 value"),
                               ("MUST-FAIL a value that could pass for a flag", ["@who", "--all"], "starts with `-`")):
        try:
            expand(toks, who)
            check(label_, False)
        except Refusal as e:
            check(label_, frag in str(e))
    bad_p = copy.deepcopy(who)
    bad_p["snippets"]["who"]["params"] = ["path"]
    check("MUST-FAIL a placeholder not declared (or a param not used)",
          any("differ" in x for x in grievances(bad_p, cd)))

    with tempfile.TemporaryDirectory() as tmp:
        led = Path(tmp) / "usage.jsonl"
        record_use("dirty", 43, True, session="conv-a", path=led)
        record_use("dirty", 0, True, session="conv-b", path=led)
        check("uses are joined to their conversation by KEY", [e["session"] for e in uses("conv-a", path=led)], ["conv-a"])
        check("the listing counts every use", listing(good, path=led)[0]["uses"], 2)

    cmd = "cd /work && python -m stilhawt_cli git status '|' where modified gt 0 '|' count --json 2>&1 | tail -3"
    check("harvest reads a CLI line out of a Bash command, normalised",
          cli_lines_from_command(cmd), ["git status | where modified gt 0 | count"])
    check("MUST-FAIL a selftest run is not a use case", cli_lines_from_command("python -m stilhawt_cli --selftest"), [])
    script = "cd /work && printf 'git status | group branch\\nexplain git status | count\\n' | python -m stilhawt_cli shell 2>&1 | tail -4"
    check("harvest reads the lines of a SCRIPT fed to the shell (explain is not a use case)",
          cli_lines_from_command(script), ["git status | group branch"])
    chained = "stilhawt ai quota; stilhawt git status | stilhawt where modified gt 100 && echo done"
    check("chained commands give one line per segment, OS pipes into the CLI merged",
          cli_lines_from_command(chained), ["ai quota", "git status | where modified gt 100"])
    check("a line of today's grammar is valid", is_valid_line("git status | where modified gt 0", cd), True)
    check("MUST-FAIL a v0 French name is not a line of today's grammar", is_valid_line("git statut | ou modifies sup 0", cd), False)
    check("MUST-FAIL prose containing the CLI's name is not a line", is_valid_line("<espace> <verbe> [--json]. Grammaire", cd), False)
    check("the real library passes its lint (against the REAL grammar, no fixture)", grievances(load(), real_cd), [])

    for f in failures:
        print(f)
    print(f"{cases - len(failures)}/{cases} selftests passed")
    return 1 if failures else 0


def _tools():
    """The CLI-facing verbs, declared in the Toolset format (cli/tool.py) — CLI-PLAN H11."""
    from stilhawt_cli.tool import Arg, Toolset
    ts = Toolset("stilhawt_cli.snippets")
    ts.verb("list", does="every snippet — its intent, its line, how many times it was run",
            output=["id", "status", "intent", "line", "uses", "source"],
            types={"id": "str", "status": "str", "intent": "str", "line": "str", "uses": "int", "source": "str"})(
        lambda: listing(load()))
    ts.verb("uses", does="every recorded run of a snippet — when, which conversation, how many rows, ok or refused",
            output=["ts", "id", "ok"], types={"ts": "str", "id": "str", "ok": "bool"},
            args=[Arg("--session"), Arg("--since")])(
        lambda session, since: list(uses(session, since)))
    ts.selftest(_selftest)
    return ts


tools = _tools()


def main(argv: list[str]) -> int:
    if argv and (argv[0] in tools.verbs or argv[0] in ("--describe", "-h", "--help")):
        return tools.main(argv)
    if "--selftest" in argv:
        return _selftest()
    doc = load()
    if "--check" in argv:
        g = grievances(doc)
        for x in g:
            print(x)
        print(f"{len(doc.get('snippets') or {})} snippet(s), {len(active(doc))} active · {len(g)} grievance(s)")
        return 1 if g else 0
    if "--list" in argv:
        for row in listing(doc):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    if "--uses" in argv:
        s = argv[argv.index("--session") + 1] if "--session" in argv else None
        t = argv[argv.index("--since") + 1] if "--since" in argv else None
        for e in uses(s, t):
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if "--harvest" in argv:
        tr = Path(os.path.expanduser("~")) / ".claude" / "projects" / "C--dev"
        for row in harvest(tr, doc=doc):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
