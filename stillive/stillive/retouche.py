"""Restauration et mise en couleur en une seule passe avec Qwen Image 2.1
(édition depuis la photo, réglages validés en interne : 25 étapes, cfg 1).

L'encodeur est le même Qwen3-VL 8B que l'analyse (version int8 de Comfy-Org,
pas la version « heretic » sous licence non commerciale).
"""

MODEL = "qwen_image_2.1_int8_convrot.safetensors"
TEXT_ENCODER = "qwen3vl_8b_int8_convrot.safetensors"
VAE = "qwen_image_2.1_vae_bf16.safetensors"
NODE_TYPES = ["TextEncodeQwenImage21"]

KEEP = ("Keep exactly the same people, faces, expressions, poses, clothing, composition and framing. "
        "Do not add or remove anything.")
RESTORE = ("Restore this old photograph: remove scratches, creases, stains, dust, tears and fading, "
           "repair missing or torn areas, keep natural film grain and sharp details. ")
COLORIZE = ("Turn this black and white photo into a full color photograph: natural, realistic colors "
            "faithful to the era, lifelike skin tones, colored clothing, foliage, sky and surroundings, "
            "soft daylight, no oversaturation, no remaining gray or sepia tint. ")
REMOVE_BORDER = "Remove the paper border so the photo fills the whole frame. "


def is_black_and_white(path):
    """Noir et blanc ou sépia : peu de couleur ET une seule teinte dominante.

    Mesuré sur les photos de démo : sépia 1965 à 0,17 de saturation moyenne et 0 de
    variété, couleur délavée des années 70 à 0,34 et plus, couleur 2005 à 0,21 et 0,64.
    """
    from PIL import Image, ImageStat
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((256, 256))
        hsv = im.convert("HSV")
    saturation = ImageStat.Stat(hsv).mean[1] / 255
    pixels = [p for p in hsv.get_flattened_data() if p[1] > 25 and p[2] > 40]
    if len(pixels) < 50:
        return True
    hist = [0] * 12
    for h, _, _ in pixels:
        hist[h * 12 // 256] += 1
    top = max(range(12), key=hist.__getitem__)
    variety = 1 - sum(hist[(top + d) % 12] for d in (-1, 0, 1)) / len(pixels)
    return saturation < 0.19 and variety < 0.1


def prompt(colorize=True, damaged=True, border=False):
    text = RESTORE if damaged else "Clean up this photograph and keep it sharp. "
    if colorize:
        text += COLORIZE
    if border:
        text += REMOVE_BORDER
    return text + KEEP


def graph(image_name, text, seed=0, resolution=1024, prefix="stillive/retouche"):
    # Référence : entrée « autogrow » nommée avec un point (« images.image_1 »), sinon
    # elle est ignorée sans erreur. La sortie [2] donne un latent à la taille de la photo.
    return {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": MODEL, "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": TEXT_ENCODER, "type": "qwen_image", "device": "default"}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": VAE}},
        "10": {"class_type": "LoadImage", "inputs": {"image": image_name}},
        "4": {"class_type": "TextEncodeQwenImage21", "inputs": {
            "clip": ["2", 0], "prompt": text, "negative_prompt": "", "resolution": resolution,
            "vae": ["3", 0], "images.image_1": ["10", 0]}},
        "6": {"class_type": "KSampler", "inputs": {
            "model": ["1", 0], "positive": ["4", 0], "negative": ["4", 1], "latent_image": ["4", 2],
            "seed": seed, "steps": 25, "cfg": 1, "sampler_name": "euler", "scheduler": "simple", "denoise": 1}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}},
        "8": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["7", 0]}},
    }
