"""stilhawt_cli.ext — the EXTENSION POINTS of the CLI engine: how a model is reached (transports)
and what guards what leaves (the egress guard). A registry only: PURE, no model, no network
(contract SBR, sub-brick `cli.ext`).

Why. Until 2026-09-27 the engine imported its transports from a script outside the package and
reached its anonymiser by pointing `sys.path` at another repository: the CLI's core could not leave
the workspace. Now the core only knows this registry; the grammar NAMES the modules that fill it
(`extensions: [...]` in commandes.dsl.yaml), and the engine imports them by name. Each installation
registers its own transports, guard and data place in its own extension module.

    from stilhawt_cli import ext

    @ext.transport("generate", "my.model")
    def ask(prompt: str) -> dict: ...          # {"reponse": text} or {"refus": why}

    @ext.egress_guard
    def guard(text: str, pan: str, destination: str) -> str: ...   # the text to send, or raise

FAIL-CLOSED (Rule no. 1). With no guard registered, nothing goes to a model: the default guard
refuses and says how to install one. A second guard is refused — two guards would mean nobody knows
which one decided.
"""
from __future__ import annotations

import importlib

from stilhawt_cli.grammar import MODES, Refusal

# mode → {transport name → function}. `decide` functions take (phrase, options, question);
# `generate` functions take (prompt). Filled by the extensions, never by the engine.
TRANSPORTS: dict[str, dict] = {m: {} for m in sorted(MODES)}
_GUARD: list = []
_LOADED: list[str] = []


def transport(mode: str, name: str):
    """Register a transport for a mode. A mode the language does not know, or a name already taken
    by ANOTHER function, is refused (re-registering the same function is a no-op: a module imported
    twice must not fail)."""
    if mode not in TRANSPORTS:
        raise Refusal(f"transport '{name}': mode '{mode}' unknown — modes: {sorted(TRANSPORTS)}")

    def deco(fn):
        prev = TRANSPORTS[mode].get(name)
        if prev is not None and prev is not fn and getattr(prev, "__qualname__", None) != fn.__qualname__:
            raise Refusal(f"transport '{name}' ({mode}) registered twice, by {prev.__module__} and {fn.__module__}")
        TRANSPORTS[mode][name] = fn
        return fn
    return deco


def egress_guard(fn):
    """Register THE egress guard: `fn(text, pan, destination) -> text to send`, raising to refuse."""
    if _GUARD and _GUARD[0] is not fn and _GUARD[0].__qualname__ != fn.__qualname__:
        raise Refusal(f"an egress guard is already registered ({_GUARD[0].__module__}): one guard decides")
    _GUARD[:] = [fn]
    return fn


def _refuse_all(text: str, pan: str, destination: str) -> str:
    raise Refusal(f"no egress guard: nothing is sent to `{destination}` — declare an extension that "
                  f"registers one (`extensions:` in the grammar, `@ext.egress_guard`)")


def guard():
    """The registered guard, or the default that REFUSES everything (fail-closed)."""
    return _GUARD[0] if _GUARD else _refuse_all


_GRAPH_AUDIT: list = []


def graph_audit(fn=None):
    """Register THE graph audit: `fn(pivot, positions, chemins=None) -> [faults]` (GRF stage 1), or,
    called bare, return the registered one — None when no extension audits graphs. The rendering is
    public; the audit is an extension (decision 20260927b): without one, a page says the stage did
    NOT run — it never reports « 0 fault » for an audit nobody ran."""
    if fn is None:
        return _GRAPH_AUDIT[0] if _GRAPH_AUDIT else None
    if _GRAPH_AUDIT and _GRAPH_AUDIT[0] is not fn and _GRAPH_AUDIT[0].__qualname__ != fn.__qualname__:
        raise Refusal(f"a graph audit is already registered ({_GRAPH_AUDIT[0].__module__}): one audit decides")
    _GRAPH_AUDIT[:] = [fn]
    return fn


_EGRESS_CONTRACT: list = []


def egress_contract(fn=None):
    """Register THE guard's vocabulary: `fn() -> {"pans": [...], "destinations": [...]}` — the pans the
    guard knows and the destinations it ranks — or, called bare, return it (None when none). The
    engine checks the grammar's pans and each model pipe's destination against it (`--check`), so a
    pan the guard does not know is caught at lint time, not refused in the middle of a pipe. Until
    2026-09-29 the engine read ANO's contract BY PATH in another repository: the guard now says it."""
    if fn is None:
        return _EGRESS_CONTRACT[0] if _EGRESS_CONTRACT else None
    if _EGRESS_CONTRACT and _EGRESS_CONTRACT[0] is not fn and _EGRESS_CONTRACT[0].__qualname__ != fn.__qualname__:
        raise Refusal(f"an egress contract is already registered ({_EGRESS_CONTRACT[0].__module__}): one guard, one vocabulary")
    _EGRESS_CONTRACT[:] = [fn]
    return fn


_CREDENTIALS: dict = {}


def credential(provider: str, variable: str, probe=None, how: str = ""):
    """Declare the credential a transport needs: the environment VARIABLE that holds it, how to set it,
    and an optional `probe() -> "valid" | "refused (…)" | "unreachable (…)"` (one tiny read call).
    `stilhawt ai keys` lists what is set — NEVER a value; `ai probe` asks each provider. A provider
    declared twice by two modules is refused."""
    prev = _CREDENTIALS.get(provider)
    if prev and prev["variable"] != variable:
        raise Refusal(f"credential `{provider}` already declared with {prev['variable']}")
    _CREDENTIALS[provider] = {"variable": variable, "probe": probe, "how": how}


def credentials() -> dict:
    return dict(_CREDENTIALS)


_DATA_DIR: list = []


def data_dir(fn=None):
    """Register WHERE the CLI writes its files (`view` pages, `diff` snapshots, snippet uses):
    `fn(sub) -> Path` — or, called bare, return the function in force. Without an extension: the
    directory named by STILHAWT_CLI_DATA, else ~/.stilhawt-cli. A second registration is refused."""
    if fn is None:
        return _DATA_DIR[0] if _DATA_DIR else _default_data_dir
    if _DATA_DIR and _DATA_DIR[0] is not fn and _DATA_DIR[0].__qualname__ != fn.__qualname__:
        raise Refusal(f"a data directory is already registered ({_DATA_DIR[0].__module__}): one place")
    _DATA_DIR[:] = [fn]
    return fn


def _default_data_dir(sub: str):
    import os
    from pathlib import Path
    d = Path(os.environ.get("STILHAWT_CLI_DATA") or Path.home() / ".stilhawt-cli") / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_path(sub: str):
    """The directory for `sub`, created: the registered place, else the default."""
    return data_dir()(sub)


def load_extensions(modules) -> list[str]:
    """Import the extension modules the grammar declares (each registers itself). Idempotent.
    A module that cannot be imported is a refusal that names it — never a silent partial CLI."""
    for m in modules or []:
        if m in _LOADED:
            continue
        try:
            importlib.import_module(m)
        except ImportError as e:
            raise Refusal(f"extension '{m}' declared by the grammar cannot be imported: {e}") from None
        _LOADED.append(m)
    return list(_LOADED)


def _selftest() -> int:
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"✗ {name}")

    saved_t, saved_g, saved_a = {m: dict(t) for m, t in TRANSPORTS.items()}, list(_GUARD), list(_GRAPH_AUDIT)
    saved_c, saved_d, saved_k = list(_EGRESS_CONTRACT), list(_DATA_DIR), dict(_CREDENTIALS)
    try:
        for t in TRANSPORTS.values():
            t.clear()
        _GUARD.clear()
        _GRAPH_AUDIT.clear()
        check("MUST-FAIL no graph audit registered: None (the caller says « not run », never « 0 fault »)",
              graph_audit() is None)

        @graph_audit
        def a1(pivot, positions, chemins=None):
            return []
        check("the registered graph audit is returned", graph_audit() is a1)
        try:
            guard()("x", "texte", "somewhere")
            got = "sent"
        except Refusal:
            got = "refused"
        check("MUST-FAIL with no guard registered, nothing is sent (fail-closed)", got == "refused")

        @transport("generate", "fake")
        def fake(prompt):
            return {"reponse": prompt}
        check("a registered transport is found by mode and name", TRANSPORTS["generate"]["fake"] is fake)
        transport("generate", "fake")(fake)
        check("re-registering the same function is a no-op", TRANSPORTS["generate"]["fake"] is fake)
        try:
            transport("generate", "fake")(lambda p: {})
            got = "accepted"
        except Refusal:
            got = "refused"
        check("MUST-FAIL a name taken by another function is refused", got == "refused")
        try:
            transport("sing", "x")
            got = "accepted"
        except Refusal:
            got = "refused"
        check("MUST-FAIL a mode the language does not know is refused", got == "refused")

        @egress_guard
        def g1(text, pan, destination):
            return text.upper()
        check("the registered guard decides", guard()("abc", "texte", "d") == "ABC")
        try:
            egress_guard(lambda t, p, d: t)
            got = "accepted"
        except Refusal:
            got = "refused"
        check("MUST-FAIL a second guard is refused (one guard decides)", got == "refused")
        try:
            load_extensions(["no_such_package.no_such_extension"])
            got = "loaded"
        except Refusal as e:
            got = "refused" if "no_such_extension" in str(e) else f"bad message {e}"
        check("MUST-FAIL an undeclarable extension is a refusal that names it", got == "refused")

        # egress contract: the guard SAYS its vocabulary; none registered = None (the lint says it)
        _EGRESS_CONTRACT.clear()
        check("MUST-FAIL no egress contract registered: None", egress_contract() is None)

        @egress_contract
        def c1():
            return {"pans": ["texte"], "destinations": ["d"]}
        check("the registered contract is returned", egress_contract()() == {"pans": ["texte"], "destinations": ["d"]})
        try:
            egress_contract(lambda: {})
            got = "accepted"
        except Refusal:
            got = "refused"
        check("MUST-FAIL a second contract is refused", got == "refused")

        # credentials: declared by name of VARIABLE, never a value
        _CREDENTIALS.clear()
        credential("p", "P_KEY", how="export P_KEY=…")
        check("a credential is declared by its variable", credentials()["p"]["variable"] == "P_KEY")
        try:
            credential("p", "OTHER_KEY")
            got = "accepted"
        except Refusal:
            got = "refused"
        check("MUST-FAIL one provider, two variables is refused", got == "refused")
        # data dir: the default place, overridable by env; a registered one wins
        import os
        import tempfile
        from pathlib import Path
        _DATA_DIR.clear()
        with tempfile.TemporaryDirectory() as t:
            old = os.environ.get("STILHAWT_CLI_DATA")
            os.environ["STILHAWT_CLI_DATA"] = t
            try:
                check("the default data dir is STILHAWT_CLI_DATA/<sub>, created",
                      data_path("cli") == Path(t) / "cli" and (Path(t) / "cli").is_dir())
            finally:
                if old is None:
                    os.environ.pop("STILHAWT_CLI_DATA", None)
                else:
                    os.environ["STILHAWT_CLI_DATA"] = old

            @data_dir
            def d1(sub):
                return Path(t) / "ws" / sub
            check("a registered data dir wins", data_path("cli") == Path(t) / "ws" / "cli")
            try:
                data_dir(lambda sub: Path(t))
                got = "accepted"
            except Refusal:
                got = "refused"
            check("MUST-FAIL a second data dir is refused", got == "refused")
    finally:
        for m, t in TRANSPORTS.items():
            t.clear()
            t.update(saved_t[m])
        _GUARD[:] = saved_g
        _GRAPH_AUDIT[:] = saved_a
        _EGRESS_CONTRACT[:] = saved_c
        _DATA_DIR[:] = saved_d
        _CREDENTIALS.clear()
        _CREDENTIALS.update(saved_k)
    print(f"{ok}/{total} selftests passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
