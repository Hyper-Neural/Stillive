"""Une photo -> un clip en ligne de commande (musique faite par H3, aucune voix).

    python outils/clip.py photo.jpg [--sortie dossier] [--secondes 7]   (4 à 15)
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from stillive import clip  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("photo")
ap.add_argument("--comfy", default=os.environ.get("STILLIVE_COMFY", "http://127.0.0.1:8188"))
ap.add_argument("--sortie", default="sorties/clip")
ap.add_argument("--secondes", type=int, default=7, help="durée du clip, de 4 à 15")
ap.add_argument("--scene", help="ce qui doit se passer dans la photo, en langue libre")
ap.add_argument("--recette", default="revit", help="revit (défaut), polaroid ou temps")
ap.add_argument("--mouvement", default="auto", help="auto ou push_in, pull_out, orbit, truck_right, rise, bullet_time")
ap.add_argument("--look", default="aucun", help="aucun, auto, super8, vhs ou camescope")
ap.add_argument("--proposer", action="store_true", help="affiche 3 idées d'action pour la photo et s'arrête")
ap.add_argument("--idee", type=int, help="fabrique le clip avec l'idée N (1 à 3) de propositions.json")
ap.add_argument("--langue", default="fr", help="langue des titres et résumés des idées (fr, en...)")
ap.add_argument("--moi-visage", help="« J'entre dans la photo » : photo de la personne (une seule suffit)")
ap.add_argument("--moi-corps", help="« J'entre dans la photo » : photo de la personne en pied, facultative (avec --moi-visage)")
ap.add_argument("--sans-retouche", action="store_true", help="ni restauration ni mise en couleur")
ap.add_argument("--sans-signature", action="store_true")
a = ap.parse_args()
start = time.time()


def rapport(nom, donnees):
    print(f"[{time.time() - start:6.0f} s] {nom} : {donnees}", flush=True)


souhait = a.scene
try:
    clip.valider_moi(a.recette, a.moi_visage, a.moi_corps)
except ValueError as e:
    sys.exit(f"Erreur : {e}")
if a.proposer:
    for n, i in enumerate(clip.proposer(a.photo, a.sortie, a.comfy, a.langue, a.secondes), 1):
        print(f"{n}. [{i['ton']}] {i['titre']}\n   {i['resume']}\n   ({i['souhait']})")
    print(f"Choisir avec : --idee N --sortie {a.sortie}")
    sys.exit(0)
if a.idee:
    idees = clip.lire_propositions(a.sortie)
    if not idees or not 1 <= a.idee <= len(idees):
        sys.exit(f"Idée {a.idee} introuvable : lancer d'abord --proposer avec la même --sortie.")
    souhait = idees[a.idee - 1]["souhait"]
res = clip.fabriquer_clip(a.photo, a.sortie, a.comfy, secondes=a.secondes, avec_retouche=not a.sans_retouche, souhait=souhait,
                          recette=a.recette, mouvement=a.mouvement, look=a.look,
                          moi_visage=a.moi_visage, moi_corps=a.moi_corps,
                          signature=None if a.sans_signature else "Fait avec Stillive", rapport=rapport)
if res["repli"]:
    print("ATTENTION : la recette « temps » demande une retouche, impossible ici : recette « revit » utilisée.")
