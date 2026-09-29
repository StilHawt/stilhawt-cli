"""stilhawt_cli — the workspace CLI `stilhawt` (contract CLI). A THIN, LAZY facade.

    stilhawt                       # the interactive shell
    stilhawt <namespace> <command> # one line; `stilhawt help` for the grammar

The engine lives in `cli/engine.py`, the tool interface in `cli/tool.py`. This file loads NOTHING
at import: importing `stilhawt_cli.tool` runs this `__init__` first, and until 2026-09-27 that
meant the whole engine (3 500+ lines) for every tool brick that only wanted the Toolset — a
coupling the import list did not show (measured by contract SBR once the measure counted package
inits). Any name asked of this module is fetched from the engine, on first use.
"""
from __future__ import annotations


def __getattr__(name: str):
    # A dunder asked by Python's machinery (copy, pickle, pydoc…) never loads the engine.
    if name.startswith("__") and name.endswith("__"):
        raise AttributeError(name)
    # `from stilhawt_cli import grammar` asks for `grammar` BEFORE importing it: a name that is
    # a submodule of this package is imported as such — asking for the language never loads the engine.
    import importlib.util
    if importlib.util.find_spec(f"{__name__}.{name}") is not None:
        return importlib.import_module(f"{__name__}.{name}")
    # `import a.b.c` imports the submodule FIRST, then binds it: it never asks this module for
    # `engine` — `from stilhawt_cli import engine` did, and looped back here (2026-09-27).
    import stilhawt_cli.engine as _engine   # the ONLY path to the engine from the facade
    return getattr(_engine, name)


def __dir__() -> list[str]:
    import stilhawt_cli.engine as _engine
    return sorted(set(globals()) | set(dir(_engine)))
