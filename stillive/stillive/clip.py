"""Chaîne « une photo -> un clip » : analyse -> retouche -> un seul plan H3 (durée de 4 à 15 s,
musique instrumentale faite par H3 lui-même, aucune voix) -> avant/après -> film vertical et
horizontal. Reprend sur disque comme `chaine.fabriquer`.

Événements envoyés à `rapport(nom, données)` : "etape", "analyse", "plan", "fini" (comme chaine.py).
Le résultat de `fabriquer_clip` contient `horizontal`, `vertical`, `secondes`, `paroles_coupees`
(toujours vide, gardé pour les anciennes pages) et `message` (code "accel_absente" ou None).
"""
import json
import os
import random
import subprocess
import time

from PIL import Image

from . import analyse, h3, media, montage, retouche
from .chaine import _load, _save
from .comfy import Comfy, capacites_ou_none as _capacites

SECONDES_MIN, SECONDES_MAX = 4, 15


def _retouche_dispo(comfy):
    """Qwen Image 2.1 et son VAE présents dans ComfyUI ? L'installateur ne les télécharge pas : sans eux,
    la photo est animée telle quelle (au lieu d'un film en erreur). Capacités illisibles : on tente."""
    caps = _capacites(comfy)
    if caps is None:
        return True
    return retouche.MODEL in caps.get("unet", []) and retouche.VAE in caps.get("vae", [])
# Durées mesurées sur une RTX 5080 (10 films des 26 et 27/10, chargements des modèles compris) : analyse
# Gemma 28 à 38 s, retouche 22 s, montage 6 à 10 s ; plan H3 turbo 102 s à 4 s, 138 à 158 s à 7 s,
# 148 à 150 s à 8 s, soit environ 50 s + 12,5 s par seconde de vidéo (15 s non mesuré).
DUREES = {"analyse": 32, "retouche": 30, "montage": 10}
PLAN_FIXE, PLAN_PAR_SECONDE = 50, 12.5
# Mode « Entrer » (références) : plan plus long, 161 à 163 s à 8 s contre 141 à 150 s (films du 27/10).
PLAN_REF_EN_PLUS = 13


def duree_valide(secondes):
    """Entier de 4 à 15 : une valeur hors bornes est ramenée à la borne la plus proche."""
    return max(SECONDES_MIN, min(SECONDES_MAX, int(round(float(secondes)))))


def _plan(secondes, ref=False):
    return round(PLAN_FIXE + PLAN_PAR_SECONDE * secondes + (PLAN_REF_EN_PLUS if ref else 0))


def _estimation(secondes, retouche_faite, depuis, ref=False):
    order = ["analyse", "retouche", "plan", "montage"]
    cost = {"analyse": DUREES["analyse"], "retouche": DUREES["retouche"] if retouche_faite else 0,
            "plan": _plan(secondes, ref), "montage": DUREES["montage"]}
    return sum(cost[s] for s in order[order.index(depuis):])


SURPRISE = "__surprise__"


def valider_moi(recette, moi_visage, moi_corps):
    """« J'entre dans la photo » : une photo de la personne (moi_visage) suffit, la photo en pied
    (moi_corps) est facultative mais ne va pas seule ; photos existantes, recette compatible
    (revit seulement). Codes d'erreur : moi_incomplet, moi_introuvable, recette_incompatible_moi."""
    if moi_corps and not moi_visage:
        raise ValueError("moi_incomplet")
    if moi_visage:
        if not (os.path.isfile(moi_visage) and (not moi_corps or os.path.isfile(moi_corps))):
            raise ValueError("moi_introuvable")
        if recette != "revit":
            raise ValueError("recette_incompatible_moi")


def valider_geste(geste, moi_visage=None, valeur=None):
    """Option « geste » : code connu de analyse.GESTES ; les codes de analyse.GESTES_ENTRER demandent un selfie
    (moi_visage). Un geste de analyse.GESTES_VALEUR demande une précision (option « geste_valeur »).
    Renvoie la valeur normalisée (année en texte, lieu nettoyé, clé) ou None.
    Codes d'erreur : geste_inconnu, geste_incompatible, geste_valeur_invalide."""
    if not geste:
        return None
    if not isinstance(geste, str) or geste not in analyse.GESTES:
        raise ValueError("geste_inconnu")
    if geste in analyse.GESTES_ENTRER and not moi_visage:
        raise ValueError("geste_incompatible")
    if geste not in analyse.GESTES_VALEUR:
        return None
    genre = analyse.GESTES_VALEUR[geste]
    if genre == "annee":
        if isinstance(valeur, str) and valeur in analyse.EPOQUES:   # époque célèbre (bouton de la page, 29/10)
            return valeur
        if isinstance(valeur, bool) or not (isinstance(valeur, int) or (isinstance(valeur, str) and valeur.strip().isdigit())):
            raise ValueError("geste_valeur_invalide")
        annee = int(valeur)
        if not 1000 <= annee <= 3000:
            raise ValueError("geste_valeur_invalide")
        return str(annee)
    if genre == "danse":                       # facultatif : rien = danse libre
        if isinstance(valeur, str) and valeur in analyse.DANSES:
            return valeur
        if valeur is not None and not isinstance(valeur, str):
            raise ValueError("geste_valeur_invalide")
        return analyse._nettoyer(valeur, 60) if valeur else None
    if genre == "lieu":
        if isinstance(valeur, str) and valeur in analyse.LIEUX:     # destination célèbre (bouton de la page, 29/10)
            return valeur
        lieu = analyse._nettoyer(valeur, 60) if isinstance(valeur, str) else ""
        if not lieu:
            raise ValueError("geste_valeur_invalide")
        return lieu
    if not isinstance(valeur, str) or valeur not in genre:
        raise ValueError("geste_valeur_invalide")
    return valeur


def composer_souhait(geste, souhait, valeur=None):
    """Souhait final : phrase du geste (précision incluse), suivie du texte libre s'il existe.
    valeur : résultat de valider_geste."""
    phrase = analyse.GESTES[geste] if geste else ""
    if geste in analyse.GESTES_VALEUR:
        genre = analyse.GESTES_VALEUR[geste]
        if isinstance(genre, dict):
            texte = genre[valeur]
        elif genre == "lieu":
            texte = analyse.nom_lieu(analyse._nettoyer(valeur, 60))
        elif genre == "danse":
            texte = analyse.mouvements_danse(valeur)
        else:
            # « the year 1970 » ou une époque célèbre : sa description une fois, puis son nom court.
            phrase = phrase.replace("{valeur}", analyse.nom_epoque(valeur), 1)
            texte = analyse._court_epoque(valeur)
        phrase = phrase.replace("{valeur}", texte)
    libre = analyse._nettoyer(souhait, 500)
    return (f"{phrase} {libre}".strip() if phrase else libre) or libre


# « Comme au cinéma » et « Sors du cadre » : plan ordinaire avec un grand mouvement de caméra imposé.
# Le choix dépend de la photo (pas du hasard) : un film repris après un arrêt garde le même mouvement.
EFFETS_CAMERA = {"cinema": ("cinema_orbit", "cinema_crane", "cinema_dolly"), "cadre": ("cadre",)}


def mouvement_effet(recette, photo, mouvement="auto"):
    choix = EFFETS_CAMERA.get(recette)
    if not choix or mouvement in choix:
        return mouvement
    return choix[int(_empreinte(photo), 16) % len(choix)]


def _mode_plan(recette):
    """Mode du plan H3 : « temps » et « boucle » sont des modes à deux images, chacun distinct des autres."""
    return recette if recette in ("temps", "boucle") else ""


def _empreinte(*chemins):
    import hashlib
    h = hashlib.sha1()
    for c in chemins:
        if not c:
            continue
        with open(c, "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def proposer(photo, sortie, comfy_url, langue="fr", secondes=7, deja=()):
    """Étape 1 « L'IA propose » : envoie la photo, demande 3 idées d'action (drôle, émouvante,
    magique), les enregistre dans `propositions.json` du dossier et les renvoie. Ne touche pas
    aux fichiers de reprise du film. Une réponse illisible lève ValueError."""
    secondes = duree_valide(secondes)
    os.makedirs(sortie, exist_ok=True)
    comfy = Comfy(comfy_url)
    name = comfy.upload_image(photo)
    idees, derniere = [], None
    for _ in range(2):  # le modèle oublie parfois une idée : une seule nouvelle tentative pour en avoir trois
        entry = comfy.wait(comfy.queue(analyse.propositions_graph(name, langue, secondes, deja), devant=True))
        try:
            essai = analyse.parse_propositions(entry)
        except ValueError as e:
            derniere = e  # réponse illisible : on réessaie une fois
            continue
        if len(essai) > len(idees):
            idees = essai
        if len(idees) >= len(analyse.TONS):
            break
    if not idees:
        raise derniere
    path = os.path.join(sortie, "propositions.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump({"langue": langue, "secondes": secondes, "deja": list(deja or ()), "propositions": idees},
                  f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)
    return idees


def lire_propositions(sortie):
    """Idées enregistrées par `proposer`, ou None."""
    try:
        with open(os.path.join(sortie, "propositions.json"), encoding="utf-8") as f:
            return json.load(f)["propositions"] or None
    except (OSError, ValueError, KeyError):
        return None


def idee_surprise(idees):
    """« Surprends-moi » : l'idée magique si elle existe, sinon la première."""
    return next((i for i in idees if i["ton"] == "magique"), idees[0])


def fabriquer_clip(photo, sortie, comfy_url, secondes=8, avec_retouche=True, couleur=None,
                   souhait=None, recette="revit", mouvement="auto", look="aucun",
                   moi_visage=None, moi_corps=None, signature=None, langue="fr", rapport=lambda nom, donnees: None,
                   geste=None, mot=None, geste_valeur=None, forcer_enfant=False):
    """geste : code de analyse.GESTES (ignoré en recette « boucle »), ajouté avant le texte libre ;
    forcer_enfant : pour « calin_enfant », saute la vérification « jeune enfant sur la photo »
    (sinon, si l'analyse répond clairement non, ValueError("pas_enfant") avant la retouche) ;
    geste_valeur : précision du geste (année, lieu ou clé), voir analyse.GESTES_VALEUR ;
    mot : dédicace écrite sur la fin du film (le montage seul est refait quand il change).
    couleur : False pour garder une photo noir et blanc telle quelle ; None = mise en couleur auto.
    recette : "revit" ou "polaroid" ; mouvement : "auto" ou une clé de analyse.CAMERA_MOVES ;
    look : "aucun", "auto", "super8", "vhs" ou "camescope"."""
    secondes = duree_valide(secondes)
    if recette not in montage.RECETTES:
        raise ValueError(f"recette inconnue : {recette}")
    mouvement = mouvement_effet(recette, photo, mouvement)
    if mouvement != "auto" and mouvement not in analyse.CAMERA_MOVES:
        raise ValueError(f"mouvement inconnu : {mouvement}")
    if look not in montage.LOOKS:
        raise ValueError(f"look inconnu : {look}")
    valider_moi(recette, moi_visage, moi_corps)
    geste_valeur = valider_geste(geste, moi_visage, geste_valeur)
    if geste == "animer" and geste_valeur == "surprise" and mouvement == "auto":   # caméra audacieuse (29/10)
        mouvement = EFFETS_CAMERA["cinema"][int(_empreinte(photo), 16) % len(EFFETS_CAMERA["cinema"])]
    moi_visage, moi_corps = moi_visage or None, moi_corps or None
    mot = montage.nettoyer_mot(mot)
    if recette == "boucle":
        geste = geste_valeur = None  # boucle parfaite : aucune action, le geste est ignoré
    moi_id = _empreinte(moi_visage, moi_corps) if moi_visage else ""
    os.makedirs(sortie, exist_ok=True)
    comfy = Comfy(comfy_url)
    times = {}
    alertes = {}  # ex. {"accel_absente": True} : repli vers 20 étapes, signalé dans le résultat
    jobs_file = os.path.join(sortie, "travaux.json")
    known = json.load(open(jobs_file, encoding="utf-8")) if os.path.isfile(jobs_file) else {}

    def job(key, make_graph):
        pid = known.get(key)
        if pid and comfy.statut(pid) in ("success", "en_cours"):
            return pid
        known[key] = comfy.queue(make_graph())
        with open(jobs_file, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
        return known[key]

    # 1. Analyse
    # Retouche comptée d'avance seulement pour une photo noir et blanc (la plupart des photos n'en ont pas besoin :
    # la compter toujours annonçait 20 à 30 s de trop) ; si elle a lieu quand même, l'étape « retouche » recale.
    retouche_probable = avec_retouche and retouche.is_black_and_white(photo)
    rapport("etape", {"etape": "analyse", "restant": _estimation(secondes, retouche_probable, "analyse", bool(moi_visage))})
    t = time.time()
    name = name_origine = comfy.upload_image(photo)
    souhait = composer_souhait(geste, souhait, geste_valeur)
    results = _load(sortie, 1)
    if results is not None and (results[0].get("souhait", "") != souhait or results[0].get("moi", "") != moi_id):
        # Le souhait ou les photos de la personne ont changé : analyse, retouche réutilisée mais plan et séquence à refaire.
        for f in ("sequence00.mp4", "plan00.mp4", "plan00_2.mp4", "plan00_muet.mp4"):
            if os.path.isfile(os.path.join(sortie, f)):
                os.remove(os.path.join(sortie, f))
        for k in ("analyse", "plan00", "plan00_2"):
            known.pop(k, None)
        with open(jobs_file, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
        results = None
    if results is not None:
        # Mouvement de caméra changé : le plan H3 est à refaire. Recette ou look changés : seul le montage.
        def effacer(noms, cles=()):
            for f in noms:
                if os.path.isfile(os.path.join(sortie, f)):
                    os.remove(os.path.join(sortie, f))
            for k in cles:
                known.pop(k, None)
            with open(jobs_file, "w", encoding="utf-8") as f:
                json.dump(known, f, indent=1)
        if results[0].get("mouvement", "auto") != mouvement:
            effacer(("sequence00.mp4", "plan00.mp4", "plan00_2.mp4", "plan00_muet.mp4"), ("plan00", "plan00_2"))
        elif _mode_plan(results[0].get("recette", "revit")) != _mode_plan(recette):
            # « temps » et « boucle » sont d'autres modes H3 (deux images) : le plan lui-même est à refaire.
            effacer(("sequence00.mp4", "plan00.mp4", "plan00_2.mp4", "plan00_muet.mp4"), ("plan00", "plan00_2"))
        elif (results[0].get("recette", "revit"), results[0].get("look", "aucun")) != (recette, look):
            effacer(("sequence00.mp4",))
    if results is None:
        # La personne est décrite d'après la photo en pied, à défaut d'après sa seule photo.
        moi_nom = comfy.upload_image(moi_corps or moi_visage) if moi_visage else None
        entry = comfy.wait(job("analyse", lambda: analyse.graph([name], secondes=secondes, souhait=souhait,
                                                                 moi_corps=moi_nom, langue=langue,
                                                                 verifier_enfant=(geste == "calin_enfant"))))
        results = analyse.parse(entry, 1)
        if "error" in results[0]:
            raise RuntimeError(f"Analyse illisible pour {os.path.basename(photo)}")
        results[0]["photo"] = photo
        results[0]["souhait"] = souhait
        results[0]["moi"] = moi_id
        if moi_visage:
            results[0]["moi_description"] = analyse.parse_moi(entry)
        results[0]["black_and_white"] = bool(results[0].get("black_and_white")) or retouche.is_black_and_white(photo)
        _save(results, sortie)
    r = results[0]
    times["analyse"] = round(time.time() - t, 1)
    rapport("analyse", {"photos": [{"anecdote": r.get("fun_fact", ""), "langues": analyse.anecdotes(r), "annee": r.get("year_guess"),
                                    "occasion": r.get("occasion", "")}]})
    if geste == "calin_enfant" and not forcer_enfant and r.get("child_present") is False:
        raise ValueError("pas_enfant")  # seulement un non clair : absente ou illisible, le film continue

    # 2. Retouche (photo ancienne, abîmée ou en noir et blanc)
    t = time.time()
    restored = None
    if r.get("restored") and os.path.isfile(r["restored"]):
        restored = r["restored"]
        name = comfy.upload_image(restored)
    elif avec_retouche and _retouche_dispo(comfy):
        old = r.get("decade", "2000s") < "1990s"
        if r["black_and_white"] or r.get("damaged") or old:
            rapport("etape", {"etape": "retouche", "restant": _estimation(secondes, True, "retouche", bool(moi_visage))})
            colorize = r["black_and_white"] and (couleur if couleur is not None else True)
            text = retouche.prompt(colorize=colorize, damaged=bool(r.get("damaged")) or old,
                                   border=bool(r.get("is_scan_with_border")))
            pid = job("retouche00", lambda: retouche.graph(
                name, text, seed=random.randint(0, 2**32 - 1), prefix="stillive/retouche00"))
            f = comfy.download_outputs(comfy.wait(pid), sortie)[0]
            restored = os.path.join(sortie, "retouche00.png")
            os.replace(f, restored)
            r["restored"] = restored
            r["black_and_white"] = r["black_and_white"] and not colorize
            r["is_scan_with_border"] = False
            name = comfy.upload_image(restored)
    times["retouche"] = round(time.time() - t, 1)
    # Consigne recalculée à chaque fois : musique H3 et longueur dépendent de ce mode.
    # « temps » demande une photo retouchée (dernière image) : sans retouche, repli sur « revit ».
    repli = "temps_vers_revit" if recette == "temps" and not restored else None
    utilisee = "revit" if repli else recette
    boucle_h3 = utilisee == "boucle"
    temps_h3 = utilisee == "temps"
    if boucle_h3:
        r["h3_prompt"] = analyse.h3_prompt_boucle(r, 0, secondes=secondes, music=True)
    elif moi_visage:
        analyse.reparer_entree(r)
        r["h3_prompt"] = analyse.h3_prompt_entrer(r, r.get("moi_description", ""), 0, secondes=secondes,
                                                  music=True, mouvement=mouvement, avec_corps=bool(moi_corps),
                                                  sol=geste == "super_heros", fond=geste)
    elif temps_h3:
        r["h3_prompt"] = analyse.h3_prompt_temps(r, 0, secondes=secondes, music=True, mouvement=mouvement)
    else:
        r["h3_prompt"] = analyse.h3_prompt(r, 0, music=True, mouvement=mouvement, secondes=secondes)
    if geste in analyse.GESTES_VISAGE and not boucle_h3:   # verrou du visage, mot pour mot (l'analyse le perdait)
        r["h3_prompt"] = r["h3_prompt"].replace(analyse.VISAGES, analyse.VISAGES + analyse.GESTES_VISAGE[geste].format(valeur=analyse.nom_lieu(geste_valeur) if geste == "teleportation" and geste_valeur else geste_valeur or "the new place"), 1)
    if geste == "epoque" and not boucle_h3:   # transformation pièce par pièce, horodatée selon la durée (29/10)
        r["h3_prompt"] = r["h3_prompt"].replace(analyse.VISAGES, analyse.VISAGES + analyse.choregraphie_epoque(geste_valeur, secondes), 1)
        # Musique caricaturale de l'année demandée à la place de l'ambiance de l'analyse (29/10).
        r["h3_prompt"] = r["h3_prompt"].replace(analyse.musique_h3(r), analyse.musique_h3(
            {"music_mood": analyse.musique_epoque(geste_valeur)}), 1)
    if geste == "danser" and geste_valeur and not boucle_h3:   # style de danse choisi (29/10)
        # Mouvements mot pour mot (l'analyse réécrit la phrase du geste), musique EXACTEMENT du style de la danse.
        r["h3_prompt"] = r["h3_prompt"].replace(analyse.VISAGES, analyse.VISAGES + "Dance: from the first second "
                                                "everyone is " + analyse.mouvements_danse(geste_valeur) + ". ", 1)
        r["h3_prompt"] = r["h3_prompt"].replace(analyse.musique_h3(r), analyse.musique_h3(
            {"music_mood": analyse.musique_danse(geste_valeur)}), 1)
    r["repli"] = repli
    r["secondes"] = secondes
    r["recette"], r["look"], r["mouvement"] = recette, look, mouvement
    _save(results, sortie)

    # 3. Le plan H3 (musique comprise). Une seule relance, autre graine, si des mots sont entendus.
    with Image.open(restored or photo) as im:
        w, hgt = im.size
    sequence = os.path.join(sortie, "sequence00.mp4")
    t = time.time()
    if not os.path.isfile(sequence):
        rapport("etape", {"etape": "plans", "restant": _estimation(secondes, False, "plan", bool(moi_visage))})
        # Pas de contrôle des paroles : sur 158 clips « No dialogue », H3 n'a dit aucun mot (seulement des rires).
        target, key = os.path.join(sortie, "plan00.mp4"), "plan00"
        if not os.path.isfile(target):
            if moi_visage:
                faire = lambda: h3.graphe_verifie(h3.build_ref(name, comfy.upload_image(moi_visage),
                                             comfy.upload_image(moi_corps) if moi_corps else None,
                                             r["h3_prompt"], w, hgt, secondes, caps=_capacites(comfy),
                                             seed=random.randint(0, 2**32 - 1), prefix=f"stillive/{key}"), alertes)
            else:
                faire = lambda: h3.graphe_verifie(h3.build(name_origine if temps_h3 else name, r["h3_prompt"], w, hgt, secondes,
                                         caps=_capacites(comfy), turbo=True,  # LoRA 4 étapes + euler (J5-11, 26/10)
                                         seed=random.randint(0, 2**32 - 1), prefix=f"stillive/{key}",
                                         last_image_name=name if (temps_h3 or boucle_h3) else None), alertes)
            e = comfy.wait(job(key, faire))
            os.replace([x for x in comfy.download_outputs(e, sortie) if x.endswith(".mp4")][0], target)
        # H3 rend un peu plus long que demandé (grille de 17 images) : plan coupé à la durée choisie.
        # « temps » garde son plan entier (sa dernière image est la photo retouchée).
        plan = target
        if not (temps_h3 or boucle_h3):  # « boucle » aussi : couper ferait perdre la dernière image
            plan =os.path.join(sortie, "plan00_coupe.mp4")
            montage.couper(target, plan, secondes)
        montage.RECETTES[utilisee](photo, plan, sequence + ".tmp.mp4", restored)
        look_reel = montage.look_auto(r.get("decade")) if look == "auto" else look
        if look_reel != "aucun":
            annee = r.get("year_guess")
            try:
                annee = int(annee) if str(annee).isdigit() else int(str(r.get("decade", "2000"))[:4] or 2000)
            except (ValueError, TypeError):
                annee = 2000
            montage.appliquer_look(sequence + ".tmp.mp4", sequence + ".look.mp4", look_reel, annee)
            os.replace(sequence + ".look.mp4", sequence + ".tmp.mp4")
        os.replace(sequence + ".tmp.mp4", sequence)
        _save(results, sortie)
    times["plans"] = round(time.time() - t, 1)
    apercu = os.path.join(sortie, "sequence00_apercu.mp4")  # le navigateur reçoit une version légère, pas l'intermédiaire
    montage.apercu(sequence, apercu)
    rapport("plan", {"index": 0, "video": apercu, "fait": 1, "total": 1, "restant": DUREES["montage"]})

    # 4. Montage : la musique est dans le clip, une piste muette remplace la musique de l'album.
    rapport("etape", {"etape": "montage", "restant": DUREES["montage"]})
    t = time.time()
    silence = os.path.join(sortie, "silence.mp3")
    if not os.path.isfile(silence):
        subprocess.run([media.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                        "-t", str(montage.duration(sequence) + montage.END_CARD + 2), silence], check=True)
    films = {}
    for label, (fw, fh) in montage.tailles_film(sequence).items():  # taille du plan H3, sans agrandissement
        films[label] = os.path.join(sortie, f"film_{label}.mp4")
        montage.film([sequence], silence, films[label], fw, fh, ambience=1.0, signature=signature,
                     carton=False, mot=mot)  #signature sur la fin du film : pas de carton noir qui rallonge
    times["montage"] = round(time.time() - t, 1)
    # Films faits : le gros intermédiaire (quasi sans perte) est inutile. Une reprise ou un changement de
    # recette/look le refabrique vite depuis plan00.mp4 ; l'aperçu léger reste pour l'affichage.
    try:
        os.remove(sequence)
    except OSError:
        pass
    json.dump(times, open(os.path.join(sortie, "temps.json"), "w"), indent=1)
    resultat = {**films, "temps": times, "secondes": secondes, "paroles_coupees": [],
                "message": "accel_absente" if alertes else None,
                "alertes": sorted(alertes), "recette": utilisee, "repli": repli}  # repli : "temps_vers_revit" si « temps » n'a pas pu s'appliquer
    rapport("fini", resultat)
    return resultat
