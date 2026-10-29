"""Graphe MiniMax H3 « photo vers vidéo » avec les réglages de référence.

Kitchen + Sparse 30 % (± dense), Spectrum en phase 1, deux phases (agrandissement
du latent puis finition sur 3 étapes à partir du bruit 0,9035), son gelé : la
piste son de la phase 1 est gardée telle quelle.
"""
import math

FPS = 24
FRAME_BLOCK = 17
FRAME_OFFSET = 5
# Plancher validé en interne (896×512). Sous ce seuil, la vidéo est faite en une seule
# phase à la taille finale. Abaissé à 400 k le 23/10, il faisait passer les photos 4:3
# et 3:2 par une phase 1 à 576×768 : doigts et anatomie ratés (constat du 24/10).
MIN_PHASE1_PIXELS = 896 * 512
UNET = "minimax_h3_fl2va_pruned_int8_convrot.safetensors"
TEXT_ENCODER = "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"
VIDEO_VAE = "minimax_h3_video_vae_int8_convrot_comfyorg.safetensors"
AUDIO_VAE = "minimax_h3_audio_vae_fp32.safetensors"
UPSCALER = "minimax_h3_latent_upscaler_3d_bf16.safetensors"

NODE_TYPES = [
    "UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "MiniMaxH3ImageToVideo",
    "H3MemoryOptimization", "H3SparseAttention", "SpectrumApplyMiniMaxH3",
    "RandomNoise", "KSamplerSelect", "BasicScheduler", "BasicGuider", "SamplerCustomAdvanced",
    "LTXVSeparateAVLatent", "LTXVConcatAVLatent", "MinimaxH3LatentUpscaler3D", "ManualSigmas",
    "VAEDecode", "VAEDecodeAudio", "CreateVideo", "SaveVideo",
]


def frame_count(seconds):
    """Nombre d'images à 24 i/s, arrondi au-dessus sur la grille 17k+5 du modèle."""
    raw = max(FRAME_OFFSET, round(seconds * FPS))
    return raw + (FRAME_OFFSET - raw % FRAME_BLOCK) % FRAME_BLOCK


def resolution(photo_w, photo_h, megapixels=0.98):
    """Taille de la vidéo finale au rapport de la photo, multiples de 32."""
    ratio = photo_w / photo_h
    height = math.sqrt(megapixels * 1_000_000 / ratio)
    ceil32 = lambda v: math.ceil(v / 32) * 32
    return ceil32(height * ratio), ceil32(height)


def phase1_size(w, h):
    """Taille réduite de la phase 1, ou None si la cible est trop petite pour deux phases."""
    align32 = lambda v: max(32, round(v / 32) * 32)
    for scale in (2.0, 1.75, 1.5):
        pw, ph = align32(w / scale), align32(h / scale)
        if pw * ph >= MIN_PHASE1_PIXELS:
            return pw, ph
    return None


UNET_REF = "minimax_h3_ref2va_pruned_int8_convrot.safetensors"
# Nom officiel Comfy du VAE vidéo (le nôtre porte le suffixe « _comfyorg »).
VIDEO_VAE_OFFICIEL = "minimax_h3_video_vae_int8_convrot.safetensors"
# LoRA d'accélération officielles (LoraLoaderModelOnly, force 1) et nombre d'étapes qui va avec.
# Image de départ : la 4 étapes 768p d'abord (comparatif J5-11 du 26/10 : son et image au niveau de la
# 8 étapes, 112 s contre 178 s), la 8 étapes en repli si seule elle est installée.
TURBO_FL2V = ("minimax_h3_fl2v_turbo_4step_v1.0_768p_comfyui_bf16.safetensors", 4)
TURBO_FL2V_8 = ("minimax_h3_fl2v_turbo_8step_v1.0_comfyui_bf16.safetensors", 8)
TURBO_REF2V = ("minimax_h3_ref2v_turbo_4step_v0.1_comfyui_bf16.safetensors", 4)
# Échantillonneur avec une LoRA turbo : euler (une évaluation par étape, ce pour quoi elles sont
# distillées). res_multistep, celui du modèle officiel Comfy, rend le son sourd à 4 ou 8 étapes
# (mesuré le 26/10 : 0,4 à 0,7 % de l'énergie au-dessus de 2 kHz contre 6 à 7 % avec euler).
TURBO_SAMPLER = "euler"


def _trouver(nom, disponibles):
    """Nom exact d'un fichier dans une liste de ComfyUI (qui peut le placer dans un sous-dossier), ou None."""
    base = lambda s: s.replace("\\", "/").split("/")[-1]
    for d in disponibles:
        if d == nom or base(d) == nom:
            return d
    return None


def profil(caps=None, turbo=False, ref=False):
    """Choix automatiques selon les capacités de la machine (`comfy.capacites_depuis`).
    caps=None : tout est supposé présent (comportement historique, sans ComfyUI sous la main).
    Renvoie un dict : kitchen, sparse, spectrum (nœuds tiers disponibles), upscaler (nom ou None,
    donc deux phases possibles), vae (nom du VAE vidéo), lora (nom ou None) et turbo_steps."""
    if caps is None:
        tiers = dict.fromkeys(("H3MemoryOptimization", "H3SparseAttention", "SpectrumApplyMiniMaxH3",
                               "MinimaxH3LatentUpscaler3D"), True)
        p = {"kitchen": True, "sparse": True, "spectrum": True, "upscaler": UPSCALER,
             "vae": VIDEO_VAE, "lora": None, "turbo_steps": None}
        lora_dispo = None
    else:
        tiers = caps.get("noeuds", {})
        vaes = caps.get("vae", [])
        p = {"kitchen": bool(tiers.get("H3MemoryOptimization")),
             "sparse": bool(tiers.get("H3SparseAttention")),
             "spectrum": bool(tiers.get("SpectrumApplyMiniMaxH3")),
             "upscaler": (_trouver(UPSCALER, caps.get("upscalers", []))
                          if tiers.get("MinimaxH3LatentUpscaler3D") else None),
             "vae": _trouver(VIDEO_VAE, vaes) or _trouver(VIDEO_VAE_OFFICIEL, vaes) or VIDEO_VAE,
             "lora": None, "turbo_steps": None}
        lora_dispo = caps.get("loras", [])
    if turbo:
        for nom, etapes in ((TURBO_REF2V,) if ref else (TURBO_FL2V, TURBO_FL2V_8)):
            exact = nom if lora_dispo is None else _trouver(nom, lora_dispo)
            if exact:
                p["lora"], p["turbo_steps"] = exact, etapes
                break
    return p


def graphe_verifie(resultat, alertes=None):
    """Prend le résultat de build/build_ref, signale tout repli lent (pas de LoRA turbo : 20 étapes
    au lieu de 4 ou 8, film bien plus long) dans le journal, note le code « accel_absente » dans
    `alertes` (dict) s'il est donné, et renvoie le graphe."""
    g, info = resultat
    prof = info.get("profil", {})
    if not prof.get("turbo"):
        print(f"ATTENTION : accélération (LoRA turbo) absente, génération en {prof.get('steps', 20)} étapes, "
              "le clip sera nettement plus long.", flush=True)
        if alertes is not None:
            alertes["accel_absente"] = True
    return g


ALIGNEMENT_PHOTO = ("How the reference pictures align with the target video — Picture 1 (from Shot 1) aligns with the "
                    "0.00-second mark of the target video.\n\n")


def build_ref(photo_name, face_name, body_name, prompt, photo_w, photo_h, seconds=7, **kwargs):
    """Plan « J'entre dans la photo » (Ref2VA) : nœud natif MiniMaxH3ReferenceToVideo, modèle Ref2VA,
    références dans l'ordre (0 = photo, citée comme première image dans la consigne, 1 = photo de la
    personne, 2 = photo en pied, FACULTATIVE : body_name=None donne deux références seulement),
    `ref_image_size: match`, audio_vae branché, aucune `first_frame`.
    Même graphe, mêmes accélérations et mêmes phases que `build` (le nœud du conditionnement
    est remplacé dans les deux phases). Le nœud combiné VRGDG ignorait la photo : ne pas l'utiliser.
    Turbo par défaut (LoRA ref2v 4 étapes + euler, si elle est installée), turbo=False pour 20 étapes.
    premiere_image=True, par défaut depuis le 29/10 (doc Comfy « MiniMax H3 multiframe », validé par l'utilisateur) :
    la photo est AUSSI la première image du clip (MiniMaxH3AddGuide natif à l'image 0 dans chaque phase), la scène
    n'est plus redessinée (1re image 0,78 de ressemblance contre 0,36 ; 162 s contre 140 s)."""
    kwargs.setdefault("turbo", True)
    premiere_image = kwargs.pop("premiere_image", True)
    if premiere_image and not prompt.startswith(ALIGNEMENT_PHOTO):
        prompt = ALIGNEMENT_PHOTO + prompt
    g, info = build(photo_name, prompt, photo_w, photo_h, seconds, ref=True, **kwargs)
    g["12"] = {"class_type": "LoadImage", "inputs": {"image": face_name}}
    if body_name:
        g["13"] = {"class_type": "LoadImage", "inputs": {"image": body_name}}
    for key in ("20", "21"):
        if key in g:
            node = g[key]
            node["class_type"] = "MiniMaxH3ReferenceToVideo"
            node["inputs"].pop("first_frame", None)
            node["inputs"].update({"ref_image_size": "match", "audio_vae": ["4", 0],
                                   "ref_images.ref_image_0": ["10", 0],
                                   "ref_images.ref_image_1": ["12", 0]})
            if body_name:
                node["inputs"]["ref_images.ref_image_2"] = ["13", 0]
    if premiere_image:
        for cond, guide, guider in (("20", "22", "33"), ("21", "23", "204")):
            if cond in g:
                g[guide] = {"class_type": "MiniMaxH3AddGuide", "inputs": {
                    "positive": [cond, 0], "latent": [cond, 1], "frame_idx": 0, "vae": ["3", 0], "image": ["10", 0]}}
                g[guider]["inputs"]["conditioning"] = [guide, 0]
    return g, info


def build(image_name, prompt, photo_w, photo_h, seconds=7, megapixels=0.98, steps=20,
          seed=0, prefix="stillive/plan", accel=True, two_phase=True, last_image_name=None,
          caps=None, turbo=False, ref=False):
    """accel=False : ni attention creuse (Sparse) ni Spectrum, pour mesurer leur coût en
    qualité. two_phase=False : toujours une seule phase à la taille finale.
    last_image_name : dernière image imposée (mode FL2VA, recette « temps »), donnée aux
    deux conditionnements (phase 1 et phase 2).
    caps : capacités de la machine (`Comfy.capacites()`), None = tout présent. Sans les nœuds
    tiers (Kitchen, Sparse, Spectrum) le modèle va droit à l'échantillonneur ; sans agrandisseur
    de latent (nœud ET fichier), une seule phase à la taille finale ; VAE vidéo : le nôtre, sinon
    l'officiel. turbo : LoRA officielle d'accélération si présente, avec son nombre d'étapes
    (Spectrum est alors omis : 4 à 8 étapes ne laissent rien à prévoir). ref : modèle Ref2VA."""
    prof = profil(caps, turbo, ref)
    width, height = resolution(photo_w, photo_h, megapixels)
    p1 = phase1_size(width, height) if two_phase and prof["upscaler"] else None
    gw, gh = p1 if p1 else (width, height)
    if prof["lora"]:
        steps = prof["turbo_steps"]
    sampler = TURBO_SAMPLER if prof["lora"] else "res_multistep"
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": UNET_REF if ref else UNET, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": prof["vae"]}},
        "4": {"class_type": "VAELoader", "inputs": {"vae_name": AUDIO_VAE}},
        "10": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "20": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
            "prompt": prompt, "width": gw, "height": gh, "length": frame_count(seconds),
            "clip": ["2", 0], "vae": ["3", 0], "first_frame": ["10", 0]}},
        "30": {"class_type": "RandomNoise", "inputs": {"noise_seed": seed}},
        "31": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": sampler}},
        "32": {"class_type": "BasicScheduler", "inputs": {"scheduler": "simple", "steps": steps, "denoise": 1.0, "model": ["1", 0]}},
        "33": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["20", 0]}},
        "34": {"class_type": "SamplerCustomAdvanced", "inputs": {
            "noise": ["30", 0], "guider": ["33", 0], "sampler": ["31", 0], "sigmas": ["32", 0],
            "latent_image": ["20", 1]}},
        "40": {"class_type": "VAEDecode", "inputs": {"samples": ["34", 0], "vae": ["3", 0]}},
        "41": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["34", 0], "vae": ["4", 0]}},
        "50": {"class_type": "CreateVideo", "inputs": {"fps": float(FPS), "bit_depth": 8, "images": ["40", 0], "audio": ["41", 0]}},
        "60": {"class_type": "SaveVideo", "inputs": {"filename_prefix": prefix, "format": "auto", "codec": "auto", "video": ["50", 0]}},
    }
    if p1:
        # Phase 2 : latent vidéo agrandi, finition sans Spectrum, son de la phase 1 gelé.
        # Le conditionnement porte la photo encodée à SA taille : la phase 2 a besoin du
        # sien, à la taille finale (sinon « shape mismatch » dans _embed_and_pack).
        g.update({
            "21": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {
                "prompt": prompt, "width": width, "height": height, "length": frame_count(seconds),
                "clip": ["2", 0], "vae": ["3", 0], "first_frame": ["10", 0]}},
            "200": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["34", 0]}},
            "201": {"class_type": "MinimaxH3LatentUpscaler3D", "inputs": {
                "latent": ["200", 0], "model_name": prof["upscaler"], "mode": "target dimensions",
                "mode.width": width, "mode.height": height, "align": 32, "enable_chunking": True,
                "device": "cuda", "precision": "bf16"}},
            "202": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["201", 0], "audio_latent": ["200", 1]}},
            "203": {"class_type": "ManualSigmas", "inputs": {"sigmas": "0.9035, 0.6316, 0.3158, 0"}},
            "204": {"class_type": "BasicGuider", "inputs": {"model": ["1", 0], "conditioning": ["21", 0]}},
            "205": {"class_type": "SamplerCustomAdvanced", "inputs": {
                "noise": ["30", 0], "guider": ["204", 0], "sampler": ["31", 0], "sigmas": ["203", 0],
                "latent_image": ["202", 0]}},
            "207": {"class_type": "LTXVSeparateAVLatent", "inputs": {"av_latent": ["205", 0]}},
            "208": {"class_type": "LTXVConcatAVLatent", "inputs": {"video_latent": ["207", 0], "audio_latent": ["200", 1]}},
        })
        g["40"]["inputs"]["samples"] = ["208", 0]
        g["41"]["inputs"]["samples"] = ["208", 0]
    if last_image_name:
        g["11"] = {"class_type": "LoadImage", "inputs": {"image": last_image_name}}
        for key in ("20", "21"):
            if key in g:
                g[key]["inputs"]["last_frame"] = ["11", 0]
    # Chaîne du modèle : [LoRA turbo] puis Kitchen, Sparse, Spectrum, chacun seulement s'il existe
    # (et accel pour Sparse et Spectrum). `m_fin` va à la phase 1, `m_p2` (sans Spectrum) à la phase 2.
    m = ["1", 0]
    if prof["lora"]:
        g["80"] = {"class_type": "LoraLoaderModelOnly", "inputs": {
            "model": m, "lora_name": prof["lora"], "strength_model": 1.0}}
        m = ["80", 0]
    if prof["kitchen"]:
        g["70"] = {"class_type": "H3MemoryOptimization", "inputs": {
            "model": m, "fused_qkv": "auto", "mlp_memory": "auto", "chunk_rows": 4096,
            "preserve_precision": True, "precision_mode": "Auto", "qkv_streaming_mode": "Auto",
            "embedding_memory_mode": "Auto", "kitchen_v_memory_mode": "Standard"}}
        m = ["70", 0]
    use_sparse = accel and prof["sparse"]
    use_spectrum = accel and prof["spectrum"] and not prof["lora"]
    if use_sparse:
        g["71"] = {"class_type": "H3SparseAttention", "inputs": {
            "model": m, "video_budget": 0.3, "denser_early_late_steps": True}}
        m = ["71", 0]
    m_p2 = m
    if use_spectrum:
        g["73"] = {"class_type": "SpectrumApplyMiniMaxH3", "inputs": {
            "model": m, "enabled": True, "blend_weight": 0.5, "degree": 4, "ridge_lambda": 0.1,
            "window_size": 2.0, "flex_window": 0.75, "warmup_steps": 5, "tail_actual_steps": 1,
            "max_history": 8, "debug": False, "history_storage": "system_ram",
            "bootstrap_first_forecast": False}}
        m = ["73", 0]
    for key in ("32", "33"):
        g[key]["inputs"]["model"] = m
    if "204" in g:
        g["204"]["inputs"]["model"] = m_p2
    profil_utilise = {"kitchen": prof["kitchen"], "sparse": use_sparse, "spectrum": use_spectrum,
                      "phases": 2 if p1 else 1, "turbo": bool(prof["lora"]), "lora": prof["lora"],
                      "steps": steps, "sampler": sampler, "vae": prof["vae"]}
    return g, {"width": width, "height": height, "phase1": p1, "frames": frame_count(seconds),
               "profil": profil_utilise}
