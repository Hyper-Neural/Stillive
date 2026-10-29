"""Moteur d'analyse de Stillive : détection des programmes déjà installés, choix du modèle selon la
carte graphique, installation de llama.cpp + Gemma 4 12B avec son MTP (ou téléchargement via Ollama), configuration.

La config est stillive/config_local.json (hors git).
"""
import concurrent.futures
import errno
import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "config_local.json")
MOTEUR = os.path.join(ROOT, "moteur")
DONNEES = os.path.join(ROOT, "donnees")  # photos, films et propositions créés par Stillive
# Modèle d'analyse par défaut : Gemma 4 12B (conclusion des tests des nuits du 26/10 : 22 à 34 s par photo avec
# le MTP et l'encodeur de vision sur la carte, sans réflexion). Dépôt vérifié sur Hugging Face le 26/10.
REPO = "unsloth/gemma-4-12b-it-GGUF"
OLLAMA_MODELE = "gemma4:12b"  # nom vérifié dans la bibliothèque Ollama le 26/10
RECOMMANDE = re.compile(r"gemma[.\-_ ]?4.*12b", re.I)
# Tête MTP (décodage spéculatif) : celle des tests du 26/10, 0,25 Go (Q4_0), dépôt voisin « qat » d'Unsloth
# (empreinte identique au fichier testé, vérifiée sur Hugging Face le 26/10). Accepte le modèle UD-Q4_K_XL.
MTP_REPO = "unsloth/gemma-4-12B-it-qat-GGUF"
MTP_FICHIER = "mtp-gemma-4-12b-it.gguf"
# Taille des fichiers .gguf en Go (relevée sur Hugging Face le 26/10). Seule UD-Q4_K_XL est validée par des
# tests (16 Go) ; les autres sont des choix estimés pour les cartes plus petites ou plus grosses ❓.
TAILLES = {"UD-IQ3_XXS": 4.64, "UD-Q3_K_XL": 6.02, "UD-Q4_K_XL": 7.37, "UD-Q5_K_XL": 8.61, "UD-Q6_K_XL": 10.69}
ANNEXES_LLM_GO = 0.45  # mmproj (0,18) + MTP (0,25), en plus du modèle
# (id, nom, port, API Ollama native ?)
PROGRAMMES = [("ollama", "Ollama", 11434, True), ("lmstudio", "LM Studio", 1234, False),
              ("jan", "Jan", 1337, False), ("koboldcpp", "KoboldCpp", 5001, False),
              ("textgen", "text-generation-webui", 5000, False), ("llamacpp", "llama-server", 8080, False)]
H3_REPO = "Comfy-Org/MiniMax-H3"
# (sous-dossier ComfyUI, nom, taille approximative en Go, option). Chemins vérifiés dans le dépôt HF le 25/10
# (à la racine, sans préfixe). Les tailles réelles sont relues sur l'API HF au lancement de l'installation.
H3_FICHIERS = [
    ("diffusion_models", "minimax_h3_fl2va_pruned_int8_convrot.safetensors", 20.97, "base"),
    ("text_encoders", "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors", 15.69, "base"),
    ("vae", "minimax_h3_video_vae_int8_convrot.safetensors", 2.81, "base"),
    ("vae", "minimax_h3_audio_vae_fp32.safetensors", 0.61, "base"),
    ("loras", "minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", 1.96, "base"),
    ("diffusion_models", "minimax_h3_ref2va_pruned_int8_convrot.safetensors", 20.97, "ref2va"),
    ("loras", "minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", 1.96, "ref2va"),
]
# Seuils de compatibilité : HYPOTHÈSES non vérifiées (❓), choisies par prudence, pas mesurées.
VRAM_MIN_GO, RAM_MIN_GO, MARGE_DISQUE_GO = 12, 32, 5
_AUTO = object()
_inst = {"etat": "aucune", "etape": "", "fait": 0, "total": 0, "message": ""}
_verrou = threading.Lock()
_verrou_cfg = threading.RLock()  # protège chaque séquence lire-modifier-écrire de config_local.json


# ---- Configuration ----
def lire_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def ecrire_config(cfg):
    with _verrou_cfg:
        tmp = f"{CONFIG}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=1, ensure_ascii=False)
        os.replace(tmp, CONFIG)


def _taille_chemin(chemin):
    """Taille en octets d'un fichier ou d'un dossier (0 s'il n'existe pas)."""
    if os.path.isfile(chemin):
        return os.path.getsize(chemin)
    total = 0
    for d, _, fichiers in os.walk(chemin):
        for f in fichiers:
            try:
                total += os.path.getsize(os.path.join(d, f))
            except OSError:
                pass
    return total


def _enregistrer(chemin, categorie):
    """Note dans config_local.json (clé « installe ») ce que Stillive vient d'installer lui-même.
    Appelé seulement après un téléchargement réussi d'un fichier qui n'existait pas avant."""
    chemin = os.path.abspath(chemin).replace("\\", "/")
    taille = _taille_chemin(chemin)
    with _verrou_cfg:
        cfg = lire_config()
        liste = [e for e in cfg.get("installe", []) if isinstance(e, dict) and e.get("chemin") != chemin]
        liste.append({"chemin": chemin, "categorie": categorie, "taille": taille})
        cfg["installe"] = liste
        ecrire_config(cfg)


def _mode(cfg):
    return cfg.get("mode") or ("llamacpp" if cfg.get("llama_server") else None)


# ---- Détection (127.0.0.1 seulement, 0,5 s par sonde) ----
def _json_get(url, timeout=0.5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read())


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kw):
        return None  # une redirection devient une erreur : jamais suivie vers une autre adresse


_OUVREUR_LOCAL = urllib.request.build_opener(_SansRedirection)


def url_locale(url):
    """Vrai seulement pour http(s) vers 127.0.0.1, localhost ou ::1 (le moteur d'analyse est sur ce PC)."""
    try:
        p = urllib.parse.urlsplit(url or "")
        return p.scheme in ("http", "https") and (p.hostname or "").lower() in ("127.0.0.1", "localhost", "::1")
    except ValueError:
        return False


def _json_get_local(url, timeout=0.5):
    if not url_locale(url):
        raise OSError("adresse non locale refusée")
    with _OUVREUR_LOCAL.open(url, timeout=timeout) as r:
        return json.loads(r.read())


def _modeles(url, ollama, timeout=0.5):
    """Noms des modèles d'un programme joignable, sinon None."""
    try:
        if ollama:
            return [m["name"] for m in _json_get_local(url + "/api/tags", timeout).get("models", [])]
        return [m["id"] for m in _json_get_local(url + "/v1/models", timeout).get("data", [])]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _trouve_recommande(noms):
    return next((n for n in noms if RECOMMANDE.search(n)), None)


def detecter():
    def sonde(p):
        url = f"http://127.0.0.1:{p[2]}"
        noms = _modeles(url, p[3])
        if noms is None:
            return None
        return {"programme": p[0], "nom": p[1], "url": url, "modeles": noms, "modele_recommande": _trouve_recommande(noms)}
    with concurrent.futures.ThreadPoolExecutor(len(PROGRAMMES)) as ex:
        return [d for d in ex.map(sonde, PROGRAMMES) if d]


# ---- Carte graphique et quantification ----
def carte():
    """{"nom", "vram_mo", "pilote"} de la carte NVIDIA la plus grande, ou None."""
    try:
        sortie = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        cartes = []
        for ligne in sortie.strip().splitlines():
            nom, mem, pilote = [x.strip() for x in ligne.split(",")]
            cartes.append({"nom": nom, "vram_mo": int(float(mem)), "pilote": pilote})
        return max(cartes, key=lambda c: c["vram_mo"]) if cartes else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def choisir_quant(vram_go):
    """(quantification, faible). Table estimée ; UD-Q4_K_XL validée par les tests sur 16 Go."""
    if vram_go is None or vram_go < 11:
        return "UD-IQ3_XXS", True
    for seuil, quant in ((32, "UD-Q6_K_XL"), (24, "UD-Q5_K_XL"), (15, "UD-Q4_K_XL")):
        if vram_go >= seuil:
            return quant, False
    return "UD-Q3_K_XL", False


# ---- Modèles H3 (dossiers de ComfyUI) ----
def _racine_modeles():
    try:
        import folder_paths  # présent quand Stillive tourne dans ComfyUI
        return folder_paths.models_dir
    except Exception:
        return os.path.join(MOTEUR, "modeles")


def _dossiers_type(sous_dossier):
    """Dossiers où ComfyUI cherche ce type de modèle (le premier reçoit les téléchargements)."""
    try:
        import folder_paths
        liste = [d for d in folder_paths.get_folder_paths(sous_dossier) if d]
        if liste:
            return liste
    except Exception:
        pass
    return [os.path.join(_racine_modeles(), sous_dossier)]


H3_EQUIVALENTS = {"minimax_h3_video_vae_int8_convrot.safetensors": "minimax_h3_video_vae_int8_convrot_comfyorg.safetensors"}


def _h3_present(sous_dossier, nom):
    """Présent sous son nom officiel ou sous un nom équivalent connu (ex. VAE vidéo « comfyorg », que h3.py accepte aussi)."""
    nom2 = H3_EQUIVALENTS.get(nom)
    if nom2 and any(os.path.isfile(os.path.join(d, nom2)) for d in _dossiers_type(sous_dossier)):
        return True
    return any(os.path.isfile(os.path.join(d, nom)) for d in _dossiers_type(sous_dossier))


def etat_h3():
    fichiers = [{"nom": n, "sous_dossier": sd, "taille_go": t, "option": opt, "present": _h3_present(sd, n)}
                for sd, n, t, opt in H3_FICHIERS]
    base = [f for f in fichiers if f["option"] == "base"]
    return {"pret": all(f["present"] for f in base), "fichiers": fichiers,
            "a_telecharger_go": round(sum(f["taille_go"] for f in base if not f["present"]), 2),
            "a_telecharger_ref2va_go": round(sum(f["taille_go"] for f in fichiers
                                                 if f["option"] == "ref2va" and not f["present"]), 2),
            "dossier_modeles": _racine_modeles()}


# ---- Compatibilité du PC ----
def _ram_go():
    """RAM totale en Go sans dépendance externe, ou None si inconnue."""
    try:
        systeme = platform.system()
        if systeme == "Windows":
            import ctypes

            class MemoireStatut(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MemoireStatut()
            m.dwLength = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return round(m.ullTotalPhys / 2 ** 30, 1)
        elif systeme == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for ligne in f:
                    if ligne.startswith("MemTotal:"):
                        return round(int(ligne.split()[1]) / 2 ** 20, 1)
    except Exception:
        pass
    return None


def _disque_libre_go(chemin):
    """Espace libre (Go) du disque qui contient `chemin` (même s'il n'existe pas encore), ou None."""
    try:
        while chemin and not os.path.exists(chemin):
            parent = os.path.dirname(chemin)
            if parent == chemin:
                break
            chemin = parent
        return round(shutil.disk_usage(chemin).free / 2 ** 30, 1)
    except (OSError, ValueError):
        return None


def _llamacpp_pret(cfg):
    return _mode(cfg) == "llamacpp" and all(cfg.get(k) and os.path.isfile(cfg[k]) for k in ("llama_server", "model"))


def compatibilite(c=_AUTO, quant=None, h3=True, ref2va=False, llm_a_telecharger=True):
    """Ce PC peut-il faire tourner Stillive (H3 + analyse) ? Règles = HYPOTHÈSES ❓ non vérifiées :
    NVIDIA obligatoire (Windows ou Linux), Mac refusé (H3 non vérifié dessus), VRAM >= 12 Go, RAM >= 32 Go,
    disque libre >= reste à télécharger + 5 Go. Une valeur inconnue (None) ne bloque jamais."""
    if c is _AUTO:
        c = carte()
    systeme = platform.system()
    vram = round(c["vram_mo"] / 1024, 1) if c else None
    if quant not in TAILLES:
        quant, _ = choisir_quant(vram)
    ram = _ram_go()
    requis = TAILLES[quant] + ANNEXES_LLM_GO if llm_a_telecharger else 0.0
    if h3:
        requis += sum(t for sd, n, t, opt in H3_FICHIERS
                      if (opt == "base" or ref2va) and not _h3_present(sd, n))
    libre = _disque_libre_go(_racine_modeles())
    requis = round(requis + MARGE_DISQUE_GO, 1)
    raison = None
    if systeme == "Darwin":
        raison = "mac"
    elif systeme not in ("Windows", "Linux"):
        raison = "systeme"
    elif c is None:
        raison = "pas_nvidia"
    elif vram is not None and vram < VRAM_MIN_GO - 0.5:  # 12 Go réels s'affichent ~11,9 : petite tolérance
        raison = "vram_faible"
    elif ram is not None and ram < RAM_MIN_GO - 1:  # 32 Go installés s'affichent ~31,x
        raison = "ram_faible"
    elif libre is not None and libre < requis:
        raison = "disque"
    return {"ok": raison is None, "raison": raison, "systeme": systeme, "carte": c["nom"] if c else None,
            "vram_go": vram, "ram_go": ram, "disque_libre_go": libre, "disque_requis_go": requis}


def etat():
    cfg = lire_config()
    mode = _mode(cfg)
    c = carte()
    vram = round(c["vram_mo"] / 1024, 1) if c else None
    quant, faible = choisir_quant(vram)
    if mode == "llamacpp":
        modele = os.path.basename(cfg.get("model", ""))
        pret = all(cfg.get(k) and os.path.isfile(cfg[k]) for k in ("llama_server", "model"))
        if cfg.get("mmproj") and not os.path.isfile(cfg["mmproj"]):
            pret = False
    elif mode in ("ollama", "openai"):
        modele = cfg.get("modele", "")
        pret = _modeles((cfg.get("url") or "").rstrip("/"), mode == "ollama") is not None
    else:
        modele, pret = "", False
    with _verrou:
        inst = {k: _inst[k] for k in ("etat", "etape", "fait", "total", "message")}
    return {"pret": pret, "mode": mode, "modele": modele, "recommande": bool(RECOMMANDE.search(modele)),
            "programme": cfg.get("programme"), "carte": c["nom"] if c else None, "vram_go": vram,
            "quant": quant, "taille_go": TAILLES[quant], "faible": faible, "detectes": detecter(),
            "quants": [{"nom": n, "taille_go": t, "recommande": n == quant} for n, t in TAILLES.items()],
            "h3": etat_h3(), "compat": compatibilite(c, quant, llm_a_telecharger=not pret),
            "installation": inst}


def utiliser(programme, url, modele):
    """Teste le programme et le modèle, puis enregistre la config. Erreurs : injoignable, modele_absent."""
    url = (url or "").rstrip("/")
    if not url_locale(url):  # sécurité : jamais une adresse extérieure (les photos y partiraient)
        return {"ok": False, "recommande": False, "erreur": "injoignable"}
    ollama = programme == "ollama"
    noms = _modeles(url, ollama, timeout=3)
    if noms is None:
        return {"ok": False, "recommande": False, "erreur": "injoignable"}
    if modele not in noms and f"{modele}:latest" not in noms:
        return {"ok": False, "recommande": bool(RECOMMANDE.search(modele or "")), "erreur": "modele_absent"}
    with _verrou_cfg:
        cfg = lire_config()
        cfg.update(mode="ollama" if ollama else "openai", url=url, modele=modele, programme=programme)
        cfg.pop("installe_par_stillive", None)
        ecrire_config(cfg)
    return {"ok": True, "recommande": bool(RECOMMANDE.search(modele)), "erreur": None}


# ---- Téléchargement parallèle par plages, avec reprise ----
def _maj(**kw):
    with _verrou:
        _inst.update(kw)


def _ouvrir(url, entetes=None):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Stillive", **(entetes or {})}), timeout=60)


class _SansPlages(Exception):
    """Le serveur ne sait pas servir une partie de fichier (en-tête Range ignoré)."""


_PAUSES = (2, 5, 10, 20, 30, 30, 30)  # secondes entre 8 essais : environ 2 minutes sans réseau avant l'échec


def _definitive(e):
    """Erreur qui ne se règle pas en attendant (adresse refusée, disque plein...)."""
    if isinstance(e, urllib.error.HTTPError):
        return 400 <= e.code < 500 and e.code not in (408, 429)
    return getattr(e, "errno", None) in (errno.ENOSPC, errno.EACCES, errno.EROFS)


def _ecrire_etat(chemin, donnees):
    """Écriture atomique du fichier de progression (une coupure de courant ne le corrompt pas)."""
    tmp = chemin + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(donnees, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, chemin)


def _telecharger_simple(url, part, total, rapport):
    """Repli : un seul flux, depuis le début, pour un serveur sans plages. Nouveaux essais avec attente."""
    essai = 0
    while True:
        try:
            lus = 0
            with _ouvrir(url) as r, open(part, "wb") as f:
                while d := r.read(1 << 20):
                    f.write(d)
                    lus += len(d)
                    rapport(lus)
                f.flush()
                os.fsync(f.fileno())
            if lus != total:
                raise OSError("téléchargement incomplet")
            return
        except (OSError, http.client.HTTPException) as e:
            if essai >= len(_PAUSES) or _definitive(e):
                raise
            time.sleep(_PAUSES[essai])
            essai += 1


def telecharger(url, dest, total, sha256=None, rapport=None, connexions=16):
    """Télécharge url vers dest (taille `total` connue). Reprend dest.part (et dest.part.json) s'ils existent,
    même après une fermeture de l'application ou un redémarrage du PC : seuls les blocs non terminés sont refaits,
    et un bloc interrompu repart de l'octet atteint. Plusieurs essais avec attente croissante."""
    if os.path.isfile(dest) and os.path.getsize(dest) == total:
        return  # arrivé ici seulement après contrôle complet
    rapport = rapport or (lambda fait: None)
    part, etat_f = dest + ".part", dest + ".part.json"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    taille = max(1 << 20, min(64 << 20, -(-total // connexions)))
    n = -(-total // taille)
    faits = set()
    try:
        with open(etat_f, encoding="utf-8") as f:
            e = json.load(f)
        if e["total"] == total and e["taille"] == taille and os.path.getsize(part) == total:
            faits = set(e["faits"])
    except (OSError, ValueError, KeyError):
        pass
    if not faits:
        with open(part, "wb") as f:
            f.truncate(total)
    verrou, compte = threading.Lock(), {"fait": sum(min(taille, total - i * taille) for i in faits)}
    rapport(compte["fait"])

    def bloc(i):
        debut, fin = i * taille, min(total, (i + 1) * taille) - 1
        lus, essai = 0, 0  # `lus` est conservé d'un essai à l'autre : on reprend là où la coupure a eu lieu
        while True:
            avant = lus
            try:
                with _ouvrir(url, {"Range": f"bytes={debut + lus}-{fin}"}) as r, open(part, "r+b") as f:
                    if r.status != 206:
                        raise _SansPlages(f"le serveur ignore les plages (code {r.status})")
                    f.seek(debut + lus)
                    while d := r.read(1 << 20):
                        f.write(d)
                        lus += len(d)
                        with verrou:
                            compte["fait"] += len(d)
                            rapport(compte["fait"])
                    f.flush()
                    os.fsync(f.fileno())  # les données sont sur le disque avant de noter le bloc comme fait
                if lus != fin - debut + 1:
                    raise OSError("bloc incomplet")
                with verrou:
                    faits.add(i)
                    _ecrire_etat(etat_f, {"total": total, "taille": taille, "faits": sorted(faits)})
                return
            except (OSError, http.client.HTTPException) as e:
                if lus > avant:
                    essai = 0  # du progrès a eu lieu : le compteur d'essais repart
                if essai >= len(_PAUSES) or _definitive(e):
                    raise
                time.sleep(_PAUSES[essai])
                essai += 1

    try:
        ex = concurrent.futures.ThreadPoolExecutor(connexions)
        try:
            for f in [ex.submit(bloc, i) for i in range(n) if i not in faits]:
                f.result()
        finally:
            ex.shutdown(wait=True, cancel_futures=True)
    except _SansPlages:
        # Le serveur ne gère pas les plages : on recommence proprement, d'un seul flux, sans reprise possible
        _telecharger_simple(url, part, total, rapport)
        if os.path.isfile(etat_f):
            os.remove(etat_f)
    if os.path.getsize(part) != total:
        raise OSError(f"taille incorrecte pour {os.path.basename(dest)}")
    if sha256:
        _maj(message="Vérification du fichier")
        h, lu = hashlib.sha256(), 0
        with open(part, "rb") as f:
            while d := f.read(8 << 20):
                h.update(d)
                lu += len(d)
                rapport(lu)
        if h.hexdigest() != sha256:
            os.remove(part)
            if os.path.isfile(etat_f):
                os.remove(etat_f)
            raise OSError(f"{os.path.basename(dest)} corrompu (somme de contrôle différente), à retélécharger")
    os.replace(part, dest)
    if os.path.isfile(etat_f):
        os.remove(etat_f)


# ---- llama.cpp ----
def _dossier_modeles():
    try:
        import folder_paths  # présent quand Stillive tourne dans ComfyUI
        return os.path.join(folder_paths.models_dir, "LLM")
    except Exception:
        return os.path.join(MOTEUR, "modeles")


def _serveur_llama():
    nom = "llama-server.exe" if os.name == "nt" else "llama-server"
    for d, _, fichiers in os.walk(os.path.join(MOTEUR, "llama.cpp")):
        if nom in fichiers:
            return os.path.join(d, nom)
    return None


def choisir_archives(assets, c):
    """Noms des archives llama.cpp (programme, cudart ou None) pour ce PC, d'après la liste des assets."""
    noms = {a["name"] for a in assets}
    systeme, arm = platform.system(), platform.machine().lower() in ("arm64", "aarch64")
    ext = {"Windows": "zip"}.get(systeme, "tar.gz")
    # Hypothèse : CUDA 13.4 si pilote NVIDIA >= 580, sinon CUDA 12 (12.4 Windows, 12.8 Linux)
    try:
        neuf = c is not None and int(c["pilote"].split(".")[0]) >= 580
    except ValueError:
        neuf = False
    if systeme == "Darwin":
        motifs = [rf"llama-b\d+-bin-macos-{'arm64' if arm else 'x64'}\.tar\.gz"]
    elif systeme == "Windows" and c:
        v = ["13.4", "12.4"] if neuf else ["12.4", "13.4"]
        motifs = [rf"llama-b\d+-bin-win-cuda-{x}-x64\.zip" for x in v]
    elif systeme == "Windows":
        motifs = [r"llama-b\d+-bin-win-vulkan-x64\.zip"]
    elif c:
        v = ["13.4", "12.8"] if neuf else ["12.8", "13.4"]
        motifs = [rf"llama-b\d+-bin-ubuntu-cuda-{x}-x64\.tar\.gz" for x in v]
    else:
        motifs = [r"llama-b\d+-bin-ubuntu-x64\.tar\.gz"]
    for m in motifs:
        prog = next((n for n in sorted(noms) if re.fullmatch(m, n)), None)
        if prog:
            cuda = re.search(r"cuda-([\d.]+)", prog)
            cr = None
            if cuda:
                cr = next((n for n in sorted(noms) if re.fullmatch(rf"cudart-llama-(b\d+-)?bin-(win|ubuntu)-cuda-{re.escape(cuda.group(1))}-x64\.{re.escape(ext)}", n)), None)
            return prog, cr
    raise RuntimeError("aucune version de llama.cpp adaptée à ce PC")


LLAMA_ETIQUETTE = "b11821"  # version réellement testée (commit d81d0d8fb) : les options de lancement en dépendent


def _release_llama():
    """Version figée de llama.cpp ; si l'étiquette a disparu, la dernière version qui a des archives
    (/releases/latest ne les contient pas toujours)."""
    try:
        r = _json_get(f"https://api.github.com/repos/ggml-org/llama.cpp/releases/tags/{LLAMA_ETIQUETTE}", 30)
        if len(r.get("assets", [])) > 5:
            return r
    except (OSError, ValueError):
        pass
    for r in _json_get("https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10", 30):
        if len(r.get("assets", [])) > 5:
            return r
    raise RuntimeError("version de llama.cpp introuvable sur GitHub")


def _extraire(archive, dest):
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest, filter="data")


def _llama_complet():
    """Installation de llama.cpp terminée ? Marqueur écrit après l'extraction de toutes les archives ;
    sans marqueur (installation d'avant), on accepte si llama-server répond à --version, sinon on efface tout."""
    dossier = os.path.join(MOTEUR, "llama.cpp")
    marqueur = os.path.join(dossier, ".complet")
    serveur = _serveur_llama()
    if serveur and os.path.isfile(marqueur):
        return True
    if serveur:
        try:
            r = subprocess.run([serveur, "--version"], capture_output=True, text=True, timeout=60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode == 0:
                with open(marqueur, "w", encoding="utf-8") as f:
                    f.write("ok")
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    if os.path.isdir(dossier):
        _supprimer_chemin(dossier)  # extraction interrompue : on repart de zéro
    return False


def _installer_llama(c):
    if _llama_complet():
        return
    _maj(etape="llama", fait=0, total=0, message="Recherche de llama.cpp")
    assets = _release_llama()["assets"]
    prog, cudart = choisir_archives(assets, c)
    tmp = os.path.join(MOTEUR, "telechargements")
    for nom in [x for x in (prog, cudart) if x]:
        a = next(x for x in assets if x["name"] == nom)
        _maj(fait=0, total=a["size"], message=f"Téléchargement de {nom}")
        chemin = os.path.join(tmp, nom)
        digest = str(a.get("digest") or "")
        somme = digest[7:].lower() if digest.startswith("sha256:") else None  # contrôle fourni par GitHub
        telecharger(a["browser_download_url"], chemin, a["size"], somme, rapport=lambda f: _maj(fait=f))
        _maj(message=f"Installation de {nom}")
        _extraire(chemin, os.path.join(MOTEUR, "llama.cpp"))
        os.remove(chemin)
    if not _serveur_llama():
        raise RuntimeError("llama-server absent de l'archive")
    with open(os.path.join(MOTEUR, "llama.cpp", ".complet"), "w", encoding="utf-8") as f:
        f.write("ok")  # marqueur : toutes les archives sont extraites
    _enregistrer(os.path.join(MOTEUR, "llama.cpp"), "llama")


def _fichier_hf(liste, regle, etape, message, repo=REPO):
    f = next((x for x in liste if x.get("type") == "file" and regle(x["path"])), None)
    if not f:
        raise RuntimeError("fichier introuvable sur Hugging Face")
    nom, taille = os.path.basename(f["path"]), f["size"]
    _maj(etape=etape, fait=0, total=taille, message=message)
    chemin = os.path.join(_dossier_modeles(), nom)
    existait = os.path.exists(chemin)  # un fichier déjà là n'est jamais noté : il n'est pas à Stillive
    telecharger(f"https://huggingface.co/{repo}/resolve/main/{nom}", chemin, taille,
                (f.get("lfs") or {}).get("oid"), lambda fait: _maj(fait=fait))
    if not existait:
        _enregistrer(chemin, "llm")
    return chemin


def _message_erreur(e):
    """Message d'erreur en français simple pour les pannes réseau et disque courantes."""
    if isinstance(e, urllib.error.HTTPError):
        return f"Le serveur de téléchargement a répondu une erreur ({e.code}). Réessaie dans un moment."
    if isinstance(e, OSError) and getattr(e, "errno", None) == errno.ENOSPC:
        return "Le disque est plein. Libère de la place puis réessaie : le téléchargement reprendra où il s'est arrêté."
    if isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException)) \
            or e.__class__.__name__ in ("gaierror", "timeout"):
        return "Connexion internet interrompue. Vérifie ta connexion puis réessaie : le téléchargement reprendra où il s'est arrêté."
    return str(e) or e.__class__.__name__


def _lancer(cible):
    def corps():
        try:
            _maj(etat="en_cours", etape="", fait=0, total=0, message="")
            cible()
            _maj(etat="finie", message="Terminé")
        except Exception as e:
            _maj(etat="erreur", message=_message_erreur(e))
    with _verrou:
        if _inst["etat"] == "en_cours":
            return {"ok": False, "erreur": "deja_en_cours"}
        _inst["etat"] = "en_cours"
    threading.Thread(target=corps, name="stillive-moteur", daemon=True).start()
    return {"ok": True}


def _installer_h3(ref2va):
    """Télécharge les fichiers H3 manquants dans le premier dossier de chaque type de ComfyUI."""
    manquants = [(sd, n) for sd, n, _, opt in H3_FICHIERS if (opt == "base" or ref2va) and not _h3_present(sd, n)]
    if not manquants:
        return
    _maj(etape="h3", fait=0, total=0, message="Recherche des modèles H3")
    arbre = [x for x in _json_get(f"https://huggingface.co/api/models/{H3_REPO}/tree/main?recursive=true", 30)
             if x.get("type") == "file"]
    choisis = []
    for sd, nom in manquants:
        x = next((x for x in arbre if x["path"] == f"{sd}/{nom}" or x["path"].endswith(f"/{sd}/{nom}")), None)
        if not x:
            raise RuntimeError(f"{nom} introuvable sur Hugging Face")
        choisis.append((sd, nom, x))
    total = sum(x["size"] for _, _, x in choisis)
    fait_avant = 0
    for i, (sd, nom, x) in enumerate(choisis, 1):
        _maj(total=total, fait=fait_avant, message=f"Téléchargement de {nom} ({i}/{len(choisis)})")
        dest = os.path.join(_dossiers_type(sd)[0], nom)
        existait = os.path.exists(dest)  # jamais noté si déjà là
        telecharger(f"https://huggingface.co/{H3_REPO}/resolve/main/{x['path']}", dest, x["size"],
                    (x.get("lfs") or {}).get("oid"), lambda fait, base=fait_avant: _maj(fait=base + fait))
        if not existait:
            _enregistrer(dest, "h3")
        fait_avant += x["size"]
        _maj(fait=fait_avant)


def lancer_installation(quant=None, h3=True, ref2va=False, llm=True, forcer=False):
    """Options : quant (clé de TAILLES, sinon auto), h3 (modèles H3), ref2va (+ « entrer dans l'image »),
    llm (llama.cpp + Gemma 4 12B), forcer (passer outre une incompatibilité détectée)."""
    if not (h3 or llm):
        return {"ok": False, "erreur": "rien_a_installer"}
    c = carte()
    if quant not in TAILLES:
        quant, _ = choisir_quant(round(c["vram_mo"] / 1024, 1) if c else None)
    llm = bool(llm)
    compat = compatibilite(c, quant, bool(h3), bool(ref2va), llm and not _llamacpp_pret(lire_config()))
    if not compat["ok"] and not forcer:
        return {"ok": False, "erreur": "incompatible", "raison": compat["raison"]}

    def faire():
        if llm:
            _installer_llama(c)
            liste = _json_get(f"https://huggingface.co/api/models/{REPO}/tree/main", 30)
            modele = _fichier_hf(liste, lambda p: p.lower().endswith(f"-{quant.lower()}.gguf") and not p.startswith("mmproj"),
                                 "modele", f"Téléchargement du modèle ({quant})")
            mmproj = _fichier_hf(liste, lambda p: p.lower() == "mmproj-bf16.gguf",
                                 "vision", "Téléchargement de la vision")
            liste_mtp = _json_get(f"https://huggingface.co/api/models/{MTP_REPO}/tree/main", 30)
            mtp = _fichier_hf(liste_mtp, lambda p: p.lower() == MTP_FICHIER,
                              "mtp", "Téléchargement de l'accélération de l'analyse", MTP_REPO)
            _maj(etape="verif", fait=0, total=0, message="Vérification de llama.cpp")
            serveur = _serveur_llama()
            r = subprocess.run([serveur, "--version"], capture_output=True, text=True, timeout=120,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                raise RuntimeError("llama-server ne démarre pas : " + (r.stderr or r.stdout)[-500:])
        if h3:
            _installer_h3(bool(ref2va))
        if llm:
            with _verrou_cfg:
                cfg = lire_config()  # remplacée seulement maintenant que tout a réussi
                cfg.update(mode="llamacpp", llama_server=serveur.replace("\\", "/"), model=modele.replace("\\", "/"),
                           mmproj=mmproj.replace("\\", "/"), brouillon=mtp.replace("\\", "/"), vision_gpu=True,
                           reflexion=False, programme="llamacpp", installe_par_stillive=True)
                cfg.setdefault("ctx", 32768)
                ecrire_config(cfg)
    return _lancer(faire)


def telecharger_ollama(url):
    url = (url or "http://127.0.0.1:11434").rstrip("/")
    if not url_locale(url):
        return {"ok": False, "erreur": "injoignable"}

    def faire():
        _maj(etape="ollama", fait=0, total=0, message="Connexion à Ollama")
        req = urllib.request.Request(url + "/api/pull", json.dumps({"model": OLLAMA_MODELE}).encode(),
                                     {"Content-Type": "application/json"})
        fini = False
        with _OUVREUR_LOCAL.open(req, timeout=300) as r:
            for ligne in r:
                d = json.loads(ligne)
                if d.get("error"):
                    raise RuntimeError(d["error"])
                _maj(message=d.get("status", ""), **({"fait": d["completed"], "total": d["total"]} if d.get("total") else {}))
                fini = fini or d.get("status") == "success"
        if not fini:
            raise RuntimeError("Ollama a interrompu le téléchargement")
        with _verrou_cfg:
            cfg = lire_config()
            cfg.update(mode="ollama", url=url, modele=OLLAMA_MODELE, programme="ollama")
            cfg.pop("installe_par_stillive", None)
            ecrire_config(cfg)
    return _lancer(faire)


# ---- Désinstallation : seulement ce que Stillive a lui-même installé (registre « installe ») ----
CATEGORIES_DESINSTALLATION = ("llama", "llm", "h3", "donnees", "reglages")
_SOUS_DOSSIERS_DONNEES = ("films", "scans", "propositions")


def _norm(chemin):
    return os.path.normcase(os.path.realpath(chemin))


def _sous(chemin, racine, egal=False):
    """Vrai si `chemin` est dans `racine` (ou est `racine` si egal), liens résolus."""
    try:
        c, r = _norm(chemin), _norm(racine)
        if c == r:
            return egal
        return os.path.commonpath([c, r]) == r
    except (ValueError, OSError):
        return False


def _chemin_autorise(entree):
    """Un chemin du registre n'est supprimé que s'il est exactement à sa place attendue."""
    ch, cat = entree.get("chemin"), entree.get("categorie")
    if not isinstance(ch, str) or not ch or ".." in re.split(r"[\\/]", ch) or os.path.islink(ch):
        return False
    if cat == "llama":
        return _norm(ch) == _norm(os.path.join(MOTEUR, "llama.cpp"))
    if os.path.isdir(ch):
        return False
    if cat == "llm":
        return ch.lower().endswith(".gguf") and _sous(ch, _dossier_modeles())
    if cat == "h3":
        return os.path.basename(ch) in {n for _, n, _, _ in H3_FICHIERS} and _sous(ch, _racine_modeles())
    return False


def _registre():
    return [e for e in lire_config().get("installe", []) if isinstance(e, dict)]


def _go(octets):
    return round(octets / 1e9, 2)


def apercu_desinstallation():
    """Par catégorie : ce qui serait supprimé (fichiers du registre qui existent encore et sont à leur place)."""
    cats = {c: {"nombre": 0, "taille_go": 0.0, "noms": []} for c in CATEGORIES_DESINSTALLATION}
    octets = dict.fromkeys(CATEGORIES_DESINSTALLATION, 0)
    for e in _registre():
        if e.get("categorie") in ("llama", "llm", "h3") and _chemin_autorise(e) and os.path.exists(e["chemin"]):
            c = e["categorie"]
            cats[c]["nombre"] += 1
            cats[c]["noms"].append(os.path.basename(e["chemin"]))
            octets[c] += _taille_chemin(e["chemin"])
    for sd in _SOUS_DOSSIERS_DONNEES:
        d = os.path.join(DONNEES, sd)
        if os.path.isdir(d):
            cats["donnees"]["nombre"] += len(os.listdir(d))
            octets["donnees"] += _taille_chemin(d)
    if os.path.isfile(CONFIG):
        cats["reglages"]["nombre"] = 1
        octets["reglages"] = os.path.getsize(CONFIG)
    for c in cats:
        cats[c]["taille_go"] = _go(octets[c])
    return {"categories": cats, "installation_en_cours": _inst["etat"] == "en_cours",
            "inscrit": len(_registre()),
            "dossier": os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}


def _arreter_llama_stillive():
    """Arrête le llama-server lancé par Stillive (celui du dossier moteur/llama.cpp), jamais un autre."""
    for m in list(sys.modules.values()):  # lancé par le nœud ComfyUI, dans ce même processus
        arret, etat = getattr(m, "_arreter_serveur", None), getattr(m, "_serveur", None)
        if callable(arret) and isinstance(etat, dict) and "proc" in etat:
            try:
                arret()
            except Exception:
                pass
    base = os.path.join(MOTEUR, "llama.cpp")
    if not os.path.isdir(base):
        return
    pids = []
    try:
        if os.name == "nt":
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath} | "
                                "ForEach-Object { \"$($_.ProcessId)`t$($_.ExecutablePath)\" }"],
                               capture_output=True, text=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            paires = [l.split("\t", 1) for l in r.stdout.splitlines() if "\t" in l]
        else:
            paires = []
            for p in os.listdir("/proc"):
                try:
                    paires.append([p, os.readlink(f"/proc/{p}/exe")])
                except OSError:
                    pass
        pids = [int(p) for p, exe in paires if p.strip().isdigit() and _sous(exe.strip(), base)]
    except (OSError, subprocess.SubprocessError):
        return
    for pid in pids:
        if pid == os.getpid():
            continue
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            try:
                os.kill(pid, 15)
            except OSError:
                pass
    if pids:
        import time
        time.sleep(1.5)  # laisse Windows relâcher les fichiers


def _supprimer_chemin(chemin):
    def reessayer(fonction, path, *_):  # fichier en lecture seule : on lève la protection puis on réessaie
        os.chmod(path, 0o700)
        fonction(path)
    if os.path.isdir(chemin):
        if sys.version_info >= (3, 12):
            shutil.rmtree(chemin, onexc=reessayer)
        else:
            shutil.rmtree(chemin, onerror=reessayer)
    else:
        os.remove(chemin)


def _retirer_dossiers_vides(depuis, racine):
    """Remonte depuis le dossier de `depuis` et enlève les dossiers devenus vides, sans jamais sortir de `racine`
    ni retirer un dossier de premier niveau de ComfyUI (sauf LLM, créé par Stillive)."""
    chez_nous = _sous(racine, MOTEUR, egal=True)
    d = os.path.dirname(os.path.abspath(depuis))
    while _sous(d, racine):
        premier_niveau = _norm(os.path.dirname(d)) == _norm(racine)
        if premier_niveau and not chez_nous and os.path.basename(d) != "LLM":
            break
        try:
            os.rmdir(d)
        except OSError:
            break
        d = os.path.dirname(d)


def desinstaller(categories):
    with _verrou_cfg:  # un seul à la fois à toucher le registre et les réglages
        return _desinstaller(categories)


def _desinstaller(categories):
    """Supprime exactement les catégories demandées (llama, llm, h3, donnees, reglages).
    Rend {"ok", "retire": [...], "echecs": [...], "libere_go"} ou {"ok": False, "erreur": code}."""
    if not isinstance(categories, (list, tuple, set)) or not all(isinstance(c, str) for c in categories):
        return {"ok": False, "erreur": "categorie_inconnue"}
    demandees = list(dict.fromkeys(categories))
    if any(c not in CATEGORIES_DESINSTALLATION for c in demandees):
        return {"ok": False, "erreur": "categorie_inconnue"}
    if not demandees:
        return {"ok": False, "erreur": "rien_a_supprimer"}
    with _verrou:
        if _inst["etat"] == "en_cours":
            return {"ok": False, "erreur": "installation_en_cours"}
    if "llama" in demandees or "llm" in demandees:
        _arreter_llama_stillive()
    retire, echecs, octets, enleves = [], [], 0, set()
    for e in _registre():
        cat = e.get("categorie")
        if cat not in demandees or cat not in ("llama", "llm", "h3"):
            continue
        ch = e.get("chemin")
        if not _chemin_autorise(e):
            echecs.append({"categorie": cat, "chemin": str(ch), "raison": "chemin_refuse"})
            continue
        if not os.path.exists(ch):
            enleves.add(ch)  # déjà parti : on l'efface seulement du registre
            continue
        taille = _taille_chemin(ch)
        try:
            _supprimer_chemin(ch)
        except OSError as ex:
            echecs.append({"categorie": cat, "chemin": ch, "raison": "impossible", "detail": str(ex)})
            continue
        enleves.add(ch)
        octets += taille
        retire.append({"categorie": cat, "chemin": ch, "taille_go": _go(taille)})
        if cat in ("h3", "llm"):
            _retirer_dossiers_vides(ch, _racine_modeles())
    if "llama" in demandees or "llm" in demandees or "h3" in demandees:
        for sd in ("telechargements", "modeles"):
            try:
                os.rmdir(os.path.join(MOTEUR, sd))  # seulement s'ils sont vides
            except OSError:
                pass
        try:
            os.rmdir(MOTEUR)
        except OSError:
            pass
    if "donnees" in demandees:
        for sd in _SOUS_DOSSIERS_DONNEES:
            d = os.path.join(DONNEES, sd)
            if not os.path.isdir(d):
                continue
            taille = _taille_chemin(d)
            try:
                _supprimer_chemin(d)
                octets += taille
                retire.append({"categorie": "donnees", "chemin": d.replace("\\", "/"), "taille_go": _go(taille)})
            except OSError as ex:
                echecs.append({"categorie": "donnees", "chemin": d, "raison": "impossible", "detail": str(ex)})
        try:
            os.rmdir(DONNEES)
        except OSError:
            pass
    # registre et réglages (le fichier de réglages contient le registre : on garde ce qui reste dedans)
    cfg = lire_config()
    avant = json.dumps(cfg, sort_keys=True)
    if os.path.isfile(CONFIG):
        reste = [e for e in cfg.get("installe", []) if not (isinstance(e, dict) and e.get("chemin") in enleves)]
        retires_cfg = {_norm(c) for c in enleves}
        valeurs = [cfg.get(k) for k in ("llama_server", "model", "mmproj", "brouillon")]
        if any(isinstance(v, str) and v and (_norm(v) in retires_cfg or any(_sous(v, c) for c in enleves))
               for v in valeurs):  # la config pointait vers ce qui vient d'être retiré
            for k in ("mode", "programme", "llama_server", "model", "mmproj", "brouillon", "vision_gpu", "reflexion",
                      "installe_par_stillive"):
                cfg.pop(k, None)
        if "reglages" in demandees:
            conservees = [e for e in reste if e.get("chemin") and os.path.exists(e["chemin"])]
            try:
                if conservees:
                    ecrire_config({"installe": conservees})
                    retire.append({"categorie": "reglages", "chemin": CONFIG.replace("\\", "/"), "taille_go": 0.0,
                                   "registre_garde": True})
                elif os.path.isfile(CONFIG):
                    taille = os.path.getsize(CONFIG)
                    os.remove(CONFIG)
                    octets += taille
                    retire.append({"categorie": "reglages", "chemin": CONFIG.replace("\\", "/"), "taille_go": _go(taille)})
            except OSError as ex:
                echecs.append({"categorie": "reglages", "chemin": CONFIG, "raison": "impossible", "detail": str(ex)})
        else:
            if reste:
                cfg["installe"] = reste
            else:
                cfg.pop("installe", None)
            if json.dumps(cfg, sort_keys=True) != avant:
                ecrire_config(cfg)
    return {"ok": not echecs, "retire": retire, "echecs": echecs, "libere_go": _go(octets)}
