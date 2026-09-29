#!/usr/bin/env python3
"""pivot — le modèle neutre et sa validation (GRF).

Un producteur émet `{noeuds, aretes}` dans un vocabulaire FERMÉ : cinq formes, cinq traits,
aucun nom de bibliothèque. Un graphe qui passe ici se rend à l'identique par tous les moteurs —
c'est tout l'objet de la validation. Écrire `barrel` (un mot de Cytoscape) devient indisable,
donc un producteur ne peut pas devenir inrendable ailleurs sans qu'on le voie tout de suite.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DSL = Path(__file__).resolve().with_name("graphe.dsl.yaml")
_CACHE: dict | None = None


def dsl() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = yaml.safe_load(DSL.read_text(encoding="utf-8"))
    return _CACHE


def valider(graphe: dict, d: dict | None = None) -> list[str]:
    """Violations du modèle pivot. Liste vide = rendable par TOUS les moteurs."""
    d = d or dsl()
    genres, types = d["genres"], d["types_arete"]
    req_n, req_a = d["pivot"]["noeud_requis"], d["pivot"]["arete_requise"]
    err: list[str] = []
    vus: set[str] = set()
    for n in graphe.get("noeuds") or []:
        manquants = [c for c in req_n if not n.get(c)]
        if manquants:
            err.append(f"nœud {n.get('id', '?')!r} : champ(s) requis manquant(s) "
                       f"{', '.join(manquants)}")
            continue
        if n["genre"] not in genres:
            err.append(f"nœud {n['id']!r} : genre {n['genre']!r} hors vocabulaire "
                       f"({', '.join(genres)})")
        if n["id"] in vus:
            err.append(f"identifiant en double : {n['id']!r}")
        vus.add(n["id"])
    for n in graphe.get("noeuds") or []:
        if n.get("parent") and n["parent"] not in vus:
            err.append(f"nœud {n.get('id')!r} : parent {n['parent']!r} inconnu")
        if n.get("parent") and n.get("parent") == n.get("id"):
            err.append(f"nœud {n['id']!r} : se déclare son propre parent")
    for a in graphe.get("aretes") or []:
        manquants = [c for c in req_a if not a.get(c)]
        if manquants:
            err.append(f"arête {a.get('id', '?')!r} : champ(s) requis manquant(s) "
                       f"{', '.join(manquants)}")
            continue
        for bout in ("de", "vers"):
            if a[bout] not in vus:
                err.append(f"arête {a['id']!r} : {bout} {a[bout]!r} ne désigne aucun nœud")
        if a.get("type") and a["type"] not in types:
            err.append(f"arête {a['id']!r} : type {a['type']!r} non déclaré "
                       f"({', '.join(types)})")
    # UN CONTENEUR VIDE N'EST PAS UN CONTENEUR — et c'est le pivot qui doit le dire, parce que
    # chaque moteur s'en tire autrement : le SVG lui invente une boîte par défaut, Cytoscape le
    # rend à la taille de son libellé (61 × 12 px, le cadre sur les lettres). Le même modèle
    # donne alors deux dessins, ce qui est exactement ce que ce vocabulaire existe pour empêcher.
    # Constaté le 24/09/2026 : six applications sans entité, déclarées `groupe`, s'affichaient
    # comme des étiquettes nues à côté de groupes bien cadrés.
    parents = {n.get("parent") for n in (graphe.get("noeuds") or []) if n.get("parent")}
    for n in graphe.get("noeuds") or []:
        g = genres.get(n.get("genre") or "")
        if g and g.get("conteneur") and n.get("id") not in parents:
            err.append(f"nœud {n.get('id')!r} : genre conteneur {n.get('genre')!r} mais AUCUN "
                       f"enfant — un conteneur vide se rend différemment selon le moteur")
    return err


def style(n: dict, d: dict | None = None) -> tuple[dict, str]:
    """Genre + couleur d'état d'un nœud. Un état inconnu retombe sur `neutre` : un graphe ne
    doit pas devenir illisible parce qu'un producteur a inventé un état."""
    d = d or dsl()
    g = d["genres"].get(n["genre"], d["genres"]["bloc"])
    e = d["etats"].get(n.get("etat") or "neutre", d["etats"]["neutre"])
    return g, e["couleur"]


def check(d: dict | None = None) -> list[str]:
    """Le DSL lui-même : chaque moteur doit savoir traduire TOUTES les formes et tous les traits.
    C'est ce contrôle qui empêche « agnostique » de rester une intention."""
    d = d or dsl()
    err: list[str] = []
    formes = {g["forme"] for g in d["genres"].values()}
    # Le CONTOUR d'un nœud réemploie le vocabulaire des traits d'arête : un moteur qui sait
    # tracer une arête en tirets sait border une boîte en tirets. Un vocabulaire de plus serait
    # un vocabulaire de plus à traduire, pour la même idée.
    traits = ({t["trait"] for t in d["types_arete"].values()}
              | {g.get("contour", "plein") for g in d["genres"].values()})
    fleches = {t["fleche"] for t in d["types_arete"].values()}
    # NIVEAU 3 — chaque moteur DIT comment il se comporte face à la géométrie du niveau 2.
    # Sans cette déclaration, un moteur qui ajoute ses propres marges passe inaperçu : c'est
    # exactement ce qui est arrivé à Cytoscape, dont le `padding` s'ajoutait à la taille servie
    # pendant que le SVG, lui, dessinait juste. Le vocabulaire est FERMÉ : on ne peut pas
    # inventer un quatrième comportement sans l'écrire ici.
    RESPECTS = {"taille_servie", "dessine_directement", "place_lui_meme"}
    for nom, m in d["moteurs"].items():
        for f in formes - set(m.get("formes") or {}):
            err.append(f"moteur `{nom}` : forme `{f}` non traduite")
        for t in traits - set(m.get("traits") or {}):
            err.append(f"moteur `{nom}` : trait `{t}` non traduit")
        if "fleches" in m:
            for f in fleches - set(m["fleches"]):
                err.append(f"moteur `{nom}` : flèche `{f}` non traduite")
        g3 = (m.get("geometrie") or {}).get("respecte")
        if g3 not in RESPECTS:
            err.append(f"moteur `{nom}` : `geometrie.respecte` absent ou hors vocabulaire "
                       f"({', '.join(sorted(RESPECTS))}) — un moteur qui ne dit pas ce qu'il "
                       f"fait de la géométrie peut la trahir sans qu'on le voie")
    if sum(1 for m in d["moteurs"].values() if m.get("defaut")) != 1:
        err.append("il faut exactement un moteur `defaut: true`")
    if sum(1 for x in d["dispositions"].values() if x.get("defaut")) != 1:
        err.append("il faut exactement une disposition `defaut: true`")
    if "neutre" not in d["etats"]:
        err.append("l'état `neutre` est obligatoire — c'est le repli d'un état inconnu")
    # Chaque type d'arête et chaque état doivent porter un LIBELLÉ : c'est ce que la légende
    # affiche. Sans lui elle montrerait la clé technique, qui n'explique rien au lecteur.
    for nom, t in d["types_arete"].items():
        if not (t or {}).get("label"):
            err.append(f"type d'arête `{nom}` sans `label` — la légende afficherait la clé")
    for nom, e in d["etats"].items():
        if not (e or {}).get("label"):
            err.append(f"état `{nom}` sans `label` — la légende afficherait la clé")
    # Un GENRE aussi porte un mot : c'est lui qui dit au lecteur ce qu'est la boîte qu'il voit.
    for nom, g in d["genres"].items():
        if not (g or {}).get("label"):
            err.append(f"genre `{nom}` sans `label` — le lecteur verrait la clé technique")
    err += _check_geometrie(d.get("geometrie") or {})
    return err


def _check_geometrie(g: dict) -> list[str]:
    """La géométrie déclarée est-elle TENABLE ? Un recouvrement par construction se refuse ici.

    Le pas d'une pile et la hauteur d'une boîte vivaient à deux endroits différents, sans rien
    pour les confronter : une pile au pas de 46 px empilait des boîtes de 53 px (deux lignes de
    libellé) et personne ne pouvait le voir avant le dessin. Une contrainte qui se vérifie par
    arithmétique n'a aucune raison d'attendre le rendu.
    """
    err, n, p, gr = [], g.get("noeud") or {}, g.get("pile") or {}, g.get("groupe") or {}
    if not n or not p:
        return ["`geometrie` doit déclarer `noeud` et `pile` — sinon la taille des boîtes et "
                "l'espace entre elles se décident ailleurs, et divergent"]
    manquants = [c for c in ("largeur_min", "largeur_max", "hauteur", "hauteur_ligne",
                             "marge_texte") if n.get(c) is None]
    if manquants:
        err.append(f"`geometrie.noeud` : {', '.join(manquants)} manquant(s)")
    if gr.get("marge") is None or gr.get("hauteur_titre") is None:
        err.append("`geometrie.groupe` : `marge` et `hauteur_titre` sont requis — le cadre d'un "
                   "groupe et l'espace de son titre ne se devinent pas")
    if n.get("largeur_min") and n.get("largeur_max") and n["largeur_min"] >= n["largeur_max"]:
        err.append(f"`noeud.largeur_min` ({n['largeur_min']}) ≥ `largeur_max` ({n['largeur_max']})")
    # L'INVARIANT QUI COMPTE : le pas doit loger la PLUS HAUTE boîte possible, pas la plus
    # courante. Une boîte à deux lignes est un cas normal, pas une exception.
    haute = (n.get("hauteur") or 0) + (n.get("hauteur_ligne") or 0)
    if p.get("pas") is not None and p["pas"] <= haute:
        err.append(f"`pile.pas` ({p['pas']}) ne loge pas une boîte de {haute} px (hauteur + une "
                   f"deuxième ligne de libellé) — deux voisines se recouvrent PAR CONSTRUCTION, "
                   f"et aucun réglage d'affichage ne peut le rattraper")
    # Même raisonnement à l'horizontale : deux couches plus serrées que la boîte la plus large
    # se chevauchent dès qu'un libellé long apparaît.
    if g.get("dx") is not None and n.get("largeur_max") and g["dx"] <= n["largeur_max"]:
        err.append(f"`geometrie.dx` ({g['dx']}) ne loge pas une boîte de largeur maximale "
                   f"({n['largeur_max']}) — deux colonnes voisines se chevaucheraient")
    return err
