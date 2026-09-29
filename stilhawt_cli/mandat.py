"""mandat.py — le moteur du contrat MND : ce qu'un agent IA a le droit de toucher.

    python -m stilhawt_cli.mandat --check
    python -m stilhawt_cli.mandat --lister
    python -m stilhawt_cli.mandat --montrer promo.sujet_neuf
    python -m stilhawt_cli.mandat --selftest

CE QUE CE MODULE FAIT, ET RIEN D'AUTRE : il lit `mandats/mandats.dsl.yaml`, le REFUSE s'il est
mal formé, et DÉRIVE les `--allowedTools` d'un mandat. La barrière est l'octroi ; ce fichier ne
lit aucun prompt et ne juge aucune intention.

⚠ POURQUOI LA DÉRIVATION COMPTE. Tant que chaque appelant écrit sa liste `--allowedTools` à la
main, la liste et le raisonnement qui l'a produite vivent à deux endroits, et la seconde dérive
sans bruit. Ici le mandat déclare des CHEMINS et des COMMANDES, le moteur produit la syntaxe du
fournisseur. Le jour où `--allowedTools` change de forme, un seul fichier bouge.

⚠ CE MODULE NE GARANTIT PAS L'ÉTANCHÉITÉ. Il pose ce que le CLI accepte de ne pas faire ; il ne
met pas l'agent en cage. Le confinement fort reste le conteneur jetable de `verified_loop`. Dire
l'inverse serait la fausse réassurance que le workspace s'interdit explicitement — un périmètre
déclaré réduit le rayon de souffle, il ne l'annule pas.
"""
from __future__ import annotations

import argparse
import os
import sys
from contextlib import contextmanager
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

RACINE = Path(__file__).resolve().parent.parent
# STILHAWT_MANDATS names another contract; without the workspace's, the example shipped beside this
# module (a public install, OSS-PLAN B3) — found, never guessed: it is the file next to the code.
_ATELIER = RACINE / "mandats" / "mandats.dsl.yaml"
CONTRAT = Path(os.environ.get("STILHAWT_MANDATS")
               or (_ATELIER if _ATELIER.is_file() else Path(__file__).with_name("mandats.dsl.yaml")))

# Ce qu'un mandat DOIT déclarer. Vocabulaire fermé : une clé de plus est refusée, parce qu'un
# champ d'apparence normative que personne n'interprète est pire qu'un champ absent.
CHAMPS = {"id", "pour", "racine", "outils", "modeles", "budget", "preuve", "destructif"}
# Le seul champ FACULTATIF, et il ne l'est qu'au sens où l'on peut s'en passer : dès qu'un joker
# apparaît en écriture, il devient obligatoire. Une exception se déclare, elle ne se tolère pas.
FACULTATIFS = {"ecrire_joker_pourquoi", "voisinage"}
CHAMPS_PREUVE = {"ecrit", "artefact", "secondes_mini"}
# VOISINAGE (l'utilisateur, 26/09/2026 : « il faut travailler à la frontière : chaque agent a connaissance de ses
# voisins et de leur zone d'influence »). Les outils disent ce qu'un agent peut TOUCHER ; le voisinage dit ce qu'il
# doit VOIR pour ne pas refaire, ou contredire, ce qu'un voisin couvre déjà. Chaque entrée nomme le RÔLE qui
# voit, le VOISIN (un autre mandat déclaré, ou un chemin réel sous la racine du workspace), sa ZONE d'influence
# en une phrase, et le code qui le lui MONTRE (`vu_par`) — sans ce dernier, le voisinage serait une promesse.
# DISTANCE (l'utilisateur, 26/09/2026 : « une connaissance décroissante en fonction de l'éloignement ») : 0 = le propre
# domaine de l'agent, connu en ENTIER ; 1 = un voisin direct, connu par son rôle et son contrat ; au-delà, de moins
# en moins — jamais les internes d'un autre agent.
CHAMPS_VOISIN = {"role", "voisin", "zone", "vu_par", "distance"}

# Une racine peut être un dossier FIXE, ou naître à l'exécution (bac jetable, worktree). Le
# second cas est déclaré par ce mot : le mandat dit alors que l'appelant DOIT fournir la racine,
# et le moteur refuse de l'accorder sans. Laisser un champ vide voudrait dire « n'importe où ».
RACINE_DYNAMIQUE = "dynamique"


class Refus(ValueError):
    """Le contrat, ou un mandat, n'est pas recevable."""


def charger(fichier: Path | None = None) -> dict:
    import yaml
    f = fichier or CONTRAT
    if not f.is_file():
        raise Refus(f"contrat absent : {f}")
    return yaml.safe_load(f.read_text(encoding="utf-8"))


# ───────────────────────────── fonctions pures ─────────────────────────────

def chemin_sur(chemin: str) -> bool:
    """Ce chemin reste-t-il DANS le périmètre ? Fonction PURE.

    Refuse l'absolu (`C:\\…`, `/etc/…`) et toute remontée (`..`). Un mandat qui nommerait
    `C:\\Users\\…\\.ssh\\id_ed25519` en écriture serait syntaxiquement valide et
    catastrophique ; la racine ne borne rien si les chemins peuvent en sortir.

    ⚠ L'ARTEFACT DE PREUVE, lui, a le droit de remonter — il vit souvent dans un dossier voisin
    (`..\\stilhawt-autopilot\\data\\demos\\…`). Mais c'est une LECTURE de contrôle faite par
    l'appelant, jamais un octroi à l'agent : la distinction est portée par `griefs`, qui
    n'applique cette règle qu'aux outils.
    """
    if not chemin or not chemin.strip():
        return False
    c = chemin.replace("\\", "/")
    if c.startswith("/") or (len(c) > 1 and c[1] == ":"):
        return False
    return ".." not in c.split("/")


def commande_nommee(commande: str) -> bool:
    """Cette commande est-elle NOMMÉE, ou est-ce un blanc-seing ? Fonction PURE.

    `python tournage.py` nomme un programme. `python`, `bash -c`, `*` ou une chaîne vide
    accordent tout ce que l'interpréteur sait faire, c'est-à-dire le disque entier.
    """
    if not commande or not commande.strip():
        return False
    morceaux = commande.split()
    if "*" in commande:
        return False
    if morceaux[0] in {"bash", "sh", "cmd", "powershell", "pwsh"}:
        return False
    # Un interpréteur doit être suivi de ce qu'il exécute.
    if morceaux[0] in {"python", "pythonw", "node", "deno", "ruby", "perl"} and len(morceaux) < 2:
        return False
    return True


def outils_cli(mandat: dict, familles: dict, modeles_connus: dict | None = None,
               serveur: str = "modeles") -> list[str]:
    """Le mandat → la liste `--allowedTools` du CLI. Fonction PURE.

    C'est ICI, et nulle part ailleurs, que la syntaxe du fournisseur est écrite.

    ⚠ LES DÉLÉGUÉS AUSSI SONT DES OUTILS. `modeles: [groq, jev]` ne décorait rien tant que le
    champ ne produisait pas d'octroi : il se traduit désormais en `mcp__<serveur>__<verbe>`.
    C'est ce qui ferme la boucle — autoriser un sous-modèle dans le contrat et l'autoriser au
    CLI étaient deux gestes séparés, donc deux vérités qui auraient divergé.
    """
    rendu: list[str] = []
    for famille, valeurs in (mandat.get("outils") or {}).items():
        spec = familles.get(famille)
        if spec is None:
            raise Refus(f"famille « {famille} » hors du vocabulaire {sorted(familles)}")
        for outil in spec["rend"]:
            if spec["portee"] == "aucune":
                rendu.append(outil)
            elif spec["portee"] == "cli":
                # `stilhawt git status` → `Bash(stilhawt git status:*)` : le verbe est NOMMÉ, ses
                # options passent. Le CLI re-vérifie le mandat à l'exécution (pipes de modèle).
                for v in valeurs or []:
                    rendu.append(f"{outil}(stilhawt {v}:*)")
            else:
                for v in valeurs or []:
                    rendu.append(f"{outil}({v})" if spec["portee"] == "chemins"
                                 else f"{outil}({v}:*)")
    for nom in mandat.get("modeles") or []:
        spec = (modeles_connus or {}).get(nom) or {}
        verbe = spec.get("outil")
        if not verbe:
            raise Refus(f"délégué « {nom} » n'expose aucun verbe : l'accorder produirait un "
                        f"octroi qui ne désigne rien")
        rendu.append(f"mcp__{serveur}__{verbe}")
    return rendu


def griefs_cli(valeurs: list[str], grammaire: dict | None = None) -> list[str]:
    """Ce qu'un octroi `cli` a le droit de nommer. PURE si `grammaire` est fournie.

    Une commande `<espace> <verbe>` du CLI, en LECTURE — ou un pipe PUR. Jamais un pipe de
    modèle (il s'accorde par `modeles`), jamais une commande hors grammaire (l'octroi ne
    désignerait rien), jamais un blanc-seing (`stilhawt` seul, une étoile).
    """
    if grammaire is None:
        from stilhawt_cli.grammar import load as _load   # the grammar alone, never the engine (SBR)
        grammaire = _load()
    g = []
    pipes = grammaire.get("pipes") or {}
    espaces = grammaire.get("namespaces") or {}
    for v in valeurs:
        mots = str(v).split()
        if not mots or "*" in str(v):
            g.append(f"octroi cli « {v} » : blanc-seing — nommer la commande")
        elif len(mots) == 1:
            p = pipes.get(mots[0])
            if p is None:
                g.append(f"octroi cli « {v} » : ni pipe ni commande `<espace> <verbe>`")
            elif p.get("mode"):
                g.append(f"octroi cli « {v} » : pipe de MODÈLE — il s'accorde par `modeles`, "
                         f"pas par une ligne de CLI")
            elif p.get("effect", "read") != "read":
                g.append(f"octroi cli « {v} » : effet « {p.get('effect')} » — un agent lit, il "
                         f"n'affiche ni n'envoie rien (seule la LECTURE s'accorde)")
        elif len(mots) == 2:
            c = ((espaces.get(mots[0]) or {}).get("commands") or {}).get(mots[1])
            if c is None:
                g.append(f"octroi cli « {v} » : commande inconnue de la grammaire du CLI")
            elif c.get("effect") != "read":
                g.append(f"octroi cli « {v} » : effet « {c.get('effect')} » — seule la LECTURE "
                         f"s'accorde en v0")
        else:
            g.append(f"octroi cli « {v} » : trop de mots — `<espace> <verbe>`, les options passent seules")
    return g


def griefs_voisinage(m: dict, ids: set[str], racine_ws: Path | None) -> list[str]:
    """Le voisinage d'un mandat. Sans `racine_ws`, la forme seule ; avec, l'existence sur le disque aussi."""
    ou = f"mandat « {m.get('id') or '(sans id)'} »"
    g = []
    for i, v in enumerate(m.get("voisinage") or []):
        if not isinstance(v, dict):
            g.append(f"{ou} : voisinage[{i}] n'est pas un objet")
            continue
        if set(v) - CHAMPS_VOISIN:
            g.append(f"{ou} : voisinage[{i}] — champ(s) hors vocabulaire {sorted(set(v) - CHAMPS_VOISIN)}")
        for k in sorted(CHAMPS_VOISIN - {"distance"}):
            if not str(v.get(k) or "").strip():
                g.append(f"{ou} : voisinage[{i}] — « {k} » manque (un voisin sans {k} ne sert à rien)")
        d = v.get("distance")
        if not isinstance(d, int) or isinstance(d, bool) or d < 0:
            g.append(f"{ou} : voisinage[{i}] — « distance » manque ou invalide (entier ≥ 0 : 0 = son propre domaine, "
                     f"1 = voisin direct, …)")
        if racine_ws is None:
            continue
        voisin, vu = str(v.get("voisin") or ""), str(v.get("vu_par") or "")
        if voisin and voisin not in ids and not (racine_ws / voisin).exists():
            g.append(f"{ou} : voisinage[{i}] — voisin « {voisin} » : ni mandat déclaré, ni chemin réel")
        if vu and not (racine_ws / vu).is_file():
            g.append(f"{ou} : voisinage[{i}] — « {vu} » (vu_par) introuvable : le voisin n'est montré par rien")
    return g


def griefs(doc: dict, grammaire_cli: dict | None = None, racine_ws: Path | None = None) -> list[str]:
    """Tout ce qui rend le contrat irrecevable. Fonction PURE (sauf `racine_ws` : existence des voisins), et c'est
    elle qui a les dents."""
    g: list[str] = []
    ids = {m.get("id") for m in doc.get("mandats") or []}
    for m in doc.get("mandats") or []:
        g += griefs_voisinage(m, ids, racine_ws)
    familles = doc.get("familles") or {}
    modeles = doc.get("modeles_connus") or {}
    if not familles:
        return ["aucune famille d'outils déclarée : le vocabulaire fermé est vide"]

    vus = set()
    for m in doc.get("mandats") or []:
        mid = m.get("id") or "(sans id)"
        ou = f"mandat « {mid} »"
        if mid in vus:
            g.append(f"{ou} : identifiant en double")
        vus.add(mid)

        inconnus = set(m) - CHAMPS - FACULTATIFS
        if inconnus:
            g.append(f"{ou} : champ(s) hors vocabulaire {sorted(inconnus)}")
        for champ in CHAMPS:
            if champ not in m:
                g.append(f"{ou} : « {champ} » manque — aucun champ n'est facultatif")

        for famille, valeurs in (m.get("outils") or {}).items():
            spec = familles.get(famille)
            if spec is None:
                g.append(f"{ou} : famille « {famille} » hors de {sorted(familles)}")
                continue
            if spec["portee"] == "chemins":
                for v in valeurs or []:
                    if not chemin_sur(v):
                        g.append(f"{ou} : « {v} » sort du périmètre (absolu ou remontée)")
                    # ⚠ UN JOKER EN ÉCRITURE NE BORNE RIEN, et c'est le piège de ce contrat :
                    # `Edit(**)` a l'air d'un périmètre et n'en est pas un. On ne l'interdit
                    # pas — `verified_loop` en a un besoin réel, son agent édite un bac entier —
                    # mais on exige que la raison soit ÉCRITE, pour que la faiblesse soit lisible
                    # dans le contrat au lieu d'être cachée dans un glob.
                    joker = "*" in v or "?" in v
                    if famille == "ecrire" and joker and not m.get("ecrire_joker_pourquoi"):
                        g.append(f"{ou} : écriture par joker « {v} » sans justification — "
                                 f"un joker en écriture ne borne rien, déclarer "
                                 f"`ecrire_joker_pourquoi`")
            elif spec["portee"] == "commandes":
                for v in valeurs or []:
                    if not commande_nommee(v):
                        g.append(f"{ou} : « {v} » n'est pas une commande nommée — blanc-seing")
            elif spec["portee"] == "cli":
                g += [f"{ou} : {x}" for x in griefs_cli(valeurs or [], grammaire_cli)]
            elif spec["portee"] == "aucune" and valeurs:
                g.append(f"{ou} : la famille « {famille} » ne se borne pas ; "
                         f"la lister avec des valeurs laisse croire l'inverse")

        for nom in m.get("modeles") or []:
            if nom not in modeles:
                g.append(f"{ou} : délégué « {nom} » hors de {sorted(modeles)}")
            elif not modeles[nom].get("foyer"):
                g.append(f"{ou} : délégué « {nom} » n'a pas de foyer — il n'est pas branché, "
                         f"l'accorder promet une capacité qui n'existe pas")
            elif not modeles[nom].get("cout"):
                g.append(f"{ou} : délégué « {nom} » ne déclare pas son coût")

        racine = m.get("racine")
        if not racine or not str(racine).strip():
            g.append(f"{ou} : racine vide — cela voudrait dire « n'importe où »")

        minutes = (m.get("budget") or {}).get("minutes")
        if not isinstance(minutes, (int, float)) or minutes <= 0:
            g.append(f"{ou} : budget.minutes doit être un nombre positif")

        preuve = m.get("preuve") or {}
        if set(preuve) - CHAMPS_PREUVE:
            g.append(f"{ou} : preuve — champ(s) inconnu(s) {sorted(set(preuve) - CHAMPS_PREUVE)}")
        if not preuve.get("ecrit") and not preuve.get("artefact"):
            g.append(f"{ou} : aucune preuve de travail déclarée — sans elle, « l'agent n'a rien "
                     f"fait » et « le CLI n'était pas authentifié » rendent le même résultat")
        if not isinstance(preuve.get("secondes_mini"), (int, float)):
            g.append(f"{ou} : preuve.secondes_mini manque")

        if m.get("destructif") and not m.get("pour"):
            g.append(f"{ou} : destructif sans justification")
    return g


def verdict(mandat: dict, code: int, secondes: float, ecrit: bool, artefact: bool) -> dict:
    """Ce que l'agent a RÉELLEMENT produit, jugé sur la preuve DÉCLARÉE. Fonction PURE.

    ⚠ `a_travaille` n'est pas `code == 0`. Un CLI non authentifié rend 0 en dix secondes sans
    rien tenter, et ce cas ressemble trait pour trait à « l'agent a examiné et renoncé ». Trois
    runs muets ont été publiés comme un résultat le 18/09 pour cette raison exacte.
    """
    preuve = mandat.get("preuve") or {}
    mini = preuve.get("secondes_mini", 45)
    attendu_ecrit = bool(preuve.get("ecrit"))
    attendu_artefact = bool(preuve.get("artefact"))
    a_travaille = ecrit or artefact or secondes > mini
    if attendu_artefact and artefact:
        etat = "abouti"
    elif attendu_ecrit and ecrit:
        etat = "partiel"
    elif a_travaille:
        etat = "sans effet"
    else:
        etat = "muet"
    return {
        "mandat": mandat.get("id"),
        "code": code,
        "secondes": round(secondes, 1),
        "a_travaille": a_travaille,
        "etat": etat,
        "dit": {
            "abouti": "l'artefact attendu existe",
            "partiel": "le fichier attendu a été écrit, l'artefact non",
            "sans effet": "l'agent a travaillé sans rien produire de ce qui était attendu",
            "muet": "aucune trace et trop rapide — vérifier l'authentification du CLI",
        }[etat],
    }


# ───────────────────────────── lecture ─────────────────────────────

def mandat(identifiant: str, doc: dict | None = None) -> dict:
    d = doc or charger()
    for m in d.get("mandats") or []:
        if m.get("id") == identifiant:
            return m
    connus = [m.get("id") for m in d.get("mandats") or []]
    raise Refus(f"mandat « {identifiant} » inconnu — déclarés : {connus}")


def accorder(identifiant: str, doc: dict | None = None, racine: str | None = None) -> dict:
    """Le mandat prêt à l'emploi : outils du CLI, racine, budget, preuve.

    REFUSE si le contrat entier est irrecevable : accorder un mandat pris dans un contrat qui
    ne passe pas son propre lint reviendrait à faire confiance à la moitié qu'on a regardée.

    `racine` ne sert QUE pour un mandat déclaré `dynamique` — un bac jetable, un worktree, un
    dossier qui n'existe pas encore quand on écrit le contrat. Sur un mandat à racine FIXE, la
    passer est REFUSÉ : un appelant qui peut déplacer le périmètre déclaré le déplacera, et la
    déclaration ne déclarerait plus rien.
    """
    d = doc or charger()
    g = griefs(d)
    if g:
        raise Refus("contrat irrecevable : " + " · ".join(g[:3]))
    m = mandat(identifiant, d)
    declaree = str(m["racine"])
    if declaree == RACINE_DYNAMIQUE:
        if not racine:
            raise Refus(f"mandat « {identifiant} » : racine dynamique — l'appelant doit fournir "
                        f"le dossier de travail")
        effective = str(racine)
    else:
        if racine is not None and str(racine) != declaree:
            raise Refus(f"mandat « {identifiant} » : racine FIXE ({declaree}) — on ne la "
                        f"remplace pas à l'appel")
        effective = declaree
    modeles = m.get("modeles") or []
    serveur = d.get("serveur_modeles") or {}
    if modeles and not serveur.get("nom"):
        raise Refus(f"mandat « {identifiant} » délègue à {modeles} mais aucun "
                    f"`serveur_modeles` n'est déclaré : l'octroi ne désignerait rien")
    return {
        "id": m["id"],
        "pour": m["pour"],
        "racine": effective,
        "racine_dynamique": declaree == RACINE_DYNAMIQUE,
        "outils": outils_cli(m, d["familles"], d.get("modeles_connus"), serveur["nom"]),
        "modeles": modeles,
        "minutes": m["budget"]["minutes"],
        "preuve": m["preuve"],
        "destructif": bool(m.get("destructif")),
        # Le CÂBLAGE vient avec l'octroi, jamais après : accorder `mcp__modeles__…` sans lancer
        # le serveur produit un outil qui n'existe pas, et l'agent ne le découvre qu'à l'appel.
        # `strict` coupe les serveurs MCP du POSTE — sans lui l'agent hériterait de chrome,
        # blender et le reste, c'est-à-dire d'un périmètre que ce contrat n'a pas accordé.
        "mcp_config": _mcp_config(serveur, m["id"]) if modeles else None,
        "mcp_strict": True,
    }


def _mcp_config(serveur: dict, mandat_id: str) -> dict:
    """Le `--mcp-config` d'un mandat qui délègue. Fonction PURE.

    Le mandat voyage jusqu'au serveur par l'environnement : c'est lui qui, à l'autre bout,
    REFUSE un délégué non accordé. Le CLI filtre les noms d'outils, le serveur filtre les
    appels — deux barrières, parce que la première ne connaît pas le contrat.
    """
    cwd = str(RACINE if serveur.get("cwd") in (None, RACINE.name) else serveur["cwd"])
    cmd = list(serveur["commande"])
    return {"mcpServers": {serveur["nom"]: {
        "command": cmd[0],
        "args": [str(RACINE / a) if a.endswith(".py") else a for a in cmd[1:]],
        "cwd": cwd,
        "env": {"STILHAWT_MANDAT": mandat_id},
    }}}


def griefs_transport(argv: list[str]) -> list[str]:
    """Ce qui n'arrivera PAS entier à l'agent si on le passe en ligne de commande. Fonction PURE.

    ⚠ MESURÉ LE 25/09 : sous Windows, `claude` est `claude.CMD`, un fichier batch — il repasse
    par `cmd.exe` avec ou sans `shell=True`, et `cmd.exe` COUPE la ligne au premier saut de ligne.
    Tout ce qui suit disparaît sans erreur : la fin de la consigne, ET les drapeaux placés après
    elle. Les six runs du 18/09 n'ont reçu que la première ligne de leur consigne (transcripts) ;
    le mandat du 24/09, posé après la consigne, n'aurait jamais atteint le CLI. Une consigne
    passe par l'entrée standard (`input=`), jamais par argv.
    """
    return [f"argument {i} contient un saut de ligne — cmd.exe le tronquerait : "
            f"{a.splitlines()[0][:60]!r}…"
            for i, a in enumerate(argv) if "\n" in a or "\r" in a]


@contextmanager
def drapeaux(octroi: dict):
    """Les arguments du CLI Claude (mode print, -p) pour un octroi, câblage MCP compris.

    ⚠ POURQUOI UN SEUL ENDROIT. Sans ça, chaque appelant écrit sa ligne de flags, et le jour où
    un mandat se met à déléguer, ceux qui n'ont pas pensé au `--mcp-config` accordent un outil
    qui n'existe pas — l'agent ne le découvre qu'à l'appel, et en silence. Trois listes en dur
    viennent d'être supprimées pour cette raison exacte ; on ne les remplace pas par trois
    lignes de flags.

    Le fichier de configuration est TEMPORAIRE et porte le mandat dans son environnement : il
    vit le temps de l'exécution, et rien ne subsiste qui accorderait quoi que ce soit ensuite.

    ⚠ `--strict-mcp-config` EST INCONDITIONNEL (corrigé le 25/09). Il n'était ajouté que dans la
    branche où le mandat délègue, alors que `accorder()` annonçait `mcp_strict: True` pour tous :
    un mandat sans délégué héritait des serveurs MCP du poste, et le champ affirmait l'inverse.
    Sans `--mcp-config`, strict veut dire « aucun serveur » — c'est exactement l'octroi.
    """
    import json as _json
    import tempfile
    # `--tools` borne ce qui EXISTE pour l'agent ; `--allowedTools` ne dit que ce qui passe SANS
    # demander. Corrigé le 25/09 (conv 32918cfc) : sans `--tools`, un mandat `outils: {}` gardait
    # tout l'outillage natif, et `acceptEdits` lui approuvait Edit/Write d'office — un « aucun outil
    # de fichier » qui pouvait écrire. UN seul argument, virgules (un drapeau variadique avalerait
    # la suite) ; "" = aucun outil natif. Les outils MCP ne sont pas natifs : régis par allowedTools.
    #
    # `dontAsk` et non `acceptEdits` (même jour, même conv) : `acceptEdits` approuve TOUTE écriture
    # dans la racine, quel que soit le nom accordé — mesuré : `Write(autorise.txt)` accordé, un
    # `interdit.txt` écrit quand même. `dontAsk` refuse tout ce qui n'est pas pré-autorisé : l'octroi
    # nommé devient enfin la frontière (les écritures sont régies par les règles `Edit(...)`,
    # que `outils_cli` émet à côté de `Write(...)`).
    natifs = sorted({o.split("(", 1)[0] for o in octroi["outils"] if not o.startswith("mcp__")})
    args = ["--setting-sources", "project,local",
            "--permission-mode", "dontAsk",
            "--tools", ",".join(natifs),
            "--allowedTools", " ".join(octroi["outils"])]
    if octroi.get("mcp_strict", True):
        args.append("--strict-mcp-config")
    if not octroi.get("mcp_config"):
        yield args
        return
    f = tempfile.NamedTemporaryFile("w", suffix=".mcp.json", delete=False, encoding="utf-8")
    try:
        _json.dump(octroi["mcp_config"], f, ensure_ascii=False)
        f.close()
        args += ["--mcp-config", f.name]
        yield args
    finally:
        try:
            Path(f.name).unlink()
        except Exception:  # noqa: BLE001
            pass


# ───────────────────────────── autotests ─────────────────────────────

def _selftest() -> int:
    cas, echecs = 0, []

    def check(label, obtenu, attendu=True):
        nonlocal cas
        cas += 1
        if obtenu != attendu:
            echecs.append(f"  [ÉCHEC] {label} : {obtenu!r} ≠ {attendu!r}")

    def refuse(label, fn, fragment=None):
        nonlocal cas
        cas += 1
        try:
            fn()
            echecs.append(f"  [ÉCHEC] {label} : aucune exception levée")
        except Refus as e:
            if fragment and fragment not in str(e):
                echecs.append(f"  [ÉCHEC] {label} : « {e} » ne dit pas « {fragment} »")

    # --- le VOISINAGE (« travailler à la frontière », 26/09/2026) ---
    import copy
    import tempfile
    reel = charger()
    with tempfile.TemporaryDirectory() as ws:
        wsp = Path(ws)
        (wsp / "lib").mkdir()
        (wsp / "montre.py").write_text("# shows lib\n", encoding="utf-8")
        base = copy.deepcopy(reel)
        for m in base["mandats"]:
            m.pop("voisinage", None)          # the real ones point at the real disk, not at this temp one
        m0 = base["mandats"][0]
        bon = {"role": "architecte", "voisin": "lib", "zone": "les bribes éprouvées", "vu_par": "montre.py",
               "distance": 0}
        # DISTANCE (26/09/2026): its own domain in FULL (0), the other agents less and less with distance
        m0 = base["mandats"][0]
        m0["voisinage"] = [{k: v for k, v in bon.items() if k != "distance"}]
        check("MUST-FAIL voisinage : sans distance → refusé (connaissance décroissante non déclarée)",
              any("distance" in x for x in griefs(base, racine_ws=wsp)), True)
        m0["voisinage"] = [dict(bon, distance=-1)]
        check("MUST-FAIL voisinage : distance négative → refusée",
              any("distance" in x for x in griefs(base, racine_ws=wsp)), True)
        m0["voisinage"] = [bon]
        check("voisinage : un voisin réel, montré par un code réel → recevable",
              [x for x in griefs(base, racine_ws=wsp) if "voisin" in x], [])
        m0["voisinage"] = [{**bon, "voisin": m0["id"]}]
        check("voisinage : un voisin peut être un AUTRE mandat déclaré",
              [x for x in griefs(base, racine_ws=wsp) if "voisin" in x], [])
        m0["voisinage"] = [{k: v for k, v in bon.items() if k != "zone"}]
        check("MUST-FAIL voisinage : sans zone d'influence → refusé",
              any("zone" in x for x in griefs(base, racine_ws=wsp)), True)
        m0["voisinage"] = [{**bon, "voisin": "nulle_part"}]
        check("MUST-FAIL voisinage : un voisin qui n'existe pas → refusé",
              any("nulle_part" in x for x in griefs(base, racine_ws=wsp)), True)
        m0["voisinage"] = [{**bon, "vu_par": "absent.py"}]
        check("MUST-FAIL voisinage : un code « qui le montre » absent → promesse, refusée",
              any("absent.py" in x for x in griefs(base, racine_ws=wsp)), True)
        m0["voisinage"] = [{**bon, "voir": "x"}]
        check("MUST-FAIL voisinage : champ hors vocabulaire → refusé",
              any("voir" in x for x in griefs(base, racine_ws=wsp)), True)
    check("le contrat RÉEL, voisinages compris, est recevable sur le disque réel",
          griefs(reel, racine_ws=RACINE.parent), [])

    # --- les chemins : la racine ne borne rien si on peut en sortir ---
    check("un chemin relatif passe", chemin_sur("plans.dsl.yaml"))
    check("un glob relatif aussi", chemin_sur("**"))
    check("un sous-dossier aussi", chemin_sur("data/musique/source"))
    # MUST-FAIL : l'absolu et la remontée. Un mandat qui nomme la clé SSH de l'utilisateur en
    # écriture serait syntaxiquement valide et catastrophique.
    check("MUST-FAIL un chemin absolu Windows est refusé",
          chemin_sur("C:\\keys\\.ssh\\id_ed25519"), False)
    check("MUST-FAIL un chemin absolu POSIX est refusé", chemin_sur("/etc/passwd"), False)
    check("MUST-FAIL une remontée est refusée", chemin_sur("../../secrets.yaml"), False)
    check("une remontée au milieu aussi", chemin_sur("data/../../ailleurs"), False)
    check("un chemin vide est refusé", chemin_sur("  "), False)
    # Un nom de fichier qui CONTIENT deux points n'est pas une remontée.
    check("un fichier nommé « ..truc » n'est pas une remontée", chemin_sur("a/..truc.yaml"))

    # --- les commandes : nommées, jamais un blanc-seing ---
    check("une commande nommée passe", commande_nommee("python tournage.py"))
    check("avec un sous-verbe aussi", commande_nommee("python tournage.py blanc"))
    # MUST-FAIL : l'interpréteur seul, c'est le disque entier.
    check("MUST-FAIL « python » seul est refusé", commande_nommee("python"), False)
    check("MUST-FAIL un shell est refusé", commande_nommee("bash -c echo"), False)
    check("MUST-FAIL powershell aussi", commande_nommee("powershell -Command ls"), False)
    check("MUST-FAIL une étoile est refusée", commande_nommee("python *"), False)
    check("vide refusé", commande_nommee(""), False)

    # --- la dérivation : c'est le seul endroit où la syntaxe du fournisseur est écrite ---
    familles = {
        "lire": {"rend": ["Read", "Glob"], "portee": "chemins"},
        "ecrire": {"rend": ["Edit"], "portee": "chemins"},
        "lancer": {"rend": ["Bash"], "portee": "commandes"},
        "naviguer": {"rend": ["WebFetch"], "portee": "aucune"},
    }
    m = {"outils": {"lire": ["**"], "ecrire": ["a.yaml"], "lancer": ["python x.py"]}}
    outils = outils_cli(m, familles)
    check("la lecture est bornée au périmètre", "Read(**)" in outils)
    check("chaque outil de la famille est rendu", "Glob(**)" in outils)
    check("l'écriture nomme SON fichier", outils.count("Edit(a.yaml)"), 1)
    check("la commande reçoit son joker d'arguments", "Bash(python x.py:*)" in outils)
    # MUST-FAIL : aucun outil large ne doit pouvoir sortir de la dérivation.
    check("MUST-FAIL aucun Bash nu", "Bash" in outils, False)
    check("MUST-FAIL aucun Edit nu", "Edit" in outils, False)
    check("une famille sans portée rend l'outil seul",
          outils_cli({"outils": {"naviguer": []}}, familles), ["WebFetch"])
    refuse("famille inconnue refusée",
           lambda: outils_cli({"outils": {"voler": ["x"]}}, familles), "hors du vocabulaire")

    # --- l'octroi du CLI (CLI-PLAN:E3) : une commande NOMMÉE, en lecture, jamais un modèle ---
    fam_cli = {**familles, "cli": {"rend": ["Bash"], "portee": "cli"}}
    check("une commande du CLI devient un Bash préfixé",
          outils_cli({"outils": {"cli": ["git status", "where"]}}, fam_cli),
          ["Bash(stilhawt git status:*)", "Bash(stilhawt where:*)"])
    gram = {"pipes": {"where": {}, "claude": {"mode": "generate"}},
            "namespaces": {"git": {"commands": {"status": {"effect": "read"}, "push": {"effect": "write"}}}}}
    check("une commande en lecture et un pipe pur passent", griefs_cli(["git status", "where"], gram), [])
    check("MUST-FAIL un pipe de modèle ne s'accorde pas par le CLI",
          any("MODÈLE" in x for x in griefs_cli(["claude"], gram)), True)
    check("MUST-FAIL une commande qui écrit ne s'accorde pas en v0",
          any("LECTURE" in x for x in griefs_cli(["git push"], gram)), True)
    check("MUST-FAIL une commande inconnue ne désigne rien",
          any("inconnue" in x for x in griefs_cli(["git blame"], gram)), True)
    check("MUST-FAIL `stilhawt *` est un blanc-seing",
          any("blanc-seing" in x for x in griefs_cli(["*"], gram)), True)

    # --- le lint, sur un contrat FABRIQUÉ pour être faux ---
    base = {
        "familles": familles,
        "serveur_modeles": {"nom": "modeles", "commande": ["python", "scripts/models_server.py"]},   # a FABRICATED contract
        "modeles_connus": {"groq": {"cout": "quota", "foyer": "gate", "outil": "demander_groq"},
                           "jev": {"cout": "gpu", "foyer": "decide", "outil": "decider"},
                           "codex": {"cout": "inconnu", "foyer": None, "outil": None}},
        "mandats": [{
            "id": "bon", "pour": "faire", "racine": "C:\\dev\\x",
            "outils": {"lire": ["**"], "ecrire": ["a.yaml"]},
            "modeles": ["groq"], "budget": {"minutes": 5},
            "preuve": {"ecrit": "a.yaml", "secondes_mini": 30}, "destructif": False,
        }],
    }
    check("un contrat bien formé ne fait aucun grief", griefs(base), [])

    def avec(**patch):
        import copy
        d = copy.deepcopy(base)
        d["mandats"][0].update(patch)
        return d

    # MUST-FAIL, un par loi.
    check("MUST-FAIL un chemin absolu en écriture est refusé",
          any("sort du périmètre" in x
              for x in griefs(avec(outils={"ecrire": ["C:\\keys\\.ssh\\id"]}))))
    check("MUST-FAIL un blanc-seing de commande est refusé",
          any("blanc-seing" in x for x in griefs(avec(outils={"lancer": ["python"]}))))
    check("MUST-FAIL un délégué sans foyer est refusé",
          any("pas de foyer" in x for x in griefs(avec(modeles=["codex"]))))
    check("MUST-FAIL un délégué inconnu est refusé",
          any("hors de" in x for x in griefs(avec(modeles=["fantome"]))))
    check("MUST-FAIL un budget absent est refusé",
          any("budget.minutes" in x for x in griefs(avec(budget={}))))
    # MUST-FAIL : sans preuve, « rien fait » et « CLI muet » rendent le même résultat.
    check("MUST-FAIL une preuve absente est refusée",
          any("preuve de travail" in x for x in griefs(avec(preuve={"secondes_mini": 1}))))
    check("MUST-FAIL un champ hors vocabulaire est refusé",
          any("hors vocabulaire" in x for x in griefs(avec(surprise=1))))
    check("MUST-FAIL un champ obligatoire manquant est vu",
          any("« pour » manque" in x for x in griefs({**base, "mandats": [
              {k: v for k, v in base["mandats"][0].items() if k != "pour"}]})))
    d2 = {**base, "mandats": base["mandats"] + base["mandats"]}
    check("MUST-FAIL deux mandats de même id",
          any("en double" in x for x in griefs(d2)))

    # --- le verdict : un agent muet ne ressemble pas à un agent qui a renoncé ---
    mb = base["mandats"][0]
    mb2 = {**mb, "preuve": {"ecrit": "a.yaml", "artefact": "b.mp4", "secondes_mini": 45}}
    check("artefact présent : abouti",
          verdict(mb2, 0, 300, ecrit=True, artefact=True)["etat"], "abouti")
    check("écrit seulement : partiel",
          verdict(mb2, 0, 300, ecrit=True, artefact=False)["etat"], "partiel")
    check("du temps mais rien : sans effet",
          verdict(mb2, 0, 300, ecrit=False, artefact=False)["etat"], "sans effet")
    # MUST-FAIL : code 0 en huit secondes sans trace = MUET, pas « rien à faire ».
    check("MUST-FAIL code 0 en 8 s sans trace : muet",
          verdict(mb2, 0, 8, ecrit=False, artefact=False)["etat"], "muet")
    check("et le message nomme la cause probable",
          "authentification" in verdict(mb2, 0, 8, False, False)["dit"])
    check("un code non nul avec artefact reste abouti",
          verdict(mb2, 1, 300, ecrit=True, artefact=True)["etat"], "abouti")

    # --- la racine dynamique : un bac qui n'existe pas quand on écrit le contrat ---
    dyn = {**base, "mandats": [{**base["mandats"][0], "id": "dyn", "racine": "dynamique"}]}
    check("un mandat dynamique passe le lint", griefs(dyn), [])
    a = accorder("dyn", dyn, racine=r"C:\tmp\bac")
    check("la racine fournie est celle qui sert", a["racine"], r"C:\tmp\bac")
    check("et c'est dit", a["racine_dynamique"])
    # MUST-FAIL : sans racine fournie, on n'accorde pas — « n'importe où » n'est pas un périmètre.
    refuse("MUST-FAIL dynamique sans racine refusé",
           lambda: accorder("dyn", dyn), "doit fournir")
    # MUST-FAIL : sur un mandat à racine FIXE, on ne la remplace pas à l'appel.
    refuse("MUST-FAIL une racine fixe ne se remplace pas",
           lambda: accorder("bon", base, racine=r"C:\ailleurs"), "on ne la remplace pas")
    check("mais la repasser à l'identique est tolérée",
          accorder("bon", base, racine="C:\\dev\\x")["racine"], "C:\\dev\\x")
    refuse("racine vide refusée",
           lambda: accorder("v", {**base, "mandats": [
               {**base["mandats"][0], "id": "v", "racine": "  "}]}), "racine vide")

    # --- le joker en écriture : autorisé, mais JAMAIS muet ---
    joker = {**base, "mandats": [{**base["mandats"][0], "outils": {"ecrire": ["**"]}}]}
    check("MUST-FAIL un joker en écriture sans justification est refusé",
          any("joker" in x for x in griefs(joker)))
    joker_ok = {**base, "mandats": [{**base["mandats"][0], "outils": {"ecrire": ["**"]},
                                     "ecrire_joker_pourquoi": "la tache change a chaque run"}]}
    check("avec sa justification, il passe", griefs(joker_ok), [])
    # Le joker n'est exigé qu'en ÉCRITURE : lire largement ne donne aucun pouvoir d'écrire.
    check("un joker en lecture ne demande rien",
          griefs({**base, "mandats": [{**base["mandats"][0], "outils": {"lire": ["**"]}}]}), [])

    # --- les délégués DEVIENNENT des outils, sinon `modeles:` ne décorait rien ---
    a = accorder("bon", base)
    check("un délégué accordé produit son outil MCP",
          "mcp__modeles__demander_groq" in a["outils"])
    check("et le câblage vient avec l'octroi", a["mcp_config"] is not None)
    check("le mandat voyage jusqu'au serveur par l'environnement",
          a["mcp_config"]["mcpServers"]["modeles"]["env"]["STILHAWT_MANDAT"], "bon")
    # MUST-FAIL : sans `--strict-mcp-config`, l'agent hérite des serveurs MCP du POSTE (chrome,
    # blender, youtube…), donc d'un périmètre que ce contrat n'a jamais accordé.
    check("MUST-FAIL le câblage est strict", a["mcp_strict"])
    # Un mandat sans délégué ne câble RIEN : lancer un serveur pour personne serait un process
    # de plus et une surface de plus, tous deux gratuits.
    sans = {**base, "mandats": [{**base["mandats"][0], "id": "sans", "modeles": []}]}
    check("aucun délégué : aucun câblage", accorder("sans", sans)["mcp_config"], None)
    check("et aucun outil MCP",
          any("mcp__" in o for o in accorder("sans", sans)["outils"]), False)
    # MUST-FAIL : un délégué sans verbe exposé ne s'accorde pas — l'octroi ne désignerait rien.
    muet = {**base,
            "modeles_connus": {**base["modeles_connus"],
                               "groq": {"cout": "q", "foyer": "gate", "outil": None}}}
    refuse("MUST-FAIL délégué sans verbe refusé", lambda: accorder("bon", muet), "ne désigne")
    # MUST-FAIL : déléguer sans serveur déclaré.
    orphelin = {k: v for k, v in base.items() if k != "serveur_modeles"}
    refuse("MUST-FAIL déléguer sans serveur déclaré",
           lambda: accorder("bon", orphelin), "serveur_modeles")

    # --- les drapeaux : strict TOUJOURS, une seule fois ---
    # MUST-FAIL : sans délégué, `--strict-mcp-config` manquait alors que l'octroi le promettait.
    with drapeaux(accorder("sans", sans)) as args_sans:
        check("MUST-FAIL strict même sans délégué", "--strict-mcp-config" in args_sans)
        check("et sans délégué, aucun --mcp-config", "--mcp-config" in args_sans, False)
    with drapeaux(accorder("bon", base)) as args_bon:
        check("avec délégué, strict une seule fois", args_bon.count("--strict-mcp-config"), 1)
        check("avec délégué, le câblage suit", "--mcp-config" in args_bon)
        check("les drapeaux eux-mêmes sont transportables", griefs_transport(args_bon), [])
        # `--tools` borne ce qui EXISTE : exactement les natifs accordés, dans UN argument
        check("--tools = les natifs accordés, pas plus", args_bon[args_bon.index("--tools") + 1],
              "Edit,Glob,Read")   # familles du selftest : lire → Read+Glob, ecrire → Edit
    # MUST-FAIL (25/09) : un mandat sans outil de fichier gardait tout l'outillage natif, et
    # acceptEdits lui approuvait Edit/Write — `--tools ""` est ce qui l'en prive réellement.
    nu = {**base, "mandats": [{**base["mandats"][0], "id": "nu", "outils": {}, "modeles": []}]}
    with drapeaux(accorder("nu", nu)) as args_nu:
        check("MUST-FAIL mandat sans outil = aucun outil natif", args_nu[args_nu.index("--tools") + 1], "")
        # MUST-FAIL (25/09) : acceptEdits approuvait toute écriture de la racine, nom accordé ou non
        check("MUST-FAIL jamais acceptEdits", "acceptEdits" in args_nu, False)
        check("dontAsk : seul le pré-autorisé passe", args_nu[args_nu.index("--permission-mode") + 1], "dontAsk")

    # --- le transport : une consigne multiligne ne passe JAMAIS par argv ---
    check("une ligne passe", griefs_transport(["claude", "-p", "une ligne"]), [])
    # MUST-FAIL : c'est la forme exacte de l'appel du banc jusqu'au 25/09.
    check("MUST-FAIL une consigne multiligne en argv est refusée",
          len(griefs_transport(["claude", "-p", "ligne 1\nligne 2", "--allowedTools", "Read"])), 1)
    check("MUST-FAIL un \\r seul aussi", len(griefs_transport(["a\rb"])), 1)

    # --- contrôle positif contre le VRAI contrat : les fixtures valident ma croyance ---
    if CONTRAT.is_file():
        vrai = charger()
        check("le contrat réel passe son propre lint", griefs(vrai), [])
        refuse("mandat inconnu refusé", lambda: accorder("fantome", vrai), "inconnu")
        ids = {m.get("id") for m in vrai.get("mandats") or []}
    # Les cas suivants visent les mandats du FILM, déclarés par le contrat de l'atelier ; un contrat
    # d'exemple (installation publique) ne les a pas — ils ne jouent alors pas, et c'est dit.
    if CONTRAT.is_file() and {"promo.sujet_neuf", "promo.sujet_repetition"} <= ids:
        a = accorder("promo.sujet_neuf", vrai)
        check("le mandat du film accorde l'écriture d'UN fichier",
              [o for o in a["outils"] if o.startswith(("Edit", "Write"))],
              ["Edit(plans.dsl.yaml)", "Write(plans.dsl.yaml)"])
        check("il peut lancer le tournage",
              "Bash(python tournage.py:*)" in a["outils"])
        check("il n'a aucun délégué", a["modeles"], [])
        # MUST-FAIL : le mandat de répétition ne doit PAS pouvoir lancer la caméra.
        b = accorder("promo.sujet_repetition", vrai)
        check("MUST-FAIL la répétition n'accorde que le blanc",
              [o for o in b["outils"] if o.startswith("Bash")],
              ["Bash(python tournage.py blanc:*)"])
    elif CONTRAT.is_file():
        print("  (cas des mandats du film non joués : ce contrat ne les déclare pas)")

    for l in echecs:
        print(l)
    print(f"\n{cas - len(echecs)}/{cas} autotests verts")
    return 1 if echecs else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="lint le contrat")
    ap.add_argument("--lister", action="store_true")
    ap.add_argument("--montrer")
    ap.add_argument("--racine", help="pour un mandat a racine dynamique (bac jetable)")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()
    try:
        doc = charger()
        if args.montrer:
            import json
            print(json.dumps(accorder(args.montrer, doc, args.racine),
                             ensure_ascii=False, indent=2))
            return 0
        if args.lister:
            for m in doc.get("mandats") or []:
                print(f"  {m['id']:<26} {m['pour']}")
            return 0
        g = griefs(doc, racine_ws=RACINE.parent)
    except Refus as e:
        print(f"REFUS : {e}")
        return 1
    for x in g:
        print(f"  {x}")
    print(f"\n{len(doc.get('mandats') or [])} mandat(s) · {len(g)} grief(s)")
    return 1 if g else 0


if __name__ == "__main__":
    raise SystemExit(main())
