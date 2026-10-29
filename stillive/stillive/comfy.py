"""Petit client pour piloter un ComfyUI par son interface HTTP (bibliothèque standard seule)."""
import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class ComfyError(RuntimeError):
    pass


# Nœuds de paquets tiers que le graphe H3 utilise s'ils existent (sinon il s'en passe).
NOEUDS_TIERS = ("H3MemoryOptimization", "H3SparseAttention", "SpectrumApplyMiniMaxH3",
                "MinimaxH3LatentUpscaler3D")
# Nœud chargeur et champ dont on lit la liste de fichiers disponibles.
_CHARGEURS = {"unet": ("UNETLoader", "unet_name"), "vae": ("VAELoader", "vae_name"),
              "loras": ("LoraLoaderModelOnly", "lora_name"), "text_encoders": ("CLIPLoader", "clip_name"),
              "upscalers": ("MinimaxH3LatentUpscaler3D", "model_name")}


def _options(info, noeud, champ):
    """Liste des choix d'un champ « combo » (ancien format : liste ; nouveau : "COMBO" + options)."""
    try:
        entree = info[noeud]["input"]
        spec = {**entree.get("required", {}), **entree.get("optional", {})}[champ]
        if isinstance(spec[0], list):
            return list(spec[0])
        if spec[0] == "COMBO":
            return list(spec[1].get("options", []))
    except (KeyError, IndexError, TypeError, AttributeError):
        pass
    return []


def capacites_depuis(info):
    """Capacités d'une machine d'après le contenu de /object_info : {"noeuds": {nom: bool},
    "unet": [...], "vae": [...], "loras": [...], "text_encoders": [...], "upscalers": [...]}.
    Fonction pure (testable sans ComfyUI)."""
    caps = {"noeuds": {n: n in info for n in NOEUDS_TIERS}}
    for cle, (noeud, champ) in _CHARGEURS.items():
        caps[cle] = _options(info, noeud, champ)
    return caps


def capacites_ou_none(comfy):
    """Capacités de la machine pour h3.build ; None (tout présent) si ComfyUI ne les donne pas
    (ComfyUI simulé des tests, lecture impossible)."""
    try:
        return comfy.capacites()
    except Exception:
        return None


class Comfy:
    def __init__(self, url="http://127.0.0.1:8188"):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=30) as r:
            return json.load(r)

    def _post(self, path, data, headers):
        req = urllib.request.Request(self.url + path, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise ComfyError(f"{path} : {e.code} {e.read().decode('utf-8', 'replace')[:2000]}") from e

    def attendre_pret(self, delai=180, poll=3.0):
        """Attend que ComfyUI réponde (/system_stats), en tolérant les connexions refusées ;
        ComfyError si rien après `delai` secondes."""
        debut = time.time()
        while True:
            try:
                self._get("/system_stats")
                return
            except OSError as e:
                if time.time() - debut >= delai:
                    raise ComfyError(f"ComfyUI ne répond pas ({self.url}) : {e}") from e
                time.sleep(poll)

    def capacites(self, rafraichir=False):
        """Capacités de ce ComfyUI (voir `capacites_depuis`), lues une fois puis gardées en cache."""
        if rafraichir or getattr(self, "_capacites", None) is None:
            self._capacites = capacites_depuis(self._get("/object_info"))
        return self._capacites

    def missing_nodes(self, class_types):
        known = self._get("/object_info").keys()
        return sorted(set(class_types) - set(known))

    def upload_image(self, path):
        """Envoie une image dans le dossier input de ComfyUI, renvoie le nom à donner à LoadImage."""
        name = f"stillive_{uuid.uuid4().hex[:8]}_{os.path.basename(path)}"
        boundary = uuid.uuid4().hex
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            content = f.read()
        body = (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode() + content + (
            f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\ntrue"
            f"\r\n--{boundary}--\r\n"
        ).encode()
        res = self._post("/upload/image", body, {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        return res["name"] if not res.get("subfolder") else f"{res['subfolder']}/{res['name']}"

    def queue(self, graph, devant=False):
        """Met un graphe en file. devant=True : en tête de la file ComfyUI (option « front » de /prompt),
        il passe juste après le travail en cours, avant ceux qui attendent."""
        corps = {"prompt": graph, "client_id": self.client_id}
        if devant:
            corps["front"] = True
        data = json.dumps(corps).encode()
        res = self._post("/prompt", data, {"Content-Type": "application/json"})
        if res.get("node_errors"):
            raise ComfyError(f"graphe refusé : {res['node_errors']}")
        return res["prompt_id"]

    def wait(self, prompt_id, poll=3.0, timeout=3600):
        """Attend la fin du travail, renvoie l'entrée d'historique (statut, sorties, horodatages)."""
        return self._attendre(prompt_id, poll, timeout)

    def _attendre(self, prompt_id, poll, timeout):
        start = time.time()
        dernier_controle, inconnu = start, 0
        while time.time() - start < timeout:
            if time.time() - dernier_controle >= 30:
                # Travail disparu (ComfyUI relancé : ni historique ni file) : 3 relevés de suite, puis erreur
                # au lieu d'attendre l'heure entière.
                dernier_controle = time.time()
                inconnu = inconnu + 1 if self.statut(prompt_id) is None else 0
                if inconnu >= 3:
                    raise ComfyError(f"travail {prompt_id} introuvable dans ComfyUI (relancé ?)")
            try:
                hist = self._get(f"/history/{prompt_id}")
            except urllib.error.HTTPError:
                raise
            except OSError:  # ComfyUI coupé un instant (redémarrage) : on attend, sans rejouer le POST
                time.sleep(poll)
                continue
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("completed") or status.get("status_str") in ("success", "error"):
                    if status.get("status_str") == "error":
                        errs = [m[1] for m in status.get("messages", []) if m[0] == "execution_error"]
                        detail = "; ".join(f"{e.get('node_type')} : {e.get('exception_message', '')[:500]}" for e in errs)
                        raise ComfyError(f"échec : {detail or 'erreur inconnue'}")
                    return entry
            time.sleep(poll)
        raise ComfyError(f"délai dépassé ({timeout} s) pour {prompt_id}")

    def statut(self, prompt_id):
        """Où en est un travail déjà envoyé : "success", "error", "en_cours" (en file ou en
        calcul) ou None si ComfyUI ne le connaît plus (redémarré, historique vidé)."""
        try:
            hist = self._get(f"/history/{prompt_id}")
            if prompt_id in hist:
                return "error" if hist[prompt_id].get("status", {}).get("status_str") == "error" else "success"
            q = self._get("/queue")
        except OSError:
            return None
        waiting = q.get("queue_running", []) + q.get("queue_pending", [])
        return "en_cours" if any(len(j) > 1 and j[1] == prompt_id for j in waiting) else None

    @staticmethod
    def duration(entry):
        """Durée d'exécution mesurée par ComfyUI, en secondes."""
        msgs = entry.get("status", {}).get("messages", [])
        t = {m[0]: m[1]["timestamp"] for m in msgs}
        if "execution_start" in t and "execution_success" in t:
            return (t["execution_success"] - t["execution_start"]) / 1000
        return None

    def download_outputs(self, entry, dest_dir):
        """Télécharge tous les fichiers produits (vidéos, images, sons) dans dest_dir."""
        os.makedirs(dest_dir, exist_ok=True)
        saved = []
        for node_out in entry.get("outputs", {}).values():
            for items in node_out.values():
                if not isinstance(items, list):
                    continue
                for it in items:
                    if not isinstance(it, dict) or "filename" not in it:
                        continue
                    q = urllib.parse.urlencode({k: it.get(k, "") for k in ("filename", "subfolder", "type")})
                    target = os.path.join(dest_dir, it["filename"])
                    # Flux avec délai : un ComfyUI figé ne bloque plus le film indéfiniment.
                    with urllib.request.urlopen(f"{self.url}/view?{q}", timeout=60) as r, open(target, "wb") as out:
                        while True:
                            bloc = r.read(1 << 20)
                            if not bloc:
                                break
                            out.write(bloc)
                    saved.append(target)
        return saved
