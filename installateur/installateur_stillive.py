"""Installateur Stillive pour qui n'a pas ComfyUI.

Fenêtre (tkinter) ou console : installe ComfyUI (paquet portable officiel sous Windows, dépôt officiel +
environnement Python sous Linux), y ajoute Stillive, écrit un fichier de lancement, puis démarre ComfyUI et
ouvre la page de préparation de Stillive. Bibliothèque standard seulement. Textes dans langues.json.

Disposition de Stillive dans ComfyUI : custom_nodes/stillive_nodes/ contient TOUT le dossier stillive/
(comfy_nodes, stillive, web, requirements.txt) plus un __init__.py de passerelle écrit ici.
"""
import sys

if sys.version_info < (3, 9):  # avant tout le reste, texte en dur (langues.json n'est pas encore lu)
    sys.exit("Stillive a besoin de Python 3.9 ou plus (installe-le depuis python.org ou avec winget install Python.Python.3.13) / "
             "Stillive needs Python 3.9 or newer.")

import argparse
import concurrent.futures
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser

VERSION = "0.1"

# ---- Constantes (mêmes valeurs que stillive/stillive/moteur.py ; hypothèses non vérifiées) ----
VRAM_MIN_GO, RAM_MIN_GO, MARGE_DISQUE_GO = 12, 32, 5
STILLIVE_GO = 50  # Gemma 4 12B (~8 Go) + modèles H3 + llama.cpp téléchargés ensuite par la page de préparation (estimation)
EXTRAIT_GO = 4.5  # paquet portable extrait : 4,14 Go mesurés le 25/10 (4,6 Go au disque) + marge
LINUX_GO = 12.0  # ComfyUI + torch CUDA sous Linux (estimation, non mesurée)
PILOTE_CUDA13 = 580  # pilote NVIDIA minimal pour CUDA 13 (même hypothèse que moteur.py)
PORT_STILLIVE = 8765
NOM_DOSSIER_STILLIVE = "stillive_nodes"
DEPOT_API = "https://api.github.com/repos/Comfy-Org/ComfyUI/releases/latest"
ARCHIVES = {"cuda13": "ComfyUI_windows_portable_nvidia.7z", "cu126": "ComfyUI_windows_portable_nvidia_cu126.7z"}
# Repli si l'API GitHub est injoignable (relevé le 25/10/2026, version v0.42.1)
REPLI = {
    "tag": "v0.42.1",
    "ComfyUI_windows_portable_nvidia.7z": (2018444114, "496afd18790ec685d99ebba2edf200d7371a29fbdd3a992bb0f4f52f244a6874"),
    "ComfyUI_windows_portable_nvidia_cu126.7z": (1910153206, "0506abc6563ea5928a052fe0bc0b32c2a063098558bd593fe39345a27f1e47fc"),
}
URL_7ZR = "https://www.7-zip.org/a/7zr.exe"
SHA_7ZR = {"256feca8e274e5da655e2a284fabafd9f554365eb164862089dacd4e8276d282"}  # mesuré le 25/10/2026 (non publié par 7-zip.org)
TORCH_INDEX = {"cuda13": "https://download.pytorch.org/whl/cu130", "cu126": "https://download.pytorch.org/whl/cu126"}
# paquet pip -> module importable
MODULES = {"aiohttp": "aiohttp", "numpy": "numpy", "pillow": "PIL",
           "opencv-python-headless": "cv2", "imageio-ffmpeg": "imageio_ffmpeg"}
SANS_FENETRE = getattr(subprocess, "CREATE_NO_WINDOW", 0)

DOSSIER_SCRIPT = os.path.dirname(os.path.abspath(__file__))
RESSOURCES = getattr(sys, "_MEIPASS", DOSSIER_SCRIPT)
DOSSIER_LOG = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else DOSSIER_SCRIPT

# Simulations pour les essais (--simuler-*)
SIM = {"systeme": None, "carte": "auto", "disque_go": None}


# ---------------------------------------------------------------- textes
class Textes:
    def __init__(self, langue):
        with open(os.path.join(RESSOURCES, "langues.json"), encoding="utf-8") as f:
            self.tout = json.load(f)
        self.langue = langue if langue in self.tout else "fr"

    def __call__(self, cle, **kw):
        texte = self.tout[self.langue].get(cle) or self.tout["fr"].get(cle) or cle
        return texte.format(**kw) if kw else texte

    def go(self, x):
        s = f"{x:.1f}"
        return s.replace(".", ",") if self.langue == "fr" else s


def langue_systeme():
    env = os.environ.get("STILLIVE_LANGUE", "")
    if env[:2] in ("fr", "en"):
        return env[:2]
    try:
        if os.name == "nt":
            import ctypes
            return "fr" if (ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0xFF) == 0x0C else "en"
    except Exception:
        pass
    for v in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(v, "").lower().startswith("fr"):
            return "fr"
    return "en"


# ---------------------------------------------------------------- journal et erreurs
class Journal:
    def __init__(self, chemin):
        self.chemin = chemin
        self.verrou = threading.Lock()
        with open(chemin, "a", encoding="utf-8"):  # dossier en lecture seule : lève OSError, l'appelant bascule vers TEMP
            pass

    def __call__(self, msg):
        ligne = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n"
        with self.verrou:
            try:
                with open(self.chemin, "a", encoding="utf-8") as f:
                    f.write(ligne)
            except OSError:
                pass


class ErreurInstall(Exception):
    """Erreur à expliquer à l'utilisateur : clé de langues.json + paramètres."""

    def __init__(self, cle, detail="", **params):
        super().__init__(cle)
        self.cle, self.detail, self.params = cle, detail, params


class Interrompu(Exception):
    pass


# ---------------------------------------------------------------- système (copie de la logique de moteur.py)
def systeme():
    return SIM["systeme"] or platform.system()


def carte():
    """{"nom", "vram_mo", "pilote", "cap"} de la carte NVIDIA la plus grande, ou None."""
    if SIM["carte"] != "auto":
        if SIM["carte"] in (None, "aucune"):
            return None
        nom, mem, pilote, cap = (SIM["carte"].split(":") + ["", "", "", ""])[:4]
        return {"nom": nom, "vram_mo": int(mem), "pilote": pilote, "cap": float(cap) if cap else None}
    for champs in ("name,memory.total,driver_version,compute_cap", "name,memory.total,driver_version"):
        try:
            sortie = subprocess.run(["nvidia-smi", f"--query-gpu={champs}", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=10, creationflags=SANS_FENETRE).stdout
            cartes = []
            for ligne in sortie.strip().splitlines():
                p = [x.strip() for x in ligne.split(",")]
                cap = None
                if len(p) > 3:
                    try:
                        cap = float(p[3])
                    except ValueError:
                        pass
                cartes.append({"nom": p[0], "vram_mo": int(float(p[1])), "pilote": p[2], "cap": cap})
            if cartes:
                return max(cartes, key=lambda c: c["vram_mo"])
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
    return None


def ram_go():
    try:
        s = platform.system()
        if s == "Windows":
            import ctypes

            class Memoire(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = Memoire()
            m.dwLength = ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return round(m.ullTotalPhys / 2 ** 30, 1)
        elif s == "Linux":
            with open("/proc/meminfo", encoding="utf-8") as f:
                for ligne in f:
                    if ligne.startswith("MemTotal:"):
                        return round(int(ligne.split()[1]) / 2 ** 20, 1)
    except Exception:
        pass
    return None


def disque_libre_go(chemin):
    if SIM["disque_go"] is not None:
        return SIM["disque_go"]
    try:
        chemin = os.path.abspath(chemin)
        while chemin and not os.path.exists(chemin):
            parent = os.path.dirname(chemin)
            if parent == chemin:
                break
            chemin = parent
        return round(shutil.disk_usage(chemin).free / 2 ** 30, 1)
    except (OSError, ValueError):
        return None


def verifier_systeme(T):
    """Renvoie la carte, ou lève ErreurInstall. Mêmes règles que moteur.compatibilite() (hors disque)."""
    machine = platform.machine().lower()
    if systeme() not in ("Windows", "Linux") or (not SIM["systeme"] and machine not in ("amd64", "x86_64")):
        raise ErreurInstall("err.systeme", f"système {systeme()} {machine}")
    c = carte()
    if c is None:
        raise ErreurInstall("err.pas_nvidia")
    vram = round(c["vram_mo"] / 1024, 1)
    if vram < VRAM_MIN_GO - 0.5:  # 12 Go réels s'affichent ~11,9
        raise ErreurInstall("err.vram", carte=c["nom"], vram=T.go(vram), min=VRAM_MIN_GO)
    ram = ram_go()
    if ram is not None and ram < RAM_MIN_GO - 1:  # 32 Go installés s'affichent ~31,x
        raise ErreurInstall("err.ram", ram=T.go(ram), min=RAM_MIN_GO)
    return c


def choisir_variante(c, demande):
    """cuda13 (cartes RTX 20 et plus récentes, capacité >= 7.5) ou cu126 (cartes plus anciennes)."""
    if demande in ARCHIVES:
        return demande
    if c.get("cap") is not None:
        return "cuda13" if c["cap"] >= 7.5 else "cu126"
    ancien = re.search(r"GTX\s*(9|10)\d\d|TITAN\s*(X|Xp|V)\b|Quadro\s*P|Tesla\s*(P|V)\d|GTX\s*7\d\d|GTX\s*6\d\d", c["nom"], re.I)
    return "cu126" if ancien else "cuda13"


# ---------------------------------------------------------------- dossiers
def trouver_comfyui(dossier):
    """Dossier ComfyUI (celui qui contient main.py, custom_nodes et comfy) trouvé dans `dossier`, ou None."""
    for c in (dossier, os.path.join(dossier, "ComfyUI"), os.path.join(dossier, "ComfyUI_windows_portable", "ComfyUI")):
        if all(os.path.exists(os.path.join(c, x)) for x in ("main.py", "custom_nodes", "comfy")):
            return os.path.abspath(c)
    return None


def trouver_python(comfy):
    racine = os.path.dirname(comfy)
    for p in (os.path.join(racine, "python_embeded", "python.exe"),
              os.path.join(comfy, "venv", "Scripts", "python.exe"), os.path.join(comfy, ".venv", "Scripts", "python.exe"),
              os.path.join(comfy, "venv", "bin", "python"), os.path.join(comfy, ".venv", "bin", "python"),
              os.path.join(racine, "venv", "Scripts", "python.exe"), os.path.join(racine, "venv", "bin", "python")):
        if os.path.isfile(p):
            return p
    return None


def trouver_source(demande=None):
    for d in (demande, os.path.join(DOSSIER_SCRIPT, "..", "stillive"), os.path.join(DOSSIER_SCRIPT, "stillive"),
              os.path.join(DOSSIER_LOG, "stillive")):
        if d and os.path.isfile(os.path.join(d, "comfy_nodes", "__init__.py")) \
                and os.path.isfile(os.path.join(d, "stillive", "moteur.py")):
            return os.path.abspath(d)
    return None


def taille_dossier(chemin):
    total = 0
    for racine, _, fichiers in os.walk(chemin):
        for f in fichiers:
            try:
                total += os.path.getsize(os.path.join(racine, f))
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- réseau : téléchargement avec reprise (logique de moteur.telecharger)
def ouvrir(url, entetes=None, timeout=60):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "StilliveInstallateur", **(entetes or {})}),
                                  timeout=timeout)


def json_get(url, timeout=30):
    with ouvrir(url, {"Accept": "application/vnd.github+json"}, timeout) as r:
        return json.loads(r.read())


def telecharger(url, dest, total, sha256=None, rapport=None, connexions=32, annule=None, journal=None,
                rapport_verif=None):
    """Télécharge url vers dest (taille `total` connue) par plages HTTP en parallèle. Reprend dest.part si présent."""
    rapport = rapport or (lambda fait: None)
    annule = annule or threading.Event()
    if os.path.isfile(dest) and os.path.getsize(dest) == total:
        return
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
    verrou = threading.Lock()
    compte = {"fait": sum(min(taille, total - i * taille) for i in faits)}
    if faits and journal:
        journal(f"reprise du téléchargement : {len(faits)}/{n} blocs déjà faits ({compte['fait'] / 2**30:.2f} Go)")
    rapport(compte["fait"])
    arret = threading.Event()

    def bloc(i):
        debut, fin = i * taille, min(total, (i + 1) * taille) - 1
        for essai in range(5):
            lus = 0
            try:
                with ouvrir(url, {"Range": f"bytes={debut}-{fin}"}) as r, open(part, "r+b") as f:
                    if r.status != 206:
                        raise OSError(f"le serveur ignore les plages (code {r.status})")
                    f.seek(debut)
                    while d := r.read(1 << 20):
                        if annule.is_set() or arret.is_set():
                            raise Interrompu()
                        f.write(d)
                        lus += len(d)
                        with verrou:
                            compte["fait"] += len(d)
                            rapport(compte["fait"])
                if lus != fin - debut + 1:
                    raise OSError("bloc incomplet")
                with verrou:
                    faits.add(i)
                    with open(etat_f, "w", encoding="utf-8") as f:
                        json.dump({"total": total, "taille": taille, "faits": sorted(faits)}, f)
                return
            except OSError as e:
                with verrou:
                    compte["fait"] -= lus
                if journal:
                    journal(f"bloc {i} essai {essai + 1} raté : {e}")
                if essai == 4:
                    raise
                time.sleep(1 + essai)

    with concurrent.futures.ThreadPoolExecutor(connexions) as ex:
        futurs = [ex.submit(bloc, i) for i in range(n) if i not in faits]
        try:
            for f in futurs:
                f.result()
        except BaseException:
            arret.set()
            raise
    if os.path.getsize(part) != total:
        raise OSError(f"taille incorrecte pour {os.path.basename(dest)}")
    if sha256:
        h, lu = hashlib.sha256(), 0
        with open(part, "rb") as f:
            while d := f.read(8 << 20):
                h.update(d)
                lu += len(d)
                (rapport_verif or rapport)(lu)
        if h.hexdigest() != sha256:
            os.remove(part)
            os.remove(etat_f)
            raise ErreurInstall("err.verification", f"sha256 différent pour {dest}")
    os.replace(part, dest)
    if os.path.isfile(etat_f):
        os.remove(etat_f)


def infos_comfyui(variante, journal):
    """(tag, nom d'archive, taille, sha256 ou None, url) pour la dernière version stable de ComfyUI."""
    nom = ARCHIVES[variante]
    try:
        r = json_get(DEPOT_API)
        a = next(x for x in r["assets"] if x["name"] == nom)
        sha = (a.get("digest") or "")
        sha = sha.split(":", 1)[1] if sha.startswith("sha256:") else None
        return r["tag_name"], nom, int(a["size"]), sha, a["browser_download_url"]
    except Exception as e:
        journal(f"API GitHub injoignable ({e}) : version de repli {REPLI['tag']}")
        taille, sha = REPLI[nom]
        return REPLI["tag"], nom, taille, sha, f"https://github.com/Comfy-Org/ComfyUI/releases/download/{REPLI['tag']}/{nom}"


# ---------------------------------------------------------------- extraction du .7z (Windows)
def _lancer_suivi(cmd, journal, annule, sondage):
    """Lance cmd sans fenêtre ; appelle sondage() ~ toutes les 2 s. Renvoie le code de sortie."""
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=SANS_FENETRE)
    sortie = []
    lecteur = threading.Thread(target=lambda: [sortie.append(b) for b in iter(lambda: p.stdout.read(256), b"")], daemon=True)
    lecteur.start()
    while p.poll() is None:
        if annule.is_set():
            p.kill()
            p.wait()
            raise Interrompu()
        sondage(b"".join(sortie[-4:]))
        time.sleep(2)
    lecteur.join(5)
    texte = b"".join(sortie).decode("utf-8", "replace")
    journal(f"{os.path.basename(cmd[0])} code {p.returncode}\n" + texte[-1500:])
    return p.returncode


def obtenir_7zr(dossier, journal, annule):
    exe = os.path.join(dossier, "7zr.exe")
    if not os.path.isfile(exe):
        os.makedirs(dossier, exist_ok=True)
        with ouvrir(URL_7ZR) as r:
            donnees = r.read(5 << 20)
        if not (300_000 < len(donnees) < 3_000_000 and donnees[:2] == b"MZ"):
            raise OSError("7zr.exe téléchargé invalide")
        sha = hashlib.sha256(donnees).hexdigest()
        if sha not in SHA_7ZR:
            # Jamais d'exécutable non vérifié : on refuse et on explique quoi faire.
            journal(f"7zr.exe : somme {sha} inconnue (7-zip a peut-être publié une nouvelle version), refusé")
            raise ErreurInstall("err.7zr_inconnu", sha)
        with open(exe, "wb") as f:
            f.write(donnees)
    return exe


def extraire_7z(archive, staging, attendu, rapport, journal, annule, ui_etape_outil=None):
    """Extrait le .7z dans staging : tar.exe de Windows (bsdtar) d'abord, 7zr.exe officiel en secours."""
    methodes = []
    tar = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe")
    if os.path.isfile(tar):
        methodes.append("tar")
    methodes.append("7zr")
    for m in methodes:
        shutil.rmtree(staging, ignore_errors=True)
        os.makedirs(staging)
        journal(f"extraction avec {m}")
        try:
            if m == "tar":
                cmd = [tar, "-xf", archive, "-C", staging]
                sonde = lambda _s: rapport(min(0.99, taille_dossier(staging) / attendu))
            else:
                if ui_etape_outil:
                    ui_etape_outil()
                exe = obtenir_7zr(os.path.join(os.path.dirname(archive), "outil7z"), journal, annule)
                cmd = [exe, "x", archive, f"-o{staging}", "-y", "-bsp1", "-bso0"]

                def sonde(s):
                    p = re.findall(rb"(\d+)%", s)
                    if p:
                        rapport(min(0.99, int(p[-1]) / 100))
            if _lancer_suivi(cmd, journal, annule, sonde) == 0 and os.listdir(staging):
                rapport(1.0)
                return m
        except (Interrompu, ErreurInstall):
            raise
        except Exception as e:
            journal(f"échec de {m} : {e}")
    raise ErreurInstall("err.extraction", "toutes les méthodes d'extraction ont échoué")


# ---------------------------------------------------------------- Stillive dans ComfyUI
PASSERELLE = '''"""Passerelle ComfyUI vers Stillive (écrite par l'installateur Stillive).

Tout le dossier stillive/ est ici : comfy_nodes/ (les nœuds), stillive/ (le moteur), web/ (les pages).
comfy_nodes importe le paquet `stillive` situé dans le dossier parent, c'est-à-dire ce dossier-ci."""
from .comfy_nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./comfy_nodes/js"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
'''


def est_lien(chemin):
    return os.path.islink(chemin) or os.path.realpath(chemin) != os.path.abspath(chemin)


def copier_stillive(source, comfy, journal):
    """Copie le code de Stillive dans custom_nodes. Renvoie 'neuf', 'maj' ou 'lien'. Garde réglages, films, modèles."""
    dest = os.path.join(comfy, "custom_nodes", NOM_DOSSIER_STILLIVE)
    if os.path.lexists(dest) and est_lien(dest):
        journal(f"{dest} est un lien : rien copié")
        return "lien"
    etat = "maj" if os.path.isdir(dest) else "neuf"
    os.makedirs(dest, exist_ok=True)
    ignorer = shutil.ignore_patterns("__pycache__", "*.pyc")
    for sous in ("comfy_nodes", "stillive", "web"):
        cible = os.path.join(dest, sous)
        if os.path.isdir(cible):
            shutil.rmtree(cible)
        shutil.copytree(os.path.join(source, sous), cible, ignore=ignorer)
    for f in ("requirements.txt", "README.md"):
        if os.path.isfile(os.path.join(source, f)):
            shutil.copy2(os.path.join(source, f), os.path.join(dest, f))
    os.makedirs(os.path.join(dest, "donnees"), exist_ok=True)
    with open(os.path.join(dest, "__init__.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(PASSERELLE)
    journal(f"Stillive copié dans {dest} ({etat})")
    return etat


def exigences(source_dest):
    """[(nom du paquet, ligne pip)] lues dans requirements.txt."""
    sortie = []
    try:
        with open(os.path.join(source_dest, "requirements.txt"), encoding="utf-8") as f:
            for ligne in f:
                ligne = ligne.split("#")[0].strip()
                m = re.match(r"([A-Za-z0-9_.\-]+)", ligne)
                if m:
                    sortie.append((m.group(1).lower(), ligne))
    except OSError:
        pass
    return sortie


def installer_outils(python, comfy, journal, annule, detail):
    """Installe dans le Python de ComfyUI (jamais le Python du système) ce qui manque dans requirements.txt."""
    dest = os.path.join(comfy, "custom_nodes", NOM_DOSSIER_STILLIVE)
    manque = []
    for nom, ligne in exigences(dest):
        module = MODULES.get(nom, nom.replace("-", "_"))
        r = subprocess.run([python, "-I", "-c", f"import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('{module}') else 1)"],
                           capture_output=True, creationflags=SANS_FENETRE)
        if r.returncode != 0:
            manque.append(ligne)
    journal(f"outils manquants : {manque or 'aucun'}")
    if manque:
        commande(python, ["-m", "pip", "install", "--disable-pip-version-check", *manque], journal, annule, detail, "pip stillive")
    return manque


def commande(python_ou_exe, args, journal, annule, detail, nom, cwd=None):
    """Exécute une commande en suivant sa dernière ligne de sortie ; lève ErreurInstall si elle échoue."""
    cmd = [python_ou_exe, *args]
    journal(f"commande ({nom}) : {' '.join(cmd)}")
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, cwd=cwd,
                         creationflags=SANS_FENETRE, text=True, encoding="utf-8", errors="replace")
    fin = []
    for ligne in p.stdout:
        ligne = ligne.rstrip()
        if ligne:
            fin.append(ligne)
            del fin[:-40]
            detail(ligne[:90])
        if annule.is_set():
            p.kill()
            p.wait()
            raise Interrompu()
    p.wait()
    journal(f"{nom} : code {p.returncode}\n" + "\n".join(fin[-15:]))
    if p.returncode != 0:
        raise ErreurInstall("err.commande", f"{nom} code {p.returncode}", etape=nom)


# ---------------------------------------------------------------- lanceurs
def chemin_bat(p, cible):
    """Chemin relatif au .bat (rangé dans `cible`) s'il est dedans, sinon absolu."""
    p, cible = os.path.abspath(p), os.path.abspath(cible)
    if os.path.normcase(p).startswith(os.path.normcase(cible) + os.sep):
        return "%~dp0" + os.path.relpath(p, cible)
    return p


def ecrire_lanceurs(plan, comfy, python, journal):
    """Écrit lancer_stillive.bat/.sh et arreter_stillive.*. Renvoie (lanceur, arret)."""
    cible = plan.cible
    if os.name == "nt":
        py = chemin_bat(python, cible) if python else "python"
        main = chemin_bat(os.path.join(comfy, "main.py"), cible)
        racine = chemin_bat(os.path.dirname(comfy) if os.path.basename(comfy) == "ComfyUI" else comfy, cible)
        portable = "--windows-standalone-build " if os.path.isdir(os.path.join(os.path.dirname(comfy), "python_embeded")) else ""
        bat = ("@echo off\r\nchcp 65001 >nul\r\nrem Lance ComfyUI avec Stillive. Ecrit par l'installateur Stillive.\r\n"
               f"cd /d \"{racine}\"\r\n"
               f"set STILLIVE_PORT={PORT_STILLIVE}\r\n"
               f"\"{py}\" -s \"{main}\" {portable}--port {plan.port}\r\n"
               "pause\r\n")
        arret = ("@echo off\r\nchcp 65001 >nul\r\nrem Arrete le ComfyUI demarre par l'installateur Stillive.\r\n"
                 "if not exist \"%~dp0stillive.pid\" (echo Aucun ComfyUI lance par l'installateur.& pause & exit /b)\r\n"
                 # Le numéro peut avoir été réattribué à un autre programme (après un redémarrage) :
                 # on n'arrête que si ce numéro est encore un processus python.
                 "for /f \"usebackq\" %%p in (\"%~dp0stillive.pid\") do ("
                 "tasklist /FI \"PID eq %%p\" /NH 2>nul | find /I \"python\" >nul "
                 "&& (taskkill /PID %%p /T /F) "
                 "|| (echo Ce numero n'est plus celui de ComfyUI, rien n'a ete arrete.))\r\n"
                 "del \"%~dp0stillive.pid\"\r\npause\r\n")
        l1, l2 = os.path.join(cible, "lancer_stillive.bat"), os.path.join(cible, "arreter_stillive.bat")
        for chemin, texte in ((l1, bat), (l2, arret)):
            with open(chemin, "w", encoding="utf-8", newline="") as f:
                f.write(texte)
    else:
        py = python or "python3"
        sh = ("#!/bin/sh\n# Lance ComfyUI avec Stillive. Écrit par l'installateur Stillive.\n"
              f"cd \"{comfy}\" || exit 1\nexport STILLIVE_PORT={PORT_STILLIVE}\n"
              f"exec \"{py}\" main.py --port {plan.port}\n")
        stop = ("#!/bin/sh\n# Arrête le ComfyUI démarré par l'installateur Stillive.\n"
                f"if [ -f \"{cible}/stillive.pid\" ]; then\n"
                f"  p=$(cat \"{cible}/stillive.pid\")\n"
                "  if ps -p \"$p\" -o comm= | grep -qi python; then kill \"$p\"; "
                "else echo \"Ce numéro n'est plus celui de ComfyUI, rien n'a été arrêté.\"; fi\n"
                f"  rm -f \"{cible}/stillive.pid\"\nfi\n")
        l1, l2 = os.path.join(cible, "lancer_stillive.sh"), os.path.join(cible, "arreter_stillive.sh")
        for chemin, texte in ((l1, sh), (l2, stop)):
            with open(chemin, "w", encoding="utf-8", newline="\n") as f:
                f.write(texte)
            os.chmod(chemin, 0o755)
    journal(f"lanceurs écrits : {l1}")
    return l1, l2


def creer_raccourci(plan, lanceur, journal):
    """Raccourci « Stillive » sur le bureau (ou dans plan.raccourci_dans pour les essais). Renvoie son chemin ou None."""
    try:
        if os.name == "nt":
            ps = lambda s: str(s).replace("'", "''")  # apostrophe dans un chemin (« O'Brien ») : doublée pour PowerShell
            if plan.raccourci_dans:
                os.makedirs(plan.raccourci_dans, exist_ok=True)
                dossier = f"'{ps(plan.raccourci_dans)}'"
            else:
                dossier = "[Environment]::GetFolderPath('Desktop')"
            script = (f"$d={dossier}; $s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'Stillive.lnk')); "
                      f"$s.TargetPath='{ps(lanceur)}'; $s.WorkingDirectory='{ps(os.path.dirname(lanceur))}'; "
                      "$s.Description='Stillive'; $s.Save(); Write-Output (Join-Path $d 'Stillive.lnk')")
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
                               capture_output=True, text=True, timeout=60, creationflags=SANS_FENETRE)
            chemin = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else None
            journal(f"raccourci : code {r.returncode} {chemin} {r.stderr.strip()[:200]}")
            return chemin if r.returncode == 0 and chemin and os.path.isfile(chemin) else None
        dossier = plan.raccourci_dans or os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(dossier) and not plan.raccourci_dans:
            return None
        os.makedirs(dossier, exist_ok=True)
        chemin = os.path.join(dossier, "Stillive.desktop")
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=Stillive\nExec=\"{lanceur}\"\nTerminal=true\nCategories=Graphics;\n")
        os.chmod(chemin, 0o755)
        return chemin
    except Exception as e:
        journal(f"raccourci impossible : {e}")
        return None


def demarrer_comfyui(plan, comfy, python, journal):
    """Démarre ComfyUI sans fenêtre console ; sortie dans comfyui.log ; PID dans stillive.pid."""
    cmd = [python or ("python" if os.name == "nt" else "python3"), "-s" if os.name == "nt" else "-u", os.path.join(comfy, "main.py"),
           "--port", str(plan.port), "--disable-auto-launch"]
    if os.name == "nt" and os.path.isdir(os.path.join(os.path.dirname(comfy), "python_embeded")):
        cmd.insert(3, "--windows-standalone-build")
    env = dict(os.environ, STILLIVE_PORT=str(PORT_STILLIVE))
    log = open(os.path.join(plan.cible, "comfyui.log"), "ab")
    kw = {"creationflags": SANS_FENETRE | 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    p = subprocess.Popen(cmd, cwd=comfy if os.name != "nt" else os.path.dirname(comfy), stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, env=env, **kw)
    with open(os.path.join(plan.cible, "stillive.pid"), "w") as f:
        f.write(str(p.pid))
    journal(f"ComfyUI démarré : pid {p.pid} {' '.join(cmd)}")
    return p


def repond(url):
    try:
        with ouvrir(url, timeout=3) as r:
            return r.status == 200
    except OSError:
        return False


# ---------------------------------------------------------------- plan et déroulement
class Plan:
    def __init__(self, cible, variante="auto", port=8188, lancer=True, source=None, essai=False,
                 stillive_seulement=None, raccourci_dans=None, langue="fr"):
        self.cible, self.variante, self.port, self.lancer = os.path.abspath(cible), variante, port, lancer
        self.source, self.essai, self.stillive_seulement = source, essai, stillive_seulement
        self.raccourci_dans = raccourci_dans
        self.langue = langue
        self.comfy_existant = None


def liste_etapes(plan, existant):
    e = ["verif", "place"]
    if not existant:
        if systeme() == "Windows":
            e += ["telecharge", "extrait"]
        else:
            e += ["linux_prereq", "linux_code", "linux_venv", "linux_torch", "linux_deps"]
    e += ["stillive", "outils", "lanceurs"]
    if systeme() == "Windows" and not existant:
        e += ["nettoyage"]
    if plan.lancer and not plan.essai and not existant:   # ComfyUI existant : jamais de deuxième ComfyUI lancé à côté
        e += ["lance"]
    return e


def executer(plan, ui, T, journal, annule):
    """Déroule l'installation. Lève ErreurInstall / Interrompu ; renvoie un dict de résultats pour l'écran final."""
    debut_total = time.time()
    existant = plan.comfy_existant
    etapes = liste_etapes(plan, existant)
    res = {"url": f"http://127.0.0.1:{PORT_STILLIVE}/web/ordinateur.html", "lance": False, "page": False}
    n, courante = len(etapes), [0]

    def etape(cle):
        courante[0] += 1
        ui.etape(courante[0], n, T("etape." + cle))
        journal(f"--- étape {courante[0]}/{n} : {cle}")
        res["_t"] = time.time()

    def fin_etape(cle):
        journal(f"étape {cle} terminée en {time.time() - res['_t']:.1f} s")

    def fraction(f):
        ui.progres(f, T("detail.pourcent", p=int(f * 100)))

    journal(f"=== Installateur Stillive {VERSION} : cible={plan.cible} variante={plan.variante} port={plan.port} "
            f"essai={plan.essai} existant={existant} système={systeme()}")

    if existant and repond(f"http://127.0.0.1:{plan.port}/system_stats"):
        ui.info(T("info.comfy_ouvert", port=plan.port))   # son ComfyUI est ouvert : il doit être relancé pour charger Stillive

    # 1. système
    etape("verif")
    ui.progres(None, "")
    c = verifier_systeme(T)
    variante = choisir_variante(c, plan.variante)
    ui.info(T("info.systeme", carte=c["nom"], vram=T.go(c["vram_mo"] / 1024)))
    journal(f"carte {c}")
    try:
        os.makedirs(plan.cible, exist_ok=True)
        test = os.path.join(plan.cible, ".ecriture_test")
        open(test, "w").close()
        os.remove(test)
    except OSError as e:
        raise ErreurInstall("err.dossier", str(e), dossier=plan.cible)
    if len(plan.cible) > 90 and os.name == "nt":
        ui.info(T("info.chemin_long"))
    try:
        if int(c["pilote"].split(".")[0]) < PILOTE_CUDA13 and variante == "cuda13" and systeme() == "Windows":
            ui.info(T("info.pilote_ancien", pilote=c["pilote"]))
    except ValueError:
        pass
    if existant:
        ui.info(T("info.existant", dossier=existant))
    else:
        ui.info(T("info.variante", nom=T("info.variante." + variante)))
    fin_etape("verif")

    # 2. place
    etape("place")
    archive_info = None
    if existant:
        besoin = 0.5
    elif systeme() == "Windows":
        archive_info = infos_comfyui(variante, journal)
        ui.info(T("info.version", tag=archive_info[0]))
        besoin = archive_info[2] / 2 ** 30 + EXTRAIT_GO
    else:
        besoin = LINUX_GO
    requis = round(besoin + MARGE_DISQUE_GO, 1)
    libre = disque_libre_go(plan.cible)
    journal(f"disque libre {libre} Go, requis {requis} Go")
    if libre is not None and libre < requis:
        raise ErreurInstall("err.disque", libre=T.go(libre), requis=T.go(requis))
    if libre is not None and not existant:
        # Archive + extraction + modèles téléchargés ensuite (~55 Go) + marge : sinon l'échec arriverait
        # après des heures de téléchargement. Bloquant, avec le chiffre à libérer.
        total = round(besoin + STILLIVE_GO + MARGE_DISQUE_GO, 1)
        if libre < total:
            raise ErreurInstall("err.disque_modeles", f"libre {libre} Go, total requis {total} Go",
                                libre=T.go(libre), total=T.go(total), manque=T.go(total - libre),
                                stillive=STILLIVE_GO)
    ui.info(T("info.place_ok", libre=T.go(libre) if libre is not None else "?", requis=T.go(requis)))
    if libre is not None and existant and libre - besoin < STILLIVE_GO + MARGE_DISQUE_GO:
        ui.info(T("avert.place_stillive", reste=T.go(max(0.0, libre - besoin)), stillive=STILLIVE_GO))
    fin_etape("place")

    source = trouver_source(plan.source)
    if source is None:
        raise ErreurInstall("err.source")

    comfy, python = existant, (trouver_python(existant) if existant else None)

    # 3. ComfyUI
    if not existant and systeme() == "Windows":
        tag, nom, taille, sha, url = archive_info
        archive = os.path.join(plan.cible, nom)
        etape("telecharge")
        t0 = time.time()
        deja = [0]

        def rapport(fait):
            if not deja[0]:
                deja[0] = fait
            vit = (fait - deja[0]) / max(1e-3, time.time() - t0) / 2 ** 20
            ui.progres(fait / taille, T("detail.telecharge", fait=T.go(fait / 2 ** 30), total=T.go(taille / 2 ** 30),
                                        vitesse=f"{vit:.0f}"))
        if os.path.exists(archive + ".part.json"):
            ui.info(T("detail.reprise", fait=T.go(os.path.getsize(archive + ".part") / 2 ** 30 if os.path.exists(archive + ".part") else 0)))
        try:
            telecharger(url, archive, taille, sha, rapport, 32, annule, journal,
                        rapport_verif=lambda lu: ui.progres(lu / taille, T("etape.verifie")))
        except (OSError, urllib.error.URLError) as e:
            journal(f"téléchargement échoué : {e}")
            raise ErreurInstall("err.telechargement", str(e))
        journal(f"téléchargement {taille / 2**30:.2f} Go en {time.time() - t0:.0f} s (sha256 {'vérifié' if sha else 'non disponible'})")
        fin_etape("telecharge")

        etape("extrait")
        staging = os.path.join(plan.cible, ".extraction")
        attendu = EXTRAIT_GO * 2 ** 30
        methode = extraire_7z(archive, staging, attendu, fraction, journal, annule,
                              ui_etape_outil=lambda: ui.info(T("etape.outil7z")))
        racine = next((d for d in os.listdir(staging) if os.path.isdir(os.path.join(staging, d))), None)
        if racine is None:
            raise ErreurInstall("err.extraction", "archive vide")
        final = os.path.join(plan.cible, racine)
        if os.path.exists(final):
            shutil.rmtree(final)
        os.replace(os.path.join(staging, racine), final)
        shutil.rmtree(staging, ignore_errors=True)
        comfy = os.path.join(final, "ComfyUI")
        python = os.path.join(final, "python_embeded", "python.exe")
        journal(f"ComfyUI installé dans {final} (extraction {methode}) : {taille_dossier(final) / 2**30:.2f} Go")
        if not (os.path.isfile(os.path.join(comfy, "main.py")) and os.path.isfile(python)):
            raise ErreurInstall("err.extraction", "structure inattendue")
        fin_etape("extrait")
    elif not existant:
        comfy, python = installer_linux(plan, variante, ui, T, journal, annule, etape, fin_etape)

    # 4. Stillive
    etape("stillive")
    ui.progres(None, "")
    etat = copier_stillive(source, comfy, journal)
    if etat == "lien":
        ui.info(T("info.deja_present"))
    elif etat == "maj":
        ui.info(T("info.mise_a_jour"))
    fin_etape("stillive")

    # 5. outils
    etape("outils")
    ui.progres(None, "")
    if python and etat != "lien":
        manque = installer_outils(python, comfy, journal, annule, lambda s: ui.progres(None, s))
        if not manque:
            ui.info(T("info.outil_ok"))
    fin_etape("outils")

    # 6. lanceurs
    etape("lanceurs")
    lanceur, arret = ecrire_lanceurs(plan, comfy, python, journal)
    ui.info(T("info.lanceur", chemin=lanceur))
    raccourci = None if (plan.essai and not plan.raccourci_dans) else creer_raccourci(plan, lanceur, journal)
    if raccourci:
        ui.info(T("info.raccourci", chemin=raccourci))
    elif not (plan.essai and not plan.raccourci_dans):
        ui.info(T("info.raccourci_absent", chemin=lanceur))
    res.update(lanceur=lanceur, arret=arret, raccourci=raccourci, comfy=comfy)
    fin_etape("lanceurs")

    # 7. nettoyage
    if systeme() == "Windows" and not existant:
        etape("nettoyage")
        for f in (archive, archive + ".part", archive + ".part.json"):
            if os.path.exists(f):
                os.remove(f)
        shutil.rmtree(os.path.join(plan.cible, "outil7z"), ignore_errors=True)
        fin_etape("nettoyage")

    # 8. lancement
    if plan.lancer and not plan.essai and not existant:   # ComfyUI existant : jamais de deuxième ComfyUI lancé à côté
        etape("lance")
        ui.progres(None, T("info.attente"))
        proc = demarrer_comfyui(plan, comfy, python, journal)
        res["lance"] = True
        debut = time.time()
        page = False
        port_connu = False
        while time.time() - debut < 900:
            if annule.is_set():
                raise Interrompu()
            if proc.poll() is not None:
                raise ErreurInstall("err.lancement", f"code {proc.returncode}", journal=os.path.join(plan.cible, "comfyui.log"))
            if not port_connu:   # port réel de Stillive, donné par ComfyUI (il peut avoir changé si 8765 était pris)
                try:
                    with ouvrir(f"http://127.0.0.1:{plan.port}/stillive_port", timeout=3) as r:
                        n = int(json.loads(r.read())["port"])
                    res["url"] = f"http://127.0.0.1:{n}/web/ordinateur.html"
                    port_connu = True
                except Exception:
                    pass
            if port_connu and repond(res["url"]):
                page = True
                break
            time.sleep(2)
        res["page"] = page
        if page:
            ui.info(T("info.ouverture", url=res["url"]))
            webbrowser.open(res["url"])
        fin_etape("lance")
    journal(f"=== terminé en {time.time() - debut_total:.0f} s")
    return res


# ---------------------------------------------------------------- Linux
def trouver_python_linux():
    for nom in ("python3.13", "python3.12", "python3.11", "python3.10", "python3"):
        p = shutil.which(nom)
        if p:
            r = subprocess.run([p, "-c", "import sys;print(sys.version_info>=(3,10))"], capture_output=True, text=True)
            if r.stdout.strip() == "True":
                return p
    return None


def installer_linux(plan, variante, ui, T, journal, annule, etape, fin_etape):
    """Voie officielle sans paquet portable : code de ComfyUI + venv + torch CUDA + requirements (README de ComfyUI)."""
    etape("linux_prereq")
    py = trouver_python_linux()
    if py is None:
        raise ErreurInstall("err.linux_prereq", "python3 >= 3.10 introuvable", outil="python3")
    git = shutil.which("git")
    journal(f"python {py}, git {git}")
    fin_etape("linux_prereq")

    comfy = os.path.join(plan.cible, "ComfyUI")
    etape("linux_code")
    ui.progres(None, "")
    tag = infos_comfyui_tag(journal)
    ui.info(T("info.version", tag=tag))
    if not os.path.isfile(os.path.join(comfy, "main.py")):
        shutil.rmtree(comfy, ignore_errors=True)
        if git:
            commande(git, ["clone", "--depth", "1", "--branch", tag, "https://github.com/Comfy-Org/ComfyUI.git", comfy],
                     journal, annule, lambda s: ui.progres(None, s), "git clone")
        else:
            telecharger_tarball(f"https://github.com/Comfy-Org/ComfyUI/archive/refs/tags/{tag}.tar.gz", comfy, plan.cible, ui, journal, annule)
    fin_etape("linux_code")

    etape("linux_venv")
    ui.progres(None, "")
    venv = os.path.join(comfy, "venv")
    try:
        commande(py, ["-m", "venv", venv], journal, annule, lambda s: ui.progres(None, s), "venv")
    except ErreurInstall:
        raise ErreurInstall("err.linux_prereq", "module venv absent", outil="python3-venv")
    python = os.path.join(venv, "bin", "python")
    fin_etape("linux_venv")

    etape("linux_torch")
    commande(python, ["-m", "pip", "install", "--disable-pip-version-check", "torch", "torchvision", "torchaudio",
                      "--extra-index-url", TORCH_INDEX[variante]], journal, annule, lambda s: ui.progres(None, s), "pip torch")
    fin_etape("linux_torch")

    etape("linux_deps")
    commande(python, ["-m", "pip", "install", "--disable-pip-version-check", "-r", os.path.join(comfy, "requirements.txt")],
             journal, annule, lambda s: ui.progres(None, s), "pip requirements")
    fin_etape("linux_deps")
    return comfy, python


def infos_comfyui_tag(journal):
    try:
        return json_get(DEPOT_API)["tag_name"]
    except Exception as e:
        journal(f"API GitHub injoignable ({e}) : version de repli {REPLI['tag']}")
        return REPLI["tag"]


def telecharger_tarball(url, comfy, cible, ui, journal, annule):
    """Sans Git : archive du dépôt à la version choisie, extraite sans le dossier racine."""
    tmp = os.path.join(cible, "comfyui.tar.gz")
    try:
        with ouvrir(url, timeout=120) as r, open(tmp, "wb") as f:
            while d := r.read(1 << 20):
                if annule.is_set():
                    raise Interrompu()
                f.write(d)
        os.makedirs(comfy, exist_ok=True)
        with tarfile.open(tmp) as t:
            membres = t.getmembers()
            for m in membres:
                m.name = m.name.split("/", 1)[1] if "/" in m.name else ""
            t.extractall(comfy, members=[m for m in membres if m.name], **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
    except (OSError, tarfile.TarError) as e:
        raise ErreurInstall("err.telechargement", str(e))
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


# ---------------------------------------------------------------- interfaces
class UIConsole:
    def __init__(self, T):
        self.T, self.derniere = T, 0

    def etape(self, i, n, texte):
        print(f"\n[{self.T('etape.titre', i=i, n=n)}] {texte}", flush=True)

    def progres(self, f, detail):
        if time.time() - self.derniere < 1 and f != 1:
            return
        self.derniere = time.time()
        barre = f"{int(f * 100):3d} % " if f is not None else ""
        print(f"\r  {barre}{detail}".ljust(100), end="", flush=True)

    def info(self, msg):
        print(f"\n  {msg}", flush=True)


class UIFenetre:
    """File d'événements : le fil de travail écrit, la fenêtre lit (tkinter n'aime pas les autres fils)."""

    def __init__(self):
        self.q = queue.Queue()

    def etape(self, i, n, texte):
        self.q.put(("etape", i, n, texte))

    def progres(self, f, detail):
        self.q.put(("progres", f, detail))

    def info(self, msg):
        self.q.put(("info", msg))


def demander_console(question, defaut=True):
    r = input(f"{question} [{'O/n' if defaut else 'o/N'}] ").strip().lower()
    return defaut if not r else r[0] in "oOyY".lower()


def lancer_travail(plan, ui, T, journal, annule, fini):
    def corps():
        try:
            res = executer(plan, ui, T, journal, annule)
            fini("ok", res)
        except Interrompu:
            journal("interrompu par l'utilisateur")
            fini("interrompu", None)
        except ErreurInstall as e:
            journal(f"ERREUR {e.cle} : {e.detail}")
            p = dict(e.params)
            p.setdefault("journal", journal.chemin)
            fini("erreur", T(e.cle, **{k: v for k, v in p.items()}))
        except Exception as e:  # imprévu
            import traceback
            journal("ERREUR imprévue : " + traceback.format_exc())
            fini("erreur", T("err.generique", detail=str(e)[:200]))
    t = threading.Thread(target=corps, name="installation", daemon=True)
    t.start()
    return t


def texte_final(T, res, plan):
    sav = STILLIVE_GO
    if plan.comfy_existant:
        t = T("fin.existant", comfy=plan.comfy_existant, url=res["url"], stillive=sav)
    elif res.get("lance"):
        t = T("fin.texte", stillive=sav) if res.get("page") else T("fin.page_non_ouverte", url=res["url"])
        t += "\n\n" + T("fin.arret", chemin=res["arret"])
    else:
        t = T("fin.texte_sans_lancer", chemin=res["lanceur"], url=res["url"], stillive=sav)
    if not res.get("raccourci") and not plan.essai:
        t += "\n\n" + T("fin.sans_raccourci", chemin=res["lanceur"])
    return t


def mode_console(args, T, journal):
    plan = plan_depuis(args)
    print(T("titre"), "-", VERSION)
    if not args.auto and not args.essai:
        print(T("accueil.dossier", dossier=plan.cible))
        if not demander_console(T("bouton.installer") + " ?"):
            return 1
    if plan.comfy_existant is None:
        plan.comfy_existant = trouver_comfyui(plan.cible)
        if plan.comfy_existant and not args.auto and not args.essai:
            if not demander_console(T("existant.texte", dossier=plan.comfy_existant, go=STILLIVE_GO) + "\n" + T("existant.titre") + " ?"):
                return 1
    ui, annule = UIConsole(T), threading.Event()
    resultat = {}
    fin = threading.Event()
    t = lancer_travail(plan, ui, T, journal, annule, lambda k, v: (resultat.update(k=k, v=v), fin.set()))
    try:
        while not fin.wait(0.5):
            pass
    except KeyboardInterrupt:
        annule.set()
        fin.wait(30)
    print()
    if resultat.get("k") == "ok":
        print("\n" + T("fin.titre") + "\n" + texte_final(T, resultat["v"], plan))
        return 0
    if resultat.get("k") == "interrompu":
        print(T("interrompu.texte"))
        return 2
    print("\n" + T("erreur.titre") + "\n" + str(resultat.get("v")) + "\n" + T("erreur.reprise", journal=journal.chemin))
    return 1


def dossier_defaut():
    return os.path.join(os.path.expanduser("~"), "Stillive")


def plan_depuis(args):
    cible = args.essai or args.dossier or dossier_defaut()
    plan = Plan(cible, args.variante, args.port, not args.sans_lancer and not args.essai, args.source, bool(args.essai),
                raccourci_dans=args.raccourci_dans, langue=args.langue)
    return plan


class Fenetre:
    def __init__(self, args, T, journal):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
        self.tk, self.ttk, self.fd, self.mb = tk, ttk, filedialog, messagebox
        self.args, self.T, self.journal = args, T, journal
        self.racine = tk.Tk()
        self.racine.geometry("680x520")
        self.racine.minsize(600, 480)
        self.racine.protocol("WM_DELETE_WINDOW", self.fermer)
        self.dossier = tk.StringVar(value=args.essai or args.dossier or dossier_defaut())
        self.variante = tk.StringVar(value=args.variante)
        self.port = tk.StringVar(value=str(args.port))
        self.sans_lancer = tk.BooleanVar(value=bool(args.sans_lancer))
        self.annule = threading.Event()
        self.ui = None
        self.travail = None
        self.termine = None
        self.cadre = None
        self.accueil()

    # -- utilitaires
    def vider(self):
        if self.cadre is not None:
            self.cadre.destroy()
        self.racine.title(self.T("titre"))
        self.cadre = self.ttk.Frame(self.racine, padding=24)
        self.cadre.pack(fill="both", expand=True)
        return self.cadre

    def texte(self, parent, t, taille=10, gras=False, **kw):
        l = self.ttk.Label(parent, text=t, wraplength=600, justify="left",
                           font=("Segoe UI" if os.name == "nt" else "TkDefaultFont", taille, "bold" if gras else "normal"), **kw)
        l.pack(anchor="w", pady=(0, 10))
        return l

    def bouton_langue(self, parent):
        autre = "en" if self.T.langue == "fr" else "fr"
        self.ttk.Button(parent, text=self.T("langue.autre"), command=lambda: self.changer_langue(autre)).pack(anchor="e")

    def changer_langue(self, code):
        self.T = Textes(code)
        self.accueil()

    def fermer(self):
        if self.travail is not None and self.travail.is_alive():
            self.annule.set()
            self.racine.after(300, self.fermer)
            return
        self.racine.destroy()

    # -- écrans
    def accueil(self):
        T = self.T
        c = self.vider()
        self.bouton_langue(c)
        self.texte(c, T("accueil.titre"), 20, True)
        self.texte(c, T("accueil.texte"), 11)
        self.lbl_dossier = self.texte(c, T("accueil.dossier", dossier=self.dossier.get()), 10)
        self.texte(c, T("accueil.place", go=int(round(2 + EXTRAIT_GO + MARGE_DISQUE_GO)), stillive=STILLIVE_GO), 10)
        self.ttk.Button(c, text=T("bouton.installer"), command=self.lancer, padding=14).pack(pady=24, fill="x")
        self.ttk.Button(c, text=T("bouton.avance"), command=self.avancee).pack(anchor="w")

    def avancee(self):
        T, tk, ttk = self.T, self.tk, self.ttk
        c = self.vider()
        self.texte(c, T("avance.titre"), 18, True)
        self.texte(c, T("avance.dossier"), 10, True)
        ligne = ttk.Frame(c)
        ligne.pack(fill="x", pady=(0, 12))
        ttk.Entry(ligne, textvariable=self.dossier).pack(side="left", fill="x", expand=True)
        ttk.Button(ligne, text=T("bouton.parcourir"), command=self.parcourir).pack(side="left", padx=(8, 0))
        self.texte(c, T("avance.variante"), 10, True)
        for val in ("auto", "cuda13", "cu126"):
            ttk.Radiobutton(c, text=T("avance.variante." + val), variable=self.variante, value=val).pack(anchor="w")
        self.texte(c, "", 4)
        self.texte(c, T("avance.port"), 10, True)
        ttk.Entry(c, textvariable=self.port, width=8).pack(anchor="w", pady=(0, 12))
        ttk.Checkbutton(c, text=T("avance.sans_lancer"), variable=self.sans_lancer).pack(anchor="w", pady=(0, 16))
        bas = ttk.Frame(c)
        bas.pack(fill="x", side="bottom")
        ttk.Button(bas, text=T("bouton.express"), command=self.accueil).pack(side="left")
        ttk.Button(bas, text=T("avance.installer"), command=self.lancer, padding=8).pack(side="right")

    def parcourir(self):
        d = self.fd.askdirectory(initialdir=self.dossier.get() if os.path.isdir(self.dossier.get()) else None)
        if d:
            self.dossier.set(os.path.normpath(d))

    def lancer(self):
        T = self.T
        try:
            port = int(self.port.get())
            if not 1024 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.mb.showerror(T("titre"), T("err.port"))
            return
        a = self.args
        plan = Plan(self.dossier.get().strip(), self.variante.get(), port, not self.sans_lancer.get() and not a.essai,
                    a.source, bool(a.essai), raccourci_dans=a.raccourci_dans, langue=T.langue)
        existant = trouver_comfyui(plan.cible)
        if existant:
            if not self.mb.askyesno(T("existant.titre"), T("existant.texte", dossier=existant, go=STILLIVE_GO)):
                return
            plan.comfy_existant = existant
        self.progression(plan)

    def progression(self, plan):
        T, ttk = self.T, self.ttk
        c = self.vider()
        self.lbl_etape = self.texte(c, "", 11, True)
        self.lbl_nom = self.texte(c, "", 13)
        self.barre = ttk.Progressbar(c, length=600, mode="determinate", maximum=1000)
        self.barre.pack(fill="x", pady=(0, 6))
        self.lbl_detail = self.texte(c, "", 9)
        self.lbl_infos = self.texte(c, "", 9)
        self.infos = []
        self.btn = ttk.Button(c, text=T("bouton.annuler"), command=self.annuler)
        self.btn.pack(side="bottom", anchor="e")
        self.indetermine = False
        self.annule = threading.Event()
        self.ui = UIFenetre()
        self.travail = lancer_travail(plan, self.ui, T, self.journal, self.annule,
                                      lambda k, v: self.ui.q.put(("fin", k, v)))
        self.plan = plan
        self.racine.after(100, self.sonder)

    def annuler(self):
        self.annule.set()
        self.btn.state(["disabled"])

    def sonder(self):
        try:
            while True:
                m = self.ui.q.get_nowait()
                if m[0] == "etape":
                    self.lbl_etape.config(text=self.T("etape.titre", i=m[1], n=m[2]))
                    self.lbl_nom.config(text=m[3])
                elif m[0] == "progres":
                    if m[1] is None:
                        if not self.indetermine:
                            self.barre.config(mode="indeterminate")
                            self.barre.start(15)
                            self.indetermine = True
                    else:
                        if self.indetermine:
                            self.barre.stop()
                            self.barre.config(mode="determinate")
                            self.indetermine = False
                        self.barre["value"] = int(m[1] * 1000)
                    self.lbl_detail.config(text=m[2])
                elif m[0] == "info":
                    self.infos = (self.infos + [m[1]])[-4:]
                    self.lbl_infos.config(text="\n".join(self.infos))
                elif m[0] == "fin":
                    self.fin(m[1], m[2])
                    return
        except queue.Empty:
            pass
        self.racine.after(100, self.sonder)

    def fin(self, kind, valeur):
        T = self.T
        if self.indetermine:
            self.barre.stop()
        c = self.vider()
        if kind == "ok":
            self.texte(c, T("fin.titre"), 20, True)
            self.texte(c, texte_final(T, valeur, self.plan), 11)
            self.texte(c, "\n".join(self.infos), 9)
        elif kind == "interrompu":
            self.texte(c, T("interrompu.titre"), 20, True)
            self.texte(c, T("interrompu.texte"), 11)
        else:
            self.texte(c, T("erreur.titre"), 20, True)
            self.texte(c, valeur, 11)
            self.texte(c, T("erreur.reprise", journal=self.journal.chemin), 9)
        self.termine = (kind, valeur)
        bas = self.ttk.Frame(c)
        bas.pack(side="bottom", fill="x")
        self.ttk.Button(bas, text=T("bouton.fermer"), command=self.racine.destroy).pack(side="right")
        if kind != "ok":
            self.ttk.Button(bas, text=T("bouton.recommencer"), command=self.accueil).pack(side="left")

    def boucle(self):
        self.racine.mainloop()


# ---------------------------------------------------------------- point d'entrée
def lire_args(argv):
    p = argparse.ArgumentParser(description="Installateur Stillive")
    p.add_argument("--essai", metavar="DOSSIER", help="installe dans ce dossier, sans raccourci sur le bureau ni lancement")
    p.add_argument("--sans-lancer", action="store_true", help="ne démarre pas ComfyUI à la fin")
    p.add_argument("--console", action="store_true", help="pas de fenêtre : mêmes étapes dans la console")
    p.add_argument("--auto", action="store_true", help="console, sans aucune question (Stillive seul si ComfyUI est déjà là)")
    p.add_argument("--langue", choices=("fr", "en"), default=langue_systeme())
    p.add_argument("--dossier", help="dossier d'installation")
    p.add_argument("--variante", choices=("auto", "cuda13", "cu126"), default="auto")
    p.add_argument("--port", type=int, default=8188, help="port de ComfyUI")
    p.add_argument("--source", help="dossier stillive/ à installer (par défaut : à côté de l'installateur)")
    p.add_argument("--raccourci-dans", help="(essai) crée le raccourci dans ce dossier au lieu du bureau")
    p.add_argument("--simuler-disque", type=float, metavar="GO", help="(essai) fait croire à cette place libre")
    p.add_argument("--simuler-systeme", metavar="NOM", help="(essai) Windows, Linux, Darwin...")
    p.add_argument("--simuler-carte", metavar="NOM:VRAM_MO:PILOTE[:CAP]", help="(essai) ou « aucune »")
    return p.parse_args(argv)


def principal(argv=None):
    args = lire_args(argv)
    if args.simuler_disque is not None:
        SIM["disque_go"] = args.simuler_disque
    if args.simuler_systeme:
        SIM["systeme"] = args.simuler_systeme
    if args.simuler_carte:
        SIM["carte"] = args.simuler_carte
    T = Textes(args.langue)
    try:
        journal = Journal(os.path.join(DOSSIER_LOG, "installation.log"))
        journal("démarrage")
    except Exception:
        import tempfile
        journal = Journal(os.path.join(tempfile.gettempdir(), "installation_stillive.log"))
        journal("démarrage (journal dans le dossier temporaire : le dossier de l'installateur est en lecture seule)")
    if args.auto or args.console:
        return mode_console(args, T, journal)
    try:
        f = Fenetre(args, T, journal)
    except Exception as e:  # tkinter absent ou pas d'écran : repli console
        journal(f"fenêtre impossible ({e}) : repli console")
        print(f"(pas de fenêtre : {e})")
        return mode_console(args, T, journal)
    f.boucle()
    return 0


if __name__ == "__main__":
    sys.exit(principal())
