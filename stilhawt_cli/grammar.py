"""stilhawt_cli.grammar — the LINE LANGUAGE of the CLI: its grammar file, lexer, parser and static
gate. PURE: nothing here runs a command, calls a model or opens a page (contract SBR, sub-brick
`cli.grammar`).

Why a brick of its own. `mandat`, `snippets` and `cli.tool` need the grammar and the way a line is
read — not the engine that executes it. Until 2026-09-27 they loaded the whole engine to get them,
which closed three cycles (engine → mandat → engine, …). Here they read the language alone; the
engine imports it too, and re-exports every name, so `stilhawt_cli.parse_line` still works.

Two facts of the engine are stated here as DATA, and the engine's selftest checks they match its
own tables: the names of its pure pipes (`PIPE_NAMES` = the keys of `engine.PIPES`) and the AI pipe
modes (`MODES` = the keys of `engine.TRANSPORTS`). A name the engine adds without adding it here
fails that check — the language never silently ignores a pipe.
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The pure pipes the engine implements — names only (the functions stay in the engine).
PIPE_NAMES = frozenset({"where", "grep", "select", "sort", "join", "replace", "lines", "head", "count",
                        "flatten", "extract", "group", "view", "diff", "open", "notify",
                        "sum", "avg", "min", "max"})
# The AI pipe modes the engine has transports for.
MODES = frozenset({"decide", "generate"})


GRAMMAR_FILE = HERE / "commandes.dsl.yaml"


class Refusal(ValueError):
    """The grammar, or a call, is not acceptable. Exit code 2."""


def load(path: Path | None = None) -> dict:
    import yaml
    f = path or GRAMMAR_FILE
    if not f.is_file():
        raise Refusal(f"grammar not found: {f}")
    return yaml.safe_load(f.read_text(encoding="utf-8"))


def ai_spec(doc: dict | None, name: str) -> dict | None:
    """The declared AI pipe `name`, or None. An AI pipe is a pipe entry carrying a known `mode`."""
    spec = ((doc or {}).get("pipes") or {}).get(name) or {}
    return spec if spec.get("mode") in MODES else None


def ai_fields(doc: dict | None, mode: str | None = None) -> set[str]:
    """Names of the fields AI pipes add (= their names), read from the DSL — so a renderer in
    ANOTHER process, reading JSON Lines, knows them too. PURE."""
    return {n for n, p in ((doc or {}).get("pipes") or {}).items()
            if (p or {}).get("mode") in MODES and (mode is None or p["mode"] == mode)}


def pipe_aliases(doc: dict | None) -> dict[str, str]:
    """ALIASES declared in the contract (`filter` → `where`, `wc` → `count`)."""
    return {a: name for name, p in ((doc or {}).get("pipes") or {}).items() for a in (p.get("alias") or [])}


def is_pipe(doc: dict | None, name: str) -> bool:
    return name in PIPE_NAMES or name in STRUCTURAL or name in pipe_aliases(doc) or ai_spec(doc, name) is not None


STRUCTURAL = {"tee", "map", "each"}   # pipes that are grammar, not a function over objects


MULTIPLYING = {"map", "each"}          # they repeat a sub-pipeline: a declared bound is mandatory


def split_line(line: str) -> list[str]:
    """A shell line → tokens, QUOTE-AWARE for `|`, `(` and `)`. PURE.

    `shlex.split` glued a closing `)` to a quoted phrase (`"what is it?")` → one token), and after
    that nothing can tell it from a parenthesis inside the phrase. With `punctuation_chars`, the
    three separators are cut only OUTSIDE quotes — measured 2026-09-25.
    """
    import shlex
    lx = shlex.shlex(line, posix=True, punctuation_chars="()|")
    lx.whitespace_split = True
    return list(lx)


def _is_group(t: str) -> bool:
    """`(…)` whose opening parenthesis closes at the LAST character, outside quotes. PURE.
    `(a) (b)` is not one group; `"(x)"` inside quotes does not count."""
    if not (t.startswith("(") and t.endswith(")")):
        return False
    depth, quote = 0, None
    for i, ch in enumerate(t):
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(t) - 1:
                return False
    return depth == 0 and quote is None


_STAGE_WORD = re.compile(r"[a-z@][a-z0-9_-]*(\s|\)|$)")   # a command, a pipe or a snippet — never a regex


def lex(tokens: list[str]) -> list[str]:
    """Tokens → lexemes, with `|`, `(` and `)` separated. PURE.

    A token that contains whitespace was QUOTED (a question, a phrase): it is a literal, never
    split — `groq "what is it (one word)?"` keeps its parentheses. A bare token is split on `|`
    and loses a leading `(` / trailing `)`: `tee (where a gt 1|count)` works glued.
    """
    out: list[str] = []
    for t in tokens:
        if any(ch.isspace() for ch in t):
            # PowerShell passes `"(where x gt 0)"` as ONE argument: a token WHOLLY wrapped in one
            # balanced pair of parentheses is a branch, re-split quote-aware. Anything else with
            # a space stays a literal phrase (2026-09-27).
            # ... but only when the group OPENS with a stage word (a lowercase name, `@snippet`): a
            # quoted REGEX is wholly a group too — `"(?P<w>\w+ \w+)"` was re-split, lost its
            # backslashes and matched nothing (found by the publication parser reading the code's
            # own examples, 2026-09-29).
            if _is_group(t.strip()) and _STAGE_WORD.match(t.strip()[1:].lstrip()):
                out += lex(split_line(t.strip()))
            else:
                out.append(t)
            continue
        # An edge parenthesis is STRUCTURAL only when the word is unbalanced on that side, or is
        # wholly one group; `|` cuts only at depth 0. A regex argument keeps its own parentheses and
        # bars: `"^(?P<first>\w+)"` and `"^(feat|fix)/"` lost a `)` or were cut in two until the
        # publication parser found it (2026-09-29).
        head, t, tail = _peel(t)
        pieces, depth, cur = [], 0, ""
        for ch in t:
            depth += (ch == "(") - (ch == ")")
            if ch == "|" and depth == 0:
                pieces.append(cur)
                cur = ""
            else:
                cur += ch
        pieces.append(cur)
        body: list[str] = []
        for i, piece in enumerate(pieces):
            if i:
                body.append("|")
            h, p, tl = _peel(piece)
            body += h + ([p] if p else []) + tl
        out += head + body + tail
    return out


def _peel(t: str) -> tuple[list[str], str, list[str]]:
    """The STRUCTURAL parentheses at the edges of a bare word: a side that is unbalanced, or the
    whole word being one group `(…)`. Balanced inner parentheses (a regex) stay. PURE."""
    head: list[str] = []
    tail: list[str] = []
    while t.startswith("(") and (t.count("(") > t.count(")") or _is_group(t)):
        grouped = _is_group(t)
        head.append("(")
        t = t[1:]
        if grouped:
            tail.insert(0, ")")
            t = t[:-1]
    while t.endswith(")") and t.count(")") > t.count("("):
        tail.insert(0, ")")
        t = t[:-1]
    return head, t, tail


def parse_line(tokens: list[str]) -> list[dict]:
    """Tokens → the tree (a list of nodes). PURE. Refusal on a malformed line."""
    lx = lex(tokens)
    pos = 0

    def group(name: str, top: bool) -> list[dict]:
        nonlocal pos
        if pos >= len(lx) or lx[pos] != "(":
            raise Refusal(f"`{name}` needs a group in parentheses — `{name} (…)`")
        pos += 1
        body = [] if pos < len(lx) and lx[pos] == ")" else pipeline(top)
        if pos >= len(lx) or lx[pos] != ")":
            raise Refusal(f"a `(` of `{name}` is not closed")
        pos += 1
        return body

    def pipeline(top: bool) -> list[dict]:
        nonlocal pos
        nodes: list[dict] = []
        while True:
            if pos < len(lx) and lx[pos] == "map":
                pos += 1
                nodes.append({"op": "map", "branch": group("map", False)})
            elif pos < len(lx) and lx[pos] == "each":
                pos += 1
                if pos >= len(lx) or lx[pos] in ("(", ")", "|"):
                    raise Refusal("`each` needs the key whose value fills `{}` — `each name (fs search {})`")
                key = lx[pos]
                pos += 1
                body = group("each", True)
                if not body or body[0]["op"] != "command":
                    raise Refusal("`each` runs a COMMAND per object — `each name (fs search {})`")
                nodes.append({"op": "each", "key": key, "branch": body})
            elif pos < len(lx) and lx[pos] == "tee":
                pos += 1
                branches = []
                while pos < len(lx) and lx[pos] == "(":
                    pos += 1
                    branches.append([] if pos < len(lx) and lx[pos] == ")" else pipeline(False))
                    if pos >= len(lx) or lx[pos] != ")":
                        raise Refusal("a `(` of `tee` is not closed")
                    pos += 1
                if not branches:
                    raise Refusal("`tee` needs at least one branch: `tee (…) (…)` — `()` passes objects through")
                nodes.append({"op": "tee", "branches": branches})
            else:
                words = []
                while pos < len(lx) and lx[pos] not in ("|", "(", ")"):
                    words.append(lx[pos])
                    pos += 1
                if pos < len(lx) and lx[pos] == "(":
                    raise Refusal("`(` only opens a `tee` branch — quote it if it is part of a value")
                if not words:
                    raise Refusal("an empty stage: something is expected between two `|`")
                nodes.append({"op": "command" if top and not nodes else "pipe", "tokens": words})
            if pos < len(lx) and lx[pos] == "|":
                pos += 1
                continue
            return nodes

    if not lx:
        return []
    tree = pipeline(True)
    if pos != len(lx):
        raise Refusal(f"unexpected `{lx[pos]}` — a `)` without its `tee (`")
    return tree


def mark_sources(doc: dict | None, tree: list[dict]) -> list[dict]:
    """A `tee` branch whose first word is a NAMESPACE of the grammar (and not a pipe) is a SOURCE:
    it runs its own command and ignores the objects it receives. The parser does not know the
    grammar, this pass does: `tee (git status .) (fs files .)` runs two commands side by
    side and merges them. Returns the same tree, its source heads turned into `command` nodes."""
    spaces = (doc or {}).get("namespaces") or {}

    def walk(nodes: list[dict]) -> None:
        for n in nodes:
            if n["op"] == "tee":
                for b in n["branches"]:
                    head = b[0] if b else None
                    if head and head["op"] == "pipe" and head["tokens"][0] in spaces \
                            and not is_pipe(doc, head["tokens"][0]):
                        b[0] = {"op": "command", "tokens": head["tokens"]}
                    walk(b)
            elif n["op"] in MULTIPLYING:
                walk(n["branch"])
    walk(tree)
    return tree


def _pipe_name(doc: dict | None, tokens: list[str]) -> str:
    return pipe_aliases(doc).get(tokens[0], tokens[0])


def tree_grievances(doc: dict, tree: list[dict]) -> list[str]:
    """Everything the tree would be refused for — found BEFORE anything runs. PURE.

    A command may only open the top-level line or an `each` group. Inside `tee` and `map` every
    stage is a pipe: a branch transforms the objects it receives, it does not fetch new ones.
    `map` and `each` repeat a sub-pipeline, so they need a declared bound; `each` repeats READ
    commands only, and its command must use `{}` (otherwise it runs the SAME command n times).
    """
    g: list[str] = []
    allowed = set(doc.get("allowed_effects") or [])
    pipes = doc.get("pipes") or {}
    spaces = doc.get("namespaces") or {}

    def bounded(name: str, at: str) -> None:
        b = (pipes.get(name) or {}).get("max_objects")
        if not isinstance(b, int) or b < 1:
            g.append(f"stage {at} `{name}`: repeats a sub-pipeline without a declared `max_objects` bound")

    def walk(nodes: list[dict], where: str, start: int = 0) -> None:
        for i, n in enumerate(nodes):
            at = f"{where}{i + 1 + start}"
            op = n["op"]
            if op == "tee":
                for b, branch in enumerate(n["branches"]):
                    if branch and branch[0]["op"] == "command":
                        # A SOURCE branch: READ commands only — a branch runs beside the others,
                        # nothing acting may hide in it.
                        cmd = branch[0]["tokens"]
                        c = ((spaces.get(cmd[0]) or {}).get("commands") or {}).get(cmd[1] if len(cmd) > 1 else "")
                        if c is None:
                            g.append(f"stage {at}.{b + 1}.1 `{' '.join(cmd)}`: not a command of the grammar")
                        elif c.get("effect") != "read" or "read" not in allowed:
                            g.append(f"stage {at}.{b + 1}.1 `{' '.join(cmd[:2])}`: a `tee` branch runs READ "
                                     f"commands only (effect `{c.get('effect')}`)")
                        walk(branch[1:], f"{at}.{b + 1}.", start=1)
                    else:
                        walk(branch, f"{at}.{b + 1}.")
            elif op == "map":
                bounded("map", at)
                walk(n["branch"], f"{at}.1.")
            elif op == "each":
                bounded("each", at)
                cmd = n["branch"][0]["tokens"]
                c = ((spaces.get(cmd[0]) or {}).get("commands") or {}).get(cmd[1] if len(cmd) > 1 else "")
                if c is None:
                    g.append(f"stage {at}.1.1 `{' '.join(cmd)}`: not a command of the grammar")
                elif c.get("effect") != "read" or c.get("effect") not in allowed:
                    g.append(f"stage {at}.1.1 `{' '.join(cmd[:2])}`: `each` repeats READ commands only "
                             f"(effect `{c.get('effect')}`)")
                if not any("{}" in t for t in cmd[2:]):
                    g.append(f"stage {at}.1.1: `each {n['key']}` without `{{}}` would run the SAME command n times")
                walk(n["branch"][1:], f"{at}.1.", start=1)
            elif op == "command":
                continue
            else:
                name = _pipe_name(doc, n["tokens"])
                if name not in PIPE_NAMES and ai_spec(doc, name) is None:
                    if name in spaces:
                        g.append(f"stage {at} `{' '.join(n['tokens'])}`: a command after a `|` — a pipe "
                                 f"transforms the objects it receives (a command per object is `each`, "
                                 f"two commands side by side is `tee ({name} …) (…)`)")
                    else:
                        known = sorted(set(PIPE_NAMES) | ai_fields(doc) | set(pipes))
                        g.append(f"stage {at} `{name}`: not a pipe — pipes: {', '.join(known)} "
                                 f"(a page of any result: `view`, it picks the layout from the data)")
                    continue
                effect = (pipes.get(name) or {}).get("effect")
                if effect not in allowed:
                    g.append(f"stage {at} `{name}`: effect `{effect}` not allowed (allowed_effects = {sorted(allowed)})")

    walk(tree, "")
    return g


def tree_segments(tree: list[dict]) -> list[list[str]]:
    """The head first, then every stage of the tree, groups included, as token lists. PURE —
    what the mandate check reads: a model pipe hidden in a branch is still a model pipe, and the
    command of an `each` must be granted like the head."""
    out: list[list[str]] = []

    def walk(nodes):
        for n in nodes:
            if n["op"] == "tee":
                out.append(["tee"])
                for b in n["branches"]:
                    walk(b)
            elif n["op"] in MULTIPLYING:
                out.append([n["op"]])
                walk(n["branch"])
            else:
                out.append(n["tokens"])
    walk(tree)
    return out


AI_OPTIONS = {
    "decide": [("--options a,b,c", "the choices (at least two) — the model CHOOSES, it writes no text"),
               ("--on k1,k2", "the fields SENT per object (default: the whole object minus `_private` keys and what an "
                              "earlier AI pipe added — so after `groq`, judge its text with `--on groq`)"),
               ("--pan texte|code|…", "the nature of what is sent — sets ANO's anonymisation rank; inherited in a line"),
               ("--max N", "at most N objects (the pipe's declared bound)")],
    "generate": [("--all", "ONE call on the whole set (one object back) instead of one call per object"),
                 ("--on k1,k2", "the fields SENT per object (same rule as for a decider)"),
                 ("--pan texte|code|…", "the nature of what is sent — sets ANO's anonymisation rank"),
                 ("--max N", "at most N objects (the pipe's declared bound)")],
}
