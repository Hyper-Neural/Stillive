"""Analyse des photos et consignes H3, en ligne de commande.

    python outils/analyser.py photo1.jpg photo2.jpg ... [--sortie analyse.json]
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from stillive import analyse  # noqa: E402
from stillive.comfy import Comfy  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("photos", nargs="+")
ap.add_argument("--comfy", default=os.environ.get("STILLIVE_COMFY", "http://127.0.0.1:8188"))
ap.add_argument("--sortie", default="analyse.json")
a = ap.parse_args()

comfy = Comfy(a.comfy)
names = [comfy.upload_image(p) for p in a.photos]
t0 = time.time()
entry = comfy.wait(comfy.queue(analyse.graph(names)))
print(f"analyse : {Comfy.duration(entry)} s d'exécution pour {len(names)} photos, {time.time() - t0:.0f} s attente comprise")
results = []
for i, (photo, res) in enumerate(zip(a.photos, analyse.parse(entry, len(names)))):
    res["photo"] = photo
    if "error" not in res:
        res["h3_prompt"] = analyse.h3_prompt(res, i)
    results.append(res)
with open(a.sortie, "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print(f"résultat : {a.sortie}")
