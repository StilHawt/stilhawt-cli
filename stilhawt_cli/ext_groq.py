"""stilhawt_cli.ext_groq — the extension a public install ships: the `groq` pipe, and its egress guard.

    extensions: [stilhawt_cli.ext_groq]      # declared by the grammar (commandes.dsl.yaml)

- transport `groq` (mode generate): the Groq chat API with YOUR key, read from GROQ_API_KEY at call
  time (never stored, never printed). Model: STILHAWT_GROQ_MODEL, default below. Standard library only.
- THE egress guard. It does not anonymise anything — it says so — and it REFUSES the pans that must
  not leave as they are: `donnees` (raw data, records), `personne` (data about a person), `image`.
  `texte` and `code` are sent unchanged. Nothing reaches a model through any other door: without a
  guard the engine refuses every model pipe (fail-closed).
- its vocabulary (`egress_contract`): the pans it knows and the one destination it ranks, so
  `stilhawt --check` compares the grammar to what this guard actually decides.

To reach another model, or to anonymise before sending, write your own module on the same pattern
and name it in the grammar's `extensions:` instead of this one.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from stilhawt_cli import ext
from stilhawt_cli.grammar import Refusal

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "llama-3.1-8b-instant"
PANS = {"texte": "sent", "code": "sent", "donnees": "refused", "personne": "refused", "image": "refused"}
DESTINATION = "groq"


def ask_groq(prompt: str, timeout: float = 60.0) -> dict:
    """One completion: {"reponse": text} or {"refus": why}. The answer is DATA, never executed."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return {"refus": "GROQ_API_KEY is not set — the groq pipe uses your own key"}
    body = json.dumps({"model": os.environ.get("STILHAWT_GROQ_MODEL", DEFAULT_MODEL),
                       "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(GROQ_URL, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            doc = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"refus": f"Groq answered HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"refus": f"Groq unreachable: {type(e).__name__}"}
    try:
        return {"reponse": doc["choices"][0]["message"]["content"]}
    except (KeyError, IndexError, TypeError):
        return {"refus": "Groq returned no answer"}


def guard(text: str, pan: str, destination: str) -> str:
    """What may leave, unchanged — or a refusal that says why. No anonymisation: said, not implied."""
    if destination != DESTINATION:
        raise Refusal(f"this guard only ranks `{DESTINATION}`, not `{destination}`")
    verdict = PANS.get(pan)
    if verdict is None:
        raise Refusal(f"pan `{pan}` unknown to this guard — known: {sorted(PANS)}")
    if verdict == "refused":
        raise Refusal(f"pan `{pan}` does not leave this machine as it is (this guard does not anonymise)")
    return text


def contract() -> dict:
    return {"pans": sorted(PANS), "destinations": [DESTINATION]}


ext.transport("generate", "groq")(ask_groq)
ext.egress_guard(guard)
ext.egress_contract(contract)


def _selftest() -> int:
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"✗ {name}")

    check("the groq transport, the guard and its vocabulary are registered",
          ext.TRANSPORTS["generate"].get("groq") is ask_groq and ext.guard() is guard
          and ext.egress_contract() is contract)
    check("texte and code leave unchanged", guard("abc", "texte", "groq") == "abc" and guard("x", "code", "groq") == "x")
    for pan in ("donnees", "personne", "image", "unknown"):
        try:
            guard("x", pan, "groq")
            check(f"MUST-FAIL pan `{pan}` is refused", False)
        except Refusal:
            check(f"MUST-FAIL pan `{pan}` is refused", True)
    try:
        guard("x", "texte", "elsewhere")
        check("MUST-FAIL a destination the guard does not rank is refused", False)
    except Refusal:
        check("MUST-FAIL a destination the guard does not rank is refused", True)
    saved = os.environ.pop("GROQ_API_KEY", None)
    try:
        check("MUST-FAIL without a key: a refusal, no network call", "GROQ_API_KEY" in ask_groq("hi").get("refus", ""))
    finally:
        if saved is not None:
            os.environ["GROQ_API_KEY"] = saved
    from stilhawt_cli.grammar import load
    check("its pans are exactly the grammar's", set(contract()["pans"]) == set((load().get("grammar") or {}).get("pan") or {}))
    print(f"{ok}/{total} selftests passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
