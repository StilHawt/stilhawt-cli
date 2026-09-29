"""stilhawt_cli.graph — the graph RENDERING the CLI draws with (`view graph`, `view tree`).

A neutral model `{noeuds, aretes}` in a CLOSED vocabulary (pivot), a layout computed in Python
(disposition), and engines that only paint it (Cytoscape, SVG, Mermaid, draw.io):

    from stilhawt_cli.graph import valider, disposer, rendre

The model AUDIT (overlaps, an edge crossing a box…) is not part of this package: it is an extension
point (`stilhawt_cli.ext.graph_audit`). Without one, a page says the audit did NOT run — it never
reports « 0 fault » for an audit nobody ran.
"""
from __future__ import annotations

from .disposition import disposer  # noqa: F401
from .pivot import check, dsl, style, valider  # noqa: F401
from .rendu import liste_moteurs, rendre  # noqa: F401
