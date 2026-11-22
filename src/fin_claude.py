"""
Prévient sur Discord quand Claude a fini de traiter une issue.

L'action Claude (`.github/workflows/claude.yml`) pousse sa correction sur une
branche `claude/issue-<n>-<date>` mais n'ouvre JAMAIS la pull request : elle
laisse un lien « Create PR ➔ » dans l'issue. Le 22/11, l'alerte #3 a ainsi
été corrigée en 2 min 34 s… et la panne a continué, faute de quelqu'un pour
cliquer sur ce lien puis fusionner.

Ce script tourne en dernière étape du workflow. Si Claude a poussé du code,
il envoie en message privé son résumé et un bouton « Fusionner et appliquer »,
auquel le bot répond (bot_discord.VueFusion). Sinon, il transmet simplement sa
réponse. Il ne fusionne rien lui-même : la décision reste au clic.

N'importe QUE la bibliothèque standard, comme alerte_ci.

    NUMERO=<issue> GITHUB_TOKEN=... python src/fin_claude.py
"""

import json
import os
import sys
import urllib.parse
import urllib.request

import alerte_ci

VERT, GRIS, ROUGE = 0x2E9E5B, 0x7F8C8D, 0xC0392B


def _github(chemin):
    """GET sur l'API GitHub du dépôt courant ; rend le JSON décodé."""
    requete = urllib.request.Request(
        f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/{chemin}",
        headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
                 "Accept": "application/vnd.github+json",
                 "User-Agent": "edt-stri-ci"})
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        return json.load(reponse)


def branche_de_claude(branches, numero):
    """La branche la plus récente que Claude a créée pour cette issue.

    Le nom finit par la date au format AAAAMMJJ-HHMM : l'ordre alphabétique
    est donc l'ordre chronologique.
    """
    prefixe = f"claude/issue-{numero}-"
    return max((b for b in branches if b.startswith(prefixe)), default=None)


def titre_propose(commentaire):
    """Le titre de pull request que Claude a mis dans son lien « Create PR »."""
    for morceau in commentaire.split("(")[1:]:
        adresse = morceau.split(")")[0]
        if "quick_pull=1" in adresse:
            titre = urllib.parse.parse_qs(urllib.parse.urlsplit(adresse).query).get("title")
            if titre:
                return titre[0].strip()
    return None


def resume(commentaire):
    """Le corps de la réponse de Claude, sans l'en-tête de liens ni le pied.

    L'en-tête (« Claude finished… », View job, branche, Create PR) précède le
    premier séparateur `---` ; le pied est la ligne « · [Branche](…) ».
    """
    _, separateur, corps = commentaire.partition("\n---\n")
    corps = corps if separateur else commentaire
    lignes = [l for l in corps.strip().splitlines() if "[Branche](" not in l]
    return "\n".join(lignes).strip()


def principale():
    numero = os.environ.get("NUMERO", "").strip()
    depot = os.environ.get("GITHUB_REPOSITORY", "")
    if not numero or not os.environ.get("GITHUB_TOKEN"):
        print("ℹ️  NUMERO ou GITHUB_TOKEN absent : rien à signaler.")
        return 0
    issue = f"https://github.com/{depot}/issues/{numero}"

    if os.environ.get("ETAT_CLAUDE", "success") != "success":
        alerte_ci.signaler(f"⛔ Claude n'a pas pu terminer l'issue #{numero}",
                           f"[Voir l'issue]({issue})", ROUGE, boutons=())
        return 0

    try:
        commentaires = _github(f"issues/{numero}/comments?per_page=100")
        branches = [b["name"] for b in _github("branches?per_page=100")]
    except Exception as e:
        print(f"❌ Lecture de l'issue impossible : {e}")
        return 0

    de_claude = [c["body"] for c in commentaires
                 if (c.get("user") or {}).get("login", "").startswith("claude")]
    reponse = de_claude[-1] if de_claude else ""
    texte = resume(reponse) or "(Claude n'a rien écrit dans l'issue.)"

    branche = branche_de_claude(branches, numero)
    ecart = {}
    if branche:
        try:
            ecart = _github(f"compare/main...{urllib.parse.quote(branche, safe='/')}")
        except Exception as e:
            print(f"⚠️  Comparaison avec main impossible : {e}")

    if branche and ecart.get("ahead_by"):
        titre = titre_propose(reponse) or f"Correction de l'issue #{numero}"
        fichiers = ", ".join(f"`{f['filename']}`" for f in ecart.get("files", [])[:6])
        alerte_ci.signaler(
            f"🤖 Claude a corrigé : {titre}",
            f"{texte[:3000]}\n\n**Fichiers modifiés** : {fichiers}\n"
            f"[Voir les changements]({ecart.get('html_url', issue)}) · "
            f"[Issue #{numero}]({issue})",
            VERT,
            boutons=((alerte_ci.BOUTON_FUSION, "Fusionner et appliquer", "✅"),),
            pied=f"branche {branche} · issue #{numero}")
    else:
        alerte_ci.signaler(f"🤖 Claude a répondu à l'issue #{numero}",
                           f"{texte[:3300]}\n\nAucun code modifié. "
                           f"[Voir l'issue]({issue})", GRIS, boutons=())
    return 0


if __name__ == "__main__":
    sys.exit(principale())
