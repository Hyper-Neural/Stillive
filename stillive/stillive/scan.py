"""Scan d'un tirage papier : trouve les 4 coins, signale les problèmes de prise de vue,
puis redresse la photo (correction de perspective). Pas de carte graphique, juste OpenCV."""
import io

import cv2
import numpy as np
from PIL import Image, ImageOps

COTE_DETECTION = 700        # la détection travaille sur une image réduite (rapide)
MARGE_DEFAUT = 0.05         # sans tirage trouvé : rectangle à 5 % des bords
SEUIL_FLOU = 60.0           # variance du laplacien sous ce seuil : photo floue (tirage ramené à 600 px)
SEUIL_REFLET = 0.05         # plus de 5 % de pixels quasi blancs saturés : reflet
SEUIL_PETIT = 0.35          # tirage sous 35 % de la surface de l'image : trop loin
ANGLE_MIN, ANGLE_MAX = 65, 115   # angles du quadrilatère hors de cette plage : trop de travers
SURFACE_MAX = 0.92          # au-delà, c'est le cadre de l'image entière, pas un tirage
SURFACE_MIN = 0.10          # un candidat sous 10 % de l'image n'est pas pris pour un tirage


class ImageIllisible(Exception):
    """Fichier que Pillow ne sait pas lire (format inconnu, HEIC, fichier abîmé)."""


def ouvrir(donnees):
    """Octets -> image OpenCV (BGR), orientée comme l'a voulu le téléphone."""
    try:
        img = Image.open(io.BytesIO(donnees))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as e:
        raise ImageIllisible(str(e)) from e
    return cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)


def _ordonner(pts):
    """Haut-gauche, haut-droit, bas-droit, bas-gauche."""
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    s, d = pts.sum(axis=1), pts[:, 0] - pts[:, 1]
    return np.array([pts[np.argmin(s)], pts[np.argmax(d)], pts[np.argmax(s)], pts[np.argmin(d)]], dtype=np.float32)


def _angles(q):
    """Les 4 angles (degrés) d'un quadrilatère ordonné."""
    res = []
    for i in range(4):
        a, b = q[i - 1] - q[i], q[(i + 1) % 4] - q[i]
        cos = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
        res.append(float(np.degrees(np.arccos(np.clip(cos, -1, 1)))))
    return res


def _meilleur_quadrilatere(masque, surface_image):
    contours, _ = cv2.findContours(masque, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    meilleur, aire_max = None, 0.0
    for c in contours:
        aire = cv2.contourArea(c)
        if not SURFACE_MIN * surface_image <= aire <= SURFACE_MAX * surface_image or aire <= aire_max:
            continue
        approx = None
        for eps in (0.02, 0.035, 0.05):
            a = cv2.approxPolyDP(c, eps * cv2.arcLength(c, True), True)
            if len(a) == 4 and cv2.isContourConvex(a):
                approx = a
                break
        if approx is None:                      # contour irrégulier : rectangle incliné le plus proche
            hull = cv2.convexHull(c)
            a = cv2.boxPoints(cv2.minAreaRect(hull))
            if cv2.contourArea(hull) > 0.85 * cv2.contourArea(a):
                approx = a
        if approx is not None:
            meilleur, aire_max = approx, aire
    return meilleur


def detecter(image):
    """Retourne (coins, alertes). coins : 4 points (x, y) normalisés 0-1, dans l'ordre
    haut-gauche, haut-droit, bas-droit, bas-gauche."""
    h, w = image.shape[:2]
    k = COTE_DETECTION / max(h, w)
    petite = cv2.resize(image, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA) if k < 1 else image
    hp, wp = petite.shape[:2]
    gris = cv2.GaussianBlur(cv2.cvtColor(petite, cv2.COLOR_BGR2GRAY), (7, 7), 0)

    candidats = []
    # 1) contours (Canny), refermés par dilatation
    for bas, haut in ((40, 120), (12, 36)):         # le second réglage rattrape les bords adoucis (photo un peu floue)
        bords = cv2.dilate(cv2.Canny(gris, bas, haut), np.ones((5, 5), np.uint8), iterations=2)
        candidats.append(_meilleur_quadrilatere(bords, wp * hp))
    # 2) seuil d'Otsu, tirage clair sur fond sombre (ou l'inverse)
    _, clair = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    noyau = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    for m in (clair, 255 - clair):
        candidats.append(_meilleur_quadrilatere(cv2.morphologyEx(m, cv2.MORPH_CLOSE, noyau), wp * hp))
    candidats = [c for c in candidats if c is not None]

    alertes = []
    if candidats:
        quad = max(candidats, key=lambda c: cv2.contourArea(np.asarray(c, dtype=np.float32).reshape(4, 2)))
        px = _ordonner(quad)
    else:
        m = MARGE_DEFAUT
        px = np.array([[m * wp, m * hp], [(1 - m) * wp, m * hp], [(1 - m) * wp, (1 - m) * hp], [m * wp, (1 - m) * hp]], dtype=np.float32)
        alertes.append("introuvable")
    coins = [[float(np.clip(x / wp, 0, 1)), float(np.clip(y / hp, 0, 1))] for x, y in px]
    return coins, alertes + controles(image, coins, "introuvable" in alertes)


def controles(image, coins, introuvable=False):
    """Alertes de prise de vue pour le tirage délimité par `coins` (cherche flou, reflet,
    petit, travers). Retourne une liste de clés i18n, sous « scan.alerte_<clé> »."""
    alertes = []
    h, w = image.shape[:2]
    px = np.array([[x * w, y * h] for x, y in coins], dtype=np.float32)
    aire = cv2.contourArea(px)
    if aire < SEUIL_PETIT * w * h:
        alertes.append("petit")
    if not introuvable and any(not ANGLE_MIN <= a <= ANGLE_MAX for a in _angles(px)):
        alertes.append("travers")
    tirage = _redresser_cv(image, px, cote_max=600)
    mh, mw = tirage.shape[0] // 20, tirage.shape[1] // 20       # on ignore le bord (liseré blanc, ombre)
    centre = tirage[mh: tirage.shape[0] - mh, mw: tirage.shape[1] - mw]
    if cv2.Laplacian(cv2.cvtColor(centre, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var() < SEUIL_FLOU:
        alertes.append("flou")
    # reflet : pixels presque blancs ET sans aucune couleur (une zone « brûlée »)
    hsv = cv2.cvtColor(centre, cv2.COLOR_BGR2HSV)
    brule = (hsv[:, :, 2] > 245) & (hsv[:, :, 1] < 25)
    if brule.mean() > SEUIL_REFLET:
        alertes.append("reflet")
    return alertes


def _redresser_cv(image, px, cote_max=None):
    haut, bas = np.linalg.norm(px[1] - px[0]), np.linalg.norm(px[2] - px[3])
    gauche, droite = np.linalg.norm(px[3] - px[0]), np.linalg.norm(px[2] - px[1])
    largeur, hauteur = max(haut, bas), max(gauche, droite)
    if cote_max and max(largeur, hauteur) > cote_max:
        k = cote_max / max(largeur, hauteur)
        largeur, hauteur = largeur * k, hauteur * k
    largeur, hauteur = max(int(round(largeur)), 16), max(int(round(hauteur)), 16)
    dest = np.array([[0, 0], [largeur - 1, 0], [largeur - 1, hauteur - 1], [0, hauteur - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(px.astype(np.float32), dest)
    return cv2.warpPerspective(image, m, (largeur, hauteur), flags=cv2.INTER_CUBIC)


def redresser(image, coins):
    """Corrige la perspective : retourne les octets JPEG (qualité 95) du tirage seul."""
    h, w = image.shape[:2]
    px = np.array([[x * w, y * h] for x, y in coins], dtype=np.float32)
    sortie = _redresser_cv(image, _ordonner(px))
    ok, jpeg = cv2.imencode(".jpg", sortie, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not ok:
        raise ImageIllisible("encodage JPEG impossible")
    return jpeg.tobytes()


def enregistrer_jpeg(image, chemin):
    """Écrit l'image orientée en JPEG (qualité 95) : l'aperçu et le redressement partent de là."""
    return bool(cv2.imwrite(chemin, image, [cv2.IMWRITE_JPEG_QUALITY, 95]))
