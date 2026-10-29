"""Montage ffmpeg : plans enchaînés en fondu, fond flou pour remplir le cadre,
son d'ambiance des plans sous la musique, sans fondu au noir à la fin."""
import os
import subprocess

from . import media
from .media import FFMPEG

FADE = 0.5
END_CARD = 2.5
POLICES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "polices")


def police(nom):
    """Chemin d'une police livrée (TTF seulement : ffmpeg plante sur woff2), ou None si absente."""
    chemin = os.path.join(POLICES, nom)
    return chemin if os.path.isfile(chemin) else None


FONT = police("Fraunces.ttf")  # titres et signature (None si absente : texte omis)

# Étapes intermédiaires : quasi sans perte (crf 1, profil high donc encore lisible partout).
# Seul l'encodage final de film() est avec perte.
INTER = ["-c:v", "libx264", "-crf", "1", "-preset", "veryfast", "-pix_fmt", "yuv420p"]


def tailles_film(plan):
    """Tailles des deux films d'après le plan H3 : le sens du plan garde sa taille exacte
    (aucune mise à l'échelle), l'autre sens est la toile inversée. Tailles paires (yuv420p)."""
    w, h = size(plan)
    w, h = max(2, w - w % 2), max(2, h - h % 2)
    if w >= h:
        return {"horizontal": (w, h), "vertical": (h, w)}
    return {"horizontal": (h, w), "vertical": (w, h)}


def _alpha(t0, t1, fade=0.5):
    """Opacité d'un texte : apparaît en fondu à t0, disparaît en fondu à t1."""
    return (f"if(lt(t,{t0:.2f}),0,if(lt(t,{t0 + fade:.2f}),(t-{t0:.2f})/{fade},"
            f"if(lt(t,{t1 - fade:.2f}),1,if(lt(t,{t1:.2f}),({t1:.2f}-t)/{fade},0))))")


def couper(source, sortie, secondes):
    """Garde les `secondes` premières secondes du plan (H3 rend un peu plus que demandé : sa grille
    d'images est par blocs de 17), son en fondu sur la fin. Copie telle quelle si déjà assez court."""
    if duration(source) <= secondes + 0.05:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", source, "-c", "copy", sortie], check=True)
        return sortie
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", source, "-t", f"{secondes:.3f}",
                    "-af", f"afade=t=out:st={max(0.0, secondes - 0.3):.3f}:d=0.3",
                    *INTER, "-c:a", "aac", "-b:a", "192k", sortie], check=True)
    return sortie


def apercu(source, sortie):
    """Version légère (muette, crf 26) de la séquence pour l'aperçu du navigateur pendant la fabrication."""
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", source, "-an", "-c:v", "libx264", "-crf", "26",
                    "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", sortie], check=True)
    return sortie


def duration(path):
    return media.duree(path)


def size(path):
    return media.taille(path)


def avant_apres(original, clip, output, restored=None):
    """Plan « avant / après » : la photo d'origine figée, puis (si retouchée) sa version
    restaurée en couleur, puis l'animation H3 qui part de cette dernière image."""
    w, h = size(clip)
    fit = f"scale={w}:{h},fps=24,format=yuv420p,setsar=1"
    if restored:
        stills = [("1.4", original), ("1.2", restored)]
        parts = [f"[0:v]{fit}[o]", f"[1:v]{fit}[r]", f"[2:v]{fit}[c]",
                 "[o][r]xfade=transition=fade:duration=0.7:offset=0.7[or]",
                 "[or][c]xfade=transition=fade:duration=0.3:offset=1.6[v]"]
        start = 1.6
    else:
        stills = [("1.0", original)]
        parts = [f"[0:v]{fit}[o]", f"[1:v]{fit}[c]", "[o][c]xfade=transition=fade:duration=0.3:offset=0.7[v]"]
        start = 0.7
    inputs = []
    for seconds, path in stills:
        inputs += ["-loop", "1", "-t", seconds, "-i", path]
    inputs += ["-i", clip]
    a = len(stills)
    ms = int(start * 1000)
    parts.append(f"[{a}:a]aresample=48000,afade=t=in:d=0.3,adelay={ms}:all=1[a]")
    subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
                    "-map", "[v]", "-map", "[a]", *INTER,
                    "-c:a", "aac", "-b:a", "192k", output], check=True)
    return output


def on_tempo(lengths, bpm):
    """Raccourcit chaque séquence pour que chaque changement de plan tombe sur un temps
    fort : la part propre de chaque plan (hors fondu) devient un multiple de deux temps.
    Longueur d'un temps = 60 / tempo (aucune analyse de la musique nécessaire)."""
    unit = 2 * 60 / bpm
    out = []
    for i, length in enumerate(lengths):
        fade = FADE if i < len(lengths) - 1 else 0
        bars = max(1, int((length - fade) // unit))
        out.append(round(bars * unit + fade, 3))
    return out


def _esc(path):
    return path.replace("\\", "/").replace(":", "\\:")


def _text(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return _esc(path)


MOT_MAX = 60


def nettoyer_mot(mot):
    """Petit mot de dédicace : caractères de contrôle retirés, espaces réduits, 60 caractères au plus."""
    if not mot:
        return ""
    mot = "".join(" " if c in "\r\n\t" else c for c in str(mot) if c in "\r\n\t" or c.isprintable())
    return " ".join(mot.split())[:MOT_MAX].strip()


def _couper_mot(mot):
    """Une ligne jusqu'à 28 caractères, sinon deux lignes coupées à l'espace le plus proche du milieu."""
    if len(mot) <= 28 or " " not in mot:
        return [mot]
    milieu = len(mot) / 2
    i = min((k for k, c in enumerate(mot) if c == " "), key=lambda k: abs(k - milieu))
    return [mot[:i].strip(), mot[i:].strip()]


def film(clips, music, output, width=1920, height=1080, ambience=0.5, bpm=None,
         title=None, captions=None, signature=None, font=FONT, workdir=None, carton=True, mot=None,
         crf=16, preset="slow", maxrate="14M"):
    """Assemble le film. title : titre d'ouverture ; captions : une légende par plan
    (ou None) ; signature : carton final, None pour l'enlever. carton=False : la signature s'écrit
    sur les dernières secondes du film au lieu d'un carton noir ajouté (la durée ne s'allonge pas)."""
    import os
    workdir = workdir or os.path.dirname(os.path.abspath(output))
    lengths = [duration(c) for c in clips]
    if bpm:
        lengths = [min(l, t) for l, t in zip(lengths, on_tempo(lengths, bpm))]
    body = sum(lengths) - FADE * (len(clips) - 1)
    tail = END_CARD if signature and carton else 0
    total = body + tail
    fontfile = _esc(font) if font and os.path.isfile(font) else None  # sans police : textes omis
    small = max(24, min(width, height) // 28)
    big = max(40, min(width, height) // 14)
    inputs, parts = [], []
    for i, c in enumerate(clips):
        inputs += ["-t", f"{lengths[i]:.3f}", "-i", c]
        parts.append(
            f"[{i}:v]split[a{i}][b{i}];"
            f"[a{i}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=40:2,eq=brightness=-0.08[bg{i}];"
            f"[b{i}]scale={width}:{height}:force_original_aspect_ratio=decrease[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,fps=24,format=yuv420p,setsar=1[v{i}];"
            f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo[s{i}]"
        )
    inputs += ["-i", music]
    m = len(clips)
    v, s, offset = "v0", "s0", 0.0
    for i in range(1, m):
        offset += lengths[i - 1] - FADE
        parts.append(f"[{v}][v{i}]xfade=transition=fade:duration={FADE}:offset={offset:.3f}[vx{i}]")
        parts.append(f"[{s}][s{i}]acrossfade=d={FADE}[sx{i}]")
        v, s = f"vx{i}", f"sx{i}"
    # Textes : titre d'ouverture, légendes par plan, signature sur fond noir à la fin.
    draws = []
    shadow = "shadowcolor=black@0.6:shadowx=2:shadowy=2"
    if title and fontfile:
        tf = _text(os.path.join(workdir, "titre.txt"), title)
        draws.append(f"drawtext=fontfile='{fontfile}':textfile='{tf}':fontsize={big}:fontcolor=white:{shadow}:"
                     f"x=(w-text_w)/2:y=(h-text_h)/2:alpha='{_alpha(0.3, 3.3)}'")
    start = 0.0
    for i, cap in enumerate(captions or []):
        if cap and fontfile:
            cf = _text(os.path.join(workdir, f"legende{i:02d}.txt"), cap)
            a0, a1 = start + 0.4, start + lengths[i] - FADE - 0.2
            draws.append(f"drawtext=fontfile='{fontfile}':textfile='{cf}':fontsize={small}:fontcolor=white:{shadow}:"
                         f"x=w*0.05:y=h*0.92-text_h:alpha='{_alpha(a0, a1)}'")
        start += lengths[i] - FADE
    if signature and not carton and fontfile:
        sf = _text(os.path.join(workdir, "signature.txt"), signature)
        draws.append(f"drawtext=fontfile='{fontfile}':textfile='{sf}':fontsize={small}:fontcolor=white:{shadow}:"
                     f"x=(w-text_w)/2:y=h*0.92-text_h:alpha='{_alpha(max(0.0, body - 2.4), body - 0.4)}'")
    mot = nettoyer_mot(mot)
    if mot and fontfile:
        lignes = _couper_mot(mot)
        taille = max(36, min(width, height) // (11 if len(lignes) == 1 else 15))
        if len(mot) > 20 and len(lignes) == 1:
            taille = max(32, min(width, height) // 14)
        t0 = max(0.0, body - 3.5)
        pas = round(taille * 1.25)  # une ligne = un drawtext : chaque ligne est centrée
        for k, ligne in enumerate(lignes):
            mf = _text(os.path.join(workdir, f"mot{k}.txt"), ligne)
            decalage = (k - (len(lignes) - 1) / 2) * pas
            draws.append(f"drawtext=fontfile='{fontfile}':textfile='{mf}':fontsize={taille}:fontcolor=white:{shadow}:"
                         f"x=(w-text_w)/2:y=h*0.70-{taille / 2:.0f}{decalage:+.0f}:"
                         f"alpha='min(1,max(0,(t-{t0:.2f})/1.2))'")
    chain = ",".join(draws) or "null"  # plus de fondu au noir à la fin (demande de l'utilisateur, 26/10)
    parts.append(
        f"[{m}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=0:{total:.3f},"
        f"afade=t=out:st={max(0, total - 3):.3f}:d=3[mus];"
        f"[{s}]volume={ambience},apad=whole_dur={total:.3f}[amb];[mus][amb]amix=inputs=2:duration=first:normalize=0[aout]"
    )
    if signature and carton:
        # Carton noir fabriqué à part puis collé (tpad ne prolonge pas la vidéo ici).
        inputs += ["-f", "lavfi", "-i", f"color=black:s={width}x{height}:r=24:d={tail}"]
        parts.append(f"[{v}]{chain},setsar=1[body]")
        texte = ""
        if fontfile:
            sf = _text(os.path.join(workdir, "signature.txt"), signature)
            texte = (f",drawtext=fontfile='{fontfile}':textfile='{sf}':fontsize={small}:"
                     f"fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2:alpha='{_alpha(0.3, tail - 0.3)}'")
        parts.append(f"[{m + 1}:v]format=yuv420p,setsar=1{texte}[card]")
        parts.append("[body][card]concat=n=2:v=1:a=0[vout]")
    else:
        parts.append(f"[{v}]{chain}[vout]")
    cmd = [FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
           "-map", "[vout]", "-map", "[aout]", "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
           # Plafond de débit (téléphone) : seuls les looks à grain (vhs, super 8) l'atteignent.
           "-maxrate", maxrate, "-bufsize", f"{2 * int(maxrate[:-1])}M",
           "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", output]
    subprocess.run(cmd, check=True)
    return total


# ---------------------------------------------------------------------------
# Recettes d'ouverture. Chacune prend (original, clip H3, sortie, retouchée) et renvoie
# l'instant (en secondes) où le plan H3 démarre dans la séquence produite.
# ---------------------------------------------------------------------------
def revit(original, clip, output, restored=None):
    """« Ça revit » (29/10, avis de l'utilisateur : la ligne blanche qui balayait l'image faisait perdre plus
    d'une seconde, surtout sur 4 s) : sans retouche, le plan H3 tel quel (il part déjà de la photo) ; avec
    retouche, la photo d'origine se fond en 0,3 s dans le plan H3 (avant/après express)."""
    if not restored:
        return temps(original, clip, output)
    w, h = size(clip)
    fit = f"scale={w}:{h},fps=24,format=yuv420p,setsar=1"
    start = 0.2
    parts = [f"[0:v]{fit}[o]", f"[1:v]{fit}[c]", f"[o][c]xfade=transition=fade:duration=0.3:offset={start}[v]",
             f"[1:a]aresample=48000,adelay={int(start * 1000)}:all=1[a]"]
    subprocess.run([FFMPEG, "-v", "error", "-y", "-loop", "1", "-t", "0.5", "-i", original, "-i", clip,
                    "-filter_complex", ";".join(parts), "-map", "[v]", "-map", "[a]", *INTER,
                    "-c:a", "aac", "-b:a", "192k", output], check=True)
    return start


def revit_balayage(original, clip, output, restored=None):
    """Ancienne ouverture « Ça revit » (ligne lumineuse de 1,5 s), plus utilisée depuis le 29/10."""
    w, h = size(clip)
    start, sweep, total, fade = 0.3, 0.9, 1.5, 0.2
    # Position de la ligne (L) en pixels, hors cadre avant et après le balayage ; à gauche
    # de la ligne on voit la photo retouchée (B), à droite l'originale (A).
    g = "(W*0.004)"
    line = f"((W+8*{g})*clip((T-{start})/{sweep},0,1)-4*{g})"
    k = f"min(1,exp(-pow((X-{line})/{g},2))+0.25*exp(-pow((X-{line})/(6*{g}),2)))"
    base = f"if(gte(X,{line}),A,B)"
    expr = f"{base}+(255-({base}))*{k}"
    fit = f"scale={w}:{h},fps=24,setsar=1"
    start_clip = total - fade
    parts = [f"[0:v]{fit},format=gbrp[o]", f"[1:v]{fit},format=gbrp[r]",
             f"[o][r]blend=all_expr='{expr}',format=yuv420p,setsar=1[i]",
             f"[2:v]{fit},format=yuv420p[c]",
             f"[i][c]xfade=transition=fade:duration={fade}:offset={start_clip}[v]",
             f"[2:a]aresample=48000,afade=t=in:d=0.3,adelay={int(start_clip * 1000)}:all=1[a]"]
    inputs = []
    for path in (original, restored):
        inputs += ["-loop", "1", "-t", str(total), "-i", path]
    inputs += ["-i", clip]
    subprocess.run([FFMPEG, "-v", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
                    "-map", "[v]", "-map", "[a]", *INTER,
                    "-c:a", "aac", "-b:a", "192k", output], check=True)
    return start_clip


CREME = (246, 239, 227)


def _lisse(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3 - 2 * x)


def polaroid(original, clip, output, restored=None):
    """« Polaroïd magique » : fond crème, un polaroïd penché dont l'image se développe en 1,3 s,
    puis zoom qui plonge dans l'image jusqu'à remplir l'écran. La dernière image de l'ouverture
    est exactement la photo (première image du plan H3) : le raccord est invisible."""
    import math
    from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
    w, h = size(clip)
    fps = 24
    photo = Image.open(restored or original).convert("RGB").resize((w, h), Image.LANCZOS)
    m = min(w, h)
    bord, bas, pad = round(0.07 * m), round(0.24 * m), round(0.09 * m)
    cw, ch = w + 2 * bord, h + bord + bas
    sw, sh = cw + 2 * pad, ch + 2 * pad
    ox, oy = pad + bord, pad + bord  # coin haut gauche de l'image dans la carte
    # Carte sans image : ombre douce décalée, cadre blanc cassé.
    ombre = Image.new("L", (sw, sh), 0)
    ImageDraw.Draw(ombre).rectangle([pad + pad // 4, pad + pad // 2, pad + cw + pad // 4, pad + ch + pad // 2], fill=255)
    ombre = ombre.filter(ImageFilter.GaussianBlur(pad * 0.35)).point(lambda v: int(v * 0.38))
    carte = Image.new("RGBA", (sw, sh), CREME + (0,))
    couche = Image.new("RGBA", (sw, sh), (70, 50, 30, 0))
    couche.putalpha(ombre)
    carte = Image.alpha_composite(carte, couche)
    ImageDraw.Draw(carte).rounded_rectangle([pad, pad, pad + cw, pad + ch], radius=max(2, m // 90), fill=(252, 250, 245, 255))
    laiteux = Image.new("RGB", (w, h), (232, 238, 246))

    def image_a(f):
        """Photo en cours de développement : f de 0 (blanc laiteux bleuté) à 1 (photo)."""
        pale = Image.blend(ImageEnhance.Color(photo).enhance(f), laiteux, min(1.0, 0.92 * (1 - f) ** 1.4))
        return pale

    cx, cy = w / 2, h / 2
    centre_carte = (sw / 2, sh / 2)
    centre_image = (ox + w / 2, oy + h / 2)
    s0 = min(0.72 * h / ch, 0.78 * w / cw, 1.0)
    th0 = math.radians(-4)

    def rendre(sp, mx, my, s, th):
        q = min(1.0, s)
        spq = sp.resize((max(1, round(sw * q)), max(1, round(sh * q))), Image.LANCZOS) if q < 1 else sp
        mxq, myq, se = mx * q, my * q, s / q
        c, sn = math.cos(th), math.sin(th)
        coef = (c / se, sn / se, mxq - (c * cx + sn * cy) / se,
                -sn / se, c / se, myq + (sn * cx - c * cy) / se)
        sortie = spq.transform((w, h), Image.AFFINE, coef, resample=Image.BICUBIC)
        fond = Image.new("RGB", (w, h), CREME)
        fond.paste(sortie, (0, 0), sortie)
        return fond

    attente, dev, pause, zoom = round(0.2 * fps), round(1.3 * fps), round(0.2 * fps), round(0.8 * fps)
    avant = attente + dev + pause
    total = avant + zoom
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
           "-i", "pipe:0", "-i", clip, "-filter_complex",
           f"[0:v]fps=24,format=yuv420p,setsar=1[i];[1:v]fps=24,format=yuv420p,setsar=1[c];"
           f"[i][c]concat=n=2:v=1:a=0[v];[1:a]aresample=48000,adelay={int(total / fps * 1000)}:all=1[a]",
           "-map", "[v]", "-map", "[a]", *INTER,
           "-c:a", "aac", "-b:a", "192k", output]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    s_fin_pause = s0 * 1.015
    try:
        for n in range(total):
            f = _lisse((n - attente) / dev)
            sp = carte.copy()
            sp.paste(image_a(f), (ox, oy))
            if n < avant:
                u = n / avant
                frame = rendre(sp, *centre_carte, s0 * (1 + 0.015 * u), th0)
            else:
                e = _lisse((n - avant + 1) / zoom)
                mx = centre_carte[0] + (centre_image[0] - centre_carte[0]) * e
                my = centre_carte[1] + (centre_image[1] - centre_carte[1]) * e
                frame = rendre(sp, mx, my, s_fin_pause * (1 / s_fin_pause) ** e, th0 * (1 - e))
            try:
                proc.stdin.write(frame.tobytes())
            except OSError:  # ffmpeg est mort : son vrai message d'erreur vaut mieux que « tube cassé »
                err = proc.stderr.read().decode(errors="replace")
                proc.wait()
                raise RuntimeError(f"Polaroïd : ffmpeg s'est arrêté : {err}") from None
        proc.stdin.close()
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"Polaroïd : ffmpeg a échoué : {err}")
    finally:
        if proc.poll() is None:
            proc.kill()
    return total / fps


def temps(original, clip, output, restored=None):
    """« Remonte le temps » : H3 fait lui-même la transformation (première image = photo
    d'origine, dernière = retouchée) : pas d'intro, le plan est repris tel quel."""
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", clip, "-c", "copy", output], check=True)
    return 0.0


def boucle(original, clip, output, restored=None):
    """« Portrait vivant » : plan H3 première = dernière image, copié tel quel (boucle parfaite)."""
    return temps(original, clip, output, restored)


RECETTES = {"revit": revit, "polaroid": polaroid, "temps": temps, "boucle": boucle,
            "cinema": revit, "cadre": revit}  # cinéma et cadre : même ouverture, seul le mouvement de caméra change


# ---------------------------------------------------------------------------
# Looks : filtres ffmpeg appliqués à la séquence finale, le son est copié tel quel.
# ---------------------------------------------------------------------------
LOOKS = ("aucun", "auto", "super8", "vhs", "camescope")


def look_auto(decade):
    """Look choisi selon la décennie de l'analyse (ex. « 1978s » ou « 1970s »)."""
    try:
        d = int(str(decade)[:4])
    except ValueError:
        return "aucun"
    if d < 1980:
        return "super8"
    if d < 1990:
        return "vhs"
    if d < 2010:
        return "camescope"
    return "aucun"


def date_camescope(annee):
    """Date style caméscope, stable pour une année donnée (ex. « OCT 12 1998 »)."""
    import random
    r = random.Random(int(annee))
    return f"{r.choice(['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'])} {r.randint(1, 28):02d} {int(annee)}"


def look_filtre(look, w, h, annee=2000):
    if look == "super8":
        # Grain, vignette, teinte chaude, scintillement très léger. Aucun mouvement de
        # l'image : le flottement (24/10) donnait la nausée.
        return ("eq=saturation=0.85:contrast=1.08:brightness='0.006*sin(t*37)':eval=frame,"
                "colorbalance=rs=.08:gs=.02:bs=-.10:rm=.06:bm=-.08,"
                "noise=alls=22:allf=t+u,vignette=PI/4:eval=init,format=yuv420p")
    if look == "vhs":
        # Bavure de couleur, lignes, bruit, image un peu molle.
        return ("gblur=sigma=0.8,chromashift=cbh=4:crh=-4,eq=saturation=1.25:contrast=0.95,"
                "noise=alls=18:allf=t,drawgrid=w=iw:h=4:t=1:c=black@0.18,format=yuv420p")
    if look == "camescope":
        chemin = police("DMSans-Medium.ttf")
        date = ""
        if chemin:  # sans police : date omise plutôt qu'un échec
            date = (f"drawtext=fontfile='{_esc(chemin)}':text='{date_camescope(annee)}':fontsize={max(18, h // 22)}:"
                    "fontcolor=white:shadowcolor=black@0.8:shadowx=2:shadowy=2:x=w-text_w-w*0.04:y=h-text_h-h*0.05,")
        return ("eq=contrast=1.12:saturation=1.1,unsharp=5:5:1.0:5:5:0.0,noise=alls=6:allf=t,"
                f"{date}format=yuv420p")
    raise ValueError(f"look inconnu : {look}")


def appliquer_look(source, sortie, look, annee=2000, max_secondes=None):
    w, h = size(source)
    cmd = [FFMPEG, "-v", "error", "-y", "-i", source]
    if max_secondes:
        cmd += ["-t", str(max_secondes)]
    cmd += ["-vf", look_filtre(look, w, h, annee), *INTER,
            "-c:a", "copy", sortie]
    subprocess.run(cmd, check=True)
    return sortie
