"""
Lecture des cours directement dans la couche texte du PDF, sans IA.

Le PDF de l'EDT n'est pas une image : chaque titre, chaque salle et chaque
bordure de cellule y sont des objets positionnés. On peut donc reconstruire les cours de façon déterministe, instantanée et gratuite, là où l'analyse d'images dépendait de Gemini (quotas, 403, découpages fantômes).

Le module renvoie exactement la même structure de blocs que l'appel IA, plus les horaires, pour que `traiter_journee()` n'ait pas à distinguer les deux sources :

    {"position": "FULL"|"TOP"|"BOTTOM", "color": "BLANC"|"JAUNE"|"ORANGE",
     "course": ..., "prof": ..., "group": ..., "room": ...,
     "start": "08h00", "end": "10h00"}

Structures reconnues dans l'emploi du temps M1 :
  - cellule pleine hauteur : titre, puis le professeur sur la ligne du dessous
    (en italique le plus souvent, mais pas toujours — voir `_est_ligne_prof`) ;
  - demi-cellule : « Adm. Windows (CC) » sur une seule ligne ;
  - la salle est le texte posé sur une case verte.
"""

import re
from pathlib import Path

# --- Reconnaissance des couleurs de fond -------------------------------------
# Le vert marque les salles, le jaune et l'orange les examens / cours annulés.
TOLERANCE_COULEUR = 0.15


def _proche(couleur, cible):
    """Deux couleurs se ressemblent-elles ? Écarte tout ce qui n'est pas un
    triplet RVB — les pointillés de la grille portent un nom de motif,
    « P67 », que prendre pour du noir créerait des cellules fantômes."""
    if not isinstance(couleur, (tuple, list)) or len(couleur) != 3:
        return False
    return all(abs(c - r) < TOLERANCE_COULEUR for c, r in zip(couleur, cible))


def est_vert(c):
    """Vert vif : fond des cases de salle."""
    return _proche(c, (0.0, 0.98, 0.0))


def est_jaune(c):
    """Jaune : fond des examens."""
    return _proche(c, (1.0, 1.0, 0.0))


def est_orange(c):
    """Orange : cours réservé aux Ingé, pas un cours annulé."""
    return _proche(c, (1.0, 0.75, 0.0))


def est_olive(c):
    """Le vert olive de la L3, symétrique de l'orange : il marque l'autre
    demi-promo. Assez éloigné du vert vif des salles pour ne pas s'y confondre."""
    return _proche(c, (0.573, 0.816, 0.314))


def est_noir(c):
    """Noir plein : bordure de cellule, par opposition aux pointillés gris."""
    return _proche(c, (0.0, 0.0, 0.0))


def est_gris(c):
    """Gris neutre : la date d'un jour où les alternants sont en entreprise.

    Le PDF de la L3 l'explique dans sa légende : « À partir de la semaine du
    28/09, les alternants (ayant signé leurs contrats) sont en entreprise les
    jours grisés. » Le gris mesuré vaut 0,749 sur les trois canaux.

    Défini par sa NEUTRALITÉ plutôt que par une valeur exacte : le document
    emploie déjà deux gris (0,749 pour les dates, 0,851 pour la légende), et un
    troisième ton ne doit pas passer inaperçu. Le blanc et le noir sont exclus,
    l'un étant le fond des pages et l'autre les bordures.
    """
    if not isinstance(c, (tuple, list)) or len(c) != 3:
        return False
    return max(c) - min(c) < 0.06 and 0.45 < min(c) < 0.95


# --- Table des enseignants ---------------------------------------------------

def charger_profs(chemin=None):
    """Lit la table des enseignants : initiales du PDF -> nom complet.

    Elle vivait dans `prompt_creneau.txt`, le prompt envoyé à Gemini. L'IA
    ayant été retirée, le reste du fichier ne servait plus à rien : la table
    est désormais seule dans `professeurs.txt`.
    """
    chemin = Path(chemin or Path(__file__).with_name("professeurs.txt"))
    try:
        texte = chemin.read_text(encoding="utf-8")
        bloc = texte.split("PROFS = {")[1].split("}")[0]
        return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', bloc))
    except (OSError, IndexError):
        return {}


PROFS = charger_profs()

# La parenthèse fermante est parfois doublée dans le PDF source
# (« Tél. Spat. (MA & FM)) ») : sans le `+`, le professeur n'était pas
# reconnu et les initiales restaient collées au titre.
REGEX_PROF = re.compile(r'\(([^()]{1,40})\)+\s*$')
REGEX_GROUPE = re.compile(r'/\s*(G[ABC])\b', re.IGNORECASE)

# Mention de promotion dans le titre : « Gestion (M1 RT) ». Elle occupe la
# place habituelle du professeur, et « M1 RT » se retrouvait donc publié comme
# nom d'enseignant. Elle dit en réalité À QUI s'adresse le cours : c'est le seul
# signal explicite du PDF sur ce point, et il prime sur la géométrie comme sur
# les couleurs (voir DESTINATAIRES dans edt_stri.py).
MENTIONS_PROMO = {"M1 RT": re.compile(r'\bM1\s*-?\s*RT\b', re.I)}


def mention_promo(texte):
    """La promotion nommée dans ce texte, ou None."""
    for nom, motif in MENTIONS_PROMO.items():
        if motif.search(texte):
            return nom
    return None


def _nom_complet(initiales):
    """« CC » -> « Cédric CHAMBAULT ». Gère « MA & FM » et laisse tel quel si inconnu."""
    initiales = initiales.strip().rstrip('+').strip()
    if not initiales:
        return ""
    if initiales in PROFS:
        return PROFS[initiales]
    if "&" in initiales:
        parties = [_nom_complet(p) for p in initiales.split("&")]
        return " & ".join(p for p in parties if p)
    return initiales


def _longueur_union(intervalles):
    """Longueur couverte par des intervalles [a, b], chevauchements comptés une fois."""
    total, fin = 0.0, None
    for debut, bout in sorted(intervalles):
        if fin is None or debut > fin:
            total += bout - debut
            fin = bout
        elif bout > fin:
            total += bout - fin
            fin = bout
    return total


def jour_en_entreprise(page, zone, x_min_pdf, part=0.8):
    """La date de cette journée est-elle grisée ?

    Le gris part de la COLONNE DES DATES, à gauche de la grille, et non d'une
    case de cours : c'est la journée entière qui est marquée, pas un créneau.
    Deux mises en page coexistent, et toutes deux comptent :

      - un jour isolé : le gris ne couvre que sa date (M1, 12 au 16/10) ;
      - une semaine entière : un seul rectangle large couvre les cinq bandes
        d'un coup (L3, 26 au 30/10).

    D'où le seul critère retenu : commencer à gauche de la grille — donc
    englober la date — et couvrir l'essentiel de la hauteur du jour. Borner la
    largeur à droite, comme on l'a d'abord fait, rejetait la semaine entière.

    La légende (« … sont en entreprise les jours grisés ») est elle aussi grise
    et large, mais haute de 10 pt seulement : elle ne couvre jamais 80 % d'une
    journée, qui en fait une vingtaine.
    """
    haut, bas = zone['top'], zone['bottom']
    hauteur = max(bas - haut, 1.0)
    etiquettes = [(max(r['top'], haut), min(r['bottom'], bas))
                  for r in page.rects
                  if est_gris(r['non_stroking_color'])
                  and r['x0'] < x_min_pdf - 2
                  and r['bottom'] > haut + 1 and r['top'] < bas - 1]
    return _longueur_union(etiquettes) / hauteur >= part


def _est_trait_noir(objet):
    """Un tracé noir, que le PDF le déclare en contour ou en remplissage."""
    return (est_noir(objet.get('stroking_color'))
            or est_noir(objet.get('non_stroking_color')))


def _verticales_en_courbes(page, colle=2.0, alignement=1.5):
    """Bordures verticales dessinées en COURBES plutôt qu'en rectangles.

    Presque toutes les cases sont des rectangles noirs, mais pas toutes. Celle
    d'« Interop (AA) » le 22/09 (M1) est tracée à la main : chaque bord est une
    suite de petits morceaux de courbe — oblique, droit, oblique pour un bord
    vertical de 8,5 pt. Ne cherchant que des rectangles, la lecture ne lui
    voyait aucun bord gauche ni droit : la case s'étendait de la bordure
    précédente jusqu'au bout de la grille, et le cours passait de 15h45-17h45
    à 15h30-19h30.

    On recolle les morceaux noirs, quasi verticaux, alignés sur la même
    abscisse et presque jointifs, et chaque trait obtenu est rendu sous la
    forme d'un rectangle : il passe ensuite par exactement les mêmes filtres
    que les bordures ordinaires, hauteur minimale comprise.
    """
    morceaux = sorted(
        (c for c in page.curves + page.lines
         if _est_trait_noir(c) and c['x1'] - c['x0'] < 3),
        key=lambda c: ((c['x0'] + c['x1']) / 2, c['top']))

    traits = []
    for m in morceaux:
        centre = (m['x0'] + m['x1']) / 2
        trait = next((t for t in reversed(traits)
                      if abs(t['centre'] - centre) <= alignement
                      and m['top'] - t['bottom'] <= colle), None)
        if trait is None:
            traits.append({'centre': centre, 'x0': m['x0'], 'x1': m['x1'],
                           'top': m['top'], 'bottom': m['bottom']})
        else:
            trait['x0'] = min(trait['x0'], m['x0'])
            trait['x1'] = max(trait['x1'], m['x1'])
            trait['top'] = min(trait['top'], m['top'])
            trait['bottom'] = max(trait['bottom'], m['bottom'])

    return [{'x0': t['x0'], 'x1': t['x1'], 'top': t['top'], 'bottom': t['bottom'],
             'width': t['x1'] - t['x0'], 'height': t['bottom'] - t['top'],
             'non_stroking_color': (0.0, 0.0, 0.0)}
            for t in traits]


# --- Découpage géométrique ---------------------------------------------------

class GrilleJour:
    """Cellules d'une journée, reconstruites depuis les bordures du PDF.

    Chaque case de cours est un rectangle à quatre bordures NOIRES pleines
    (1,7 pt). La grille horaire, elle, est dessinée en pointillés gris : dans
    le PDF ce sont des rectangles à motif (`non_stroking_color` = « P67 »),
    jamais du noir. C'est ce qui sépare une vraie cellule d'un simple quart
    d'heure vide — distinction impossible sur l'image rendue, où les deux sont
    des pixels sombres.

        haut   ─────────────  bord supérieur
        milieu ─────────────  séparation TOP / BOTTOM (absente si cellule pleine)
        bas    ─────────────  bord inférieur
        │                  │  bordures verticales : limites gauche et droite

    `cellules()` en tire directement la liste des cours de la journée.
    """

    def __init__(self, page, zone, x_min_pdf, x_max_pdf):
        self.haut = zone['top']
        self.bas = zone['bottom']
        self.milieu = (self.haut + self.bas) / 2
        self.x_min = x_min_pdf
        self.x_max = x_max_pdf

        # Une case de salle mesure 10,8 pt pour une demi-bande de 10,5 : celle de
        # la journée suivante commence donc AVANT la fin de celle-ci. Filtrer sur
        # `top` la faisait entrer ici et prendre la barre médiane du lendemain
        # pour un bord de salle. On se cale sur le centre du rectangle.
        dans_bande = [r for r in page.rects
                      if self.haut - 1 < (r['top'] + r['bottom']) / 2 < self.bas + 1
                      and r['x1'] > x_min_pdf - 2 and r['x0'] < x_max_pdf + 2]

        barres = [r for r in dans_bande
                  if est_noir(r['non_stroking_color'])
                  and r['height'] < 3 and r['width'] > 5
                  and r['x0'] >= x_min_pdf - 2]

        self.salles = [r for r in dans_bande if est_vert(r['non_stroking_color'])]
        self.examens = [r for r in dans_bande
                        if est_jaune(r['non_stroking_color'])
                        or est_orange(r['non_stroking_color'])]

        # Fonds porteurs de sens, pour la seule lecture de `couleur()`.
        #
        # L'olive est DÉLIBÉRÉMENT absent de `self.examens` : cette liste sert
        # aussi à `_est_bord_de_salle`, qui écarte les barres médianes n'étant
        # qu'un bord de case colorée. Or un fond olive occupe exactement une
        # demi-cellule, donc son bord supérieur EST une vraie séparation. L'y
        # inclure masquait celle du 08/09 13h30 : la cellule passait en pleine
        # hauteur et les deux demi-promos recevaient le même cours, avec les
        # deux salles concaténées en « 4A-K02 U3-04 ».
        self.fonds = self.examens + [r for r in dans_bande
                                     if est_olive(r['non_stroking_color'])]

        self.barres_milieu = [r for r in barres if abs(r['top'] - self.milieu) < 2.5]

        # Bordures verticales des cases. Elles ne peuvent PAS être cherchées
        # dans `dans_bande` : le PDF ne dessine pas un trait par journée mais un
        # seul rectangle noir traversant toutes les journées consécutives où la
        # bordure existe (x=246.5 court de y=69.7 à y=179.3, soit cinq bandes).
        # Filtrées sur `top`, elles disparaissaient toutes — d'où la détection
        # morphologique de secours, qui prenait les zones vides de la grille
        # pour des cours (« 12h00-19h15 » le 26/08, cours dupliqués le 01/10).
        self.verticales = [r for r in page.rects + _verticales_en_courbes(page)
                           if est_noir(r['non_stroking_color'])
                           and r['width'] < 3 and r['height'] > 3
                           and r['top'] < self.bas - 1 and r['bottom'] > self.haut + 1
                           and x_min_pdf - 3 <= (r['x0'] + r['x1']) / 2 <= x_max_pdf + 3]

    def _est_bord_de_salle(self, barre, jeu=2.0):
        """Le bord supérieur d'une case de salle passe à mi-hauteur et ressemble
        à une séparation TOP/BOTTOM. Il en a exactement la largeur : c'est ce qui
        permet de le reconnaître."""
        return any(abs(r['x0'] - barre['x0']) < jeu and abs(r['x1'] - barre['x1']) < jeu
                   for r in self.salles + self.examens)

    def zones_coupees(self):
        """Segments de la barre médiane : chacun délimite exactement une cellule.

        C'est le repère le plus fiable des deux emplois du temps. Les barres du
        haut et du bas, elles, sont interrompues par les cases vertes de salle
        (qui les recouvrent), ce qui fragmenterait les cours.

        CORRECTIF : une case de salle dessinée dans la moitié basse d'une cellule
        pleine hauteur pose son bord supérieur pile à mi-hauteur. Prise pour une
        séparation, elle coupait le cours en deux (« TCP/IP » 07h45→09h45 devenu
        07h45→09h15). On l'écarte en comparant sa largeur à celle des cases.
        """
        return sorted(((round(b['x0'], 1), round(b['x1'], 1))
                       for b in self.barres_milieu if not self._est_bord_de_salle(b)),
                      key=lambda s: s[0])

    def est_coupee(self, x0, x1, part_min=0.5):
        """Vrai si une séparation TOP/BOTTOM traverse cette plage.

        OpenCV fusionne les deux moitiés d'un créneau en une seule case ; c'est
        cette méthode qui dit s'il faut y lire un cours ou deux.
        """
        largeur = max(x1 - x0, 1.0)
        for gauche, droite in self.zones_coupees():
            if (min(droite, x1) - max(gauche, x0)) / largeur >= part_min:
                return True
        return False

    def _recouvre(self, rect, y0, y1, part=0.6):
        """Vrai si `rect` occupe au moins `part` de la tranche verticale."""
        return (min(rect['bottom'], y1) - max(rect['top'], y0)) >= part * (y1 - y0)

    def cellules(self, mots_bande):
        """Cases de cours de la journée, lues dans le vectoriel du PDF.

        Remplace la détection morphologique OpenCV. Une case est délimitée par
        ses bordures verticales noires ; la barre médiane dit si elle est
        coupée en deux. Une plage sans texte est de la grille vide, pas un
        cours : c'est ce qui manquait à OpenCV, incapable de distinguer les
        pointillés de la grille du contenu d'une cellule.
        """
        tranches = (("TOP", self.haut, self.milieu), ("BOTTOM", self.milieu, self.bas))
        segments = {}
        for nom, y0, y1 in tranches:
            bornes = {round(self.x_min, 1), round(self.x_max, 1)}
            for r in self.verticales:
                if self._recouvre(r, y0, y1):
                    bornes.add(round((r['x0'] + r['x1']) / 2, 1))
            triees = sorted(x for x in bornes
                            if self.x_min - 3 <= x <= self.x_max + 3)
            segments[nom] = [(a, b) for a, b in zip(triees, triees[1:]) if b - a > 4]

        cellules = []
        restants_bas = list(segments["BOTTOM"])
        for gauche, droite in segments["TOP"]:
            jumeau = next((s for s in restants_bas
                           if abs(s[0] - gauche) < 2 and abs(s[1] - droite) < 2), None)
            # Mêmes bornes en haut et en bas, sans barre médiane : cellule pleine.
            if jumeau and not self.est_coupee(gauche, droite):
                restants_bas.remove(jumeau)
                cellules.append((gauche, droite, "FULL"))
            else:
                cellules.append((gauche, droite, "TOP"))
        cellules += [(g, d, "BOTTOM") for g, d in restants_bas]

        retenues = []
        for gauche, droite, position in sorted(cellules):
            y0, y1 = self.bornes_verticales(position)
            if any(gauche - 2 <= m['x0'] and m['x1'] <= droite + 4
                   and y0 - 0.5 <= m['top'] < y1 + 1.5 for m in mots_bande):
                retenues.append({"x0": gauche, "x1": droite, "position": position})
        return retenues

    def bornes_verticales(self, position):
        """Haut et bas de la moitié visée, en points PDF."""
        if position == "TOP":
            return self.haut, self.milieu
        if position == "BOTTOM":
            return self.milieu, self.bas
        return self.haut, self.bas

    def couleur(self, x0, x1, y0, y1):
        """Couleur de FOND de la cellule.

        Un simple chevauchement ne suffit pas : les emplois du temps contiennent
        des pastilles orange sans rapport, qui faisaient passer des cours réels
        pour des cours annulés (et les faisaient disparaître de l'agenda).

        Mais exiger 60 % de la largeur était trop strict. Le PDF est incohérent :
        tantôt le fond d'examen couvre toute la cellule et la case verte de
        salle est dessinée par-dessus, tantôt il s'arrête AVANT cette case. Dans
        ce second cas il ne couvrait que 58 % — les trois examens d'« Adm. Linux »
        du 18/09 passaient donc pour des cours ordinaires.

        Un fond qui commence exactement au bord gauche de la cellule lui
        appartient : c'est ce qui le distingue d'une pastille posée au milieu.

        Le recouvrement vertical se mesure sur l'UNION des bandes d'une même
        couleur. Le PDF de L3 du 13/09 dessine le fond d'un examen en deux
        demi-bandes empilées, une par ligne de texte : chacune couvre 48 % de
        la case (10,0 pt sur 20,7), sous le seuil, alors qu'ensemble elles la
        couvrent entièrement. Les deux examens du 28/09 et du 05/10 sortaient en
        cours ordinaires, et la vérification bloquait la CI.
        """
        largeur = max(x1 - x0, 1.0)
        hauteur = max(y1 - y0, 1.0)
        bandes = {}
        for r in self.fonds:
            part = (min(r['x1'], x1) - max(r['x0'], x0)) / largeur
            commence_au_bord = abs(r['x0'] - x0) <= 3
            if part < (0.35 if commence_au_bord else 0.6):
                continue
            haut, bas = max(r['top'], y0), min(r['bottom'], y1)
            if bas <= haut:
                continue
            couleur = r['non_stroking_color']
            nom = ("JAUNE" if est_jaune(couleur)
                   else "ORANGE" if est_orange(couleur) else "OLIVE")
            bandes.setdefault(nom, []).append((haut, bas))

        # Le jaune d'abord : un examen manqué est la pire des erreurs possibles.
        for nom in ("JAUNE", "ORANGE", "OLIVE"):
            # Le fond d'un cours du haut effleure la moitié basse : il faut un
            # vrai recouvrement vertical, pas un contact.
            if _longueur_union(bandes.get(nom, [])) / hauteur >= 0.5:
                return nom
        return "BLANC"

    def est_salle(self, mot, y0, y1):
        """Ce mot est-il posé sur une case verte de salle, dans cette moitié ?

        Une case de salle mesure 10,7 pt pour une demi-bande de 10,35 : celle
        du HAUT déborde donc sous la mi-hauteur. Avec la tolérance de 2 pt, elle
        happait le titre du cours du BAS — le 08/09, « Interco - BE (TD) » à
        220,04 contre une case finissant à 218,12, soit 0,08 pt de trop. Tous
        les mots devenaient des salles, le titre disparaissait et le cours
        n'était pas publié du tout.

        On n'examine donc que les cases dont le CENTRE est dans la moitié
        interrogée : une case ne peut plus revendiquer les mots de l'autre.
        """
        for r in self.salles:
            centre = (r['top'] + r['bottom']) / 2
            if not (y0 - 1 <= centre <= y1 + 1):
                continue
            if (r['x0'] - 2 <= mot['x0'] and mot['x1'] <= r['x1'] + 2
                    and r['top'] - 2 <= mot['top'] <= r['bottom'] + 2
                    and y0 - 1 <= mot['top'] <= y1 + 1):
                return True
        return False


def _lignes(mots, tolerance=1.5):
    """Regroupe les mots par ligne de base (leur « top » à ~1 pt près)."""
    lignes = []
    for mot in sorted(mots, key=lambda w: (w['top'], w['x0'])):
        for ligne in lignes:
            if abs(ligne[0]['top'] - mot['top']) <= tolerance:
                ligne.append(mot)
                break
        else:
            lignes.append([mot])
    return [sorted(l, key=lambda w: w['x0']) for l in lignes]


def _est_italique(mots):
    """Le M1 nomme sa fonte « OpenSans-Italic » ; d'autres PDF disent
    « Oblique ». Accepter les deux évite de dépendre du logiciel qui a produit
    le document — la L3, elle, n'emploie aucune italique, d'où le repli sur la
    table des enseignants dans `_est_ligne_prof`."""
    return bool(mots) and all(
        any(marque in (m.get('fontname') or "").lower()
            for marque in ("italic", "oblique"))
        for m in mots)


REGEX_INITIALES = re.compile(r'[A-ZÀ-Ý]{2,4}(\s*[&+]\s*[A-ZÀ-Ý]{2,4})*\+?')


def _est_ligne_prof(mots):
    """La 2e ligne d'une cellule pleine hauteur porte-t-elle le professeur ?

    L'italique est le signe normal, mais le PDF n'est pas régulier : « AA » est
    en Regular là où « TG » et « TD » sont en Italic, et le cours sortait alors
    en « Interopérabilité AA » sans professeur. On accepte donc aussi une ligne
    qui est un sigle connu, ou de simples initiales majuscules.
    """
    if not mots:
        return False
    if _est_italique(mots):
        return True
    texte = " ".join(m['text'] for m in mots).strip()
    return texte in PROFS or bool(REGEX_INITIALES.fullmatch(texte))


def _analyser_texte(mots_titre):
    """« TCP/IP /GB (CT) » -> (titre, groupe, prof, promotion visée)."""
    texte = " ".join(m['text'] for m in mots_titre).strip()
    texte = re.sub(r'\s+', ' ', texte)

    promo = mention_promo(texte)

    prof = ""
    trouve = REGEX_PROF.search(texte)
    # « Gestion (M1 RT) » : la parenthèse nomme la promotion, pas l'enseignant.
    # On la laisse dans le titre, où elle reste une indication utile.
    if trouve and not mention_promo(trouve.group(1)):
        prof = _nom_complet(trouve.group(1))
        texte = texte[:trouve.start()].strip()

    groupe = None
    trouve_g = REGEX_GROUPE.search(texte)
    if trouve_g:
        groupe = trouve_g.group(1).upper()
        texte = REGEX_GROUPE.sub("", texte).strip()

    return re.sub(r'\s+', ' ', texte).strip(" -/"), groupe, prof, promo


def mots_de_la_bande(page, zone, x_min_pdf):
    """Mots d'une journée. La bordure du haut est partagée avec la veille : les
    mots posés dessus appartiennent à la journée précédente."""
    bande = page.crop((0, zone['top'], page.width, zone['bottom']), strict=False)
    return [m for m in bande.extract_words(extra_attrs=["fontname"])
            if m['top'] > zone['top'] + 1.0 and m['x0'] >= x_min_pdf - 2]


def _porte_deux_cours(grille, mots):
    """Vrai si une case pleine hauteur contient en fait DEUX cours empilés.

    Le PDF ne dessine pas toujours la barre médiane : la case paraît alors
    pleine hauteur alors qu'elle porte un cours par demi-promotion. Sans cette
    distinction, les deux se retrouvaient collés en un seul événement, attribué
    à la seule moitié haute — « Phy (Réseaux) - C Tech. de trans. (EG) » en
    salle « U3-110 U3-04 » le 08/09 — et la moitié basse perdait son cours.

    Ce qui sépare les deux situations est la ligne du BAS :

        Outils Maths          ← vraie case pleine : le bas porte le professeur
        AB

        Phy (Réseaux) - C     ← deux cours : le bas porte un intitulé
        Tech. de trans. (EG)

    `_est_ligne_prof` fait déjà cette lecture ; on s'appuie sur elle plutôt que
    d'inventer un second critère qui divergerait du premier.
    """
    titre_mots = [m for m in mots
                  if not grille.est_salle(m, grille.haut, grille.bas)]
    lignes = _lignes(titre_mots)
    if len(lignes) < 2:
        return False

    def au_dessus(ligne):
        return sum(m['top'] for m in ligne) / len(ligne) < grille.milieu

    hautes = [l for l in lignes if au_dessus(l)]
    basses = [l for l in lignes if not au_dessus(l)]
    return bool(hautes) and bool(basses) and not _est_ligne_prof(basses[0])


def lire_cellule(grille, gauche, droite, position, mots_bande, vers_heure):
    """Cours d'une cellule dont les bords sont déjà connus. Rend une LISTE.

    Utilisé avec les cases repérées par OpenCV : la géométrie vient de l'image
    (détection validée), le contenu de la couche texte du PDF (exact, sans OCR).

    La liste compte deux cours quand une case pleine hauteur en porte deux — un
    par demi-promotion. Chacun garde sa `position`, dont l'appelant se sert pour
    l'attribuer à la bonne promotion.
    """
    if position == "TOP":
        y0, y1 = grille.haut, grille.milieu
    elif position == "BOTTOM":
        y0, y1 = grille.milieu, grille.bas
    else:
        y0, y1 = grille.haut, grille.bas

    # Un mot appartient à la cellule s'il y tient entièrement. Seule exception :
    # le libellé de salle, qui commence dans la cellule mais déborde souvent à
    # droite de sa bordure — le rejeter donnait « Non attribuée » au lieu de
    # « U3-307/308 ». L'exception est restreinte aux cases vertes, sinon les
    # titres voisins se font happer d'une cellule à l'autre.
    dans_moitie = [m for m in mots_bande if y0 - 0.5 <= m['top'] < y1 + 1.5]
    dedans = [m for m in dans_moitie
              if gauche - 2 <= m['x0']
              and (m['x1'] <= droite + 4
                   or (m['x0'] < droite and grille.est_salle(m, y0, y1)))]
    if not dedans:
        return []

    # Deux cours empilés sans barre médiane : on relit chaque moitié séparément
    # plutôt que d'inventer un découpage — la machinerie existante sait déjà
    # rattacher à chacune ses mots et sa salle.
    if position == "FULL" and _porte_deux_cours(grille, dedans):
        moities = [bloc
                   for demi in ("TOP", "BOTTOM")
                   for bloc in lire_cellule(grille, gauche, droite, demi,
                                            mots_bande, vers_heure)]
        # Deux cours seulement si les deux se lisent vraiment : sinon on
        # retomberait sur une lecture pleine hauteur, qui reste la plus sûre.
        if len(moities) == 2:
            return moities

    # Une case qui coupe un titre en plein milieu n'est pas une vraie cellule :
    # c'est une case englobante mal détectée. Elle produisait des cours tronqués
    # (« Tel. Mobiles » sans son professeur, sur un créneau faux).
    if _coupe_un_titre(dedans, dans_moitie, gauche, droite, grille, y0, y1):
        return []

    bloc = _construire(grille, gauche, droite, position, y0, y1, dedans, vers_heure)
    return [bloc] if bloc else []


def _coupe_un_titre(dedans, dans_moitie, gauche, droite, grille, y0, y1, colle=5.0):
    """Vrai si un mot voisin prolonge le texte capturé, juste au-delà d'un bord."""
    exclus = [m for m in dans_moitie if m not in dedans]
    for bord, cote in ((droite, "droite"), (gauche, "gauche")):
        for m in exclus:
            if grille.est_salle(m, y0, y1):
                continue
            colles = [d for d in dedans
                      if abs(d['top'] - m['top']) < 1.5
                      and (0 <= m['x0'] - d['x1'] <= colle if cote == "droite"
                           else 0 <= d['x0'] - m['x1'] <= colle)]
            if colles and (m['x0'] >= bord - 2 if cote == "droite" else m['x1'] <= bord + 2):
                return True
    return False


def _construire(grille, gauche, droite, position, y0, y1, mots, vers_heure):
    """Assemble un cours depuis les mots d'une cellule, ou None si rien de
    lisible.

    Les mots posés sur une case verte sont la salle, les autres le titre ;
    dans une cellule pleine hauteur, la deuxième ligne porte le professeur.
    Aucun titre exploitable rend None plutôt qu'un cours vide.
    """
    if not mots:
        return None

    salle_mots = [m for m in mots if grille.est_salle(m, y0, y1)]
    titre_mots = [m for m in mots if m not in salle_mots]
    if not titre_mots:
        return None

    lignes = _lignes(titre_mots)
    titre, groupe, prof, promo = _analyser_texte(lignes[0])

    # Cellule pleine hauteur : le professeur est écrit en italique sous le titre.
    if position == "FULL" and len(lignes) > 1 and not prof:
        if _est_ligne_prof(lignes[1]):
            prof = _nom_complet(" ".join(m['text'] for m in lignes[1]))
        else:
            titre = (titre + " " + " ".join(m['text'] for m in lignes[1])).strip()

    if len(titre) < 2:
        return None

    debut, fin = vers_heure(gauche), vers_heure(droite)
    if not debut or not fin or debut >= fin:
        return None

    salle = " ".join(m['text'] for m in sorted(salle_mots, key=lambda m: m['x0'])).strip()

    return {
        "position": position,
        "color": grille.couleur(gauche, droite, y0, y1),
        "course": titre,
        # Promotion nommée dans le titre, quand le PDF le précise.
        "promo": promo,
        "prof": prof or None,
        "group": groupe,
        "room": salle or None,
        "start": debut,
        "end": fin,
    }
