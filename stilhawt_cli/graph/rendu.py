"""stilhawt_cli.graph.rendu — RENDERING a GRF graph: validate against the pivot, lay out if needed,
draw with a declared engine (cytoscape · svg · mermaid · drawio). (Contract GRF.)

Why a module of its own (2026-09-27, decision 20260927b). GRF has two halves: the rendering
(pivot → disposition → engine) and the three-stage AUDIT (relecture, sandbox, passe_ia). The
rendering ships with the public CLI; the audit stays private. Until then `rendre` lived in the
package `__init__`, which also imports the audit — so drawing a graph pulled the audit along.
Here it depends on the rendering half only; the package `__init__` still re-exports it.
"""
from __future__ import annotations

from . import moteurs as _moteurs
from .disposition import disposer
from .pivot import dsl, valider


def liste_moteurs() -> dict:
    d = dsl()
    return {k: {"sortie": v["sortie"], "defaut": bool(v.get("defaut")),
                "besoin_positions": bool(v.get("besoin_positions")), "note": v.get("note", "")}
            for k, v in d["moteurs"].items() if k in _moteurs.MOTEURS}


def rendre(graphe: dict, moteur: str | None = None, titre: str = "graphe",
           positions: dict | None = None, **reglages):
    """Valide, dispose si besoin, puis rend. Refuse un graphe non conforme plutôt que de rendre
    quelque chose de faux — un dessin faux se croit sur parole."""
    d = dsl()
    moteur = moteur or next((k for k, v in d["moteurs"].items() if v.get("defaut")), "cytoscape")
    if moteur not in _moteurs.MOTEURS:
        raise ValueError(f"moteur inconnu : {moteur!r} (déclarés : {', '.join(_moteurs.MOTEURS)})")
    pb = valider(graphe, d)
    if pb:
        raise ValueError("graphe non conforme au pivot :\n  · " + "\n  · ".join(pb))
    mesures, chemins = None, reglages.pop("chemins", None)
    if d["moteurs"][moteur].get("besoin_positions") and positions is None:
        r = disposer(graphe, **{k: v for k, v in reglages.items()
                                if k != "legende"})
        positions, mesures = r["positions"], r["mesures"]
        chemins = chemins or r.get("chemins")
    fn = _moteurs.MOTEURS[moteur]
    sortie = fn(graphe, d, positions) if moteur != "mermaid" else fn(graphe, d)
    if moteur == "svg":
        sortie = fn(graphe, d, positions, titre,
                    legende=reglages.get("legende", True), chemins=chemins)
    elif moteur == "drawio":
        sortie = fn(graphe, d, positions, titre)
    if isinstance(sortie, dict) and mesures:
        sortie["mesures"] = mesures
    return sortie
