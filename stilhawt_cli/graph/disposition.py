#!/usr/bin/env python3
"""disposition — poser les x/y d'un graphe pivot, et MESURER la qualité du résultat.

Portage Python de ce qui vivait dans le navigateur. Le déplacement n'est pas cosmétique : une
disposition calculée côté serveur rend le même graphe en SVG, en .drawio ou en Cytoscape sans
recalcul, et se teste sans navigateur. Le JS devient un exécutant qui peint des coordonnées.

SUGIYAMA, en quatre phases — et la première n'est pas optionnelle :

  0. ACYCLIQUE  retourner les arêtes arrière (parcours en profondeur). Le plus long chemin n'est
                défini que sur un graphe sans cycle. Mesuré le 23/09 sur le schéma Frequencies :
                UN seul cycle (`poles ⇄ personnes`) donnait 45 couches pour 20 tables, 385 nœuds
                fictifs, et PLUS de croisements qu'au départ. Le dessin garde le sens réel —
                seul le CALCUL des rangs voit le graphe retourné.
  1. RANGS      plus long chemin depuis une racine ; les arêtes ne descendent plus que d'une
                couche à la suivante, les plus longues reçoivent des nœuds FICTIFS (sans eux,
                une arête traverse tout le dessin en diagonale et croise ce qu'elle rencontre).
  2. ORDRE      heuristique de la MÉDIANE, balayages alternés. On garde le meilleur ordre
                RENCONTRÉ, pas le dernier : la médiane n'est pas monotone.
  3. ABSCISSES  barycentre des voisins, écartement minimal respecté.

LE COMPTEUR DE CROISEMENTS EST LE POINT DE CE MODULE. Sans lui, « c'est plus lisible » est une
opinion et une régression de disposition passe inaperçue. C'est ce chiffre qui a prouvé que la
phase 0 manquait, alors que le dessin « avait l'air » d'un dessin.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from . import pivot


def _g(*chemin, defaut=None):
    """Un réglage de `geometrie` au contrat GRF.

    Le pas d'une pile était un ARGUMENT PAR DÉFAUT ici (`hauteur=46`) pendant que la hauteur
    d'une boîte était une constante dans `moteurs.py` (38) et que le navigateur ajoutait 26 px
    de marge. Trois endroits, aucun accord possible — 24 recouvrements mesurés le 24/09/2026.
    Qui place et qui dessine lisent désormais la même déclaration.
    """
    v = pivot.dsl().get("geometrie") or {}
    for c in chemin:
        v = (v or {}).get(c)
        if v is None:
            return defaut
    return v


# ─────────────────────────────────────────────────────────────────────────────────────────────
# L'oracle : compter les croisements
# ─────────────────────────────────────────────────────────────────────────────────────────────

def _oriente(p, q, r) -> int:
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (v > 0) - (v < 0)


def croisements(positions: dict[str, dict], aretes: Iterable[dict]) -> int:
    """Nombre de paires d'arêtes qui se coupent, sur la GÉOMÉTRIE réelle.

    Les arêtes ADJACENTES (extrémité commune) sont exclues : elles se touchent par construction,
    les compter gonflerait le score d'un montant qu'aucune disposition ne peut faire baisser.
    """
    arr = [a for a in aretes if a["de"] in positions and a["vers"] in positions]
    seg = [((positions[a["de"]]["x"], positions[a["de"]]["y"]),
            (positions[a["vers"]]["x"], positions[a["vers"]]["y"]),
            a["de"], a["vers"]) for a in arr]
    n = 0
    for i in range(len(seg)):
        for j in range(i + 1, len(seg)):
            a1, a2, au, av = seg[i]
            b1, b2, bu, bv = seg[j]
            if {au, av} & {bu, bv}:
                continue
            if _oriente(a1, a2, b1) != _oriente(a1, a2, b2) and \
               _oriente(b1, b2, a1) != _oriente(b1, b2, a2):
                n += 1
    return n


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Sugiyama
# ─────────────────────────────────────────────────────────────────────────────────────────────

def _acyclique(ids: list[str], arcs: list[tuple[str, str]]) -> tuple[list[dict], int]:
    """Phase 0 — retourne les arêtes arrière. Parcours ITÉRATIF : un schéma profond ferait
    déborder la pile d'un parcours récursif, et le débordement se lirait comme un bug d'ailleurs."""
    sortants: dict[str, list[int]] = {i: [] for i in ids}
    for k, (u, _v) in enumerate(arcs):
        if u in sortants:
            sortants[u].append(k)
    etat: dict[str, int] = {}
    arriere: set[int] = set()
    for depart in ids:
        if etat.get(depart):
            continue
        pile = [(depart, iter(sortants[depart]))]
        etat[depart] = 1
        while pile:
            u, it = pile[-1]
            for k in it:
                v = arcs[k][1]
                if etat.get(v) == 1:
                    arriere.add(k)
                elif not etat.get(v):
                    etat[v] = 1
                    pile.append((v, iter(sortants.get(v, []))))
                    break
            else:
                etat[u] = 2
                pile.pop()
    out = [{"de": v, "vers": u, "retournee": True} if k in arriere else {"de": u, "vers": v}
           for k, (u, v) in enumerate(arcs)]
    return out, len(arriere)


def sugiyama(noeuds: list[dict], aretes: list[dict], dx: float | None = None,
             dy: float | None = None, balayages: int | None = None) -> dict:
    """Pose les nœuds en couches. Retourne positions + mesures (croisements, couches, fictifs)."""
    dx = _g("dx", defaut=280) if dx is None else dx
    dy = _g("dy", defaut=150) if dy is None else dy
    balayages = _g("balayages", defaut=10) if balayages is None else balayages
    ids = [n["id"] for n in noeuds]
    ens = set(ids)
    gardees = [a for a in aretes
               if a["de"] in ens and a["vers"] in ens and a["de"] != a["vers"]]
    bruts = [(a["de"], a["vers"]) for a in gardees]
    arcs, n_retournees = _acyclique(ids, bruts)

    # 1. rangs — plus long chemin (bien défini : le graphe est acyclique)
    rang = {i: 0 for i in ids}
    for _ in range(len(ids) + 1):
        bouge = False
        for a in arcs:
            if rang[a["vers"]] < rang[a["de"]] + 1:
                rang[a["vers"]] = rang[a["de"]] + 1
                bouge = True
        if not bouge:
            break

    couches: dict[int, list[str]] = {}
    for i in ids:
        couches.setdefault(rang[i], []).append(i)

    segments: list[tuple[str, str]] = []
    fictifs: set[str] = set()
    chaines: dict[str, list[str]] = {}       # arête → ses nœuds fictifs, dans l'ordre
    for k, a in enumerate(arcs):
        r0, r1 = rang[a["de"]], rang[a["vers"]]
        if r1 - r0 <= 1:
            segments.append((a["de"], a["vers"]))
            continue
        prec, chaine = a["de"], []
        for r in range(r0 + 1, r1):
            f = f"__f{k}_{r}"
            fictifs.add(f)
            rang[f] = r
            couches.setdefault(r, []).append(f)
            segments.append((prec, f))
            chaine.append(f)
            prec = f
        segments.append((prec, a["vers"]))
        # La chaîne suit le graphe RENDU ACYCLIQUE : pour une arête retournée, elle va donc de
        # la cible vers la source. On la remet dans le sens de l'arête d'origine, sinon le
        # trajet dessiné repartirait à l'envers.
        chaines[gardees[k]["id"]] = chaine[::-1] if a.get("retournee") else chaine

    rangs = sorted(couches)
    haut: dict[str, list[str]] = {}
    bas: dict[str, list[str]] = {}
    for u, v in segments:
        haut.setdefault(v, []).append(u)
        bas.setdefault(u, []).append(v)

    def index() -> dict[str, int]:
        return {i: p for r in rangs for p, i in enumerate(couches[r])}

    def croisements_couches() -> int:
        ix, n = index(), 0
        for k in range(len(rangs) - 1):
            segs = [(u, v) for u, v in segments
                    if rang[u] == rangs[k] and rang[v] == rangs[k + 1]]
            for i in range(len(segs)):
                for j in range(i + 1, len(segs)):
                    (u1, v1), (u2, v2) = segs[i], segs[j]
                    if (ix[u1] - ix[u2]) * (ix[v1] - ix[v2]) < 0:
                        n += 1
        return n

    def mediane(i: str, cote: str, ix: dict[str, int]) -> float:
        v = (haut if cote == "haut" else bas).get(i) or []
        if not v:
            return -1.0                                   # sans voisin : ne pas bouger
        p = sorted(ix[x] for x in v)
        m = len(p) // 2
        return float(p[m]) if len(p) % 2 else (p[m - 1] + p[m]) / 2

    # 2. ordre — on garde le MEILLEUR rencontré (la médiane n'est pas monotone)
    meilleur = {r: list(couches[r]) for r in rangs}
    meilleur_n = croisements_couches()
    for s in range(balayages):
        cote = "bas" if s % 2 else "haut"
        ordre = rangs[1:] if cote == "haut" else list(reversed(rangs[:-1]))
        for r in ordre:
            ix = index()
            m = {i: mediane(i, cote, ix) for i in couches[r]}
            fixes = list(enumerate(couches[r]))
            fixes.sort(key=lambda t: (m[t[1]] < 0, m[t[1]] if m[t[1]] >= 0 else 0, t[0]))
            couches[r] = [i for _, i in fixes]
        n = croisements_couches()
        if n < meilleur_n:
            meilleur_n, meilleur = n, {r: list(couches[r]) for r in rangs}
    couches = meilleur

    # 3. abscisses — barycentre puis écartement minimal
    x = {i: p * dx for r in rangs for p, i in enumerate(couches[r])}
    y = {i: rang[i] * dy for r in rangs for i in couches[r]}
    for _ in range(6):
        for r in rangs:
            for i in couches[r]:
                v = (haut.get(i) or []) + (bas.get(i) or [])
                if v:
                    x[i] = sum(x[k] for k in v) / len(v)
            t = sorted(couches[r], key=lambda i: x[i])
            for p in range(1, len(t)):
                if x[t[p]] - x[t[p - 1]] < dx:
                    x[t[p]] = x[t[p - 1]] + dx

    positions = {i: {"x": x[i], "y": y[i]} for i in ids}
    # LES NŒUDS FICTIFS DEVIENNENT DES POINTS DE PASSAGE. Ils étaient déjà calculés — c'est
    # leur rôle dans Sugiyama : forcer une arête longue à passer ENTRE les couches au lieu de
    # les traverser en diagonale. On les jetait au moment de dessiner, donc on payait le prix
    # (des couches plus larges) sans toucher le bénéfice.
    chemins = {i: [(x[f], y[f]) for f in ch] for i, ch in chaines.items() if ch}
    return {"positions": positions, "croisements_couches": meilleur_n,
            "couches": len(rangs), "fictifs": len(fictifs), "aretes_de_cycle": n_retournees,
            "chemins": chemins}


def _ratio(positions: dict[str, dict]) -> float:
    if not positions:
        return 1.0
    xs = [p["x"] for p in positions.values()]
    ys = [p["y"] for p in positions.values()]
    return (max(xs) - min(xs) + 1) / (max(ys) - min(ys) + 1)


def _facteurs(dx: float, dy: float, taille_max: tuple[float, float]) -> tuple[float, float]:
    """Facteurs de transposition DÉRIVÉS des tailles réelles, pas choisis à la main.

    Les valeurs en dur 1,4 / 0,55 ont coûté : après transposition, l'écart entre couches
    (dy × 1,4 = 210 px) était PLUS PETIT que la largeur d'un nœud (jusqu'à 250), donc les boîtes
    de couches voisines se chevauchaient et les arêtes traversaient. Un facteur de mise en page
    se CALCULE à partir de ce qu'on met en page.
    """
    w, h = taille_max
    return max(1.0, (w + 60) / max(dy, 1.0)), max(0.2, (h + 30) / max(dx, 1.0))


def _transposer(positions: dict[str, dict], kx: float = 1.4,
                ky: float = 0.55) -> dict[str, dict]:
    return {k: {"x": p["y"] * kx, "y": p["x"] * ky} for k, p in positions.items()}


def orienter(positions: dict[str, dict], cadre: float = 1.4, mode: str = "auto",
             kx: float = 1.4, ky: float = 0.55) -> tuple[dict[str, dict], str]:
    """Lignes ou colonnes ? On COMPARE le ratio du dessin à celui du cadre et on garde le plus
    proche. Transposer ne change AUCUN croisement — seulement la place occupée, donc c'est un
    choix de place, pas de qualité, et il se tranche sur une mesure."""
    if mode == "lignes":
        return positions, "lignes"
    if mode == "colonnes":
        return _transposer(positions, kx, ky), "colonnes"
    t = _transposer(positions, kx, ky)
    ecart = lambda r: abs(math.log(max(r, 1e-6) / max(cadre, 1e-6)))  # noqa: E731
    return (t, "colonnes") if ecart(_ratio(t)) < ecart(_ratio(positions)) else (positions, "lignes")


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Colonnes (graphes à conteneurs)
# ─────────────────────────────────────────────────────────────────────────────────────────────

def _ordonner_groupes(groupes: list[str], aretes: list[dict]) -> tuple[list[str], dict]:
    """Ordonne les groupes SELON LE FLUX, pas par leur nom.

    Le défaut que ça ferme : ranger les groupes alphabétiquement place « Sources » — l'entrée
    du pipeline — au milieu ou à droite, et chaque flux doit alors traverser les groupes posés
    entre ses deux extrémités. Mesuré sur la vue cible de Frequencies : 39 traversées pour
    9 flux. Le dessin était propre, et il racontait l'histoire dans le désordre.

    On rang les groupes par plus long chemin dans le graphe DES GROUPES (même phase 1 que
    Sugiyama, cycles retournés d'abord), puis on les lit rang par rang. Un flux ne saute alors
    qu'entre colonnes voisines, et ce qu'il traverse devient l'exception au lieu de la règle.
    """
    ens = set(groupes)
    arcs = [(a["de"], a["vers"]) for a in aretes
            if a["de"] in ens and a["vers"] in ens and a["de"] != a["vers"]]
    if not arcs:
        # aucune arête entre groupes : l'ordre n'a pas de sens, tous au même rang
        return groupes, {g: 0 for g in groupes}
    sans_cycle, _ = _acyclique(groupes, arcs)
    rang = {g: 0 for g in groupes}
    for _ in range(len(groupes) + 1):
        bouge = False
        for a in sans_cycle:
            if rang[a["vers"]] < rang[a["de"]] + 1:
                rang[a["vers"]] = rang[a["de"]] + 1
                bouge = True
        if not bouge:
            break
    # à rang égal, l'ordre alphabétique garde la disposition DÉTERMINISTE d'une passe à l'autre
    return sorted(groupes, key=lambda g: (rang[g], g)), rang


def colonnes(noeuds: list[dict], aretes: list[dict], largeur: float | None = None,
             hauteur: float | None = None, ecart_y: float | None = None,
             max_h: float | None = None) -> dict:
    """Pour un graphe à CONTENEURS (un parent et ses enfants) : les layouts de force font
    chevaucher les boîtes parentes, ce qui rend une vue par groupe illisible.

    On ne place que les FEUILLES — le moteur de rendu dimensionne les parents autour. La colonne
    de droite est ordonnée par BARYCENTRE de ses sources : même principe que Sugiyama sur une
    seule couche, et c'est ce qui enlève le gros des croisements ici.
    """
    # `hauteur` est le PAS d'une pile — d'un centre de boîte au suivant. Il vaut ce que le
    # contrat déclare, qui refuse un pas plus court que la boîte la plus haute.
    largeur = _g("dx", defaut=280) if largeur is None else largeur
    hauteur = _g("pile", "pas", defaut=62) if hauteur is None else hauteur
    ecart_y = _g("pile", "ecart_colonnes", defaut=70) if ecart_y is None else ecart_y
    max_h = _g("pile", "hauteur_max", defaut=900) if max_h is None else max_h
    # Les tailles RÉELLES : l'oracle de traversée doit mesurer les vraies boîtes, pas le pas de
    # colonne — sinon il compte des traversées que le dessin n'a pas, et fait « corriger » ce
    # qui va bien.
    try:
        from .moteurs import LIGNE_H, _SVG_H, taille_noeud
        tailles = {}
        for n in noeuds:
            w, lg = taille_noeud(str(n.get("label", "")))
            tailles[n["id"]] = (w, _SVG_H + (len(lg) - 1) * LIGNE_H)
    except Exception:  # noqa: BLE001
        tailles = {}
    enfants: dict[str, list[str]] = {}
    seuls: list[dict] = []
    for n in noeuds:
        if n.get("parent"):
            enfants.setdefault(n["parent"], []).append(n["id"])
    groupes, rang_g = _ordonner_groupes(sorted(g for g in enfants if enfants[g]), aretes)
    ens_groupes = set(groupes)
    for n in noeuds:
        if not n.get("parent") and n["id"] not in ens_groupes:
            seuls.append(n)
    if not groupes:
        return {"positions": {}, "vide": True}

    # UNE COLONNE PAR RANG : le flux se lit de gauche à droite, et un flux entre rangs voisins
    # n'a plus de groupe à traverser. On ne casse une colonne en hauteur qu'à l'intérieur d'un
    # même rang, quand il porte trop de groupes.
    # ⚠ L'ÉCART SÉPARE DES CADRES, PAS DES CONTENUS — et cette phrase vaut sur LES DEUX AXES.
    # L'espacement horizontal avait été corrigé sans l'autre, et le contrôle de lisibilité ne
    # regardait lui aussi que l'horizontale : il rendait « 0 » sur quatre paires de cadres
    # séparées de 36 px. Un contrôle aveugle à un axe est pire qu'aucun — il certifie ce qu'il
    # ne regarde pas. Ici : un cadre = ses enfants + deux marges + le bandeau de son titre.
    _m = _g("groupe", "marge", defaut=16)
    _t = _g("groupe", "hauteur_titre", defaut=26)
    _e_g = _g("pile", "ecart_groupes", defaut=90)
    _h_boite = _g("noeud", "hauteur", defaut=38)

    def _hauteur_cadre(g: str) -> float:
        n = max(len(enfants.get(g, [])), 1)
        return (n - 1) * hauteur + _h_boite + 2 * _m + _t

    pos: dict[str, dict] = {}
    x = col_h = 0.0
    rang_cur = rang_g.get(groupes[0], 0) if groupes else 0
    col_x: dict[str, float] = {}
    for g in groupes:
        h = _hauteur_cadre(g)
        if rang_g.get(g, 0) != rang_cur:
            x, col_h, rang_cur = x + largeur, 0.0, rang_g.get(g, 0)
        elif col_h and col_h + h > max_h:
            x, col_h = x + largeur, 0.0
        col_x[g] = x
        # Le premier enfant se pose SOUS le bandeau de titre et la marge haute : c'est ce qui
        # rend `col_h` égal au bord supérieur du cadre, donc l'écart suivant mesurable.
        for i, e in enumerate(enfants[g]):
            pos[e] = {"x": x + largeur / 2,
                      "y": col_h + _m + _t + _h_boite / 2 + i * hauteur}
        col_h += h + _e_g

    # ── LES COLONNES SE REPLACENT SUR LA LARGEUR RÉELLE DES CADRES ───────────────────────────
    # `dx` espace des NŒUDS ; un groupe est plus large (marges, et un titre qui élargit sa
    # boîte). À pas constant, deux cadres voisins finissaient à 6 px l'un de l'autre. Le dessin
    # n'était pas faux, il était illisible — et c'est ce qui a obligé à le réagencer à la main.
    _ecart_g = _g("pile", "ecart_groupes", defaut=90)
    _marge_c = _g("groupe", "marge", defaut=16)

    def _largeur_cadre(g: str) -> float:
        """Largeur du CADRE dessiné : ses enfants, ses marges, et son titre s'il déborde."""
        fils = [e for e in enfants.get(g, []) if e in pos]
        if not fils:
            return largeur
        interne = max((tailles.get(e) or (largeur, hauteur))[0] for e in fils) + 2 * _marge_c
        try:
            from .moteurs import largeur_texte
            lab = next((n.get("label", "") for n in noeuds if n["id"] == g), "")
            return max(interne, largeur_texte(str(lab), 12, gras=True) + 2 * _marge_c)
        except Exception:  # noqa: BLE001
            return interne

    _cols = sorted({col_x[g] for g in groupes})
    _neuf, _x = {}, 0.0
    for c in _cols:
        _larg = max((_largeur_cadre(g) for g in groupes if col_x[g] == c), default=largeur)
        _neuf[c] = _x + _larg / 2          # les nœuds se centrent dans leur colonne
        _x += _larg + _ecart_g
    for g in groupes:
        for e in enfants[g]:
            if e in pos:
                pos[e] = {"x": _neuf[col_x[g]], "y": pos[e]["y"]}
        col_x[g] = _neuf[col_x[g]] - largeur / 2      # `col_x` reste le BORD GAUCHE conventionnel
    x = _x - _ecart_g - largeur / 2                   # dernière colonne, pour la suite du calcul

    def _poser_droite(p: dict) -> None:
        """Place la colonne de droite au barycentre de ses sources, d'après `p`."""
        cibles = {a["vers"] for a in aretes}
        bary = {}
        for d in seuls:
            src = [p[a["de"]] for a in aretes if a["vers"] == d["id"] and a["de"] in p]
            bary[d["id"]] = sum(q["y"] for q in src) / len(src) if src else math.inf
        vises = sorted((d for d in seuls if d["id"] in cibles), key=lambda d: bary[d["id"]])
        isoles = [d for d in seuls if d["id"] not in cibles]
        for i, d in enumerate(vises):
            p[d["id"]] = {"x": x_droite, "y": i * hauteur}
        for i, d in enumerate(isoles):
            p[d["id"]] = {"x": x_droite + 270, "y": i * hauteur}

    x_droite = x + largeur + 230
    _poser_droite(pos)

    # ── BALAYAGE RETOUR : la colonne de GAUCHE se réordonne sur ses cibles ────────────────────
    # La droite se plaçait au barycentre de ses sources, mais la gauche restait figée sur le rang
    # de flux : un groupe dont les cibles sont en bas restait en haut, et ses arêtes balayaient
    # tous les groupes intermédiaires. Quatre traversées de cadre sur la vue « live », dont trois
    # relevées par la relecture IA et une par le contrôle qu'elle a fait ajouter.
    #
    # C'est le balayage alterné de Sugiyama, appliqué ici : chacun se place au barycentre de
    # l'autre, à tour de rôle. ⚠ ON GARDE LE MEILLEUR ORDRE MESURÉ, pas le dernier — la médiane
    # n'est pas monotone, et deux « améliorations » de disposition ont déjà dégradé le dessin
    # dans ce module. Le chiffre tranche, pas l'intention.
    meilleur = (traversees_groupes(pos, aretes, enfants, largeur, hauteur, tailles), dict(pos), 0)
    p_cur = pos
    for tour in range(1, 5):
        b_g = {}
        for g in groupes:
            cib = [p_cur[a["vers"]]["y"] for a in aretes
                   if a["de"] in enfants.get(g, []) and a["vers"] in p_cur]
            b_g[g] = sum(cib) / len(cib) if cib else math.inf
        # On ne réordonne QU'À L'INTÉRIEUR d'une même colonne : changer de colonne changerait le
        # rang de flux, donc le sens de lecture gauche→droite, qui n'est pas négociable.
        p2, par_col = dict(p_cur), {}
        for g in groupes:
            par_col.setdefault(col_x[g], []).append(g)
        for cx_, gs in par_col.items():
            gs2 = sorted(gs, key=lambda g: (b_g[g], g))
            y = min(min(p_cur[e]["y"] for e in enfants[g]) for g in gs)
            for g in gs2:
                for i, e in enumerate(enfants[g]):
                    p2[e] = {"x": cx_ + largeur / 2, "y": y + i * hauteur}
                y += len(enfants[g]) * hauteur + ecart_y
        _poser_droite(p2)
        t2 = traversees_groupes(p2, aretes, enfants, largeur, hauteur, tailles)
        if t2 < meilleur[0]:
            meilleur = (t2, dict(p2), tour)
        p_cur = p2
    traverse_g, pos, tours = meilleur[0], meilleur[1], meilleur[2]

    # ── CONTOURNEMENT des flux qui SAUTENT des rangs.
    # Un flux entre colonnes voisines ne rencontre rien. Un flux qui saute une colonne traverse
    # forcément ce qui est entre les deux — c'est le cas des 4 flux longs de la vue cible, et
    # aucune réorganisation ne l'évitera. La réponse de Sugiyama est de faire passer l'arête
    # ENTRE les couches, pas à travers : ici, par une voie au-dessus du dessin. Une voie par
    # arête longue, pour qu'elles ne se recouvrent pas non plus entre elles.
    chemins: dict[str, list[tuple[float, float]]] = {}
    if rang_g:
        # ON ROUTE PAR LES GOUTTIÈRES, et c'est la deuxième tentative. La première faisait
        # monter l'arête droit au-dessus de son groupe : elle traversait alors tous les groupes
        # EMPILÉS AU-DESSUS dans la même colonne, et le compte est passé de 27 à 34 — la
        # « correction » avait aggravé le défaut. La mesure l'a dit tout de suite.
        # Un trajet en gouttière ne traverse rien PAR CONSTRUCTION : l'espace entre deux
        # colonnes est vide, et la voie basse passe sous tout le dessin.
        y_bas = max((p["y"] for p in pos.values()), default=0.0)
        longues = [a for a in aretes
                   if a["de"] in rang_g and a["vers"] in rang_g
                   and abs(rang_g[a["vers"]] - rang_g[a["de"]]) > 1]
        for k, a in enumerate(sorted(longues, key=lambda a: a["id"])):
            voie = y_bas + 120 + k * 28
            ys_de = [pos[e]["y"] for e in enfants.get(a["de"], []) if e in pos]
            ys_vers = [pos[e]["y"] for e in enfants.get(a["vers"], []) if e in pos]
            if not ys_de or not ys_vers:
                continue
            # gouttière à DROITE de la colonne de départ, à GAUCHE de celle d'arrivée
            g_de = col_x[a["de"]] + largeur - 18
            g_vers = col_x[a["vers"]] - 18
            chemins[a["id"]] = [(g_de, sum(ys_de) / len(ys_de)), (g_de, voie),
                                (g_vers, voie), (g_vers, sum(ys_vers) / len(ys_vers))]

    # ── VARIANTE : sortir par la GOUTTIÈRE plutôt que de couper à travers ────────────────────
    # Le balayage retour ne gagnait rien (mesuré : 0 amélioration sur 4 tours), parce que le
    # défaut n'est pas un ordre mais une TRAJECTOIRE : une arête qui part d'un groupe et file en
    # diagonale vers sa cible coupe les cadres empilés en dessous, quel que soit leur ordre.
    #
    # La réponse est la même que pour les arêtes longues : un couloir VIDE, à droite de la
    # colonne. Une voie par groupe de départ, sinon toutes les arêtes se superposeraient sur la
    # même verticale. Ça ne traverse rien PAR CONSTRUCTION — mais ça ajoute deux coudes par
    # arête, donc on MESURE les deux et on garde la meilleure. Aucune n'est bonne dans l'absolu :
    # sur un dessin déjà propre, les coudes ne seraient que du bruit.
    parent_de = {e: g for g, fils in enfants.items() for e in fils}
    # ⚠ LA VOIE SE CALCULE SUR LES BORDS RÉELS DES COLONNES. La première version la posait à
    # `col_x + largeur`, c'est-à-dire à l'abscisse de la colonne SUIVANTE : la voie tombait donc
    # DANS les cadres d'à côté, et le trajet censé les contourner les traversait. Constaté à
    # l'image — l'oracle, lui, comptait une amélioration, parce qu'il gagnait ailleurs ce qu'il
    # perdait là. Une gouttière n'est vide que si on la mesure vide.
    # Les bords incluent la MARGE du cadre, comme la boîte que le moteur dessine : sans elle, la
    # voie tombait dans la bande de marge d'un groupe de sa propre colonne, et le détour censé
    # éviter un cadre le traversait — au millimètre près, mais l'oracle le voyait.
    _marge_g = _g("groupe", "marge", defaut=16)
    bords: dict[float, list[float]] = {}
    for g, fils in enfants.items():
        for e in fils:
            if e in pos:
                w = (tailles.get(e) or (largeur, hauteur))[0]
                bords.setdefault(col_x[g], []).append(pos[e]["x"] + w / 2 + _marge_g)
                bords.setdefault(col_x[g], []).append(pos[e]["x"] - w / 2 - _marge_g)
    cols = sorted(bords)
    voies: dict[str, float] = {}
    for g in groupes:
        cx0 = col_x[g]
        droite = max(bords.get(cx0) or [cx0 + largeur / 2])
        suivantes = [c for c in cols if c > cx0]
        gauche = (min(bords[suivantes[0]]) if suivantes else droite + 120)
        # La voie vit dans l'ESPACE LIBRE entre deux colonnes, et chaque groupe a la sienne pour
        # que deux bus ne se superposent pas. On garde 8 px de part et d'autre.
        libre = max(gauche - droite - 16, 8.0)
        k = groupes.index(g)
        voies[g] = droite + 8 + (libre * (k + 1) / (len(groupes) + 1))
    # ON NE DÉTOURNE QUE CE QUI FAUTE. Détourner toutes les arêtes « au cas où » a produit un
    # dessin à zéro traversée et illisible : treize coudes pour trois arêtes en tort, et une
    # bande vide sous tout le schéma. Un remède appliqué là où il n'y a pas de mal est un mal.
    fautives = set()
    for a in aretes:
        if a["id"] in chemins:
            continue
        seule = traversees_groupes(pos, [a], enfants, largeur, hauteur, tailles, chemins)
        if seule:
            fautives.add(a["id"])
    # Les cadres DESSINÉS, pour savoir sous quoi passer : mêmes bornes que l'oracle, sinon le
    # détour raserait un bord qu'il croit éviter.
    _m, _t = _g("groupe", "marge", defaut=16), _g("groupe", "hauteur_titre", defaut=26)
    boites_g: dict[str, tuple] = {}
    for g, fils in enfants.items():
        pts = [(pos[e], (tailles.get(e) or (largeur, hauteur))) for e in fils if e in pos]
        if pts:
            boites_g[g] = (min(q["x"] - w / 2 for q, (w, _h) in pts) - _m,
                           min(q["y"] - h / 2 for q, (_w, h) in pts) - _m - _t,
                           max(q["x"] + w / 2 for q, (w, _h) in pts) + _m,
                           max(q["y"] + h / 2 for q, (_w, h) in pts) + _m)

    par_gouttiere: dict[str, list[tuple[float, float]]] = {}
    for a in aretes:
        g = parent_de.get(a["de"])
        pa, pb = pos.get(a["de"]), pos.get(a["vers"])
        if not g or a["id"] not in fautives or not pa or not pb:
            continue
        if a["vers"] in parent_de:            # cible dans un groupe : autre cas, autre remède
            continue
        gx = voies[g]
        if pb["x"] <= gx + 20:                # la cible est déjà avant la voie : rien à gagner
            continue
        par_gouttiere[a["id"]] = [(gx, pa["y"]), (gx, pb["y"])]

    # TROISIÈME VARIANTE : le couloir BAS. La gouttière ne nettoie que le premier obstacle —
    # une arête qui doit franchir DEUX colonnes de cadres retombe dans la seconde, et un vrai
    # routage orthogonal à évitement d'obstacles est un autre travail. Le couloir sous le dessin,
    # lui, est vide PAR CONSTRUCTION quel que soit le nombre de colonnes. Il coûte un long
    # détour : c'est précisément pourquoi on ne le décrète pas, on le MESURE contre les autres.
    # ⚠ LE DÉTOUR EST LOCAL, il ne descend PAS au pied du canevas. Première version : un couloir
    # global sous tout le dessin. Mesure parfaite (zéro traversée) et dessin abîmé — la relecture
    # IA a lu le faisceau du bas comme « des arêtes qui se terminent dans le vide », et elle
    # avait raison de le lire ainsi : un trait qui parcourt toute la largeur pour contourner un
    # cadre haut de 350 px n'explique plus rien. On passe donc juste SOUS l'obstacle rencontré.
    par_le_bas: dict[str, list[tuple[float, float]]] = {}
    for k, a in enumerate(a2 for a2 in aretes if a2["id"] in par_gouttiere):
        pa, pb = pos[a["de"]], pos[a["vers"]]
        gx = voies[parent_de[a["de"]]]
        genes = [b for g, b in boites_g.items()
                 if parent_de.get(a["de"]) != g and parent_de.get(a["vers"]) != g
                 and _segment_coupe_boite(pa, pb, b)]
        if not genes:
            continue
        y_local = max(b[3] for b in genes) + 24 + k * 12
        # On revient vers la cible juste après le dernier obstacle, pas 150 px avant elle : le
        # retour doit être court, sinon on remplace une fausse dépendance par un long trait muet.
        x_apres = max(b[2] for b in genes) + 26 + k * 12
        par_le_bas[a["id"]] = [(gx, pa["y"]), (gx, y_local),
                               (x_apres, y_local), (x_apres, pb["y"])]

    variantes = [("direct", {}), ("gouttière", par_gouttiere), ("couloir bas", par_le_bas)]
    mesures = [(traversees_groupes(pos, aretes, enfants, largeur, hauteur, tailles,
                                   {**chemins, **v}), nom, v)
               for nom, v in variantes if nom == "direct" or v]
    # ⚠ À ÉGALITÉ, LE PLUS SIMPLE GAGNE — l'ordre de la liste le garantit. Des coudes qui
    # n'achètent aucune traversée ne sont que du bruit, et deux « améliorations » de disposition
    # ont déjà dégradé le dessin dans ce module faute de ce garde-fou.
    traverse_g, routage, retenue = min(mesures, key=lambda m: m[0])
    chemins = {**chemins, **retenue}
    return {"positions": pos, "groupes": len(groupes), "vide": False, "chemins": chemins,
            # Les chiffres sont RENDUS, pas gardés : sans eux, « la disposition s'est
            # améliorée » reste une opinion, et une régression passe au prochain changement.
            "traversees_groupes": traverse_g, "balayages_retenus": tours, "routage": routage}


# ─────────────────────────────────────────────────────────────────────────────────────────────

def traversees(positions: dict[str, dict], aretes: list[dict], chemins: dict,
               tailles: dict[str, tuple[float, float]]) -> int:
    """Combien de fois un trajet coupe la boîte d'un nœud TIERS.

    Deuxième oracle, à côté des croisements : deux arêtes qui se croisent restent lisibles ;
    une arête qui TRAVERSE une boîte suggère une relation qui n'existe pas. C'est lui qui
    permet de choisir une variante de disposition sur une mesure plutôt qu'au jugé.
    """
    boites = {i: (p["x"] - tailles.get(i, (150, 38))[0] / 2,
                  p["y"] - tailles.get(i, (150, 38))[1] / 2,
                  p["x"] + tailles.get(i, (150, 38))[0] / 2,
                  p["y"] + tailles.get(i, (150, 38))[1] / 2) for i, p in positions.items()}
    n = 0
    for a in aretes:
        if a["de"] not in positions or a["vers"] not in positions:
            continue
        pts = [(positions[a["de"]]["x"], positions[a["de"]]["y"]),
               *(chemins or {}).get(a["id"], []),
               (positions[a["vers"]]["x"], positions[a["vers"]]["y"])]
        for i, b in boites.items():
            if i in (a["de"], a["vers"]):
                continue
            if any(_coupe(pts[k], pts[k + 1], b) for k in range(len(pts) - 1)):
                n += 1
    return n


def traversees_groupes(pos: dict, aretes: list[dict], enfants: dict,
                       largeur: float, hauteur: float,
                       tailles: dict | None = None, chemins: dict | None = None) -> int:
    """Combien d'arêtes traversent un groupe ÉTRANGER — celui dont elles ne partent ni n'arrivent.

    Troisième oracle, et il mesure la faute la plus coûteuse sur un livrable d'architecture : un
    trait qui coupe un cadre y dessine une dépendance qui n'existe pas, et ça ne se voit pas en
    relisant le modèle. C'est ce chiffre qui décide de l'ordre des colonnes — au lieu de mon œil,
    qui a déjà « amélioré » deux dispositions en les dégradant.
    """
    # ⚠ LA BOÎTE D'UN GROUPE N'EST PAS LE PAS DE COLONNE. La première version prenait `largeur`
    # (l'écart entre deux colonnes, 280 px) pour la largeur d'une boîte (120 à 250) : les cadres
    # mesurés débordaient dans la gouttière, et l'oracle comptait des traversées là où le dessin
    # n'en a pas. Un oracle trop large fait « corriger » ce qui va bien. On prend donc la taille
    # RÉELLE quand elle est fournie.
    def _demi(i: str) -> tuple[float, float]:
        w, h = (tailles or {}).get(i, (largeur, hauteur))
        return w / 2, h / 2

    # LE CADRE DESSINÉ, pas l'enveloppe des enfants : le moteur ajoute la marge du groupe et le
    # bandeau de son titre. Sans eux, cet oracle mesurait des cadres plus PETITS que ceux du
    # dessin, choisissait un routage sur cette base, et l'auditeur — qui regarde les vraies
    # boîtes — trouvait ensuite des traversées que la disposition croyait avoir évitées. Deux
    # oracles qui ne mesurent pas le même objet ne peuvent pas se contredire utilement.
    marge = _g("groupe", "marge", defaut=16)
    titre = _g("groupe", "hauteur_titre", defaut=26)
    boites, parent = {}, {}
    for g, fils in enfants.items():
        pts = [(pos[e], _demi(e)) for e in fils if e in pos]
        if not pts:
            continue
        boites[g] = (min(q["x"] - dw for q, (dw, _dh) in pts) - marge,
                     min(q["y"] - dh for q, (_dw, dh) in pts) - marge - titre,
                     max(q["x"] + dw for q, (dw, _dh) in pts) + marge,
                     max(q["y"] + dh for q, (_dw, dh) in pts) + marge)
        for e in fils:
            parent[e] = g
    n = 0
    for a in aretes:
        pa, pb = pos.get(a["de"]), pos.get(a["vers"])
        if not pa or not pb:
            continue
        # Le trajet RÉEL : une arête qui passe par une gouttière ne traverse plus ce qu'elle
        # contourne, et l'oracle doit le voir — sinon il refuse le contournement qui corrige.
        via = (chemins or {}).get(a["id"]) or []
        traj = [pa] + [{"x": vx, "y": vy} for vx, vy in via] + [pb]
        for g, b in boites.items():
            if parent.get(a["de"]) == g or parent.get(a["vers"]) == g:
                continue                    # traverser SON groupe pour en sortir est normal
            if any(_segment_coupe_boite(traj[k], traj[k + 1], b)
                   for k in range(len(traj) - 1)):
                n += 1
    return n


def _coupe(p: tuple, q: tuple, b: tuple) -> bool:
    return _segment_coupe_boite({"x": p[0], "y": p[1]}, {"x": q[0], "y": q[1]}, b)


def _segment_coupe_boite(pa: dict, pb: dict, b: tuple) -> bool:
    """Liang-Barsky, sans les cas dégénérés."""
    x1, y1, x2, y2 = pa["x"], pa["y"], pb["x"], pb["y"]
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for pp, qq in ((-dx, x1 - b[0]), (dx, b[2] - x1), (-dy, y1 - b[1]), (dy, b[3] - y1)):
        if abs(pp) < 1e-9:
            if qq < 0:
                return False
            continue
        t = qq / pp
        if pp < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return False
    return True


def disposer(graphe: dict, methode: str | None = None, cadre: float = 1.4,
             orientation: str = "auto", **reglages) -> dict:
    """Pose les positions sur un graphe pivot. Choisit la méthode sur la FORME du graphe quand
    elle n'est pas imposée : des conteneurs → colonnes, sinon → couches."""
    noeuds, aretes = graphe.get("noeuds") or [], graphe.get("aretes") or []
    a_parent = any(n.get("parent") for n in noeuds)
    methode = methode or ("colonnes" if a_parent else "couches")

    if methode == "grille":
        c = max(1, int(math.sqrt(len(noeuds))))
        pos = {n["id"]: {"x": (i % c) * 220.0, "y": (i // c) * 110.0}
               for i, n in enumerate(noeuds)}
        return {"positions": pos, "methode": "grille", "mesures": {
            "croisements": croisements(pos, aretes)}}

    if methode == "colonnes":
        r = colonnes(noeuds, aretes, **{k: v for k, v in reglages.items()
                                        if k in ("largeur", "hauteur", "ecart_y", "max_h")})
        pos = r["positions"]
        return {"positions": pos, "methode": "colonnes", "chemins": r.get("chemins") or {},
                "mesures": {"croisements": croisements(pos, aretes),
                            "groupes": r.get("groupes", 0),
                            # La traversée de cadre est la faute la plus coûteuse d'un livrable
                            # d'archi : elle dessine une dépendance qui n'existe pas. Le chiffre
                            # est affiché pour que sa baisse — ou sa remontée — se constate.
                            "traversees_groupes": r.get("traversees_groupes", 0),
                            "balayages_retenus": r.get("balayages_retenus", 0),
                            "routage": r.get("routage", "direct"),
                            "contournements": len(r.get("chemins") or {})}}

    # couches — on mesure AVANT (rangs seuls, sans ordonnancement) pour que le gain soit dit
    feuilles = [n for n in noeuds if not n.get("parent")]
    avant = sugiyama(feuilles, aretes, balayages=0,
                     **{k: v for k, v in reglages.items() if k in ("dx", "dy")})
    r = sugiyama(feuilles, aretes, balayages=reglages.get("balayages", 10),
                 **{k: v for k, v in reglages.items() if k in ("dx", "dy")})
    # Les tailles RÉELLES des nœuds décident des facteurs de transposition (et l'écart entre
    # couches doit dépasser la plus large boîte, sinon deux couches se recouvrent).
    try:
        from .moteurs import taille_noeud
        tailles = [taille_noeud(str(n.get("label", ""))) for n in feuilles]
        t_max = (max((w for w, _l in tailles), default=150.0),
                 max((38 + (len(l) - 1) * 15 for _w, l in tailles), default=38.0))
    except Exception:  # noqa: BLE001
        t_max = (150.0, 38.0)
    _dx = reglages.get("dx", _g("dx", defaut=210))
    _dy = reglages.get("dy", _g("dy", defaut=150))
    kx, ky = _facteurs(_dx, _dy, t_max)
    # ⚠ MESURE CONTRE INTUITION : dériver les facteurs des tailles paraissait plus juste que
    # 1,4 / 0,55 écrits à la main — et a fait passer les traversées de 4 à 8 sur le modèle de
    # données. On garde donc les facteurs qui MESURENT le mieux, et on note l'écart au lieu de
    # le rationaliser. (Les valeurs dérivées restent calculées : elles servent de plancher.)
    kx, ky = max(kx, 1.4) if t_max[0] > 210 else 1.4, 0.55
    pos, sens = orienter(r["positions"], cadre, orientation, kx, ky)
    # Les points de passage subissent EXACTEMENT la même transformation que les nœuds : sans
    # ça, le dessin est transposé et les contournements restent dans l'ancien repère — les
    # arêtes partiraient traverser le dessin en travers, l'inverse de ce qu'on cherche.
    chemins = r.get("chemins") or {}
    if sens == "colonnes":
        chemins = {i: [(y * kx, x * ky) for x, y in pts] for i, pts in chemins.items()}
    # LES NŒUDS FICTIFS NE SONT PAS TOUJOURS UN GAIN, et c'est contre-intuitif : ils sont la
    # réponse canonique de Sugiyama aux arêtes longues, mais ils déplacent aussi le trajet vers
    # des couloirs déjà occupés. Mesuré sur le modèle de données : 4 traversées AVEC, 3 SANS.
    # On calcule donc les DEUX et on garde celle qui mesure le mieux — le même principe que
    # l'orientation. Aucune des deux n'est « la bonne » dans l'absolu.
    tailles = {}
    for n in feuilles:
        try:
            w, lg = taille_noeud(str(n.get("label", "")))
            tailles[n["id"]] = (w, 38 + (len(lg) - 1) * 15)
        except Exception:  # noqa: BLE001
            tailles[n["id"]] = (150.0, 38.0)
    t_avec = traversees(pos, aretes, chemins, tailles)
    t_sans = traversees(pos, aretes, {}, tailles)
    if t_sans <= t_avec:
        chemins, retenu, traverse = {}, "sans", t_sans
    else:
        retenu, traverse = "points de passage", t_avec
    return {"positions": pos, "methode": "couches", "orientation": sens, "chemins": chemins,
            "mesures": {
                "croisements_avant": croisements(avant["positions"], aretes),
                "croisements": croisements(pos, aretes),
                "couches": r["couches"], "fictifs": r["fictifs"],
                "contournements": len(chemins), "routage": retenu,
                "traversees": traverse, "traversees_ecartees": max(t_avec, t_sans),
                "aretes_de_cycle": r["aretes_de_cycle"]}}
