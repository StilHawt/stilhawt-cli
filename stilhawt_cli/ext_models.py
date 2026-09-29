"""stilhawt_cli.ext_models — the extension a public install ships: two model pipes and their egress guard.

    extensions: [stilhawt_cli.ext_models]    # declared by the grammar (commandes.dsl.yaml)

Two kinds of model, two pipes (the heart of the CLI's AI side):
  · `groq`  GENERATES — one short line per object (or `--all`: one answer for the set). Groq chat API,
            your key in GROQ_API_KEY, model STILHAWT_GROQ_MODEL.
  · `jev`   DECIDES — picks ONE of the options you give, with a probability per option. TypeSafe's
            System One API (Jev), your key in TYPESAFE_API_KEY, model STILHAWT_JEV_MODEL. It writes no
            text: filter on its MARGIN (top − second probability), not on a confidence.

THE egress guard: nothing reaches a model through any other door, and without a guard the engine refuses
every model pipe (fail-closed). This one does not anonymise — it says so — and REFUSES the pans that must
not leave as they are: `donnees` (raw data), `personne` (data about a person), `image`. `texte` and `code`
are sent unchanged, and only the fields `--on` names.

Keys are read from the environment at call time — never stored, never printed. `stilhawt ai keys` says
which variables are set; `stilhawt ai probe` tries each with one tiny read call. Standard library only.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from stilhawt_cli import ext
from stilhawt_cli.grammar import Refusal

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS = "https://api.groq.com/openai/v1/models"
# PROBED on Groq's served catalogue (2026-09-29), not recalled: a model listed yesterday can answer 404
# to a completion today — `stilhawt ai probe` checks the key, STILHAWT_GROQ_MODEL overrides the model.
GROQ_DEFAULT = "openai/gpt-oss-20b"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
JEV_MODELS = "https://api.typesafe.ai/v1/models"
JEV_DEFAULT = "jev-1.13.0"
PANS = {"texte": "sent", "code": "sent", "donnees": "refused", "personne": "refused", "image": "refused"}
DESTINATIONS = ("groq", "jev")
# An explicit client name: Groq's edge answers 403 to urllib's default `Python-urllib/x.y` (measured
# 2026-09-29: 403 on chat AND models with a valid key; the same key through httpx passed).
USER_AGENT = "stilhawt-cli"


def _post(url: str, key: str, body: dict, timeout: float) -> tuple[int | None, dict | str]:
    # ASCII-escaped JSON: the TypeSafe front end drops the TLS connection on raw UTF-8 bodies
    # (measured: raw 6/6 failed, escaped 6/6 passed). Same JSON document for the server.
    req = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=True).encode("ascii"), method="POST",
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return None, type(e).__name__


def ask_groq(prompt: str, timeout: float = 60.0) -> dict:
    """One completion: {"reponse": text} or {"refus": why}. The answer is DATA, never executed."""
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        return {"refus": "GROQ_API_KEY is not set — `stilhawt ai keys` says how"}
    code, doc = _post(GROQ_URL, key, {"model": os.environ.get("STILHAWT_GROQ_MODEL", GROQ_DEFAULT),
                                      "messages": [{"role": "user", "content": prompt}], "temperature": 0.2}, timeout)
    if code != 200:
        return {"refus": f"Groq answered {code or doc}"}
    try:
        return {"reponse": doc["choices"][0]["message"]["content"]}
    except (KeyError, IndexError, TypeError):
        return {"refus": "Groq returned no answer"}


def margin(probabilities: dict) -> float | None:
    """Top − second probability: what to filter a decision on. PURE."""
    p = sorted((float(v) for v in (probabilities or {}).values()), reverse=True)
    return round(p[0] - (p[1] if len(p) > 1 else 0.0), 6) if p else None


def ask_jev(phrase: str, options: list[str], question: str, timeout: float = 30.0) -> dict:
    """One typed decision among `options`: {"reponse": {choice, probabilities, marge}} or {"refus": why}."""
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        return {"refus": "TYPESAFE_API_KEY is not set — `stilhawt ai keys` says how"}
    body = {"model": os.environ.get("STILHAWT_JEV_MODEL", JEV_DEFAULT),
            "state": {"phrase": phrase.strip()},
            "questions": {"choice": {"type": "choice", "instructions": question or "pick the option that fits",
                                     "criteria": {o: o for o in options}}}}   # the label shown as is
    code, doc = _post(JEV_URL, key, body, timeout)
    if code != 200:
        return {"refus": f"Jev answered {code or doc}"}
    rep = dict(((doc.get("answers") or {}).get("choice") or {}) if isinstance(doc, dict) else {})
    if not rep:
        return {"refus": "Jev returned no answer"}
    return {"reponse": {"choice": rep.get("choice"), "probabilities": rep.get("probabilities") or {},
                        "marge": margin(rep.get("probabilities"))}}


def guard(text: str, pan: str, destination: str) -> str:
    """What may leave, unchanged — or a refusal that says why. No anonymisation: said, not implied."""
    if destination not in DESTINATIONS:
        raise Refusal(f"this guard ranks {list(DESTINATIONS)}, not `{destination}`")
    verdict = PANS.get(pan)
    if verdict is None:
        raise Refusal(f"pan `{pan}` unknown to this guard — known: {sorted(PANS)}")
    if verdict == "refused":
        raise Refusal(f"pan `{pan}` does not leave this machine as it is — name the fields that leave and "
                      f"what they are (`--on <field> --pan texte`), or keep it local")
    return text


def contract() -> dict:
    return {"pans": sorted(PANS), "destinations": list(DESTINATIONS)}


def _probe(url: str, variable: str):
    def probe() -> str:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {os.environ.get(variable, '')}",
                                                   "User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return "valid" if r.status == 200 else f"answered {r.status}"
        except urllib.error.HTTPError as e:
            return f"refused ({e.code})"
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return f"unreachable ({type(getattr(e, 'reason', e)).__name__})"
    return probe


ext.transport("generate", "groq")(ask_groq)
ext.transport("decide", "jev")(ask_jev)
ext.egress_guard(guard)
ext.egress_contract(contract)
ext.credential("groq", "GROQ_API_KEY", _probe(GROQ_MODELS, "GROQ_API_KEY"),
               how="a key from console.groq.com — see README, « Keys »")
ext.credential("jev", "TYPESAFE_API_KEY", _probe(JEV_MODELS, "TYPESAFE_API_KEY"),
               how="a key from typesafe.ai — see README, « Keys »")


def _selftest() -> int:
    ok = total = 0

    def check(name, cond):
        nonlocal ok, total
        total += 1
        ok += bool(cond)
        if not cond:
            print(f"✗ {name}")

    check("groq (generate) and jev (decide) are registered, with the guard and its vocabulary",
          ext.TRANSPORTS["generate"].get("groq") is ask_groq and ext.TRANSPORTS["decide"].get("jev") is ask_jev
          and ext.guard() is guard and ext.egress_contract() is contract)
    check("both keys are declared by their VARIABLE", {p: c["variable"] for p, c in ext.credentials().items()}
          == {"groq": "GROQ_API_KEY", "jev": "TYPESAFE_API_KEY"})
    check("texte and code leave unchanged, to either destination",
          guard("abc", "texte", "groq") == "abc" and guard("x", "code", "jev") == "x")
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
    check("margin: top minus second", margin({"yes": 0.8, "no": 0.15, "maybe": 0.05}) == 0.65)
    saved = {v: os.environ.pop(v, None) for v in ("GROQ_API_KEY", "TYPESAFE_API_KEY")}
    try:
        check("MUST-FAIL without a key: a refusal naming the variable, no network call",
              "GROQ_API_KEY" in ask_groq("hi").get("refus", "")
              and "TYPESAFE_API_KEY" in ask_jev("hi", ["a", "b"], "?").get("refus", ""))
    finally:
        for v, x in saved.items():
            if x is not None:
                os.environ[v] = x
    from stilhawt_cli.grammar import load
    check("its pans are exactly the grammar's", set(contract()["pans"]) == set((load().get("grammar") or {}).get("pan") or {}))
    print(f"{ok}/{total} selftests passed")
    return 0 if ok == total else 1


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
