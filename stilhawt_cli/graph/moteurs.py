#!/usr/bin/env python3
"""moteurs — traduire un graphe pivot POSITIONNÉ vers une sortie concrète (GRF).

C'est la seule partie du moteur qui connaît une techno, et elle ne fait que traduire : les
positions sont déjà posées par `disposition`. Un moteur nouveau = une entrée au DSL + une
fonction ici ; aucune vue, aucun producteur n'est retouché.

`svg` est la preuve que la disposition est bien sortie du navigateur : il rend un dessin
complet sans Cytoscape, sans DOM et sans Chrome — donc utilisable dans un rapport, un courriel
ou une page statique.
"""
from __future__ import annotations

import math

from . import pivot


def _esc(s) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


# ─────────────────────────────────────────────────────────────────────────────────────────────

def cytoscape(graphe: dict, d: dict, positions: dict | None = None) -> dict:
    """Éléments Cytoscape AVEC leur position : le navigateur passe en `layout: preset` et
    n'a plus aucune logique de disposition — il peint et il interagit."""
    m = d["moteurs"]["cytoscape"]
    positions = positions or {}
    els = []
    for n in graphe["noeuds"]:
        g, couleur = pivot.style(n, d)
        data = {k: v for k, v in n.items() if k not in ("genre", "parent")}
        data.update({"genre": n["genre"], "forme": m["formes"][g["forme"]], "couleur": couleur,
                     # Le CONTOUR distingue deux choses de même forme et de même couleur — un
                     # périmètre non évalué d'une application au repos, par exemple.
                     "contour": m["traits"][g.get("contour", "plein")],
                     "conteneur": bool(g.get("conteneur"))})
        # LA BOÎTE PART AVEC LA POSITION, et c'est indissociable. La disposition a réservé à ce
        # nœud une boîte de `taille_noeud` ; si le navigateur en dessine une autre, il peint des
        # coordonnées calculées pour un dessin qui n'est pas le sien. Mesuré le 24/09/2026 :
        # Cytoscape, laissé à `width: label`, rendait des boîtes de 61 × 12 px — la boîte
        # épousait les lettres, le cadre touchait les glyphes, et l'écartement des colonnes
        # n'avait plus de rapport avec ce qu'on voyait. Le moteur SVG, lui, lisait la même
        # taille que la disposition : deux moteurs, deux dessins, pour un seul pivot.
        #
        # Les CONTENEURS n'en reçoivent pas : Cytoscape calcule la boîte d'un nœud composé à
        # partir de ses enfants. Lui imposer une largeur la ferait entrer en conflit avec ce
        # calcul ; une fois les enfants à la bonne taille, le cadre suit.
        if not g.get("conteneur"):
            larg, lignes = taille_noeud(str(n.get("label", "")))
            data["largeur"] = round(larg, 1)
            data["hauteur"] = _SVG_H + (LIGNE_H if len(lignes) > 1 else 0)
        el: dict = {"data": data}
        if n.get("parent"):
            el["data"]["parent"] = n["parent"]
        if n["id"] in positions:
            el["position"] = {"x": round(positions[n["id"]]["x"], 1),
                              "y": round(positions[n["id"]]["y"], 1)}
        els.append(el)
    for a in graphe.get("aretes") or []:
        t = d["types_arete"].get(a.get("type") or "flux", d["types_arete"]["flux"])
        els.append({"data": {**{k: v for k, v in a.items() if k not in ("de", "vers")},
                             "source": a["de"], "target": a["vers"],
                             "trait": m["traits"][t["trait"]],
                             "fleche": m["fleches"][t["fleche"]],
                             "couleur": a.get("couleur") or t["couleur"]}})
    # LA GÉOMÉTRIE PART AVEC LES ÉLÉMENTS. Sans elle, le navigateur redéclare ses propres
    # marges — et le `padding` de Cytoscape S'AJOUTE à la taille explicite (sondé le 24/09 :
    # une boîte de 120 × 38 occupait 146 × 64). Le navigateur n'a pas à connaître ces nombres,
    # il a à les recevoir.
    g = pivot.dsl().get("geometrie") or {}
    return {"moteur": "cytoscape", "elements": els, "positionne": bool(positions),
            "style": style_cytoscape(d),
            "geometrie": {"groupe": g.get("groupe") or {}, "noeud": g.get("noeud") or {}}}


def style_cytoscape(d: dict | None = None) -> list[dict]:
    """La feuille de style GÉOMÉTRIQUE de Cytoscape, ÉMISE par le moteur.

    Elle vivait dans le tableau de bord, à côté des règles d'interaction (survol, filtre). D'où
    deux problèmes qu'on a payés le 24/09/2026 : le navigateur redéclarait des marges que le
    contrat fixait déjà, et le banc de mesure ne pouvait pas contrôler la vraie page — il aurait
    mesuré une feuille « équivalente », c'est-à-dire une autre.

    Ce qui reste au navigateur : ce qui n'existe QUE devant quelqu'un — le survol, l'estompage,
    la trace. Ce qui vient d'ici : tout ce qui décide d'une TAILLE ou d'une FORME.
    """
    d = d or pivot.dsl()
    g = d.get("geometrie") or {}
    # NIVEAU 3 : ce que CE moteur exige pour honorer le niveau 2. Lu au contrat, jamais écrit
    # ici — `marge_feuille: 0` est un fait de Cytoscape, pas une opinion de cette fonction.
    g3 = ((d.get("moteurs") or {}).get("cytoscape") or {}).get("geometrie") or {}
    marge = (g.get("groupe") or {}).get("marge", 16)
    marge_feuille = g3.get("marge_feuille", 0)
    return [
        {"selector": "node",
         "style": {"background-color": "data(couleur)", "background-opacity": 0.13,
                   "border-color": "data(couleur)", "border-width": 1.5,
                   "shape": "data(forme)", "label": "data(label)", "color": "#e6edf3",
                   "font-size": 12, "font-weight": 500, "text-valign": "center",
                   "text-wrap": "wrap", "text-max-width": NOEUD_MAX,
                   "width": "label", "height": "label", "padding": MARGE_TEXTE,
                   "border-style": "data(contour)",
                   "min-zoomed-font-size": 6, "border-opacity": 0.85, "text-margin-y": 0}},
        # ⚠ `padding: 0` n'est pas un détail : sur une FEUILLE, le `padding` de Cytoscape
        # S'AJOUTE à la taille explicite. La marge du texte est déjà comprise dans `largeur`
        # (`geometrie.noeud.marge_texte`) ; en ajouter une seconde ici reproduit exactement le
        # recouvrement que la disposition évite.
        {"selector": "node[largeur]",
         "style": {"width": "data(largeur)", "height": "data(hauteur)",
                   "text-max-width": "data(largeur)", "padding": marge_feuille}},
        # Sur un nœud COMPOSÉ, le même mot désigne autre chose : l'espace entre le cadre et ses
        # enfants. C'est le seul endroit où `padding` veut dire ce que le contrat appelle
        # `groupe.marge`, et la taille du cadre reste DÉRIVÉE des enfants (natif, non imposable).
        {"selector": "node:parent",
         "style": {"text-valign": "top", "font-size": 13, "font-weight": "bold",
                   "background-opacity": 0.05, "border-width": 2, "padding": marge,
                   "border-style": "data(contour)",
                   "text-margin-y": -4, "shape": "round-rectangle"}},
        {"selector": "node[?fictif]",
         "style": {"width": 1, "height": 1, "label": "", "border-width": 0,
                   "background-opacity": 0}},
        {"selector": "edge",
         "style": {"line-color": "data(couleur)", "target-arrow-color": "data(couleur)",
                   "target-arrow-shape": "data(fleche)", "line-style": "data(trait)",
                   "width": 1.1, "arrow-scale": 0.8, "curve-style": "straight",
                   "opacity": 0.5, "label": "data(label)", "font-size": 9, "color": "#7d8896",
                   "text-rotation": "autorotate", "min-zoomed-font-size": 9,
                   "text-background-color": "#0d1015", "text-background-opacity": 0.85,
                   "text-background-padding": 3, "text-background-shape": "roundrectangle"}},
        {"selector": 'edge[label = ""]', "style": {"label": ""}},
    ]


# ─────────────────────────────────────────────────────────────────────────────────────────────

# ── LA BOÎTE VIENT DU DSL, PAS D'UNE CONSTANTE ───────────────────────────────────────────────
# Ces valeurs vivaient en dur ici pendant que le pas d'une pile vivait en dur dans
# `disposition.py` et que le navigateur ajoutait sa propre marge. Trois sources, aucun accord :
# 24 recouvrements mesurés le 24/09/2026. Elles sont désormais LUES au contrat, qui refuse les
# combinaisons intenables (`pivot._check_geometrie`).
def _geo(*chemin, defaut=None):
    """Une valeur de `geometrie` au contrat. Le défaut ne sert qu'à un DSL amputé — que
    `check()` refuse par ailleurs : on ne veut pas planter à l'import, on veut être refusé."""
    v = pivot.dsl().get("geometrie") or {}
    for c in chemin:
        v = (v or {}).get(c)
        if v is None:
            return defaut
    return v


_SVG_L = _geo("noeud", "largeur_min", defaut=120) + 30   # boîte par défaut, sans libellé mesuré
_SVG_H = _geo("noeud", "hauteur", defaut=38)


def largeur_texte(s: str, taille: float = 12, gras: bool = False) -> float:
    """Largeur approchée d'un texte, en pixels.

    Une police proportionnelle n'a PAS de largeur fixe par caractère : « lll » et « WWW » ne
    mesurent pas pareil. Sans un moteur de fontes (qu'on ne veut pas en dépendance), on approche
    par classe de caractère — c'est grossier mais du BON CÔTÉ, ce qui suffit ici : mieux vaut une
    boîte un peu large qu'un titre qui déborde.
    """
    etroits, larges = "iljtfr.,;:'|!()[]{} ", "mwMW@%"
    u = taille * 0.58
    l = sum(u * (0.46 if c in etroits else 1.32 if c in larges else 1.0) for c in s)
    return l * (1.06 if gras else 1.0)


NOEUD_MIN = _geo("noeud", "largeur_min", defaut=120)
NOEUD_MAX = _geo("noeud", "largeur_max", defaut=250)
LIGNE_H = _geo("noeud", "hauteur_ligne", defaut=15)
MARGE_TEXTE = _geo("noeud", "marge_texte", defaut=16)
GROUPE_MARGE = _geo("groupe", "marge", defaut=16)
GROUPE_TITRE = _geo("groupe", "hauteur_titre", defaut=26)


def decouper(s: str, larg: float, taille: float = 12, max_lignes: int = 2) -> list[str]:
    """Coupe un libellé sur les ESPACES pour tenir dans `larg`, au plus `max_lignes`.

    Couper au caractère produirait « Transformation / enrichisse / ment » ; on coupe aux mots.
    Le dernier recours reste l'ellipse, mais après avoir essayé de tout montrer.
    """
    mots, lignes, cur = str(s).split(), [], ""
    for m in mots:
        essai = (cur + " " + m).strip()
        if cur and largeur_texte(essai, taille) > larg:
            lignes.append(cur)
            cur = m
            if len(lignes) == max_lignes:
                break
        else:
            cur = essai
    if len(lignes) < max_lignes and cur:
        lignes.append(cur)
    if not lignes:
        return [str(s)]
    reste = " ".join(mots[sum(len(l.split()) for l in lignes):])
    if reste:                                   # ça ne tient pas : ellipse sur la DERNIÈRE ligne
        d = lignes[-1]
        while d and largeur_texte(d + "…", taille) > larg:
            d = d[:-1]
        lignes[-1] = d + "…"
    return lignes


def taille_noeud(label: str, taille: float = 12, pad: float | None = None,
                 h_base: float | None = None) -> tuple[float, list[str]]:
    """Largeur d'un nœud et son libellé DÉCOUPÉ.

    Une boîte de largeur fixe tronque tout ce qui dépasse — « Scripts Python (panda… ». Ici la
    boîte s'élargit jusqu'à un PLAFOND, puis le texte passe sur deux lignes. Le plafond existe
    parce qu'un nœud plus large que ses voisins désaligne toute la colonne : on préfère deux
    lignes à une boîte démesurée.
    """
    pad = MARGE_TEXTE if pad is None else pad
    une = largeur_texte(label, taille) + 2 * pad
    if une <= NOEUD_MAX:
        return max(NOEUD_MIN, une), [str(label)]
    lignes = decouper(label, NOEUD_MAX - 2 * pad, taille, 2)
    larg = max(largeur_texte(l, taille) for l in lignes) + 2 * pad
    return max(NOEUD_MIN, min(NOEUD_MAX, larg)), lignes


def geometrie(graphe: dict, positions: dict, L: float | None = None, H: float | None = None,
              pad: float | None = None, hdr: float | None = None) -> dict[str, tuple]:
    """Boîte EFFECTIVE de chaque nœud : (cx, cy, largeur, hauteur).

    Un CONTENEUR n'a pas de position — sa boîte se déduit de ses enfants. Sans ce calcul
    partagé, tout ce qui touche un groupe disparaît en silence : la boîte elle-même (défaut vu
    le 23/09 au premier rendu), puis **les arêtes entre groupes**, qui n'ont alors aucune
    extrémité positionnée. La vue « cible » de Frequencies a livré ses 9 flux invisibles pour
    cette raison exacte — le dessin était propre et il manquait tout ce qu'il devait montrer.
    """
    L = _SVG_L if L is None else L
    H = _SVG_H if H is None else H
    pad = GROUPE_MARGE if pad is None else pad
    hdr = GROUPE_TITRE if hdr is None else hdr
    enfants: dict[str, list[str]] = {}
    for n in graphe["noeuds"]:
        if n.get("parent"):
            enfants.setdefault(n["parent"], []).append(n["id"])
    # Chaque FEUILLE porte la taille de SON libellé : une largeur unique tronque tout ce qui
    # dépasse, et l'auditeur voyait « libellé tronqué » sur onze nœuds d'un seul dessin.
    lab = {n["id"]: str(n.get("label", "")) for n in graphe["noeuds"]}
    geo: dict[str, tuple] = {}
    for i in positions:
        w, lignes = taille_noeud(lab.get(i, ""))
        geo[i] = (positions[i]["x"], positions[i]["y"], w,
                  H + (len(lignes) - 1) * LIGNE_H)
    # plusieurs passes : un groupe peut contenir un groupe
    for _ in range(4):
        for n in graphe["noeuds"]:
            fils = [f for f in enfants.get(n["id"], []) if f in geo]
            if not fils or n["id"] in positions:
                continue
            xs = [geo[f][0] for f in fils]
            ys = [geo[f][1] for f in fils]
            ws = [geo[f][2] for f in fils]
            hs = [geo[f][3] for f in fils]
            x1 = min(x - w / 2 for x, w in zip(xs, ws)) - pad
            x2 = max(x + w / 2 for x, w in zip(xs, ws)) + pad
            y1 = min(y - h / 2 for y, h in zip(ys, hs)) - pad - hdr
            y2 = max(y + h / 2 for y, h in zip(ys, hs)) + pad
            # LE TITRE ÉLARGIT SA BOÎTE, il ne la déborde pas. Un titre centré plus large que
            # son conteneur sort des deux côtés et se superpose au voisin — vu le 23/09 sur
            # « COUCHE 2 — BigQuery (opérationnel / analytique) ». Le tronquer perdrait
            # l'information ; c'est donc la boîte qui s'adapte.
            besoin = largeur_texte(str(n.get("label", "")), 12, gras=True) + 2 * pad
            if besoin > x2 - x1:
                c = (x1 + x2) / 2
                x1, x2 = c - besoin / 2, c + besoin / 2
            geo[n["id"]] = ((x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1)
    return geo


def _bord(cx: float, cy: float, w: float, h: float, forme: str,
          vx: float, vy: float, marge: float = 0.0) -> tuple[float, float]:
    """Point où le segment CENTRE→(vx,vy) sort de la boîte du nœud.

    LE DÉFAUT CLASSIQUE QUE ÇA FERME : une arête tracée de centre à centre TRAVERSE les deux
    boîtes — le trait passe sous le libellé, la flèche se plante au milieu du nœud au lieu de
    le désigner, et deux arêtes qui arrivent par des côtés opposés semblent arriver au même
    endroit. Le bord se CALCULE, il ne s'approxime pas : on résout l'intersection du rayon avec
    la forme, ce qui donne automatiquement LE BON CÔTÉ (celui qui fait face à l'autre nœud).
    """
    dx, dy = vx - cx, vy - cy
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return cx, cy
    ux, uy = dx / n, dy / n
    hw, hh = w / 2, h / 2
    if forme == "losange":
        # |x|/hw + |y|/hh = 1
        d = abs(ux) / hw + abs(uy) / hh
        t = 1 / d if d > 1e-9 else 0.0
    else:
        # rectangle (et cylindre/hexagone, dont l'écart au rectangle est sous le pixel ici) :
        # le premier côté atteint est celui qui donne le plus petit t.
        tx = hw / abs(ux) if abs(ux) > 1e-9 else math.inf
        ty = hh / abs(uy) if abs(uy) > 1e-9 else math.inf
        t = min(tx, ty)
    t += marge
    return cx + ux * t, cy + uy * t


def _placer_libelles(aretes: list[dict], bouts: dict, boites: list[tuple],
                     taille: float = 9) -> dict[str, tuple]:
    """Où poser le libellé de chaque arête, sans qu'il masque une boîte ni un autre libellé.

    Le milieu du segment est le choix ÉVIDENT et le pire : sur un faisceau d'arêtes qui
    convergent, tous les milieux tombent au même endroit et les textes s'empilent — c'est ce
    qu'on voyait entre COUCHE 1 et COUCHE 2, quatre libellés superposés sur un titre de groupe.

    On essaie donc plusieurs FRACTIONS le long de l'arête et on garde la première libre. Rien de
    savant : c'est une recherche bornée, déterministe, et elle suffit parce que les libellés
    sont peu nombreux. Si aucune position n'est libre, on garde la moins mauvaise et on le DIT
    (l'audit le relèvera) plutôt que d'empiler en silence.
    """
    poses: list[tuple] = []
    out: dict[str, tuple] = {}
    # les arêtes les plus courtes d'abord : elles ont le moins de place, donc le premier choix
    ordre = sorted((a for a in aretes if a.get("label") and a["id"] in bouts),
                   key=lambda a: math.dist(bouts[a["id"]][0], bouts[a["id"]][1]))
    for a in ordre:
        (xa, ya), (xb, yb) = bouts[a["id"]]
        lab = str(a["label"])
        w, h = largeur_texte(lab, taille) + 8, 15.0
        # -inf, PAS -1 : les scores sont des aires de recouvrement NÉGATIVES, souvent très
        # inférieures à -1. Avec -1 aucun candidat ne passait jamais et le libellé était
        # silencieusement ABANDONNÉ — cinq libellés disparus d'un dessin sans une erreur.
        meilleur, meilleur_score = None, -math.inf
        for t in (0.5, 0.36, 0.64, 0.26, 0.74, 0.18, 0.82):
            cx, cy = xa + (xb - xa) * t, ya + (yb - ya) * t
            b = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            chevauche = sum(_aire(b, o) for o in boites) + sum(_aire(b, o) for o in poses)
            if chevauche == 0:
                meilleur = (cx, cy, w, h, True)
                break
            score = -chevauche
            if score > meilleur_score:
                meilleur_score, meilleur = score, (cx, cy, w, h, False)
        if meilleur is None:                 # ceinture : jamais perdre un libellé en silence
            cx, cy = (xa + xb) / 2, (ya + yb) / 2
            meilleur = (cx, cy, w, h, False)
        cx, cy, w, h, _libre = meilleur
        poses.append((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2))
        out[a["id"]] = meilleur
    return out


def _aire(a: tuple, b: tuple) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def trajet(a: dict, geo: dict, formes: dict, chemins: dict | None = None,
           marge_fleche: float = 4.0) -> list[tuple[float, float]]:
    """Le trajet RÉEL d'une arête : bords clippés + points de passage.

    UNE SEULE définition, partagée par le moteur qui dessine et par l'auditeur qui juge. C'est
    la troisième fois de la soirée que les deux divergent — l'auditeur mesurait depuis le CENTRE
    des boîtes quand le rendu partait du BORD, et signalait donc des traversées inexistantes.
    Tant que deux codes calculent la même chose séparément, l'un des deux est faux sans qu'on
    sache lequel.
    """
    if a["de"] not in geo or a["vers"] not in geo:
        return []
    cxa, cya, wa, ha = geo[a["de"]]
    cxb, cyb, wb, hb = geo[a["vers"]]
    via = list((chemins or {}).get(a["id"], []))
    v1 = via[0] if via else (cxb, cyb)
    v2 = via[-1] if via else (cxa, cya)
    d = _bord(cxa, cya, wa, ha, formes.get(a["de"], "rect"), v1[0], v1[1])
    f = _bord(cxb, cyb, wb, hb, formes.get(a["vers"], "rect"), v2[0], v2[1], marge_fleche)
    return [d, *via, f]


# Marge intérieure et bandeau de titre d'un conteneur — LUS au contrat, comme le reste de la
# géométrie. Ils étaient écrits ici en double de `GROUPE_MARGE`/`GROUPE_TITRE` plus haut : deux
# noms pour une seule mesure, donc deux valeurs le jour où l'une bouge.
PAD, HDR = GROUPE_MARGE, GROUPE_TITRE
LEG_H, LEG_LIG = 30, 22    # bandeau de légende : marge haute, hauteur d'une ligne


def _legende(graphe: dict, d: dict, m: dict, x: float, y: float, larg: float,
             fond: str) -> tuple[list[str], float]:
    """Bandeau de légende, déduit de ce que le dessin utilise VRAIMENT.

    Une légende exhaustive du vocabulaire serait du bruit : on n'explique que les types
    d'arêtes et les états PRÉSENTS. Sans elle, le lecteur doit deviner ce que le pointillé
    encode — c'est le reproche que la passe de relecture a fait au premier rendu, et il était
    juste : quatre couleurs d'arêtes sans un mot pour les lire.
    """
    types = []
    for a in graphe.get("aretes") or []:
        t = a.get("type") or "flux"
        if t in d["types_arete"] and t not in types:
            types.append(t)
    etats = []
    for n in graphe["noeuds"]:
        e = n.get("etat") or "neutre"
        # `neutre` est le DÉFAUT : l'expliquer en légende n'apprend rien et occupe une ligne
        # (son libellé est littéralement « — »). Une légende ne doit porter que ce qui SIGNIFIE.
        if e in d["etats"] and e not in etats and e != "neutre":
            etats.append(e)

    out, cx, cy, lignes = [], x, y + LEG_H, 1
    def _saut(besoin: float):
        nonlocal cx, cy, lignes
        if cx > x and cx + besoin > x + larg:
            cx, cy, lignes = x, cy + LEG_LIG, lignes + 1

    for t in types:
        st = d["types_arete"][t]
        lab = st.get("label") or t
        besoin = 46 + largeur_texte(lab, 10)
        _saut(besoin)
        tiret = m["traits"][st["trait"]]
        dash = "" if tiret == "none" else f' stroke-dasharray="{tiret}"'
        marq = "fc" if m["fleches"][st["fleche"]] == "creuse" else "fl"
        out.append(f'<line x1="{cx:.0f}" y1="{cy:.0f}" x2="{cx + 30:.0f}" y2="{cy:.0f}" '
                   f'stroke="{st["couleur"]}" stroke-width="1.6" stroke-opacity="0.85"{dash} '
                   f'marker-end="url(#{marq})"/>'
                   f'<text x="{cx + 37:.0f}" y="{cy + 4:.0f}" font-size="10" fill="#9aa4b2">'
                   f'{_esc(lab)}</text>')
        cx += besoin + 16

    # Les GENRES présents, quand il y en a plusieurs : sans ça, un cylindre et un rectangle
    # côte à côte laissent le lecteur deviner ce qui les distingue — et il devinera mal.
    # DEUX FAMILLES, comptées SÉPARÉMENT. Les conteneurs étaient exclus en bloc — légitime tant
    # qu'ils étaient tous « un groupe ». Depuis que leur genre porte un sens (un PÉRIMÈTRE n'est
    # pas évalué, une application l'est), les taire laisse au lecteur une distinction visuelle
    # qu'aucun mot n'explique : le reproche exact de la relecture du 24/09. La règle reste la
    # même qu'ailleurs — on n'explique que s'il y a plusieurs choses à distinguer.
    genres, cadres = [], []
    for n in graphe["noeuds"]:
        g = n.get("genre")
        if g not in d["genres"]:
            continue
        cible = cadres if d["genres"][g].get("conteneur") else genres
        if g not in cible:
            cible.append(g)
    a_montrer = (genres if len(genres) > 1 else []) + (cadres if len(cadres) > 1 else [])
    if a_montrer:
        _saut(120)
        for gn in a_montrer:
            st = d["genres"][gn]
            lab = st.get("label") or gn
            besoin = 30 + largeur_texte(lab, 10)
            _saut(besoin)
            f = st["forme"]
            # Le CONTOUR figure dans la légende, sinon elle documenterait une forme en taisant
            # ce qui, dans le dessin, porte la différence.
            _dl = m["traits"][st.get("contour", "plein")]
            _tl = "" if _dl == "none" else f' stroke-dasharray="{_dl}"'
            if f == "cylindre":
                out.append(f'<rect x="{cx:.0f}" y="{cy - 5:.0f}" width="18" height="11" rx="1" '
                           f'fill="none" stroke="#8a93a2" stroke-width="1.2"{_tl}/>'
                           f'<ellipse cx="{cx + 9:.0f}" cy="{cy - 5:.0f}" rx="9" ry="3" '
                           f'fill="none" stroke="#8a93a2" stroke-width="1.2"/>')
            elif f == "losange":
                out.append(f'<polygon points="{cx + 9:.0f},{cy - 7:.0f} {cx + 18:.0f},{cy:.0f} '
                           f'{cx + 9:.0f},{cy + 7:.0f} {cx:.0f},{cy:.0f}" fill="none" '
                           f'stroke="#8a93a2" stroke-width="1.2"{_tl}/>')
            elif f == "hexagone":
                out.append(f'<polygon points="{cx + 4:.0f},{cy - 6:.0f} {cx + 14:.0f},{cy - 6:.0f} '
                           f'{cx + 18:.0f},{cy:.0f} {cx + 14:.0f},{cy + 6:.0f} '
                           f'{cx + 4:.0f},{cy + 6:.0f} {cx:.0f},{cy:.0f}" fill="none" '
                           f'stroke="#8a93a2" stroke-width="1.2"{_tl}/>')
            else:
                out.append(f'<rect x="{cx:.0f}" y="{cy - 6:.0f}" width="18" height="12" '
                           f'rx="{3 if f == "rectangle_arrondi" else 1}" fill="none" '
                           f'stroke="#8a93a2" stroke-width="1.2"{_tl}/>')
            out.append(f'<text x="{cx + 25:.0f}" y="{cy + 4:.0f}" font-size="10" '
                       f'fill="#9aa4b2">{_esc(lab)}</text>')
            cx += besoin + 16

    if etats:
        _saut(120)
        for e in etats:
            st = d["etats"][e]
            lab = st.get("label") or e
            besoin = 22 + largeur_texte(lab, 10)
            _saut(besoin)
            out.append(f'<rect x="{cx:.0f}" y="{cy - 6:.0f}" width="13" height="13" rx="3" '
                       f'fill="{st["couleur"]}" fill-opacity="0.2" stroke="{st["couleur"]}" '
                       f'stroke-width="1.4"/>'
                       f'<text x="{cx + 19:.0f}" y="{cy + 4:.0f}" font-size="10" fill="#9aa4b2">'
                       f'{_esc(lab)}</text>')
            cx += besoin + 16
    return out, LEG_H + lignes * LEG_LIG


def svg(graphe: dict, d: dict, positions: dict, titre: str = "graphe",
        fond: str = "#0d1015", legende: bool = True,
        chemins: dict | None = None) -> str:
    """Dessin complet, sans navigateur. Les arêtes passent SOUS les nœuds (ordre d'émission) et
    les libellés portent un fond : un trait qui traverse un mot le rend illisible."""
    m = d["moteurs"]["svg"]
    if not positions:
        raise ValueError("le moteur `svg` exige des positions — appeler disposer() d'abord")
    # Géométrie EFFECTIVE : les conteneurs y sont, avec la boîte déduite de leurs enfants.
    geo = geometrie(graphe, positions, _SVG_L, _SVG_H, PAD, HDR)
    if not geo:
        raise ValueError("aucun nœud positionné")
    marge = 60
    # Les POINTS DE PASSAGE comptent dans les bornes : sinon les contournements sont tracés
    # HORS du canevas et disparaissent à l'affichage — le SVG est valide, le viewBox les coupe,
    # et rien ne le signale. Même famille que les groupes non dessinés : ce qui n'est pas
    # mesuré dans les bornes sort du cadre sans un mot.
    pts_x = [x - w / 2 for x, _y, w, _h in geo.values()] +             [x + w / 2 for x, _y, w, _h in geo.values()] +             [vx for v in (chemins or {}).values() for vx, _vy in v]
    pts_y = [y - h / 2 for _x, y, _w, h in geo.values()] +             [y + h / 2 for _x, y, _w, h in geo.values()] +             [vy for v in (chemins or {}).values() for _vx, vy in v]
    x0, y0 = min(pts_x) - marge, min(pts_y) - marge
    L = max(pts_x) - x0 + marge
    H = max(pts_y) - y0 + marge
    # ── LE CARTOUCHE : le dessin PORTE ce qu'il dit ─────────────────────────────────────────
    # Sans lui, l'image livrée est muette : la relecture IA du 24/09 a relevé que le constat le
    # plus important de la vue — « 0 application ayant réellement exposé un export » — figurait
    # dans le résumé du modèle et NULLE PART sur l'image. Livrée nue, elle suggérait au contraire
    # un système largement câblé. Une image qui circule sans son titre ni son chiffre-clé se fait
    # lire à l'envers, et c'est le lecteur qui paie l'erreur.
    # `lecture` : LA CLÉ DE LECTURE, quand deux axes cohabitent sur un même dessin. Sans elle,
    # le lecteur réconcilie ce qu'il voit comme il peut — ici, dix boîtes vertes sous un compteur
    # à zéro, et la conclusion spontanée (« c'est vert, donc ça tourne ») était l'inverse du vrai.
    # Deux mesures différentes sur une même image doivent DIRE qu'elles sont différentes.
    cartouche = [l for l in (titre, graphe.get("resume"), graphe.get("lecture"),
                             graphe.get("source")) if l]
    haut = 0.0
    if cartouche:
        haut = 16 + 20 * len(cartouche)
        y0 -= haut          # on POUSSE le dessin vers le bas : le cartouche ne recouvre rien
        H += haut
    P = lambda i: (geo[i][0] - x0, geo[i][1] - y0)  # noqa: E731

    # La légende se calcule AVANT l'en-tête : elle change la HAUTEUR du canevas, et un viewBox
    # écrit trop tôt la couperait — défaut invisible au code, flagrant à l'image.
    leg, leg_h = ([], 0.0)
    if legende:
        leg, leg_h = _legende(graphe, d, m, marge / 2, H - marge / 2, L - marge, fond)
        H += leg_h

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{L:.0f}" height="{H:.0f}" '
           f'viewBox="0 0 {L:.0f} {H:.0f}" font-family="system-ui,-apple-system,sans-serif">',
           f'<rect width="100%" height="100%" fill="{fond}"/>',
           '<defs>'
           '<marker id="fl" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="context-stroke"/></marker>'
           '<marker id="fc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
           'markerHeight="7" orient="auto-start-reverse">'
           '<path d="M0,0 L10,5 L0,10 z" fill="none" stroke="context-stroke" '
           'stroke-width="1.6"/></marker></defs>',
           f'<title>{_esc(titre)}</title>']
    for k, ligne in enumerate(cartouche):
        # Le titre porte le regard, le reste l'informe : une hiérarchie de taille suffit, et
        # elle survit à une capture d'écran comme à une impression.
        gros = k == 0
        out.append(f'<text x="{marge / 2:.0f}" y="{18 + k * 20:.0f}" '
                   f'font-size="{16 if gros else 12}" '
                   f'font-weight="{600 if gros else 400}" '
                   f'fill="{"#e6edf3" if gros else "#9aa4b2"}">{_esc(ligne)}</text>')

    # ── Conteneurs : leur boîte se DÉDUIT de leurs enfants, elle n'est pas positionnée.
    # Cytoscape le fait seul (d'où l'oubli) ; un rendu fait main doit le faire explicitement,
    # sinon les groupes disparaissent du dessin et on perd l'information la plus structurante.
    for n in graphe["noeuds"]:
        if n["id"] in positions or n["id"] not in geo:
            continue                                   # pas un conteneur
        cx, cy, bw, bh = geo[n["id"]]
        bx1, by1 = cx - bw / 2 - x0, cy - bh / 2 - y0
        bx2, by2 = bx1 + bw, by1 + bh
        g, couleur = pivot.style(n, d)
        # Le CONTOUR distingue deux cadres de même forme et de même couleur : un périmètre
        # non évalué d'une application au repos. Sans lui, le lecteur croit que le périmètre
        # « va bien » — c'est le reproche n°1 de la relecture IA du 24/09.
        dash = m["traits"][g.get("contour", "plein")]
        tirets = "" if dash == "none" else f' stroke-dasharray="{dash}"'
        out.append(f'<rect x="{bx1:.0f}" y="{by1:.0f}" width="{bx2 - bx1:.0f}" '
                   f'height="{by2 - by1:.0f}" rx="12" fill="{couleur}" fill-opacity="0.05" '
                   f'stroke="{couleur}" stroke-width="{g.get("epaisseur", 2)}"{tirets} '
                   f'stroke-opacity="0.75"/>'
                   f'<text x="{(bx1 + bx2) / 2:.0f}" y="{by1 + 17:.0f}" font-size="12" '
                   f'font-weight="600" fill="{couleur}" text-anchor="middle">'
                   f'{_esc(n["label"])}</text>')

    formes_n = {n["id"]: m["formes"][pivot.style(n, d)[0]["forme"]] for n in graphe["noeuds"]}
    bouts: dict[str, tuple] = {}          # id d'arête → ((x,y) départ, (x,y) arrivée)
    for a in graphe.get("aretes") or []:
        if a["de"] not in geo or a["vers"] not in geo:
            continue
        t = d["types_arete"].get(a.get("type") or "flux", d["types_arete"]["flux"])
        (cxa, cya), (cxb, cyb) = P(a["de"]), P(a["vers"])
        # DE BORD À BORD, jamais de centre à centre : le trait part du côté qui fait face à
        # l'autre nœud et s'arrête sur le sien. La marge de 4 px laisse la pointe de flèche
        # DÉSIGNER la boîte au lieu de la percer.
        wa, ha = geo[a["de"]][2], geo[a["de"]][3]
        wb, hb = geo[a["vers"]][2], geo[a["vers"]][3]
        # Un CONTOURNEMENT est une suite de points de passage : l'arête vise le premier au
        # départ, vient du dernier à l'arrivée. Sans ça elle repartirait en ligne droite et
        # traverserait précisément ce qu'on cherche à éviter.
        seg_abs = trajet(a, geo, formes_n, chemins)
        seg = [(px - x0, py - y0) for px, py in seg_abs]
        (xa, ya), (xb, yb) = seg[0], seg[-1]
        via = seg[1:-1]
        tiret = m["traits"][t["trait"]]
        dash = "" if tiret == "none" else f' stroke-dasharray="{tiret}"'
        marq = "fc" if m["fleches"][t["fleche"]] == "creuse" else "fl"
        coul = a.get("couleur") or t["couleur"]
        pts = " ".join(f"{px:.0f},{py:.0f}" for px, py in seg)
        out.append(f'<polyline points="{pts}" fill="none" '
                   f'stroke="{coul}" stroke-width="1.2" stroke-opacity="0.55"{dash} '
                   f'marker-end="url(#{marq})"/>')
        # le libellé se pose sur le PLUS LONG segment du trajet : sur un contournement, le
        # segment utile est la voie horizontale, pas le petit bout vertical de raccordement.
        i_max = max(range(len(seg) - 1),
                    key=lambda i: math.dist(seg[i], seg[i + 1]))
        bouts[a["id"]] = (seg[i_max], seg[i_max + 1])

    # Les libellés d'arêtes viennent APRÈS toutes les lignes : les placer au fil de l'eau
    # reviendrait à ignorer les arêtes pas encore tracées, et à empiler les textes.
    _bn = [(gx - gw / 2 - x0, gy - gh / 2 - y0, gx + gw / 2 - x0, gy + gh / 2 - y0)
           for gx, gy, gw, gh in geo.values()]
    for ident, (cx, cy, lw, lh, _libre) in _placer_libelles(
            graphe.get("aretes") or [], bouts, _bn).items():
        lab = _esc(next(a["label"] for a in graphe["aretes"] if a["id"] == ident))
        out.append(f'<rect x="{cx - lw / 2:.0f}" y="{cy - lh / 2:.0f}" width="{lw:.0f}" '
                   f'height="{lh:.0f}" rx="3" fill="{fond}" fill-opacity="0.92"/>'
                   f'<text x="{cx:.0f}" y="{cy + 3:.0f}" font-size="9" fill="#8d97a5" '
                   f'text-anchor="middle">{lab}</text>')

    for n in graphe["noeuds"]:
        if n["id"] not in positions:
            continue
        g, couleur = pivot.style(n, d)
        x, y = P(n["id"])
        forme = m["formes"][g["forme"]]
        # Le contour d'une FEUILLE suit la même facette que celui d'un cadre : deux objets de
        # même forme et de même couleur restent distinguables.
        _d = m["traits"][g.get("contour", "plein")]
        tr = "" if _d == "none" else f' stroke-dasharray="{_d}"'
        w, h = geo[n["id"]][2], geo[n["id"]][3]
        x1, y1 = x - w / 2, y - h / 2
        if forme == "losange":
            out.append(f'<polygon points="{x:.0f},{y1:.0f} {x + w/2:.0f},{y:.0f} '
                       f'{x:.0f},{y1 + h:.0f} {x1:.0f},{y:.0f}" fill="{couleur}" '
                       f'fill-opacity="0.13" stroke="{couleur}" stroke-width="1.5"{tr}/>')
        elif forme == "hexagone":
            q = w / 6
            out.append(f'<polygon points="{x1 + q:.0f},{y1:.0f} {x1 + w - q:.0f},{y1:.0f} '
                       f'{x1 + w:.0f},{y:.0f} {x1 + w - q:.0f},{y1 + h:.0f} '
                       f'{x1 + q:.0f},{y1 + h:.0f} {x1:.0f},{y:.0f}" fill="{couleur}" '
                       f'fill-opacity="0.13" stroke="{couleur}" stroke-width="1.5"{tr}/>')
        elif forme == "cylindre":
            out.append(f'<rect x="{x1:.0f}" y="{y1 + 5:.0f}" width="{w:.0f}" '
                       f'height="{h - 10:.0f}" fill="{couleur}" fill-opacity="0.13" '
                       f'stroke="{couleur}" stroke-width="1.5"/>'
                       f'<ellipse cx="{x:.0f}" cy="{y1 + 5:.0f}" rx="{w/2:.0f}" ry="5" '
                       f'fill="{couleur}" fill-opacity="0.2" stroke="{couleur}" '
                       f'stroke-width="1.5"/>')
        else:
            rx = 9 if forme == "rect_arrondi" else 3
            out.append(f'<rect x="{x1:.0f}" y="{y1:.0f}" width="{w:.0f}" height="{h:.0f}" '
                       f'rx="{rx}" fill="{couleur}" fill-opacity="0.13" stroke="{couleur}" '
                       f'stroke-width="{g.get("epaisseur", 1.5)}"{tr}/>')
        _, lignes = taille_noeud(str(n["label"]))
        y0l = y + 4 - (len(lignes) - 1) * LIGNE_H / 2
        for k, ligne in enumerate(lignes):
            out.append(f'<text x="{x:.0f}" y="{y0l + k * LIGNE_H:.0f}" font-size="12" '
                       f'fill="#e6edf3" text-anchor="middle">{_esc(ligne)}</text>')
    if leg:
        sep = H - leg_h - marge / 2 + 10
        out.append(f'<line x1="{marge / 2:.0f}" y1="{sep:.0f}" x2="{L - marge / 2:.0f}" '
                   f'y2="{sep:.0f}" stroke="#2b3340" stroke-width="1"/>')
        out.extend(leg)
    out.append("</svg>")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────────────────────

def mermaid(graphe: dict, d: dict, positions: dict | None = None) -> str:
    """Mermaid place ses nœuds lui-même : les positions sont ignorées, et c'est déclaré au DSL
    (`besoin_positions: false`) pour qu'on ne croie pas que la disposition est perdue."""
    m = d["moteurs"]["mermaid"]
    lignes, classes = ["flowchart LR"], {}
    enfants: dict[str, list] = {}
    racine = []
    for n in graphe["noeuds"]:
        (enfants.setdefault(n["parent"], []) if n.get("parent") else racine).append(n)

    def ident(s) -> str:
        return "n_" + "".join(c if c.isalnum() else "_" for c in str(s))

    def boite(n: dict) -> str:
        g, couleur = pivot.style(n, d)
        ouvre = m["formes"][g["forme"]]
        mi = len(ouvre) // 2
        classes[ident(n["id"])] = couleur
        return f'{ident(n["id"])}{ouvre[:mi]}"{str(n["label"]).replace(chr(34), chr(39))}"{ouvre[mi:]}'

    for n in racine:
        if n["id"] in enfants:
            lignes.append(f'  subgraph {ident(n["id"])}["{n["label"]}"]')
            for f in enfants[n["id"]]:
                lignes.append("    " + boite(f))
            lignes.append("  end")
        else:
            lignes.append("  " + boite(n))
    for a in graphe.get("aretes") or []:
        t = d["types_arete"].get(a.get("type") or "flux", d["types_arete"]["flux"])
        fleche = m["traits"][t["trait"]]
        lab = str(a.get("label") or "").replace('"', "'")
        lignes.append(f'  {ident(a["de"])} {f"{fleche}|" + chr(34) + lab + chr(34) + "|" if lab else fleche} {ident(a["vers"])}')
    for i, c in classes.items():
        lignes.append(f"  style {i} stroke:{c},stroke-width:2px")
    return "\n".join(lignes)


# ─────────────────────────────────────────────────────────────────────────────────────────────

def drawio(graphe: dict, d: dict, positions: dict, titre: str = "graphe") -> str:
    """Rend un .drawio ÉDITABLE, aux positions calculées ici — draw.io n'a plus à réagencer."""
    m = d["moteurs"]["drawio"]
    L, H = 180, 36
    cells = ['<mxCell id="0"/>', '<mxCell id="1" parent="0"/>']
    xs = [p["x"] for p in positions.values()] or [0]
    ys = [p["y"] for p in positions.values()] or [0]
    x0, y0 = min(xs) - 40, min(ys) - 40
    enfants = {n["id"]: [] for n in graphe["noeuds"]}
    for n in graphe["noeuds"]:
        if n.get("parent") in enfants:
            enfants[n["parent"]].append(n["id"])
    for n in graphe["noeuds"]:
        g, couleur = pivot.style(n, d)
        p = positions.get(n["id"])
        if not p:
            continue
        est_parent = bool(enfants.get(n["id"]))
        cells.append(
            f'<mxCell id="{_esc(n["id"])}" value="{_esc(n["label"])}" '
            f'style="{m["formes"][g["forme"]]};whiteSpace=wrap;html=1;fillColor=#141922;'
            f'strokeColor={couleur};strokeWidth={g.get("epaisseur", 1)};fontColor={couleur};'
            f'verticalAlign={"top" if est_parent else "middle"};" vertex="1" parent="1">'
            f'<mxGeometry x="{p["x"] - x0 - L/2:.0f}" y="{p["y"] - y0 - H/2:.0f}" '
            f'width="{L}" height="{H}" as="geometry"/></mxCell>')
    for a in graphe.get("aretes") or []:
        t = d["types_arete"].get(a.get("type") or "flux", d["types_arete"]["flux"])
        cells.append(
            f'<mxCell id="{_esc(a["id"])}" value="{_esc(a.get("label") or "")}" '
            f'style="endArrow=classic;html=1;{m["traits"][t["trait"]]};'
            f'strokeColor={a.get("couleur") or t["couleur"]};fontColor=#9aa4b2;fontSize=10;" '
            f'edge="1" parent="1" source="{_esc(a["de"])}" target="{_esc(a["vers"])}">'
            f'<mxGeometry relative="1" as="geometry"/></mxCell>')
    corps = "\n    ".join(cells)
    return (f'<mxfile><diagram name="{_esc(titre)}">'
            f'<mxGraphModel dx="900" dy="700" grid="1" gridSize="10" background="#0d1015">'
            f'<root>\n    {corps}\n</root></mxGraphModel></diagram></mxfile>')


MOTEURS = {"cytoscape": cytoscape, "svg": svg, "mermaid": mermaid, "drawio": drawio}
