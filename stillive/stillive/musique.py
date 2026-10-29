"""Musique du film : MiniMax Music 3, un seul morceau instrumental.

Règles validées à l'écoute (23/10) : description en trois parties avec « Purely
instrumental… », paroles réduites aux balises de section, et la durée réelle prise
sur la sortie « seconds » de l'encodeur (jamais une durée fixe sur le latent).
Plus de balises = morceau plus long : environ une section par 9 secondes.
"""

DIT = "minimax_music3_dit_fp16.safetensors"
TEXT_ENCODER = "minimax_music3_text_encoder_pruned_int8_convrot.safetensors"
VAE = "minimax_music3_dav.safetensors"

# Ambiance forcée par l'utilisateur ; « auto » garde celle que l'analyse a lue sur chaque photo.
AMBIANCES = {
    "tendre": "tender, gentle, warm",
    "joyeuse": "joyful, upbeat, playful",
    "nostalgique": "nostalgic, bittersweet, warm",
}

UPBEAT_WORDS = ("upbeat", "joyful", "energetic", "youthful", "festive", "playful", "fun")


def tempo_for(moods):
    """Tempo choisi d'après l'ambiance majoritaire des photos (en battements par minute)."""
    text = " ".join(moods).lower()
    upbeat = sum(text.count(w) for w in UPBEAT_WORDS)
    return 96 if upbeat > len(moods) / 2 else 72


def caption(moods, bpm):
    mood = ", ".join(dict.fromkeys(m.strip() for m in ", ".join(moods).split(",") if m.strip()))
    return (
        f"Global Metadata: Gentle cinematic instrumental, {bpm} BPM, C major. {mood.capitalize()}, "
        "like looking back at cherished memories; quietly emotional, building softly to a hopeful "
        "swell in the middle and settling peacefully at the end.\n\n"
        "Vocal Details: Purely instrumental. No vocals, no singing, no humming, no spoken words.\n\n"
        "Arrangement: Solo felt piano opens with a simple lyrical melody, joined by soft warm strings "
        "and a light acoustic guitar. Middle section adds gentle cello and a subtle swell of strings. "
        "Outro returns to solo piano, fading out on a soft resolved chord."
    )


def lyrics(seconds):
    middle = max(1, round(seconds / 9) - 2)
    body = ["[Instrumental]"] * middle
    if middle >= 3:
        body.insert(middle // 2, "[Bridge]")
    return "\n\n".join(["[Intro]", *body, "[Outro]"])


def graph(moods, seconds, seed=222, prefix="stillive/musique"):
    bpm = tempo_for(moods)
    g = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": DIT, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "minimax", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "4": {"class_type": "MiniMaxMusic3TextEncode", "inputs": {
            "clip": ["2", 0], "caption": caption(moods, bpm), "lyrics": lyrics(seconds), "seed": seed,
            "max_duration": seconds, "cfg_scale": 1.7, "top_k": 50}},
        "5": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}},
        "6": {"class_type": "EmptyMiniMaxMusic3LatentAudio", "inputs": {"seconds": ["4", 1], "batch_size": 1}},
        "7": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0],
            "seed": seed, "steps": 30, "cfg": 1.7, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}},
        "8": {"class_type": "VAEDecodeAudio", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
        "9": {"class_type": "SaveAudioMP3", "inputs": {"audio": ["8", 0], "filename_prefix": prefix, "quality": "V0"}},
    }
    return g, bpm
