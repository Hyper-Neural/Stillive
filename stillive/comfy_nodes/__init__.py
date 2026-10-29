"""Extension ComfyUI Stillive : modèle GGUF vision par le llama-server officiel de llama.cpp.

Le serveur est lancé en sous-process par le nœud, réutilisé d'un appel à l'autre,
et arrêté par le nœud StilliveLlamaStop ou à la sortie de ComfyUI.
"""
import atexit
import base64
import http.client
import io
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlparse

CATEGORIE = "Stillive"

# État global du serveur : process, port, clé de réglages, fichier journal
_serveur = {"proc": None, "port": None, "cle": None, "journal": None}


def _fin_journal(chemin, n=30):
    try:
        with open(chemin, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return "(journal illisible)"


def _port_libre():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _arreter_serveur():
    proc = _serveur["proc"]
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    _serveur.update(proc=None, port=None, cle=None)
    try:
        os.remove(_FICHIER_PID)
    except OSError:
        pass
    journal = _serveur.get("journal")
    if journal:
        try:
            os.remove(journal)
        except OSError:
            pass
        _serveur["journal"] = None


atexit.register(_arreter_serveur)

# Fichier PID : si ComfyUI est tué brutalement, le llama-server orphelin est retrouvé au lancement suivant.
_FICHIER_PID = os.path.join(tempfile.gettempdir(), "stillive_llama_pid.json")


def _chemin_processus(pid):
    """Chemin de l'exécutable du processus, ou None s'il n'existe plus (ou illisible)."""
    try:
        import psutil
        return psutil.Process(pid).exe()
    except ImportError:
        pass
    except Exception:
        return None
    try:
        if os.name == "nt":
            sortie = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"(Get-Process -Id {int(pid)}).Path"],
                capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return sortie.stdout.strip() or None
        return os.readlink(f"/proc/{int(pid)}/exe")
    except Exception:
        return None


def _ecrire_pid(pid, exe):
    try:
        with open(_FICHIER_PID, "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "exe": os.path.abspath(exe)}, f)
    except OSError:
        pass


def _nettoyer_orphelin():
    """Tue le llama-server d'une session précédente, seulement si le PID existe encore ET que son
    exécutable est exactement celui qu'on avait lancé (jamais le llama-server d'un autre programme)."""
    try:
        with open(_FICHIER_PID, encoding="utf-8") as f:
            info = json.load(f)
        pid, exe = int(info["pid"]), info["exe"]
        courant = _chemin_processus(pid)
        if courant and os.path.normcase(os.path.abspath(courant)) == os.path.normcase(os.path.abspath(exe)):
            import signal
            os.kill(pid, signal.SIGTERM)
            print(f"[Stillive] llama-server orphelin (PID {pid}) arrêté.")
    except Exception:
        pass
    try:
        os.remove(_FICHIER_PID)
    except OSError:
        pass


_nettoyer_orphelin()


def _liberer_carte():
    try:
        import comfy.model_management as mm
        mm.unload_all_models()
        mm.soft_empty_cache()
    except Exception as e:
        print(f"[Stillive] libération de la carte graphique impossible : {e}")


def _sante_ok(port):
    try:
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        c.request("GET", "/health")
        ok = c.getresponse().status == 200
        c.close()
        return ok
    except OSError:
        return False


def _interruption():
    """Lève l'exception d'annulation de ComfyUI si l'utilisateur a cliqué sur Annuler (sans effet hors ComfyUI)."""
    try:
        import comfy.model_management as mm
        mm.throw_exception_if_processing_interrupted()
    except ImportError:
        pass


class _Mort(RuntimeError):
    """llama-server s'est arrêté pendant son démarrage."""


def _assurer_serveur(llama_server, model, mmproj, ctx, mtp=True, brouillon="", vision_gpu=False):
    cle = (llama_server, model, mmproj, ctx, brouillon, vision_gpu)
    proc = _serveur["proc"]
    if proc is not None and proc.poll() is None and _serveur["cle"] == cle:
        return _serveur["port"]
    _arreter_serveur()

    if not os.path.isfile(llama_server):
        raise RuntimeError(f"llama-server introuvable : {llama_server}")
    if not os.path.isfile(model):
        raise RuntimeError(f"modèle GGUF introuvable : {model}")
    if mmproj and not os.path.isfile(mmproj):
        raise RuntimeError(f"mmproj introuvable : {mmproj}")

    _liberer_carte()
    port = _port_libre()
    cmd = [llama_server, "--host", "127.0.0.1", "--port", str(port), "--model", model]
    if mmproj:
        cmd += ["--mmproj", mmproj] + ([] if vision_gpu else ["--no-mmproj-offload"])
    cmd += [
        "--ctx-size", str(ctx), "--jinja", "--flash-attn", "on",
        "--n-gpu-layers", "99", "--parallel", "1",
        "--batch-size", "2048", "--ubatch-size", "512",
        "--cache-type-k", "q8_0", "--cache-type-v", "q8_0",
        "--cache-ram", "16384",
        "--min-p", "0.0", "--top-k", "20", "--repeat-penalty", "1.0",
        "--no-prefill-assistant",
    ]
    # MTP (décodage spéculatif) : si cette version de llama-server le refuse, on relance sans
    spec = ["--spec-type", "draft-mtp", "--spec-draft-n-max", "2", "--draft-p-min", "0.75"]
    if brouillon and os.path.isfile(brouillon):  # Gemma 4 : tête MTP dans un fichier à part
        spec = ["--model-draft", brouillon, "--spec-type", "draft-mtp", "--spec-draft-n-max", "4"]
    essais = [True, False] if mtp and not _serveur.get("sans_mtp") else [False]
    for avec_mtp in essais:
        try:
            return _demarrer(cmd + (spec if avec_mtp else []), cle)
        except _Mort as e:
            if not avec_mtp:
                raise RuntimeError(str(e)) from None
            print("[Stillive] llama-server s'est arrêté avec le MTP : nouvel essai sans MTP (un peu plus lent)")
            _serveur["sans_mtp"] = True


def _demarrer(cmd, cle):
    port = int(cmd[cmd.index("--port") + 1])
    fd, journal = tempfile.mkstemp(prefix="stillive_llama_", suffix=".log")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    with os.fdopen(fd, "wb") as sortie:
        proc = subprocess.Popen(cmd, stdout=sortie, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
    _serveur.update(proc=proc, port=port, cle=None, journal=journal)
    _ecrire_pid(proc.pid, cmd[0])

    debut = time.time()
    while time.time() - debut < 600:
        try:
            _interruption()
        except BaseException:
            _arreter_serveur()  # Annuler : on ne laisse pas llama-server occuper la carte
            raise
        if proc.poll() is not None:
            fin = _fin_journal(journal)
            _arreter_serveur()
            raise _Mort(f"llama-server s'est arrêté (code {proc.returncode}) :\n{fin}")
        if _sante_ok(port):
            _serveur["cle"] = cle
            print(f"[Stillive] llama-server prêt sur le port {port} en {time.time() - debut:.0f} s")
            return port
        time.sleep(1)
    fin = _fin_journal(journal)
    _arreter_serveur()
    raise RuntimeError(f"llama-server pas prêt après 600 s :\n{fin}")


def _image_en_data_url(image):
    import numpy as np
    from PIL import Image
    arr = image[0].detach().cpu().numpy()
    arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype("uint8")
    if arr.shape[-1] == 1:
        arr = arr[..., 0]
    tampon = io.BytesIO()
    Image.fromarray(arr).save(tampon, format="PNG")
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode("ascii")


class _ErreurHTTP(RuntimeError):
    def __init__(self, status, texte):
        super().__init__(f"{status} {texte}")
        self.status = status


def _connexion(port, url):
    """Connexion vers llama-server local (port) ou vers l'url d'un autre programme. Renvoie (connexion, préfixe de chemin)."""
    if not url:
        # llama-server ne dit rien tant qu'il traite l'image et le long prompt : délai long, comme les autres modes
        return http.client.HTTPConnection("127.0.0.1", port, timeout=900), ""
    u = urlparse(url.strip())
    classe = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    # Un gros modèle en cours de chargement peut mettre plusieurs minutes à répondre : délai long
    return classe(u.hostname, u.port, timeout=900), re.sub(r"(/v1)?/*$", "", u.path)


def _requete(port, corps, plafond_s, url=None, ollama=False):
    """Envoie la requête en flux. Renvoie (réflexion, réponse, durée de réflexion, coupée).
    url vide : llama-server local. Sinon : API OpenAI (/v1) de ce programme, ou API native d'Ollama."""
    debut = time.time()
    conn, prefixe = _connexion(port, url)
    reflexion, reponse = [], []
    t_reflexion = None
    coupee = False
    try:
        donnees = json.dumps(corps).encode("utf-8")
        conn.request("POST", prefixe + ("/api/chat" if ollama else "/v1/chat/completions"), body=donnees,
                     headers={"Content-Type": "application/json"})
        rep = conn.getresponse()
        if rep.status != 200:
            raise _ErreurHTTP(rep.status, rep.read().decode('utf-8', 'replace')[:2000])
        while True:
            _interruption()
            ligne = rep.readline()
            if not ligne:
                break
            ligne = ligne.decode("utf-8", "replace").strip()
            if ollama and ligne:
                try:
                    morceau = json.loads(ligne)
                except ValueError:
                    continue
                if morceau.get("error"):
                    raise RuntimeError(f"Ollama : {morceau['error']}")
                msg = morceau.get("message") or {}
                if msg.get("thinking"):
                    reflexion.append(msg["thinking"])
                if msg.get("content"):
                    if t_reflexion is None:
                        t_reflexion = time.time() - debut
                    reponse.append(msg["content"])
                if morceau.get("done"):
                    break
            elif ligne.startswith("data:"):
                charge = ligne[5:].strip()
                if charge == "[DONE]":
                    break
                try:
                    choix = json.loads(charge)["choices"][0]
                except (ValueError, KeyError, IndexError):
                    continue
                delta = choix.get("delta") or {}
                pensee = delta.get("reasoning_content") or delta.get("reasoning")
                if pensee:
                    reflexion.append(pensee)
                if delta.get("content"):
                    if t_reflexion is None:
                        t_reflexion = time.time() - debut
                    reponse.append(delta["content"])
            penser = corps.get("think") if ollama else corps.get("chat_template_kwargs", {}).get("enable_thinking")
            if penser and not reponse and time.time() - debut > plafond_s:
                coupee = True
                break
    finally:
        conn.close()
    if t_reflexion is None:
        t_reflexion = time.time() - debut
    return "".join(reflexion), "".join(reponse), t_reflexion, coupee


class StilliveLlamaCpp:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "prompt": ("STRING", {"multiline": True, "default": ""}),
                "llama_server": ("STRING", {"default": ""}),
                "model": ("STRING", {"default": ""}),
                "mmproj": ("STRING", {"default": ""}),
                "ctx": ("INT", {"default": 32768, "min": 2048, "max": 262144}),
                "thinking": ("BOOLEAN", {"default": True}),
                "reasoning_effort": (["low", "medium", "high"], {"default": "medium"}),
                "max_tokens": ("INT", {"default": 16000, "min": 1, "max": 131072}),
                "reflexion_max_s": ("INT", {"default": 360, "min": 1, "max": 7200}),
                "temperature": ("FLOAT", {"default": 0.6, "min": 0.0, "max": 2.0, "step": 0.05}),
            },
            "optional": {
                "image": ("IMAGE",),
                "apres": ("STRING", {"forceInput": True}),
                # Nouveaux (anciens graphes sans eux : llama-server local comme avant)
                "mode": (["llamacpp", "ollama", "openai"], {"default": "llamacpp"}),
                "url": ("STRING", {"default": ""}),
                "modele": ("STRING", {"default": ""}),
                "mtp": ("BOOLEAN", {"default": True}),
                "brouillon": ("STRING", {"default": ""}),
                "vision_gpu": ("BOOLEAN", {"default": False}),
            },
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("texte",)
    FUNCTION = "executer"
    CATEGORY = CATEGORIE

    def executer(self, *args, **kw):
        # Une erreur en mode llamacpp ne doit pas laisser llama-server occuper la carte graphique.
        try:
            return self._executer(*args, **kw)
        except Exception:
            if kw.get("mode", "llamacpp") == "llamacpp":
                _arreter_serveur()
            raise

    def _executer(self, prompt, llama_server, model, mmproj, ctx, thinking, reasoning_effort,
                  max_tokens, reflexion_max_s, temperature, image=None, apres=None,
                  mode="llamacpp", url="", modele="", mtp=True, brouillon="", vision_gpu=False):
        debut = time.time()
        url, modele = (url or "").strip(), (modele or "").strip()
        if mode == "llamacpp":
            port, cible = _assurer_serveur(llama_server.strip(), model.strip(), mmproj.strip(), ctx, mtp, (brouillon or "").strip(), bool(vision_gpu)), None
        else:
            if not url or not modele:
                raise RuntimeError(f"mode {mode} : il faut l'adresse (url) et le nom du modèle")
            _liberer_carte()  # la carte graphique doit être libre pour ce programme
            port, cible = None, url
        ollama = mode == "ollama"
        data_url = _image_en_data_url(image) if image is not None else None

        def corps(avec_reflexion, niveau=True):
            if ollama:
                msg = {"role": "user", "content": prompt}
                if data_url:
                    msg["images"] = [data_url.split(",", 1)[1]]
                # Ollama accepte true/false, et "low/medium/high" pour certains modèles seulement
                penser = (reasoning_effort if niveau else True) if avec_reflexion else False
                return {"model": modele, "messages": [msg], "stream": True, "think": penser,
                        "options": {"num_ctx": ctx, "temperature": temperature, "num_predict": max_tokens}}
            contenu = []
            if data_url:
                contenu.append({"type": "image_url", "image_url": {"url": data_url}})
            contenu.append({"type": "text", "text": prompt})
            c = {
                "model": modele or "x",
                "messages": [{"role": "user", "content": contenu}],
                "max_tokens": max_tokens,
                "temperature": temperature,
                "stream": True,
                "chat_template_kwargs": {"enable_thinking": avec_reflexion},
            }
            if avec_reflexion:
                c["reasoning_effort"] = reasoning_effort
            return c

        def envoyer(avec_reflexion):
            try:
                return _requete(port, corps(avec_reflexion), reflexion_max_s, cible, ollama)
            except _ErreurHTTP as e:
                if not (ollama and avec_reflexion and e.status == 400 and "think" in str(e).lower()):
                    raise
                print("[Stillive] ce modèle Ollama n'accepte pas de niveau de réflexion : réflexion simple")
                return _requete(port, corps(True, niveau=False), reflexion_max_s, cible, ollama)

        reflexion, reponse, t_ref, coupee = envoyer(thinking)
        if coupee:
            print(f"[Stillive] réflexion coupée à {reflexion_max_s} s, nouvelle réponse sans réflexion")
            reflexion, reponse, t_ref, _ = envoyer(False)
        elif thinking and not reponse.strip():
            print("[Stillive] réponse vide après la réflexion : nouvelle réponse sans réflexion")
            reflexion, reponse, t_ref, _ = envoyer(False)

        texte = reponse.strip()
        if not texte:
            raise RuntimeError("llama-server a renvoyé une réponse vide "
                               f"(réflexion de {len(reflexion)} caractères, max_tokens={max_tokens} peut-être trop bas)")
        print(f"[Stillive] réflexion {t_ref:.0f} s, total {time.time() - debut:.0f} s, {len(texte)} caractères")
        return (texte,)


class StilliveLlamaStop:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"apres": ("STRING", {"forceInput": True})},
                "optional": {"mode": (["llamacpp", "ollama", "openai"], {"default": "llamacpp"}),
                             "url": ("STRING", {"default": ""}),
                             "modele": ("STRING", {"default": ""})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("apres",)
    FUNCTION = "arreter"
    CATEGORY = CATEGORIE
    OUTPUT_NODE = False

    def arreter(self, apres, mode="llamacpp", url="", modele=""):
        url, modele = (url or "").strip().rstrip("/"), (modele or "").strip()
        try:
            if mode == "ollama":
                _decharger_ollama(url, modele)
            elif mode == "openai":
                _decharger_openai(url)
            else:
                _arreter_serveur()
        except Exception as e:  # un déchargement raté ne doit pas faire échouer l'analyse déjà écrite
            print(f"[Stillive] déchargement du modèle impossible : {e}. Libère la carte graphique à la main si la vidéo manque de mémoire.")
        return (apres,)


def _json_http(url, chemin, corps=None, timeout=30):
    """Appel JSON simple (POST si corps, sinon GET). Renvoie la réponse décodée."""
    conn, prefixe = _connexion(None, url)
    conn.timeout = timeout
    try:
        conn.request("POST" if corps is not None else "GET", prefixe + chemin,
                     body=json.dumps(corps).encode("utf-8") if corps is not None else None,
                     headers={"Content-Type": "application/json"})
        rep = conn.getresponse()
        brut = rep.read()
        if rep.status != 200:
            raise _ErreurHTTP(rep.status, brut.decode("utf-8", "replace")[:300])
        return json.loads(brut) if brut.strip() else {}
    finally:
        conn.close()


def _decharger_ollama(url, modele):
    url = url or "http://127.0.0.1:11434"
    charges = [m.get("name") or m.get("model") for m in _json_http(url, "/api/ps").get("models", [])]
    cibles = [m for m in charges if not modele or m in (modele, modele + ":latest")] if modele else charges
    for m in cibles:
        _json_http(url, "/api/chat", {"model": m, "messages": [], "keep_alive": 0})
    debut = time.time()
    while time.time() - debut < 20:
        reste = [m.get("name") or m.get("model") for m in _json_http(url, "/api/ps").get("models", [])]
        if not [m for m in reste if m in cibles]:
            print("[Stillive] modèle Ollama déchargé, carte graphique libre")
            return
        time.sleep(1)
    print("[Stillive] avertissement : Ollama garde encore le modèle en mémoire après 20 s")


def _decharger_openai(url):
    """LM Studio sait décharger ses modèles (API /api/v1). Les autres programmes : simple avertissement."""
    try:
        liste = _json_http(url, "/api/v1/models")
        modeles = liste.get("models") or liste.get("data") or []
    except (OSError, ValueError, RuntimeError):
        modeles = None
    if modeles is None:
        print("[Stillive] avertissement : ce programme ne permet pas de décharger le modèle depuis Stillive. "
              "Il garde la carte graphique : décharge-le dans son interface avant de fabriquer la vidéo.")
        return
    n = 0
    for m in modeles:
        for inst in m.get("loaded_instances") or []:
            ident = inst.get("id") if isinstance(inst, dict) else inst
            _json_http(url, "/api/v1/models/unload", {"instance_id": ident})
            n += 1
    print(f"[Stillive] LM Studio : {n} modèle(s) déchargé(s)")


NODE_CLASS_MAPPINGS = {
    "StilliveLlamaCpp": StilliveLlamaCpp,
    "StilliveLlamaStop": StilliveLlamaStop,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "StilliveLlamaCpp": "Stillive llama.cpp (texte + image)",
    "StilliveLlamaStop": "Stillive llama.cpp (arrêt)",
}

# Extension web de ComfyUI : comfy_nodes/js/stillive.js affiche un seul petit message si Stillive est à préparer (pas d'icône).
WEB_DIRECTORY = "./js"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]


def _port_stillive():
    """Port du serveur web Stillive (variable STILLIVE_PORT, 8765 par défaut)."""
    try:
        return int(os.environ.get("STILLIVE_PORT", "8765"))
    except ValueError:
        return 8765


def _route_port():
    """GET /api/stillive_port (ComfyUI) : dit à l'onglet Stillive quel port ouvrir."""
    try:
        from aiohttp import web
        from server import PromptServer

        @PromptServer.instance.routes.get("/stillive_port")
        async def stillive_port(request):
            return web.json_response({"port": _web.get("port") or _port_stillive()})

        @PromptServer.instance.routes.get("/stillive_etat")
        async def stillive_etat(request):
            """Dit au petit message de ComfyUI si Stillive est à préparer (rien d'autre n'est affiché sinon)."""
            import asyncio
            from stillive import moteur
            try:
                d = await asyncio.to_thread(moteur.etat)
                a_preparer = (d.get("compat") or {}).get("ok") is not False and not (
                    d.get("pret") and (d.get("h3") or {}).get("pret", True))
            except Exception:
                a_preparer = False
            return web.json_response({"port": _web.get("port") or _port_stillive(), "a_preparer": a_preparer})
    except Exception as e:  # hors ComfyUI (tests, serveur autonome) : pas de route, l'onglet prend 8765
        print(f"[Stillive] route du port non ajoutée : {e}")


# ---- Serveur web Stillive, dans le processus ComfyUI ----
_web = {"demarre": False}


def _adresse_locale():
    """IP du PC sur le réseau local (socket UDP vers une adresse privée, rien n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _encadre(lignes):
    large = max(len(l) for l in lignes) + 4
    print("\n" + "=" * large)
    for l in lignes:
        print("| " + l.ljust(large - 4) + " |")
    print("=" * large + "\n", flush=True)


def _port_occupe(port):
    """Vrai si quelque chose répond déjà en local sur ce port (couvre une autre appli sur 127.0.0.1 sous Windows)."""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.3).close()
        return True
    except OSError:
        return False


def _fil_web(port_prefere, comfy_url, racine):
    import asyncio
    from aiohttp import web
    from stillive import serveur

    boucle = asyncio.new_event_loop()
    asyncio.set_event_loop(boucle)
    runner = web.AppRunner(serveur.app(comfy_url))
    boucle.run_until_complete(runner.setup())
    port = None
    for p in range(port_prefere, port_prefere + 21):   # le port préféré puis les 20 suivants
        if p > 65535 or _port_occupe(p):
            continue
        try:
            boucle.run_until_complete(web.TCPSite(runner, "0.0.0.0", p).start())
            port = p
            break
        except OSError:
            continue
    if port is None:
        print(f"[Stillive] ports {port_prefere} à {port_prefere + 20} indisponibles : interface web non démarrée. "
              "Change de port avec la variable d'environnement STILLIVE_PORT. ComfyUI continue normalement.")
        return
    _web["port"] = port
    try:   # port mémorisé : le lien gardé sur le téléphone reste valable aux démarrages suivants
        dossier = os.path.join(racine, "donnees")
        os.makedirs(dossier, exist_ok=True)
        with open(os.path.join(dossier, "port.txt"), "w", encoding="utf-8") as f:
            f.write(str(port))
    except OSError:
        pass
    if port != port_prefere:
        print(f"[Stillive] port {port_prefere} occupé, Stillive utilise le port {port}")
    try:
        serveur.reprendre(comfy_url)
    except Exception as e:
        print(f"[Stillive] reprise des films en cours impossible : {e}")
    try:
        adresse = serveur.adresse_qr(port)  # adresse avec le code secret, None si pas de Wi-Fi
    except Exception:
        adresse = None
    _encadre(["Stillive est prêt.",
              *(["Sur ton téléphone (même Wi-Fi), scanne le code QR de l'écran d'accueil ou ouvre :", adresse]
                if adresse else ["Connecte l'ordinateur au Wi-Fi pour ouvrir Stillive depuis ton téléphone."]),
              "Préparer ou régler Stillive (sur cet ordinateur) :",
              f"http://127.0.0.1:{port}/web/ordinateur.html"])
    boucle.run_forever()


def _demarrer_web():
    if _web["demarre"]:
        return
    _web["demarre"] = True
    try:
        import sys
        import threading
        racine = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
        if racine not in sys.path:
            sys.path.insert(0, racine)
        port = _port_stillive()
        try:   # port mémorisé au démarrage précédent, s'il est valide
            with open(os.path.join(racine, "donnees", "port.txt"), encoding="utf-8") as f:
                p = int(f.read().strip())
            if 1024 <= p <= 65535:
                port = p
        except (OSError, ValueError):
            pass
        try:
            from comfy.cli_args import args
            port_comfy = int(args.port)
        except Exception:
            port_comfy = 8188
        comfy_url = f"http://127.0.0.1:{port_comfy}"
        threading.Thread(target=_fil_web, args=(port, comfy_url, racine), name="stillive-web", daemon=True).start()
    except Exception as e:
        print(f"[Stillive] interface web non démarrée : {e}")


_route_port()
_demarrer_web()
