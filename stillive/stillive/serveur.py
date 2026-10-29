"""Serveur de la page web Stillive : reçoit les photos, lance la fabrication en arrière-plan
et donne l'avancement à la page (qui l'interroge toutes les quelques secondes).

    python -m stillive.serveur [--port 8765] [--comfy http://127.0.0.1:8188]
"""
import argparse
import asyncio
import glob
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.error
import urllib.parse
import uuid

from aiohttp import web

from . import chaine, clip, media, moteur, scan
from .comfy import Comfy, ComfyError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
DATA = os.path.join(ROOT, "donnees", "films")
SCANS = os.path.join(ROOT, "donnees", "scans")
PROPOSITIONS = os.path.join(ROOT, "donnees", "propositions")
SCAN_DUREE_MAX = 3600  # un scan ou une proposition abandonnés sont supprimés après 1 h (au démarrage et ensuite)
FILE_MAX = 6  # films non terminés (en attente ou en fabrication) acceptés en même temps
EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
PHOTO_MAX = 40 * 1024 * 1024  # octets par photo reçue
PHOTOS_MAX = 12  # photos par film
ERREUR_MAX = 2000  # caractères de détail technique gardés
films = {}
verrou =threading.Lock()  # un seul film fabriqué à la fois : une seule carte graphique
# Plusieurs téléphones : les films passent chacun leur tour, dans l'ordre d'arrivée.
file_films = []
tour = threading.Condition()


def _url(film_id, path):
    return f"/films/{film_id}/{os.path.basename(path)}" if path else None


def _url_photo(film_id):
    """Adresse de la première photo du film (miniature de la galerie, tirage de l'attente)."""
    trouvees = sorted(glob.glob(os.path.join(DATA, film_id, "photos", "*")))
    return f"/films/{film_id}/photos/{os.path.basename(trouvees[0])}" if trouvees else None


def _lancer(film_id, photos, options, comfy_url):
    film = films[film_id]
    dossier = os.path.join(DATA, film_id)

    def rapport(nom, d):
        if film.get("annule"):
            raise _Annule()  # drapeau vérifié à chaque étape rapportée
        film["maj"] = time.time()
        if nom == "etape":
            film["etape"], film["restant"] = d["etape"], d["restant"]
            film["restant_t"] = time.time()
        elif nom == "analyse":
            film["anecdotes"] = [p["anecdote"] for p in d["photos"] if p.get("anecdote")]
            film["anecdotes_langues"] = [p.get("langues") or {} for p in d["photos"] if p.get("anecdote")]
        elif nom == "plan":
            film["plans"].append(_url(film_id, d["video"]))
            film["restant"], film["restant_t"] = d["restant"], time.time()
        elif nom == "fini":
            film["etape"], film["restant"] = "fini", 0
            film["films"] = {k: _url(film_id, d[k]) for k in ("horizontal", "vertical")}
            film["repli"] = d.get("repli")  # code (ex. "temps_vers_revit") repris par web/i18n
            film["message"] = d.get("message")  # code repris par web/i18n (ex. "paroles_coupees")
            film["paroles_coupees"] = d.get("paroles_coupees", [])

    with tour:
        file_films.append(film_id)
        tour.wait_for(lambda: film.get("annule") or file_films[0] == film_id)
    try:
        if not film.get("annule"):
            _fabriquer(film, film_id, dossier, photos, options, comfy_url, rapport)
    finally:
        with tour:
            if film_id in file_films:
                file_films.remove(film_id)
            tour.notify_all()
        if film.get("annule"):  # film annulé : plus rien à garder, ni dans la galerie ni sur le disque
            if films.get(film_id) is film:
                films.pop(film_id, None)
            shutil.rmtree(dossier, ignore_errors=True)


class _Annule(BaseException):
    """Film annulé depuis le téléphone : coupe la fabrication à la prochaine étape (BaseException pour
    traverser les « except Exception » des étapes ; _fabriquer l'attrape)."""


def _annuler_film(film_id, film):
    """Annule un film. En attente : retiré tout de suite (galerie et disque). En fabrication : marqué,
    il s'arrête à la prochaine étape (ComfyUI n'est pas coupé). Rend True si le film est retiré de suite."""
    film["annule"] = True
    with tour:
        tour.notify_all()  # un film qui attend son tour se réveille et se retire de la file
    if film["etape"] == "attente":
        films.pop(film_id, None)
        shutil.rmtree(os.path.join(DATA, film_id), ignore_errors=True)
        return True
    return False


def _code_erreur(erreur):
    """Code lisible par la page (clé i18n erreur.<code>) pour une exception ou un texte d'erreur,
    ou None si l'erreur est inconnue (la page affiche alors le texte générique)."""
    texte = str(erreur).lower()
    if (isinstance(erreur, (urllib.error.URLError, ConnectionError)) and not isinstance(erreur, urllib.error.HTTPError)) \
            or any(m in texte for m in ("comfyui ne répond pas", "connection refused", "urlopen error",
                                        "connexion refusée", "winerror 10061")):
        return "comfy_injoignable"
    if "ffmpeg est introuvable" in texte:
        return "ffmpeg_absent"
    if "cannot find class" in texte or "nœud inconnu" in texte or "noeud inconnu" in texte:
        return "noeud_manquant"
    if "value not in list" in texte:
        return "modele_manquant"
    if "out of memory" in texte or "outofmemory" in texte:
        return "memoire_carte"
    if "pas_enfant" in texte:
        return "pas_enfant"
    return None


def _erreur_connexion(e):
    """Vrai si l'échec vient d'une connexion à ComfyUI (pas d'erreur.txt : la reprise réessaiera)."""
    return (isinstance(e, (urllib.error.URLError, ConnectionError)) and not isinstance(e, urllib.error.HTTPError)) \
        or _code_erreur(e) == "comfy_injoignable"


def _fabriquer(film, film_id, dossier, photos, options, comfy_url, rapport):
    with verrou:
        if film.get("annule"):
            return
        film["etape"], film["plans"], film["debut"] = "analyse", [], time.time()
        try:
            media.verifier()  # ffmpeg absent : erreur claire dès le départ
            Comfy(comfy_url).attendre_pret(180)  # ComfyUI peut démarrer en même temps que le serveur
            if len(photos) == 1:  # une seule photo : un clip, musique faite par H3
                couleurs = options.get("couleurs")
                if options.get("scene") == clip.SURPRISE:  # « Surprends-moi » : idée magique, sinon la première
                    idees = clip.lire_propositions(dossier) or clip.proposer(
                        photos[0], dossier, comfy_url, options.get("langue", "en"), options.get("secondes", 7))
                    idee = clip.idee_surprise(idees)
                    film["idee"] = {"titre": idee.get("titre", ""), "resume": idee.get("resume", "")}
                    options = {**options, "scene": idee["souhait"]}
                clip.fabriquer_clip(photos[0], dossier, comfy_url, secondes=options.get("secondes", 8),
                                    couleur=couleurs[0] if couleurs else None,
                                    souhait=options.get("scene") or None,
                                    recette=options.get("recette") or "revit",
                                    mouvement=options.get("mouvement") or "auto",
                                    look=options.get("look") or "aucun",
                                    moi_visage=options.get("moi_visage") or None,
                                    moi_corps=options.get("moi_corps") or None,
                                    geste=options.get("geste") or None, geste_valeur=options.get("geste_valeur") or None, mot=options.get("mot") or None,
                                    langue=options.get("langue") or "en", rapport=rapport,
                                    forcer_enfant=bool(options.get("forcer_enfant")))
            else:
                chaine.fabriquer(photos, dossier, comfy_url, titre=options.get("titre") or None,
                                 couleurs=options.get("couleurs"),
                                 ambiance=options.get("ambiance", "auto"), langue=options.get("langue") or "en", rapport=rapport)
        except _Annule:
            return  # annulé par l'utilisateur : _lancer efface le dossier
        except Exception as e:  # l'erreur est montrée à l'utilisateur, jamais avalée
            film["etape"], film["erreur"], film["code"] = "erreur", str(e)[:ERREUR_MAX], _code_erreur(e)
            if _erreur_connexion(e):
                return  # pas d'erreur.txt : au prochain démarrage, la reprise réessaie
            try:
                with open(os.path.join(dossier, "erreur.txt"), "w", encoding="utf-8") as f:
                    f.write(str(e))
            except OSError:
                pass  # l'erreur reste visible à l'écran même si le disque refuse


def _nouveau(film_id, total):
    dossier = os.path.join(DATA, film_id)
    try:
        options = json.load(open(os.path.join(dossier, "options.json"), encoding="utf-8"))
    except (OSError, ValueError):
        options = {}
    idee = options.get("idee") if isinstance(options.get("idee"), dict) else None
    films[film_id] = {"id": film_id, "etape": "attente", "restant": None, "restant_t": None, "debut": None,
                      "total": total, "plans": [], "anecdotes": [], "films": None, "erreur": None, "code": None,
                      "photo": _url_photo(film_id), "idee": idee, "porte": options.get("porte"),
                      "scene": options.get("scene"), "geste": options.get("geste"),
                      "geste_valeur": options.get("geste_valeur"),
                      "mot": options.get("mot"), "look": options.get("look"),
                      "secondes": options.get("secondes", 8),
                      "recette": options.get("recette") or "revit",  # « boucle » : la page lit le film en boucle
                      "cree": os.path.getmtime(dossier) if os.path.isdir(dossier) else time.time(),
                      "maj": time.time()}
    return films[film_id]


def _vue(film):
    """État d'un film pour la page : les heures du PC deviennent des durées (l'horloge du téléphone
    peut différer), plus la place dans la file d'attente."""
    v = {k: x for k, x in film.items() if k not in ("restant_t", "debut", "maj")}
    now = time.time()
    v["ecoule"] = round(now - film["debut"]) if film.get("debut") else None
    v["depuis_estimation"] = round(now - film["restant_t"]) if film.get("restant_t") else None
    v["avant"] = file_films.index(film["id"]) if film["id"] in file_films else 0
    if film.get("etape") == "attente" and film.get("total", 1) == 1:
        # En file : temps restant = fin du film en cours + films d'avant + celui-ci (compte à rebours dès l'attente).
        reste = 0
        for i, fid in enumerate(file_films[:v["avant"]]):
            autre = films.get(fid) or {}
            if i == 0 and autre.get("restant") is not None and autre.get("restant_t"):
                reste += max(15, autre["restant"] - (now - autre["restant_t"]))
            else:
                reste += clip._estimation(clip.duree_valide(autre.get("secondes", 8)), False, "analyse")
        v["restant"] = round(reste + clip._estimation(clip.duree_valide(film.get("secondes", 8)), False, "analyse"))
        v["depuis_estimation"] = 0
    return v


def reprendre(comfy_url):
    """Au démarrage : retrouve les films sur disque. Les films finis ou en erreur sont
    seulement affichés ; ceux qu'un arrêt du serveur a coupés reprennent où ils en étaient."""
    if not os.path.isdir(DATA):
        return
    for film_id in sorted(os.listdir(DATA), key=lambda d: os.path.getmtime(os.path.join(DATA, d))):
        dossier = os.path.join(DATA, film_id)
        photos = sorted(glob.glob(os.path.join(dossier, "photos", "*")))
        if not photos:
            continue
        fini = os.path.isfile(os.path.join(dossier, "temps.json"))  # écrit seulement après le montage
        en_erreur = os.path.isfile(os.path.join(dossier, "erreur.txt"))
        if not fini and not en_erreur and not os.path.isfile(os.path.join(dossier, "options.json")):
            continue  # envoi interrompu : sans options.json, ce n'est pas un film à relancer
        film = _nouveau(film_id, len(photos))
        if fini:
            film["etape"], film["restant"] = "fini", 0
            film["films"] = {k: _url(film_id, os.path.join(dossier, f"film_{k}.mp4")) for k in ("horizontal", "vertical")}
        elif en_erreur:
            film["etape"] = "erreur"
            try:
                texte = open(os.path.join(dossier, "erreur.txt"), encoding="utf-8").read()
            except OSError:
                texte = ""
            film["erreur"], film["code"] = texte[:ERREUR_MAX], _code_erreur(texte)
        else:
            try:
                options = json.load(open(os.path.join(dossier, "options.json"), encoding="utf-8"))
            except (OSError, ValueError):
                options = {}
            print(f"Reprise du film {film_id}", flush=True)
            threading.Thread(target=_lancer, args=(film_id, photos, options, comfy_url), daemon=True).start()


class _Refus(Exception):
    """Envoi refusé : le texte est le code d'erreur rendu à la page (réponse 413)."""


def redresser_photo(path):
    """Applique l'orientation EXIF (2 à 8) : réenregistre au même nom (JPEG qualité 95 sans EXIF, PNG
    et WebP sans perte de plus). Rien si pas de rotation ; repli silencieux sur l'original si Pillow échoue."""
    tmp = path + ".tmp"
    try:
        from PIL import Image, ImageOps
        with Image.open(path) as im:
            if im.getexif().get(0x0112, 1) not in range(2, 9):
                return
            fmt = im.format
            if fmt not in ("JPEG", "PNG", "WEBP"):
                return
            droite = ImageOps.exif_transpose(im)
            droite.load()
            if fmt == "JPEG":
                droite.convert("RGB").save(tmp, "JPEG", quality=95)
            elif fmt == "WEBP":
                droite.save(tmp, "WEBP", quality=95)
            else:
                droite.save(tmp, "PNG")
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


async def _recevoir(part, path):
    """Écrit un fichier reçu par morceaux (plafond PHOTO_MAX), puis le redresse (EXIF) hors de la boucle."""
    total = 0
    with open(path, "wb") as f:
        while chunk := await part.read_chunk():
            total += len(chunk)
            if total > PHOTO_MAX:
                raise _Refus("photo_trop_lourde")
            f.write(chunk)
    await asyncio.to_thread(redresser_photo, path)


def _non_termines():
    return sum(1 for f in list(films.values()) if f.get("etape") not in ("fini", "erreur") and not f.get("annule"))


async def creer_film(request):
    if _non_termines() >= FILE_MAX:  # refusé avant de recevoir les photos
        return web.json_response({"erreur": "file_pleine"}, status=429)
    nettoyer_scans()
    film_id = uuid.uuid4().hex[:10]
    ok = False
    try:
        rep = await _recevoir_film(request, film_id)
        ok = rep.status == 200
        return rep
    except _Refus as e:
        return web.json_response({"erreur": str(e)}, status=413)
    finally:
        if not ok:  # toute sortie en erreur efface le dossier à moitié rempli
            shutil.rmtree(os.path.join(DATA, film_id), ignore_errors=True)


async def _recevoir_film(request, film_id):
    dossier = os.path.join(DATA, film_id, "photos")
    os.makedirs(dossier, exist_ok=True)
    photos, options, moi = [], {}, {}
    reader = await request.multipart()
    async for part in reader:
        if part.name == "photos" and part.filename:
            ext = os.path.splitext(part.filename)[1].lower()
            if ext not in EXTENSIONS:
                continue
            if len(photos) >= PHOTOS_MAX:
                raise _Refus("trop_de_photos")
            path = os.path.join(dossier, f"{len(photos):02d}{ext}")
            await _recevoir(part, path)
            photos.append(path)
        elif part.name in ("moi_visage", "moi_corps") and part.filename:
            ext = os.path.splitext(part.filename)[1].lower()
            if ext not in EXTENSIONS:
                continue
            os.makedirs(os.path.join(DATA, film_id, "moi"), exist_ok=True)  # hors de photos/ : pas des photos du film
            moi[part.name] = os.path.join(DATA, film_id, "moi", f"{part.name}{ext}")
            await _recevoir(part, moi[part.name])
        elif part.name == "options":
            try:
                options = json.loads(await part.text())
            except ValueError:
                return web.json_response({"erreur": "requete_invalide"}, status=400)
            if not isinstance(options, dict):
                return web.json_response({"erreur": "requete_invalide"}, status=400)
            # sécurité : les chemins moi_* ne viennent jamais du client (lecture d'un fichier du PC)
            options = {k: v for k, v in options.items() if k not in ("moi_visage", "moi_corps")}
    if not photos:
        return web.json_response({"erreur": "aucune_photo"}, status=400)
    options = {**options, **moi}
    if moi and len(photos) > 1:
        return web.json_response({"erreur": "moi_une_seule_photo"}, status=400)
    try:  # codes : moi_incomplet, moi_introuvable, recette_incompatible_moi
        clip.valider_moi(options.get("recette") or "revit", options.get("moi_visage"), options.get("moi_corps"))
        clip.valider_geste(options.get("geste") or None, options.get("moi_visage"),
                           options.get("geste_valeur"))  # geste_inconnu, geste_incompatible, geste_valeur_invalide
    except ValueError as e:
        shutil.rmtree(os.path.join(DATA, film_id), ignore_errors=True)
        return web.json_response({"erreur": str(e)}, status=400)
    with open(os.path.join(DATA, film_id, "options.json"), "w", encoding="utf-8") as f:
        json.dump(options, f, ensure_ascii=False)  # pour reprendre le film si le serveur s'arrête
    _nouveau(film_id, len(photos))
    threading.Thread(target=_lancer, args=(film_id, photos, options, request.app["comfy"]), daemon=True).start()
    return web.json_response({"id": film_id})


def _proposer(photo, dossier, comfy_url, langue, secondes, deja):
    # Pas de verrou des films : la personne attendrait toute la file. Les idées passent en tête de la file
    # ComfyUI (qui fait déjà un travail à la fois sur la carte) : juste après l'étape en cours d'un film.
    return clip.proposer(photo, dossier, comfy_url, langue, secondes, deja)


async def propositions(request):
    """POST /api/propositions, multipart : `photo` (fichier) ou `film` (identifiant d'un film
    existant), `langue`, `secondes`, `deja` (titres déjà proposés, JSON). Rend {"id", "propositions"}
    ou {"erreur": code}. Une photo envoyée est rangée dans donnees/propositions/<id>, jamais
    dans donnees/films (le serveur y reprendrait un film à fabriquer)."""
    film_id, photo, champs = None, None, {}
    nettoyer_scans()
    reader = await request.multipart()
    async for part in reader:
        if part.name == "photo" and part.filename:
            ext = os.path.splitext(part.filename)[1].lower()
            if ext not in EXTENSIONS:
                continue
            film_id = uuid.uuid4().hex[:10]
            os.makedirs(os.path.join(PROPOSITIONS, film_id), exist_ok=True)
            photo = os.path.join(PROPOSITIONS, film_id, f"photo{ext}")
            try:
                await _recevoir(part, photo)
            except _Refus as e:
                shutil.rmtree(os.path.join(PROPOSITIONS, film_id), ignore_errors=True)
                return web.json_response({"erreur": str(e)}, status=413)
        elif part.name in ("film", "langue", "secondes", "deja"):
            champs[part.name] = await part.text()
    dossier = os.path.join(PROPOSITIONS, film_id) if photo else None
    if not photo and re.fullmatch(r"[0-9a-f]{10}", champs.get("film", "")):
        dossier = os.path.join(DATA, champs["film"])
        film_id = champs["film"]
        trouvees = sorted(glob.glob(os.path.join(dossier, "photos", "*")))
        photo = trouvees[0] if trouvees else None
    if not photo:
        return web.json_response({"erreur": "aucune_photo"}, status=400)
    try:
        secondes = int(champs.get("secondes") or 7)
        deja = json.loads(champs.get("deja") or "[]")
        if not isinstance(deja, list):
            raise ValueError
    except ValueError:
        return web.json_response({"erreur": "requete_invalide"}, status=400)
    try:
        idees = await asyncio.get_running_loop().run_in_executor(
            None, _proposer, photo, dossier, request.app["comfy"], champs.get("langue") or "en", secondes, deja)
    except ValueError as e:  # réponse de l'analyse illisible : code lisible par la page, jamais une liste vide
        return web.json_response({"erreur": "propositions_illisibles", "detail": str(e)}, status=502)
    return web.json_response({"id": film_id, "propositions": idees})


async def etat_film(request):
    film = films.get(request.match_info["id"])
    return web.json_response(_vue(film)) if film else web.json_response({"erreur": "inconnu"}, status=404)


async def etat_films(request):
    """GET /api/films?ids=a,b,c : la galerie d'un téléphone. Chaque téléphone ne connaît que ses
    propres films (liste gardée dans le téléphone) ; les identifiants inconnus sont absents de la réponse."""
    ids = [i for i in request.query.get("ids", "").split(",") if i][:50]
    return web.json_response({"films": [_vue(films[i]) for i in ids if i in films]})


async def supprimer_film(request):
    """DELETE /api/films/<id> : efface le film et ses photos. Un film en attente est retiré tout de suite ;
    un film en fabrication est marqué annulé et s'arrête à la prochaine étape (réponse {"ok", "annule": true})."""
    film_id = request.match_info["id"]
    film = films.get(film_id)
    if not film or not re.fullmatch(r"[0-9a-f]{10}", film_id):
        return web.json_response({"erreur": "inconnu"}, status=404)
    if film["etape"] not in ("fini", "erreur"):
        retire = _annuler_film(film_id, film)
        return web.json_response({"ok": True, "annule": True, "retire": retire})
    films.pop(film_id, None)
    shutil.rmtree(os.path.join(DATA, film_id), ignore_errors=True)
    return web.json_response({"ok": True})


async def forcer_film(request):
    """POST /api/films/<id>/forcer : relance un film arrêté par "pas_enfant" en sautant la vérification
    (l'analyse déjà faite est réutilisée). Erreurs : 404 inconnu, 409 film pas dans cet état."""
    film_id = request.match_info["id"]
    film = films.get(film_id)
    if not film or not re.fullmatch(r"[0-9a-f]{10}", film_id):
        return web.json_response({"erreur": "inconnu"}, status=404)
    if film.get("etape") != "erreur" or film.get("code") != "pas_enfant":
        return web.json_response({"erreur": "etat_incompatible"}, status=409)
    dossier = os.path.join(DATA, film_id)
    try:
        options = json.load(open(os.path.join(dossier, "options.json"), encoding="utf-8"))
    except (OSError, ValueError):
        return web.json_response({"erreur": "inconnu"}, status=404)
    options["forcer_enfant"] = True
    with open(os.path.join(dossier, "options.json"), "w", encoding="utf-8") as f:
        json.dump(options, f, ensure_ascii=False)  # la reprise après un arrêt du serveur garde le choix
    try:
        os.remove(os.path.join(dossier, "erreur.txt"))
    except OSError:
        pass
    photos = sorted(glob.glob(os.path.join(dossier, "photos", "*")))
    _nouveau(film_id, len(photos))  # remet le film en attente (champs comme à la création)
    threading.Thread(target=_lancer, args=(film_id, photos, options, request.app["comfy"]), daemon=True).start()
    return web.json_response({"id": film_id})


async def fichier_film(request):
    film_id, nom = request.match_info["id"], os.path.basename(request.match_info["nom"])
    if not re.fullmatch(r"[0-9a-f]{10}", film_id):
        return web.Response(status=404)
    sous = "photos" if request.match_info.get("sous") == "photos" else ""
    path = os.path.join(DATA, film_id, sous, nom)
    return web.FileResponse(path) if os.path.isfile(path) else web.Response(status=404)


def nettoyer_scans():
    """Supprime les scans et les photos des propositions de plus de SCAN_DUREE_MAX (démarrage, puis périodique)."""
    for racine in (SCANS, PROPOSITIONS):
        if not os.path.isdir(racine):
            continue
        for nom in os.listdir(racine):
            dossier = os.path.join(racine, nom)
            try:
                if os.path.isdir(dossier) and time.time() - os.path.getmtime(dossier) > SCAN_DUREE_MAX:
                    shutil.rmtree(dossier, ignore_errors=True)
            except OSError:
                pass


async def _nettoyage_periodique(app):
    async def boucle():
        while True:
            await asyncio.sleep(600)
            await asyncio.to_thread(nettoyer_scans)
    tache = asyncio.create_task(boucle())
    yield
    tache.cancel()


def _dossier_scan(scan_id):
    """Dossier d'un scan existant, ou None (l'identifiant est contrôlé : pas de chemin détourné)."""
    if not re.fullmatch(r"[0-9a-f]{12}", scan_id):
        return None
    dossier = os.path.join(SCANS, scan_id)
    return dossier if os.path.isdir(dossier) else None


async def creer_scan(request):
    """Reçoit la prise de vue d'un tirage papier, trouve ses 4 coins et signale les problèmes."""
    nettoyer_scans()
    reader = await request.multipart()
    donnees = None
    async for part in reader:
        if part.name == "photo" and part.filename:
            morceaux, total = [], 0
            while chunk := await part.read_chunk():
                total += len(chunk)
                if total > PHOTO_MAX:
                    return web.json_response({"erreur": "photo_trop_lourde"}, status=413)
                morceaux.append(bytes(chunk))
            donnees = b"".join(morceaux)
    if not donnees:
        return web.json_response({"erreur": "scan_aucune_photo"}, status=400)
    try:
        image = scan.ouvrir(donnees)
    except scan.ImageIllisible:
        return web.json_response({"erreur": "scan_illisible"}, status=415)
    scan_id = uuid.uuid4().hex[:12]
    dossier = os.path.join(SCANS, scan_id)
    os.makedirs(dossier)
    ecrit = scan.enregistrer_jpeg(image, os.path.join(dossier, "original.jpg"))
    if not ecrit:
        shutil.rmtree(dossier, ignore_errors=True)
        return web.json_response({"erreur": "scan_illisible"}, status=415)
    coins, alertes = scan.detecter(image)
    h, w = image.shape[:2]
    return web.json_response({"id": scan_id, "coins": coins, "alertes": alertes, "largeur": w, "hauteur": h})


async def image_scan(request):
    dossier = _dossier_scan(request.match_info["id"])
    path = os.path.join(dossier, "original.jpg") if dossier else None
    return web.FileResponse(path) if path and os.path.isfile(path) else web.Response(status=404)


async def redresser_scan(request):
    """Renvoie le tirage redressé (JPEG) puis supprime le scan."""
    dossier = _dossier_scan(request.match_info["id"])
    if not dossier:
        return web.json_response({"erreur": "scan_inconnu"}, status=404)
    try:
        coins = (await request.json())["coins"]
        if len(coins) != 4 or any(len(c) != 2 for c in coins):
            raise ValueError
        image = scan.ouvrir(open(os.path.join(dossier, "original.jpg"), "rb").read())
        jpeg = scan.redresser(image, [[float(x), float(y)] for x, y in coins])
    except (ValueError, KeyError, TypeError, OSError, scan.ImageIllisible):
        return web.json_response({"erreur": "scan_echec"}, status=400)
    shutil.rmtree(dossier, ignore_errors=True)
    return web.Response(body=jpeg, content_type="image/jpeg")


def adresse_locale():
    """IP locale du PC sur le réseau (socket UDP vers une adresse privée, rien n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


_cles = {}


def cle_secrete():
    """Code secret du Wi-Fi : créé une fois, gardé dans cle.txt à côté du dossier des films."""
    chemin = os.path.join(os.path.dirname(DATA), "cle.txt")
    if chemin in _cles:
        return _cles[chemin]
    try:
        with open(chemin, encoding="utf-8") as f:
            cle = f.read().strip()
    except OSError:
        cle = ""
    if not cle:
        cle = secrets.token_urlsafe(12)
        os.makedirs(os.path.dirname(chemin), exist_ok=True)
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(cle)
    _cles[chemin] = cle
    return cle


def adresse_qr(port):
    """Adresse du QR (téléphone) : IP du Wi-Fi + code secret ; None si seule 127.x est trouvée."""
    ip = adresse_locale()
    if ip.startswith("127."):
        return None
    return f"http://{ip}:{port}/?cle={cle_secrete()}"


_LOCAUX = ("127.0.0.1", "::1", "::ffff:127.0.0.1")
_RESERVES = ("/api/info", "/api/moteur/installer", "/api/moteur/ollama/telecharger",
             "/api/moteur/utiliser", "/api/moteur/desinstaller")


def _est_local(request):
    """Requête venue de l'ordinateur lui-même (127.x, ::1, ou sa propre adresse Wi-Fi)."""
    r = request.remote or ""
    if r in _LOCAUX:
        return True
    sock = request.transport.get_extra_info("sockname") if request.transport else None
    return bool(sock) and r == sock[0]


def _hote_valide(host):
    """Vrai si l'en-tête Host est une adresse IP ou localhost (port facultatif) : contre le DNS rebinding."""
    if host.startswith("["):
        nom = host[1:].split("]", 1)[0]
    else:
        nom = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    if nom.lower() == "localhost":
        return True
    try:
        ipaddress.ip_address(nom)
        return True
    except ValueError:
        return False


def _cle_recue(request):
    for c in (request.cookies.get("stillive_cle"), request.query.get("cle"), request.headers.get("X-Stillive-Cle")):
        if c and secrets.compare_digest(c.encode(), cle_secrete().encode()):
            return True
    return False


@web.middleware
async def garde(request, handler):
    """Sécurité du Wi-Fi : Host et Origin contrôlés, code secret pour toute donnée, réglages réservés au PC."""
    host = request.headers.get("Host", "")
    if not _hote_valide(host):
        return web.Response(status=403, text="hote_refuse")
    if request.method not in ("GET", "HEAD"):
        origine = request.headers.get("Origin")
        if origine is not None and (origine == "null" or urllib.parse.urlsplit(origine).netloc.lower() != host.lower()):
            return web.json_response({"erreur": "origine_refusee"}, status=403)
    local = _est_local(request)
    chemin = request.path.lower()
    if not local:
        if chemin in _RESERVES or (chemin.startswith("/web/") and "ordinateur" in chemin):
            if chemin.startswith("/web/"):
                return web.Response(status=403, text="reserve_ordinateur")
            return web.json_response({"erreur": "reserve_ordinateur"}, status=403)
        if (chemin.startswith("/api/") or chemin.startswith("/films/")) and not _cle_recue(request):
            return web.json_response({"erreur": "cle_requise"}, status=401)
    rep = await handler(request)
    if chemin == "/" or chemin.startswith("/web/"):  # après une mise à jour, jamais d'ancienne page gardée
        rep.headers["Cache-Control"] = "no-cache"
    if request.query.get("cle") and _cle_recue(request):  # le code du QR devient un cookie pour la suite
        rep.set_cookie("stillive_cle", cle_secrete(), max_age=10 * 365 * 24 * 3600, path="/",
                       httponly=True, samesite="Strict")
    return rep


async def info(request):
    port = request.url.port or 8765
    rep = {"adresse": adresse_qr(port), "port": port}
    if request.app.get("comfy"):
        rep["comfy_url"] = request.app["comfy"]
    return web.json_response(rep)


async def partage(request):
    """Adresse du QR pour le bouton « Partager » : ouverte au téléphone déjà connecté (code secret exigé par la garde)."""
    return web.json_response({"adresse": adresse_qr(request.url.port or 8765)})


async def _corps_json(request):
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except ValueError:
        return {}


async def moteur_etat(request):
    return web.json_response(await asyncio.to_thread(moteur.etat))


async def moteur_installer(request):
    d = await _corps_json(request)
    return web.json_response(await asyncio.to_thread(
        moteur.lancer_installation, d.get("quant") or None, d.get("h3", True) is not False,
        bool(d.get("ref2va", False)), d.get("llm", True) is not False, bool(d.get("forcer", False))))


async def moteur_ollama(request):
    d = await _corps_json(request)
    return web.json_response(await asyncio.to_thread(moteur.telecharger_ollama, d.get("url")))


async def moteur_utiliser(request):
    d = await _corps_json(request)
    return web.json_response(await asyncio.to_thread(
        moteur.utiliser, d.get("programme") or "", d.get("url") or "", d.get("modele") or ""))


async def moteur_apercu_desinstaller(request):
    return web.json_response(await asyncio.to_thread(moteur.apercu_desinstallation))


async def moteur_desinstaller(request):
    """Corps {"categories": ["llama", "llm", "h3", "donnees", "reglages"]}. Rend {"ok", "retire", "echecs",
    "libere_go"} ou {"ok": false, "erreur": code} (categorie_inconnue, rien_a_supprimer, installation_en_cours,
    film_en_cours)."""
    d = await _corps_json(request)
    cats = d.get("categories")
    if "donnees" in (cats if isinstance(cats, list) else []):
        for fid, f in list(films.items()):  # un film seulement en attente ne bloque plus : il est annulé
            if f.get("etape") == "attente":
                _annuler_film(fid, f)
        if any(f.get("etape") not in ("fini", "erreur") for f in list(films.values())):
            return web.json_response({"ok": False, "erreur": "film_en_cours"}, status=409)
    rep = await asyncio.to_thread(moteur.desinstaller, cats)
    if any(r["categorie"] == "donnees" for r in rep.get("retire", [])):
        films.clear()  # les films effacés ne doivent plus s'afficher
    return web.json_response(rep, status=200 if "retire" in rep else 400)


async def accueil(request):
    return web.FileResponse(os.path.join(WEB, "index.html"))


def app(comfy_url):
    a = web.Application(client_max_size=500 * 1024 * 1024, middlewares=[garde])
    a["comfy"] = comfy_url
    nettoyer_scans()
    a.cleanup_ctx.append(_nettoyage_periodique)
    a.router.add_get("/", accueil)
    a.router.add_get("/api/info", info)
    a.router.add_get("/api/partage", partage)
    a.router.add_post("/api/films", creer_film)
    a.router.add_post("/api/propositions", propositions)
    a.router.add_get("/api/films", etat_films)
    a.router.add_get("/api/films/{id}", etat_film)
    a.router.add_delete("/api/films/{id}", supprimer_film)
    a.router.add_post("/api/films/{id}/forcer", forcer_film)
    a.router.add_get("/films/{id}/{nom}", fichier_film)
    a.router.add_get("/films/{id}/{sous:photos}/{nom}", fichier_film)
    a.router.add_get("/api/moteur", moteur_etat)
    a.router.add_post("/api/moteur/installer", moteur_installer)
    a.router.add_post("/api/moteur/ollama/telecharger", moteur_ollama)
    a.router.add_post("/api/moteur/utiliser", moteur_utiliser)
    a.router.add_get("/api/moteur/desinstaller", moteur_apercu_desinstaller)
    a.router.add_post("/api/moteur/desinstaller", moteur_desinstaller)
    a.router.add_post("/api/scan", creer_scan)
    a.router.add_get("/api/scan/{id}/image", image_scan)
    a.router.add_post("/api/scan/{id}/redresser", redresser_scan)
    a.router.add_static("/web", WEB)
    return a


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--hote", default="0.0.0.0", help="0.0.0.0 : accessible depuis le téléphone sur le Wi-Fi")
    ap.add_argument("--comfy", default=os.environ.get("STILLIVE_COMFY", "http://127.0.0.1:8188"))
    a = ap.parse_args()
    reprendre(a.comfy)
    web.run_app(app(a.comfy), host=a.hote, port=a.port)
