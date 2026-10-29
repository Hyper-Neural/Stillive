"""Chaîne de fabrication d'un film : photos -> analyse -> retouche -> musique ->
plans H3 -> montage. Utilisée par la ligne de commande et par
le serveur de la page web, qui suit l'avancement grâce à la fonction `rapport`.
Relancée sur le même dossier de sortie, elle reprend un film interrompu là où il s'était
arrêté (`analyse.json`, retouches, `musique.mp3`, plans et `travaux.json` réutilisés).

Événements envoyés à `rapport(nom, données)` :
- "etape"   : {"etape": "analyse" | "retouche" | "musique" | "plans" | "montage", "restant": secondes}
- "analyse" : {"photos": [{"anecdote", "annee", "occasion"}...]}
- "plan"    : {"index", "video", "fait", "total", "restant"}
- "fini"    : {"horizontal", "vertical", "temps"}
"""
import json
import os
import random
import time

from PIL import Image

from . import analyse, h3, montage, musique, retouche
from .comfy import Comfy
from .comfy import capacites_ou_none as _capacites

# Durées mesurées sur une RTX 5080 (24/10), pour estimer le temps restant.
# Plan H3 (recette turbo, 26/10) : environ 120 s pour 7 s de vidéo, proportionnel à la durée.
DUREES = {"analyse": 35, "retouche": 15, "musique": 50, "plan": 120, "montage": 25}


def _plan(secondes=7):
    return round(DUREES["plan"] * secondes / 7)


def _estimation(n, n_retouche, depuis, secondes=7):
    order = ["analyse", "retouche", "musique", "plans", "montage"]
    cost = {"analyse": DUREES["analyse"] * n, "retouche": DUREES["retouche"] * n_retouche,
            "musique": DUREES["musique"], "plans": _plan(secondes) * n, "montage": DUREES["montage"]}
    return sum(cost[s] for s in order[order.index(depuis):])


def fabriquer(photos, sortie, comfy_url, secondes=7, avec_retouche=True, couleurs=None, ambiance="auto", titre=None, legendes=None,
              signature=None, langue="fr", rapport=lambda nom, donnees: None):
    """couleurs : une case par photo, False pour garder une photo noir et blanc telle
    quelle (elle est quand même réparée si besoin) ; None = mise en couleur automatique."""
    os.makedirs(sortie, exist_ok=True)
    comfy = Comfy(comfy_url)
    times = {}
    n = len(photos)
    # Reprise : tout ce qui est déjà sur disque (analyse, retouches, musique, plans) est
    # réutilisé, et les travaux déjà envoyés à ComfyUI sont attendus au lieu d'être refaits.
    jobs_file = os.path.join(sortie, "travaux.json")
    known = json.load(open(jobs_file, encoding="utf-8")) if os.path.isfile(jobs_file) else {}

    def step(label, start):
        times[label] = round(time.time() - start, 1)

    def job(key, make_graph):
        pid = known.get(key)
        if pid and comfy.statut(pid) in ("success", "en_cours"):
            return pid
        known[key] = comfy.queue(make_graph())
        with open(jobs_file, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
        return known[key]

    # 1. Analyse
    rapport("etape", {"etape": "analyse", "restant": _estimation(n, n, "analyse", secondes)})
    t = time.time()
    names = [comfy.upload_image(p) for p in photos]
    results = _load(sortie, n)
    if results is None:
        entry = comfy.wait(job("analyse", lambda: analyse.graph(names, langue=langue)))
        results = analyse.parse(entry, n)
        for i, r in enumerate(results):
            if "error" in r:
                raise RuntimeError(f"Analyse illisible pour {os.path.basename(photos[i])}")
            r["photo"] = photos[i]
            # Qwen confond le sépia avec la couleur : la mesure de l'image tranche aussi.
            r["black_and_white"] = bool(r.get("black_and_white")) or retouche.is_black_and_white(photos[i])
        _save(results, sortie)
    step("analyse", t)
    rapport("analyse", {"photos": [{"anecdote": r.get("fun_fact", ""), "langues": analyse.anecdotes(r), "annee": r.get("year_guess"),
                                    "occasion": r.get("occasion", "")} for r in results]})

    # 2. Restauration et mise en couleur des photos anciennes, abîmées ou en noir et blanc.
    t = time.time()
    restored = [None] * n
    todo = []
    for i, r in enumerate(results):
        if r.get("restored") and os.path.isfile(r["restored"]):
            restored[i] = r["restored"]  # retouche faite avant l'interruption
            names[i] = comfy.upload_image(restored[i])
        elif avec_retouche:
            old = r.get("decade", "2000s") < "1990s"
            if r["black_and_white"] or r.get("damaged") or old:
                todo.append(i)
    rapport("etape", {"etape": "retouche", "restant": _estimation(n, len(todo), "retouche", secondes)})
    jobs = []
    colorize = [r["black_and_white"] and (couleurs[i] if couleurs else True) for i, r in enumerate(results)]
    for i in todo:
        r = results[i]
        old = r.get("decade", "2000s") < "1990s"
        text = retouche.prompt(colorize=colorize[i], damaged=bool(r.get("damaged")) or old,
                               border=bool(r.get("is_scan_with_border")))
        jobs.append((i, job(f"retouche{i:02d}", lambda: retouche.graph(
            names[i], text, seed=random.randint(0, 2**32 - 1), prefix=f"stillive/retouche{i:02d}"))))
    for i, pid in jobs:
        e = comfy.wait(pid)
        f = comfy.download_outputs(e, sortie)[0]
        restored[i] = os.path.join(sortie, f"retouche{i:02d}.png")
        os.replace(f, restored[i])
        r = results[i]
        r["restored"] = restored[i]
        r["black_and_white"] = r["black_and_white"] and not colorize[i]
        r["is_scan_with_border"] = False
        names[i] = comfy.upload_image(restored[i])
        _save(results, sortie)
    step("retouche", t)
    for i, r in enumerate(results):
        r.setdefault("h3_prompt", analyse.h3_prompt(r, i))
    _save(results, sortie)

    # 3. Musique AVANT les plans : leur durée est connue d'avance, et Music 3 ne force
    # plus le déchargement de H3 (208 s pour 26 s de musique quand elle passait après).
    rapport("etape", {"etape": "musique", "restant": _estimation(n, 0, "musique", secondes)})
    t = time.time()
    moods = [r.get("music_mood", "") for r in results]
    if ambiance in musique.AMBIANCES:
        moods = [musique.AMBIANCES[ambiance]] * n
    bpm = musique.tempo_for(moods)
    clip_seconds = h3.frame_count(secondes) / h3.FPS
    expected = montage.on_tempo([(1.6 if restored[i] else 0.7) + clip_seconds for i in range(n)], bpm)
    music_seconds = round(sum(expected) - montage.FADE * (n - 1) + montage.END_CARD + 3)
    music = os.path.join(sortie, "musique.mp3")
    music_job = None if os.path.isfile(music) else job("musique", lambda: musique.graph(
        moods, music_seconds, seed=random.randint(0, 2**31 - 1))[0])

    # 4. Tous les plans en file d'un coup : H3 n'est chargé qu'une fois.
    jobs = []
    for i, (photo, name, r) in enumerate(zip(photos, names, results)):
        if os.path.isfile(os.path.join(sortie, f"sequence{i:02d}.mp4")) or os.path.isfile(os.path.join(sortie, f"plan{i:02d}.mp4")):
            jobs.append(None)  # plan déjà fabriqué avant l'interruption
            continue
        with Image.open(restored[i] or photo) as im:
            w, hgt = im.size
        jobs.append(job(f"plan{i:02d}", lambda: h3.graphe_verifie(h3.build(name, r["h3_prompt"], w, hgt, secondes,
                                                          caps=_capacites(comfy), turbo=True, seed=random.randint(0, 2**32 - 1), prefix=f"stillive/plan{i:02d}"))))
    if music_job:
        e = comfy.wait(music_job)
        os.replace([x for x in comfy.download_outputs(e, sortie) if x.endswith(".mp3")][0], music)
    step("musique", t)

    left = sum(1 for pid in jobs if pid)
    rapport("etape", {"etape": "plans", "restant": _plan(secondes) * left + DUREES["montage"]})
    t = time.time()
    clips = []
    for i, pid in enumerate(jobs):
        target = os.path.join(sortie, f"plan{i:02d}.mp4")
        sequence = os.path.join(sortie, f"sequence{i:02d}.mp4")
        if pid:
            e = comfy.wait(pid)
            os.replace([x for x in comfy.download_outputs(e, sortie) if x.endswith(".mp4")][0], target)
            left -= 1
        if not os.path.isfile(sequence):
            # Écrit à côté puis renommé : une séquence interrompue en plein montage n'est jamais prise pour finie.
            montage.avant_apres(photos[i], target, sequence + ".tmp.mp4", restored[i])
            os.replace(sequence + ".tmp.mp4", sequence)
            _save(results, sortie)
        clips.append(sequence)
        rapport("plan", {"index": i, "video": sequence, "fait": i + 1, "total": n,
                         "restant": _plan(secondes) * left + DUREES["montage"]})
    step("plans", t)

    # 5. Montage, vertical et horizontal.
    rapport("etape", {"etape": "montage", "restant": DUREES["montage"]})
    t = time.time()
    films = {}
    for label, (w, hgt) in montage.tailles_film(clips[0]).items():  # taille du premier plan, sans agrandissement
        films[label] = os.path.join(sortie, f"film_{label}.mp4")
        montage.film(clips, music, films[label], w, hgt, bpm=bpm, title=titre, captions=legendes, signature=signature)
    step("montage", t)
    json.dump(times, open(os.path.join(sortie, "temps.json"), "w"), indent=1)
    rapport("fini", {**films, "temps": times})
    return films


def _save(results, sortie):
    path = os.path.join(sortie, "analyse.json")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    os.replace(path + ".tmp", path)


def _load(sortie, n):
    """Analyse faite avant une interruption, ou None s'il faut la (re)faire."""
    path = os.path.join(sortie, "analyse.json")
    try:
        with open(path, encoding="utf-8") as f:
            results = json.load(f)
    except (OSError, ValueError):
        return None
    return results if len(results) == n and not any("error" in r for r in results) else None
