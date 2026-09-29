"""stilhawt_cli.tool — the INTERFACE between a CLI command and the tool that answers it.

The need, as the user put it on 2026-09-27: « vu le nb potentiel de commandes, il faut un générateur
"mapping" : commande <-> tool avec une interface associée, plutôt qu'une liste de if ». Before this, every home module
ended in its own `main()` with a chain of `if verb == …` — one more command, one more branch, and
nothing checked that the grammar and the module still agreed.

Now a module declares its verbs ONCE:

    from stilhawt_cli.tool import Toolset, Arg, ToolRefusal
    tools = Toolset("mypkg.keys")

    @tools.verb("resolve", output=["kind", "key", "title", "where"],
                args=[Arg("key", rest=True, required=True)])
    def resolve(key): ...           # returns a list of dicts

    if __name__ == "__main__":
        sys.exit(tools.main())

and the grammar maps a command to it in one field — `kind: tool`, `tool: mypkg.keys:resolve`.
The mapping is checked BOTH ways by `stilhawt --check` (`mapping_grievances`): the verb exists, the
command's `output` keys are keys the verb promises, the effect is the same on both sides. And
`python -m stilhawt_cli.tool generate <module> <namespace>` writes the grammar entries of the
verbs no command maps yet: the list is GENERATED from the tools, not typed twice.

Contract of a verb, enforced here and nowhere else:
  · it returns a LIST of dicts; each object carries every key of `output` (else exit 4, not a crash);
  · a refusal is a `ToolRefusal` (message on stderr, exit 3) — never a guessed default;
  · `refuse_if(rows)` marks a VERDICT that is a refusal (exit 1: the rows still carry the answer,
    the command lists 1 in `ok_codes`);
  · arguments are DECLARED (`Arg`): an unknown option is refused, never ignored.
"""
from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The top package this module lives in (the workspace SDK, or the public package) — from
# `__package__`, since run with `-m` the module's own name is `__main__`.
_PACKAGE = (__package__ or __name__).split(".")[0]
_PACKAGE_TOOL = f"{__package__}.tool" if __package__ else __name__    # this module, importable by a home
from typing import Callable


class ToolRefusal(ValueError):
    """The call is not acceptable — said on stderr, exit 3. Subclass it for a domain refusal."""


@dataclass(frozen=True)
class Arg:
    name: str                     # "device" (positional) or "--port" (option / flag)
    required: bool = False
    flag: bool = False            # "--apply": present or not
    rest: bool = False            # positional that swallows the remaining words ("find some text")
    help: str = ""

    @property
    def dest(self) -> str:
        return self.name.lstrip("-").replace("-", "_")

    @property
    def positional(self) -> bool:
        return not self.name.startswith("--")


@dataclass
class Verb:
    name: str
    fn: Callable
    output: list[str]
    effect: str = "read"
    args: list[Arg] = field(default_factory=list)
    refuse_if: Callable | None = None
    doc: str = ""
    types: dict = field(default_factory=dict)


# The output FORMAT vocabulary (closed): a verb may type any of its output keys with one of these,
# and every row is checked against it. `any` is the explicit "not typed" — silence is not a type.
TYPES = {"str": (str,), "int": (int,), "float": (int, float), "bool": (bool,), "list": (list, tuple),
         "dict": (dict,), "any": (object,)}
UNTYPED = "untyped"      # what `--describe` says of an output key with no declared type


class Toolset:
    def __init__(self, module: str):
        self.module = module
        self.verbs: dict[str, Verb] = {}
        self._selftest: Callable[[], int] | None = None

    def verb(self, name: str, *, output: list[str], effect: str = "read", args=(),
             refuse_if: Callable | None = None, does: str | None = None, types: dict | None = None):
        """Declare a verb. Its SCHEMA is mandatory: what it does (`does`, else the first line of the
        docstring — a verb that says nothing is refused), its input (`args`), its output keys and,
        optionally, their types (closed vocabulary TYPES; a key typed outside `output` is refused)."""
        def deco(fn):
            if name in self.verbs:
                raise ValueError(f"verb '{name}' declared twice in {self.module}")
            what = does or ((fn.__doc__ or "").strip().splitlines() or [""])[0]
            if not what:
                raise ValueError(f"verb '{name}' in {self.module} does not say what it does (`does=` or a docstring)")
            t = dict(types or {})
            bad = {k: v for k, v in t.items() if k not in output or v not in TYPES}
            if bad:
                raise ValueError(f"verb '{name}': types {bad} — keys must be in output, types in {sorted(TYPES)}")
            self.verbs[name] = Verb(name, fn, list(output), effect, list(args), refuse_if, what, t)
            return fn
        return deco

    def selftest(self, fn: Callable[[], int]):
        self._selftest = fn
        return fn

    def describe(self) -> list[dict]:
        return [{"verb": v.name, "does": v.doc, "effect": v.effect,
                 "input": [{"name": a.name, "required": a.required, "flag": a.flag, "rest": a.rest,
                            "help": a.help} for a in v.args],
                 # `untyped`: nothing declared — distinct from a DECLARED `any` (CLI-PLAN H13).
                 "output": {k: v.types.get(k, UNTYPED) for k in v.output}} for v in self.verbs.values()]

    # ── parsing: DECLARED arguments only ──────────────────────────────────────────────────────
    @staticmethod
    def parse(v: Verb, argv: list[str]) -> dict:
        opts = {a.name: a for a in v.args if not a.positional}
        pos = [a for a in v.args if a.positional]
        kw: dict = {a.dest: (False if a.flag else None) for a in v.args}
        words: list[str] = []
        i = 0
        while i < len(argv):
            w = argv[i]
            if w.startswith("--"):
                a = opts.get(w)
                if a is None:
                    raise ToolRefusal(f"unknown option {w} for '{v.name}' — options: {', '.join(opts) or 'none'}")
                if a.flag:
                    kw[a.dest] = True
                    i += 1
                    continue
                if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                    raise ToolRefusal(f"{w} needs a value")
                kw[a.dest] = argv[i + 1]
                i += 2
                continue
            words.append(w)
            i += 1
        for n, a in enumerate(pos):
            if a.rest:
                kw[a.dest] = " ".join(words[n:]) or None
                words = words[:n]
                break
            kw[a.dest] = words[n] if n < len(words) else None
        extra = words[len(pos):] if not any(a.rest for a in pos) else []
        if extra:
            raise ToolRefusal(f"'{v.name}' takes {len(pos)} positional argument(s), got extra {extra}")
        for a in v.args:
            if a.required and kw.get(a.dest) in (None, ""):
                raise ToolRefusal(f"'{v.name}' needs {a.name}")
        return kw

    def run(self, verb: str, argv: list[str]) -> tuple[list[dict], int]:
        v = self.verbs.get(verb)
        if v is None:
            raise ToolRefusal(f"unknown verb '{verb}' in {self.module} — verbs: {', '.join(self.verbs)}")
        rows = v.fn(**self.parse(v, argv))
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise TypeError(f"verb '{verb}' must return a list of dicts")
        for r in rows:
            missing = [k for k in v.output if k not in r]
            if missing:
                raise KeyError(f"verb '{verb}' promised {v.output}, an object lacks {missing}")
            for k, t in v.types.items():
                if r[k] is not None and not isinstance(r[k], TYPES[t]):
                    raise TypeError(f"verb '{verb}': '{k}' promised {t}, got {type(r[k]).__name__}")
        return rows, (1 if v.refuse_if and v.refuse_if(rows) else 0)

    def main(self, argv: list[str] | None = None) -> int:
        argv = list(sys.argv[1:] if argv is None else argv)
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
        if not argv or argv[0] in ("-h", "--help"):
            print(f"{self.module} — verbs:")
            for d in self.describe():
                args = " ".join((a["name"] if not a["required"] else f"<{a['name']}>") for a in d["input"])
                print(f"  {d['verb']:<16} {args:<32} → {', '.join(d['output'])}  [{d['effect']}]")
                print(f"  {'':<16} {d['does']}")
            return 0
        if argv[0] == "--selftest":
            if self._selftest is None:
                print("no selftest declared", file=sys.stderr)
                return 1
            return self._selftest()
        if argv[0] == "--describe":
            print(json.dumps(self.describe(), ensure_ascii=False))
            return 0
        try:
            rows, code = self.run(argv[0], argv[1:])
        except ToolRefusal as e:
            print(str(e), file=sys.stderr)
            return 3
        except (KeyError, TypeError) as e:
            print(f"contract broken: {e}", file=sys.stderr)
            return 4
        for r in rows:
            print(json.dumps(r, ensure_ascii=False, default=str))
        return code


# ── the mapping, checked both ways, and generated ─────────────────────────────────────────────
WORKSPACE = Path(os.environ.get("STILHAWT_WORKSPACE") or Path(__file__).resolve().parents[3])


def is_file_tool(module: str) -> bool:
    """`governance/depots_commit.py` names a tool by its FILE: a home of the workspace outside the
    SDK package (another project, a governance script). `mypkg.keys` names it by module."""
    return module.endswith(".py")


def home_argv(path: str) -> list[str]:
    """How a file tool is reached: its own `--tool` entry, so the home's other paths (hooks, other
    callers) never load the SDK — the Toolset is built only when the CLI asks for it."""
    return [str(WORKSPACE / path), "--tool"]


def _described(path: str) -> Toolset:
    """The Toolset of a FILE tool, known by its own `--tool --describe` in a SEPARATE process:
    reading a home's schema never imports the home (nor its dependencies) into the caller."""
    if not (WORKSPACE / path).is_file():
        raise ToolRefusal(f"home {path} does not exist")
    r = subprocess.run([sys.executable, *home_argv(path), "--describe"], cwd=str(WORKSPACE),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0:
        raise ToolRefusal(f"home {path} does not describe itself (`--tool --describe` → {r.returncode}): "
                          f"{(r.stderr or r.stdout).strip()[:200]}")
    ts = Toolset(path)
    for d in json.loads(r.stdout):
        args = [Arg(a["name"], a["required"], a["flag"], a["rest"], a.get("help", "")) for a in d["input"]]
        types = {k: t for k, t in d["output"].items() if t != UNTYPED}
        ts.verbs[d["verb"]] = Verb(d["verb"], None, list(d["output"]), d["effect"], args, None, d["does"], types)
    return ts


def load_toolset(module: str) -> Toolset:
    if is_file_tool(module):
        return _described(module)
    mod = importlib.import_module(module)
    ts = getattr(mod, "tools", None)
    # Checked by SHAPE, not by class identity: run as `python -m stilhawt_cli.tool`, this module
    # is `__main__` and its Toolset class is not the one the tools imported — isinstance would say no.
    if not (type(ts).__name__ == "Toolset" and isinstance(getattr(ts, "verbs", None), dict)):
        raise ToolRefusal(f"{module} exposes no `tools` Toolset")
    return ts


def argv_of(tool: str) -> list[str]:
    """`mypkg.keys:resolve` → the argv the engine runs (a separate process: timeout, crash
    isolation and the CREATE_NO_WINDOW rule stay the engine's)."""
    module, _, verb = tool.partition(":")
    if not module or not verb:
        raise ToolRefusal(f"`tool:` must be 'module:verb' or 'path/file.py:verb', got {tool!r}")
    if is_file_tool(module):
        return ["python", *home_argv(module), verb]
    return ["python", "-m", module, verb]


def mapping_grievances(doc: dict) -> list[str]:
    """Every `kind: tool` command against the verb it names: exists, output ⊆ promised, same effect."""
    g, cache = [], {}
    for ns, n in (doc.get("namespaces") or {}).items():
        for cmd, c in (n.get("commands") or {}).items():
            if c.get("kind") != "tool":
                continue
            where = f"command '{ns} {cmd}' → {c.get('tool')}"
            module, _, verb = str(c.get("tool") or "").partition(":")
            try:
                ts = cache.get(module) or load_toolset(module)
                cache[module] = ts
            except Exception as e:  # noqa: BLE001
                g.append(f"{where}: module not loadable ({e})")
                continue
            v = ts.verbs.get(verb)
            if v is None:
                g.append(f"{where}: no verb '{verb}' (verbs: {', '.join(ts.verbs)})")
                continue
            extra = [k for k in c.get("output") or [] if k not in v.output]
            if extra:
                g.append(f"{where}: output {extra} not promised by the verb (it promises {v.output})")
            if v.effect != c.get("effect"):
                g.append(f"{where}: effect '{c.get('effect')}' here, '{v.effect}' in the tool")
    return g


def generate(module: str, namespace: str, doc: dict) -> str:
    """Grammar entries (YAML) for the verbs of `module` that NO command maps yet."""
    ts = load_toolset(module)
    mapped = {str(c.get("tool")) for n in (doc.get("namespaces") or {}).values()
              for c in (n.get("commands") or {}).values() if c.get("kind") == "tool"}
    out = [f"  # generated from {module} — review the description and the display columns"]
    for v in ts.verbs.values():
        key = f"{module}:{v.name}"
        if key in mapped:
            continue
        name = v.name.replace("-", "_")
        out += [f"      {name}:",
                f"        description: {json.dumps(v.doc or v.name, ensure_ascii=False)}",
                f"        effect: {v.effect}", "        pan: texte", "        kind: tool",
                f"        tool: {key}", f"        output: [{', '.join(v.output[:4])}]",
                "        display:", f"          columns: [{', '.join(v.output)}]"]
    return "\n".join(out) if len(out) > 1 else f"  # every verb of {module} is already mapped"


def discover(roots: list[Path] | None = None, doc: dict | None = None, describe=None) -> list[dict]:
    """Every verb of every Toolset found under `roots` — FOUND by its format (a `Toolset("<module>")`
    in the source), DESCRIBED by the module itself (`python -m <module> --describe`, a separate
    process: discovering a tool never runs it in the CLI's own process), and joined to the grammar
    command that maps it, or `unmapped`. Roots: the SDK package, plus STILHAWT_TOOL_ROOTS (;-separated)."""
    import os
    import re
    import subprocess
    here = Path(__file__).resolve().parents[1]           # the directory holding the CLI's package
    roots = roots or [here, *[Path(r) for r in os.environ.get("STILHAWT_TOOL_ROOTS", "").split(";") if r]]
    rx = re.compile(r"""Toolset\(\s*["']([\w.]+)["']""")
    modules: dict[str, Path] = {}
    for root in roots:
        for f in sorted(Path(root).rglob("*.py")):
            if "__pycache__" in f.parts or f.resolve() == Path(__file__).resolve():
                continue
            try:
                m = rx.search(f.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if m:
                modules.setdefault(m.group(1), f)
    if doc is None:
        from stilhawt_cli import grammar as _cli
        doc = _cli.load()
    mapped: dict[str, str] = {}           # several commands may map one verb: all are named
    for ns, n in (doc.get("namespaces") or {}).items():
        for cmd, c in (n.get("commands") or {}).items():
            if c.get("kind") == "tool":
                k = str(c.get("tool"))
                mapped[k] = f"{mapped[k]}, {ns} {cmd}" if k in mapped else f"{ns} {cmd}"

    # File tools (homes outside the SDK) are found through the grammar that maps them.
    for k in mapped:
        m = k.partition(":")[0]
        if is_file_tool(m):
            modules.setdefault(m, WORKSPACE / m)

    def _describe(module: str):
        head = home_argv(module) if is_file_tool(module) else ["-m", module]
        r = subprocess.run([sys.executable, *head, "--describe"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return json.loads(r.stdout) if r.returncode == 0 else None
    describe = describe or _describe
    rows = []
    for module in modules:
        try:
            verbs = describe(module)
        except Exception:  # noqa: BLE001
            verbs = None
        if verbs is None:
            rows.append({"module": module, "verb": None, "does": "--describe failed: not a conforming tool",
                         "effect": None, "input": [], "output": {}, "mapped_to": None})
            continue
        for v in verbs:
            rows.append({"module": module, "verb": v["verb"], "does": v["does"], "effect": v["effect"],
                         "input": [a["name"] for a in v["input"]], "output": v["output"],
                         "mapped_to": mapped.get(f"{module}:{v['verb']}", "unmapped")})
    return rows


def _selftest() -> int:
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"✗ {name}")

    ts = Toolset("fake")

    @ts.verb("find", output=["k"], args=[Arg("text", rest=True, required=True), Arg("--max")])
    def _find(text, max):  # noqa: A002
        """echoes the text"""
        return [{"k": text, "max": max}]

    @ts.verb("act", output=["step"], effect="deploy", args=[Arg("target"), Arg("--apply", flag=True)],
             refuse_if=lambda rows: any(r["step"] == "refuse" for r in rows))
    def _act(target, apply):
        """plans or acts"""
        return [{"step": "refuse" if not target else ("done" if apply else "plan")}]

    @ts.verb("broken", output=["a", "b"])
    def _broken():
        """promises b, returns no b"""
        return [{"a": 1}]

    @ts.verb("typed", output=["n"], types={"n": "int"}, does="returns a string where an int is promised")
    def _typed():
        return [{"n": "3"}]

    rows, code = ts.run("find", ["two", "words", "--max", "3"])
    check("rest positional + option", rows == [{"k": "two words", "max": "3"}] and code == 0)
    check("flag", ts.run("act", ["x", "--apply"])[0][0]["step"] == "done")
    check("refuse_if → exit 1, rows kept", ts.run("act", [])[1] == 1)
    for name, argv in (("unknown option refused", ["act", "x", "--force"]),
                       ("missing required refused", ["find"]),
                       ("extra positional refused", ["act", "x", "y"]),
                       ("unknown verb refused", ["nope"]),
                       ("option without value refused", ["find", "t", "--max"])):
        try:
            ts.run(argv[0], argv[1:])
            got = "accepted"
        except ToolRefusal:
            got = "refused"
        check(name, got == "refused")
    try:
        ts.run("broken", [])
        got = "accepted"
    except KeyError:
        got = "caught"
    check("an object missing a promised key breaks the contract", got == "caught")
    try:
        ts.run("typed", [])
        got = "accepted"
    except TypeError:
        got = "caught"
    check("a value of the wrong promised type breaks the contract", got == "caught")
    for label, kw in (("a verb that says nothing is refused", {}),
                      ("a type on a key outside output is refused", {"does": "x", "types": {"zz": "int"}}),
                      ("a type outside the vocabulary is refused", {"does": "x", "types": {"k": "date"}})):
        try:
            Toolset("t2").verb("v", output=["k"], **kw)(lambda: [])
            got = "accepted"
        except ValueError:
            got = "refused"
        check(label, got == "refused")
    check("describe carries does, input, typed output",
          [d for d in ts.describe() if d["verb"] == "typed"][0]["output"] == {"n": "int"})
    try:
        ts.verb("find", output=["k"], does="dup")(lambda: [])
        dup = "accepted"
    except ValueError:
        dup = "refused"
    check("a verb declared twice is refused", dup == "refused")
    sys.modules["fake_tools_mod"] = type(sys)("fake_tools_mod")
    sys.modules["fake_tools_mod"].tools = ts
    doc = {"namespaces": {"x": {"commands": {
        "ok": {"kind": "tool", "tool": "fake_tools_mod:find", "output": ["k"], "effect": "read"},
        "bad_out": {"kind": "tool", "tool": "fake_tools_mod:find", "output": ["zz"], "effect": "read"},
        "bad_eff": {"kind": "tool", "tool": "fake_tools_mod:act", "output": ["step"], "effect": "read"},
        "no_verb": {"kind": "tool", "tool": "fake_tools_mod:gone", "output": ["k"], "effect": "read"}}}}}
    g = mapping_grievances(doc)
    check("mapping: good command silent, 3 bad ones caught",
          len(g) == 3 and not any("'x ok'" in x for x in g))
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        (Path(t) / "a.py").write_text('tools = Toolset("fake_tools_mod")\n', encoding="utf-8")
        (Path(t) / "b.py").write_text('x = 1  # no toolset here\n', encoding="utf-8")
        (Path(t) / "c.py").write_text('tools = Toolset("broken_mod")\n', encoding="utf-8")
        found = discover([Path(t)], doc, describe=lambda m: ts.describe() if m == "fake_tools_mod" else None)
        by = {(r["module"], r["verb"]): r for r in found}
        check("discover: found by format, joined to its mapping",
              by[("fake_tools_mod", "find")]["mapped_to"] == "x ok, x bad_out" and by[("fake_tools_mod", "broken")]["mapped_to"] == "unmapped")
        check("discover: a module that cannot describe itself is a ROW, not silence",
              by[("broken_mod", None)]["does"].startswith("--describe failed"))
        check("discover: a file without a Toolset is not a tool", len({r["module"] for r in found}) == 2)
    gen = generate("fake_tools_mod", "x", doc)
    check("generator writes only the unmapped verbs", "fake_tools_mod:broken" in gen and "fake_tools_mod:find" not in gen)
    # FILE tools (H12): a home outside the package, reached by its own `--tool` entry. The home is
    # FABRICATED in a scratch workspace: the test proves the mechanism wherever the package is installed.
    global WORKSPACE
    saved_ws = WORKSPACE
    with tempfile.TemporaryDirectory() as ws:
        WORKSPACE = Path(ws)
        (WORKSPACE / "gov").mkdir()
        (WORKSPACE / "gov" / "search.py").write_text(
            "import sys\n"
            f"from {_PACKAGE_TOOL} import Arg, Toolset\n"
            "tools = Toolset('gov/search.py')\n"
            "@tools.verb('search', output=['file', 'line'], types={'file': 'str', 'line': 'int'},\n"
            "            args=[Arg('pattern'), Arg('--regex', flag=True)])\n"
            "def _search(pattern, regex):\n"
            "    'a pattern in text files'\n"
            "    return []\n"
            "if sys.argv[1:2] == ['--tool']:\n"
            "    sys.exit(tools.main(sys.argv[2:]))\n", encoding="utf-8")
        try:
            check("a file tool runs through the home's `--tool` entry",
                  argv_of("gov/search.py:search") == ["python", str(WORKSPACE / "gov/search.py"), "--tool", "search"])
            check("a module tool keeps `-m`", argv_of("mypkg.keys:resolve")[:2] == ["python", "-m"])
            home = load_toolset("gov/search.py")
            check("a file tool's schema is read by its own --describe (typed, options kept)",
                  (home.verbs["search"].types.get("line"), [a.name for a in home.verbs["search"].args][:2]) == ("int", ["pattern", "--regex"]))
            try:
                load_toolset("gov/no_such_home.py")
                missing = "loaded"
            except ToolRefusal:
                missing = "refused"
            check("MUST-FAIL a file tool whose home does not exist is refused", missing == "refused")
        finally:
            WORKSPACE = saved_ws
    print(f"{ok}/{total} selftests passed")
    return 0 if ok == total else 1


def grammar_rows(doc: dict | None = None) -> list[dict]:
    """The CLI's own grammar AS DATA — one row per command and per pipe, with its effect, its
    options (read at the source: the Toolset for a `kind: tool` command, the declared option set
    for a model pipe) and its description. `help` prints the grammar for a human; this streams it,
    so `tools commands | view graph namespace name` draws the CLI itself."""
    if doc is None:
        from stilhawt_cli import grammar as _cli   # the grammar alone, never the engine (SBR)
        doc = _cli.load()
    from stilhawt_cli.grammar import AI_OPTIONS
    rows = []
    cache: dict = {}
    for ns, n in (doc.get("namespaces") or {}).items():
        for cmd, c in (n.get("commands") or {}).items():
            opts: list[str] = []
            if c.get("kind") == "tool":
                module, _, verb = str(c.get("tool")).partition(":")
                try:
                    ts = cache.get(module) or load_toolset(module)
                    cache[module] = ts
                    opts = [a.name if not a.positional else f"<{a.dest}>" for a in ts.verbs[verb].args]
                except Exception:  # noqa: BLE001 — a tool that does not load is said, not hidden
                    opts = ["(tool not loadable)"]
            rows.append({"namespace": ns, "name": cmd, "kind": "command", "effect": c.get("effect"),
                         "options": opts, "description": " ".join(str(c.get("description", "")).split())[:200]})
    for name, p in (doc.get("pipes") or {}).items():
        opts = [o.split()[0] for o, _ in AI_OPTIONS.get(p.get("mode"), [])] if p.get("mode") else []
        rows.append({"namespace": "pipes (model)" if p.get("mode") else "pipes", "name": name, "kind": "pipe",
                     "effect": p.get("effect"), "options": opts,
                     "description": " ".join(str(p.get("description", "")).split())[:200]})
    return rows


# The tool layer is itself a conforming tool: `stilhawt tools list` is one of its verbs.
tools = Toolset("stilhawt_cli.tool")
tools.selftest(_selftest)
tools.verb("list", does="every conforming tool found in the code, its verbs, and the command mapping each (or unmapped)",
           output=["module", "verb", "does", "effect", "input", "output", "mapped_to"],
           types={"module": "str", "verb": "str", "does": "str", "effect": "str", "input": "list", "output": "dict",
                  "mapped_to": "str"})(lambda: discover())
tools.verb("commands", does="the CLI's own grammar as data: one row per command and pipe, its effect, options and description",
           output=["namespace", "name", "kind", "effect", "options", "description"],
           types={"namespace": "str", "name": "str", "kind": "str", "effect": "str", "options": "list",
                  "description": "str"})(lambda: grammar_rows())

def conformance(doc: dict) -> tuple[int, list[str]]:
    """(number of commands NOT mapped to a conforming tool, their names) — the proof of CLI-PLAN H11."""
    left = [f"{ns} {cmd}" for ns, n in (doc.get("namespaces") or {}).items()
            for cmd, c in (n.get("commands") or {}).items() if c.get("kind") == "command"]
    return len(left), left


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["--conformance"]:              # one line, read by a PLAN proof
        from stilhawt_cli import grammar as _cli
        d = _cli.load()
        n, left = conformance(d)
        # ADAPTED is said apart: their schema lives in the package's `homes` module, not yet in their home.
        homes = f"{_PACKAGE}.homes"
        adapted = [f"{ns} {cmd}" for ns, nn in (d.get("namespaces") or {}).items()
                   for cmd, c in (nn.get("commands") or {}).items() if str(c.get("tool", "")).startswith(homes + ":")]
        # `missing=N adapted=N` with a delimiter after the number: a PLAN proof reads a substring, and
        # "10 ADAPTED" contains "0 ADAPTED" — the first format made an open step look done (27/09).
        # H13: output keys with NO declared type, over every tool the grammar maps (each loaded once).
        untyped: list[str] = []
        seen: set[str] = set()
        for nn in (d.get("namespaces") or {}).values():
            for c in (nn.get("commands") or {}).values():
                t = str(c.get("tool") or "")
                if c.get("kind") != "tool" or t in seen:
                    continue
                seen.add(t)
                module, _, verb = t.partition(":")
                try:
                    v = load_toolset(module).verbs[verb]
                except Exception:  # noqa: BLE001 — a tool that does not load is counted as untyped, not hidden
                    untyped.append(f"{t} (not loadable)")
                    continue
                untyped += [f"{t}.{k}" for k in v.output if k not in v.types]
        print(f"conformance: missing={n} adapted={len(adapted)} untyped={len(untyped)} ;"
              + (f" not conforming: {', '.join(left)} ;" if left else "")
              + (f" adapted (schema in {homes}, not in their home): {', '.join(adapted)}" if adapted else ""))
        sys.exit(0)
    if a[:1] == ["generate"] and len(a) == 3:   # prints YAML for a human to review: not a row-verb
        from stilhawt_cli import grammar as _cli
        print(generate(a[1], a[2], _cli.load()))
        sys.exit(0)
    sys.exit(tools.main())
