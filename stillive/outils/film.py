"""Chaîne complète en ligne de commande : photos -> analyse -> plans H3 -> musique -> film.

    python outils/film.py photo1.jpg photo2.jpg ... [--sortie dossier] [--secondes 7]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from stillive import chaine  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("photos", nargs="+")
ap.add_argument("--comfy", default=os.environ.get("STILLIVE_COMFY", "http://127.0.0.1:8188"))
ap.add_argument("--sortie", default="sorties/film")
ap.add_argument("--secondes", type=float, default=7)
ap.add_argument("--sans-retouche", action="store_true", help="ni restauration ni mise en couleur")
ap.add_argument("--titre", help="titre d'ouverture, par exemple « La famille Martin »")
ap.add_argument("--legendes", help="une légende par photo, séparées par « ; » (vide pour aucune)")
ap.add_argument("--sans-signature", action="store_true")
a = ap.parse_args()
start = time.time()


def rapport(nom, donnees):
    print(f"[{time.time() - start:6.0f} s] {nom} : {donnees}", flush=True)


chaine.fabriquer(a.photos, a.sortie, a.comfy, secondes=a.secondes, avec_retouche=not a.sans_retouche,
                 titre=a.titre, legendes=a.legendes.split(";") if a.legendes else None,
                 signature=None if a.sans_signature else "Fait avec Stillive", rapport=rapport)
