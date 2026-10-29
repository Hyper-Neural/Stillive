"""Outils média sans installation : ffmpeg du PATH, sinon celui d'imageio_ffmpeg
(présent dans ComfyUI) ; durée et taille d'une vidéo par PyAV, repli ffprobe."""
import shutil
import subprocess


def _trouver_ffmpeg():
    chemin = shutil.which("ffmpeg")
    if chemin:
        return chemin
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


_FFMPEG = _trouver_ffmpeg()


# Sans ffmpeg, "ffmpeg" reste le nom par défaut ; verifier() donne l'erreur claire.
FFMPEG = _FFMPEG or "ffmpeg"


def verifier():
    if not _FFMPEG:
        raise RuntimeError("ffmpeg est introuvable : ni dans le PATH, ni via imageio_ffmpeg. "
                           "Installe ffmpeg ou le paquet Python imageio-ffmpeg.")
    return _FFMPEG


def duree(path):
    """Durée du fichier en secondes (même valeur que ffprobe format=duration)."""
    try:
        import av
        with av.open(path) as c:
            if c.duration is not None:
                return float(c.duration) / av.time_base
    except ImportError:
        pass
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def taille(path):
    """(largeur, hauteur) du premier flux vidéo."""
    try:
        import av
        with av.open(path) as c:
            s = c.streams.video[0]
            return int(s.codec_context.width), int(s.codec_context.height)
    except ImportError:
        pass
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True)
    w, h = out.stdout.strip().split(",")
    return int(w), int(h)
