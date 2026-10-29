"""Génère un plan H3 à partir d'une photo.

    python outils/plan_h3.py photo.jpg "consigne" [--secondes 7] [--sortie dossier] [--verifier]

--verifier contrôle seulement que ComfyUI connaît tous les nœuds, sans rien lancer.
"""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from PIL import Image  # noqa: E402

from stillive import h3  # noqa: E402
from stillive.comfy import Comfy  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("photo")
ap.add_argument("consigne")
ap.add_argument("--secondes", type=float, default=7)
ap.add_argument("--etapes", type=int, default=20)
ap.add_argument("--comfy", default=os.environ.get("STILLIVE_COMFY", "http://127.0.0.1:8188"))
ap.add_argument("--sortie", default="sorties")
ap.add_argument("--verifier", action="store_true")
ap.add_argument("--graine", type=int, help="tirage fixe, pour comparer deux réglages")
ap.add_argument("--sans-acceleration", action="store_true")
a = ap.parse_args()

comfy = Comfy(a.comfy)
with Image.open(a.photo) as im:
    w, h = im.size
graph, info = h3.build("verification.png", a.consigne, w, h, a.secondes, steps=a.etapes)
print(f"vidéo {info['width']}x{info['height']}, phase 1 {info['phase1']}, {info['frames']} images")
missing = comfy.missing_nodes(h3.NODE_TYPES)
if missing:
    sys.exit(f"Nœuds absents de ComfyUI : {', '.join(missing)}")
if a.verifier:
    print("Tous les nœuds sont présents.")
    sys.exit(0)

name = comfy.upload_image(a.photo)
seed = a.graine if a.graine is not None else random.randint(0, 2**32 - 1)
graph, info = h3.build(name, a.consigne, w, h, a.secondes, steps=a.etapes, seed=seed, accel=not a.sans_acceleration)
t0 = time.time()
pid = comfy.queue(graph)
print(f"en file : {pid}")
entry = comfy.wait(pid)
print(f"terminé : {Comfy.duration(entry)} s d'exécution, {time.time() - t0:.0f} s attente comprise")
for f in comfy.download_outputs(entry, a.sortie):
    print(f"fichier : {f}")
