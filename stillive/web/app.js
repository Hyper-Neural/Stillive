// Stillive : page téléphone. Tous les textes viennent de web/i18n/<langue>.json.
// Deux portes : « Faire revivre un souvenir » (une photo + un effet, ou plusieurs photos pour un film)
// et « Entrer dans un souvenir » (une photo + un selfie).
// Fusionné le 28/10 : effets et gestes forment une seule grille de cartes, plus de mouvement de caméra (l'IA le choisit).
const LANGUES = ["fr", "en"];       // ajouter « es »… : un fichier par langue suffit
// Porte Faire revivre : UNE grille de cartes (dessin #g-<cle>, titre geste.<cle> ou `tit`, phrase <titre>_d).
// Chaque carte : recette envoyée, geste éventuel (= analyse.GESTES). « Automatique » est choisi par défaut ;
// les cartes « large » prennent toute la ligne, les autres vont par lignes pleines de 2.
const GESTES_SOUVENIR = [
  { cle: "auto", recette: "revit", geste: "animer", large: true, tit: "geste.animer" },   // la fonction première : animer la photo
  // Retirés le 28/10 après tests de l'utilisateur : Changer d'âge, Invité surprise, Pluie de fête (gardés côté serveur).
  { cle: "danser", recette: "revit", geste: "danser" }, { cle: "epoque", recette: "revit", geste: "epoque" },
  { cle: "teleportation", recette: "revit", geste: "teleportation" }, { cle: "saison", recette: "revit", geste: "saison" },
  { cle: "dessin_anime", recette: "revit", geste: "dessin_anime" }, { cle: "temps", recette: "temps", tit: "effet.temps" },
  { cle: "boucle", recette: "boucle", tit: "effet.boucle", large: true },
  { cle: "scene", recette: "revit", tit: "effet.scene", large: true },
];
const carteSouvenir = () => GESTES_SOUVENIR.find((c) => c.cle === etat.choix) || GESTES_SOUVENIR[0];
// Porte Entrer : « calin_enfant » demande une photo de soi enfant comme souvenir.
const GESTES_ENTRER = [
// Retour du 28/10 : en 2e position, on le choisissait avec n'importe quel souvenir (un chien...) ; il passe en
// dernier et un encadré rappelle la condition quand il est choisi.
  { code: null, cle: "auto", large: true },
  { code: "selfie" }, { code: "ballons" }, { code: "danser_avec" }, { code: "super_heros" },   // ordre du 29/10
  { code: "portail" }, { code: "retrouvailles" }, { code: "decor" },                          // ajoutées le 29/10
  { code: "liane" }, { code: "trottinette" }, { code: "glitch" },
  { code: "calin_enfant", large: true },
];
// Précision envoyée en « geste_valeur » (= analyse.GESTES_VALEUR) : année, lieu, ou un choix parmi deux.
const PRECISIONS = {
  epoque: { type: "annee", presets: ["aujourdhui", "prehistoire", "pharaons", "rome", "vikings", "moyen_age", "versailles", "pirates", "far_west", "annees_folles", "annees_50", "disco", "annees_80", "futur"] }, teleportation: { type: "lieu", presets: ["paris", "plage", "new_york", "venise", "aurores", "safari", "grand_canyon", "japon", "lune", "dinosaures", "ocean", "chateau", "nuages", "bonbons"] },
  danser: { type: "danse", presets: ["disco", "hip_hop", "rock", "salsa", "valse", "tango", "bollywood", "samba", "ballet", "country", "irlandaise", "hula"] },
  animer: { choix: ["naturel", "surprise"] }, age: { choix: ["vieillir", "rajeunir"] }, dessin_anime: { choix: ["dessin", "pate"] },
};
const codeGeste = (liste, code) => (liste.some((g) => g.code === code) ? code : null);   // ancien geste retiré : Automatique
const EMOJI_TON = { drole: "😄", emouvant: "🥹", magique: "✨" };
const DUREES = [5, 10];             // Court, Long (secondes d'animation ; seulement 5 ou 10, choix du 29/10)
const LOOKS = ["aucun", "auto", "super8", "vhs", "camescope"];   // = montage.LOOKS
const COULEURS = ["auto", "oui", "non"];
const AMBIANCES = ["auto", "tendre", "joyeuse", "nostalgique"];  // film à plusieurs photos
const MINUTES_PAR_PHOTO = 4;       // mesuré : environ 4 min par photo sur une RTX 5080

let textes = {};
let langue = localStorage.getItem("stillive.langue") || "en";   // anglais par défaut (équipe Comfy américaine), le français reste au choix
if (!LANGUES.includes(langue)) langue = "en";
const etat = {
  porte: localStorage.getItem("stillive.porte") === "entrer" ? "entrer" : "souvenir",
  photos: [], image: null, moi: null, corps: null,
  choix: "auto",                 // carte choisie dans la porte Faire revivre (clé de GESTES_SOUVENIR)
  secondes: DUREES.includes(+localStorage.getItem("stillive.secondes")) ? +localStorage.getItem("stillive.secondes") : 5,   // 5 s au départ, puis le dernier choix
  look: "aucun", couleurs: "auto", ambiance: "auto",
  film: null, format: "vertical", minuterie: null, resultat: null,
  gestes: { entrer: null },
  gesteValeur: { animer: "naturel", entrer: "naturel", epoque: "", teleportation: "", age: "vieillir", dessin_anime: "dessin" },
  // Idées de l'IA : cartes montrées, titres déjà vus, idée choisie (résumé affiché + souhait anglais), photo concernée
  idees: { liste: [], vus: [], choisie: null, pour: null, attente: false },
};
const $ = (id) => document.getElementById(id);

// --- Code secret du Wi-Fi ---
// Le code QR ouvre « /?cle=... » : on garde le code, et on le remet dans l'adresse s'il y manque
// (le favori et l'écran d'accueil du téléphone le conservent alors).
const CLE_SECRETE = "stillive.cle";
function cleSecrete() {
  try { return localStorage.getItem(CLE_SECRETE) || ""; } catch { return ""; }
}
(function retenirCle() {
  try {
    const adresseCourante = new URL(location.href);
    const dansAdresse = adresseCourante.searchParams.get("cle");
    if (dansAdresse) localStorage.setItem(CLE_SECRETE, dansAdresse);
    else if (cleSecrete()) {
      adresseCourante.searchParams.set("cle", cleSecrete());
      history.replaceState(history.state, "", adresseCourante);
    }
  } catch { /* stockage refusé : la page marche avec le cookie posé par le serveur */ }
})();

// Appel commun à l'API : ajoute le code, lit la réponse sans jamais planter sur un texte non JSON,
// et lève une erreur au texte clair (jamais un message technique). `toleres` : statuts rendus tels quels.
async function appelerApi(url, options = {}, toleres = []) {
  if (etat.cleRequise) throw new Error(texteErreur("cle_requise"));     // pas de boucle de requêtes
  const headers = { ...(options.headers || {}) };
  if (cleSecrete()) headers["X-Stillive-Cle"] = cleSecrete();
  const r = await fetch(url, { ...options, headers });
  const d = (r.headers.get("content-type") || "").includes("json") ? await r.json().catch(() => ({})) : {};
  if (!r.ok && !toleres.includes(r.status)) {
    if (d.erreur === "cle_requise") cleRequise();
    throw new Error(texteErreur(d.erreur));
  }
  return { r, d };
}

function cleRequise() {
  etat.cleRequise = true;
  montrer("cle", "remplacer");
}

function t(cle, vars = {}) {
  let s = textes[cle] ?? cle;
  if (s.includes("|")) s = s.split("|")[vars.n > 1 ? 1 : 0];      // singulier|pluriel
  return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}
const aTexte = (cle) => cle in textes;

// Lit le fichier de langue (3 essais). Rend false si rien n'a pu être lu : sans aucun texte, l'écran de
// secours (écrit en dur dans index.html) propose de recharger la page.
async function lireLangue(code) {
  for (let essai = 0; essai < 3; essai++) {
    try {
      const r = await fetch(`/web/i18n/${code}.json`);
      if (r.ok) return await r.json();
    } catch { /* réseau pas encore stable : nouvel essai */ }
    await new Promise((ok) => setTimeout(ok, 800));
  }
  return null;
}

async function chargerLangue(code) {
  const lus = await lireLangue(code);
  if (!lus) {
    if (!Object.keys(textes).length) $("secours").hidden = false;   // première langue : page vide sinon
    return false;
  }
  $("secours").hidden = true;
  textes = lus;
  langue = code;
  document.documentElement.lang = code;
  localStorage.setItem("stillive.langue", code);
  $("langue").textContent = code.toUpperCase();
  document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  document.querySelectorAll("[data-t-placeholder]").forEach((el) => { el.placeholder = t(el.dataset.tPlaceholder); });
  document.querySelectorAll("[data-t-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.tLabel)); });
  rendre();
  return true;
}

function rendre() {
  rendreAvance();
  rendreGestes();
  rendreIdees();
  majDuree();
  majPhotos();
  rendreReserve();
  rendrePrep();
  rendreGalerie();
  majCloche();
}

// Chaque écran entre dans l'historique : le bouton retour du téléphone revient à l'écran d'avant
// au lieu de quitter Stillive.
// L'historique garde une profondeur (i = nombre d'écrans empilés depuis l'accueil) : revenir à l'accueil
// recule d'autant au lieu d'empiler un nouvel accueil.
function montrer(ecran, historique = true) {
  if (etat.cleRequise) { ecran = "cle"; historique = "remplacer"; }   // sans le code, un seul écran possible
  const profondeur = history.state?.i || 0;
  if (ecran === "accueil" && historique === true) {
    if (profondeur > 0 && etat.ecran !== "accueil") { history.go(-profondeur); return; }   // onpopstate affiche l'accueil
    historique = "remplacer";
  }
  if (ecran !== "attente") arreterAttente();
  if (ecran !== "resultat") viderFilm();
  etat.ecran = ecran;
  document.body.dataset.ecran = ecran;        // la barre du haut montre « Retour » et « Accueil » hors de l'accueil
  placerFrise(ecran);
  setTimeout(majPlusBas, 600);                // après le défilement vers le haut
  document.querySelectorAll(".ecran").forEach((e) => { e.hidden = e.id !== `ecran-${ecran}`; });
  window.scrollTo({ top: 0, behavior: "smooth" });
  if (historique === "remplacer") history.replaceState({ ecran, i: ecran === "accueil" ? 0 : profondeur }, "");   // l'attente finie ne revient pas au retour
  else if (historique && history.state?.ecran !== ecran) history.pushState({ ecran, i: profondeur + 1 }, "");
  if (ecran === "accueil") { chargerGalerie(); verifierPrep(); }
  else if (ecran === "galerie" || FRISE_ECRANS.includes(ecran)) { rendreGalerie(); chargerGalerie(); }
}

// La frise « Mes souvenirs » suit la personne en bas de chaque écran (accueil, portes, attente, résultat,
// erreur) : elle voit toujours ce qui se fabrique et rejoint la galerie en un toucher.
const FRISE_ECRANS = ["souvenir", "entrer", "attente", "resultat", "erreur"];
const ancreFrise = document.createComment("place de la frise à l'accueil");
$("mes-films").before(ancreFrise);
function placerFrise(ecran) {
  const frise = $("mes-films");
  if (ecran === "accueil") ancreFrise.after(frise);
  else if (FRISE_ECRANS.includes(ecran)) $(`ecran-${ecran}`).append(frise);
}
// Un film en cours change d'état pendant qu'on navigue : la frise se met à jour seule (l'attente a son propre suivi).
setInterval(() => {
  if (document.hidden || etat.ecran === "attente" || !galerie.films.some(enCours)) return;
  if (etat.ecran === "accueil" || etat.ecran === "galerie" || FRISE_ECRANS.includes(etat.ecran)) chargerGalerie();
}, 8000);

window.onpopstate = (e) => {
  const ecran = e.state?.ecran || "accueil";
  if (ecran === "attente" && etat.film) return commencerAttente(false);
  if (ecran === "resultat" && etat.resultat) return resultat(etat.resultat, false);
  if (["souvenir", "entrer", "scan-conseils", "galerie"].includes(ecran)) return montrer(ecran, false);
  montrer("accueil", false);              // écrans de passage (scan en cours, erreur) : retour à l'accueil
};

function ouvrirPorte(porte) {
  etat.porte = porte;
  for (const id of ["souvenir-message", "entrer-message"]) $(id).hidden = true;
  majPhotos();
  montrer(porte);
}

function avertir(id, cle) {
  const p = $(id);
  p.textContent = t(cle);
  p.hidden = false;
  p.scrollIntoView({ block: "center", behavior: "smooth" });
}

// --- Boutons à choix (pastilles, mouvements) ---
function pastilles(conteneur, cles, prefixe, champ) {
  const box = $(conteneur);
  box.innerHTML = "";
  for (const cle of cles) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "pastille";
    b.textContent = t(`${prefixe}.${cle}`);
    b.setAttribute("aria-pressed", etat[champ] === cle);
    b.onclick = () => { etat[champ] = cle; rendreAvance(); majPhotos(); };
    box.append(b);
  }
}

function rendreAvance() {
  for (const s of ["s", "e"]) {
    pastilles(`looks-${s}`, LOOKS, "look", "look");
    pastilles(`couleurs-${s}`, COULEURS, "couleurs", "couleurs");
  }
  pastilles("ambiances", AMBIANCES, "ambiance", "ambiance");
}

// Les choix de photo sont des <label> reliés à un champ fichier caché : on les rend atteignables au clavier
// et annoncés comme boutons (Tab, Entrée, Espace ouvrent le choix de photo).
function rendreAccessible(label) {
  label.setAttribute("role", "button");
  label.tabIndex = 0;
  label.onkeydown = (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    const champ = document.getElementById(label.getAttribute("for") || label.htmlFor);
    if (champ) champ.click();
  };
}

// --- Photos et cases ---
const adresses = new WeakMap();
function adresse(fichier) {
  if (!adresses.has(fichier)) adresses.set(fichier, URL.createObjectURL(fichier));
  return adresses.get(fichier);
}

function majCase(id, fichier) {
  const c = $(id);
  const img = c.querySelector(".apercu");
  img.hidden = !fichier;
  if (fichier) { img.src = adresse(fichier); img.alt = t("photo.alt"); }
  c.querySelector(".case-vide").hidden = !!fichier;
  c.querySelector(".case-change").hidden = !fichier;
  c.classList.toggle("pleine", !!fichier);
  c.parentElement.querySelector(".case-retirer").hidden = !fichier;     // le « × » n'apparaît que sur une case remplie
}

// Une photo : son type commence par « image/ », ou (certains téléphones Android) il est vide et le nom
// finit par une extension d'image.
const EXTENSION_IMAGE = /\.(jpe?g|jfif|png|webp|gif|bmp|avif|heic|heif|tiff?)$/i;
const estImage = (f) => (f.type ? f.type.startsWith("image/") : EXTENSION_IMAGE.test(f.name || ""));

function premiereImage(fichiers) {
  return [...fichiers].find(estImage) || null;
}

// Le message « photo refusée » s'écrit dans l'écran où l'on se trouve.
function messagePhotoRefusee() {
  avertir(etat.ecran === "entrer" ? "entrer-message" : "souvenir-message", "photo.refusee");
}

// Choix d'une seule photo (cases de la porte « Entrer dans une image ») : on garde l'ancienne si rien n'est accepté.
function choisirUne(fichiers, appliquer, famille) {
  const f = premiereImage(fichiers);
  if (!f) { if (fichiers.length) messagePhotoRefusee(); return; }
  appliquer(f);
  majPhotos();
  if (famille) retenir(famille, f);
}

// Avant l'envoi : une photo de plus de 2048 px de côté est réduite (JPEG 0,92, orientation appliquée par le
// navigateur). En dessous, ou si le navigateur ne sait pas la lire (HEIC sur Chrome…), l'original part tel quel
// et le serveur s'en occupe.
const COTE_MAX = 2048;
const reduites = new WeakMap();
async function reduire(fichier) {
  if (reduites.has(fichier)) return reduites.get(fichier);
  let envoi = fichier;
  let img = null;
  try {
    img = await createImageBitmap(fichier);
    const k = COTE_MAX / Math.max(img.width, img.height);
    if (k < 1) {
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(img.width * k));
      c.height = Math.max(1, Math.round(img.height * k));
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.92));
      if (blob) envoi = new File([blob], `${(fichier.name || "photo").replace(/\.[^.]*$/, "") || "photo"}.jpg`, { type: "image/jpeg" });
    }
  } catch { /* format illisible ici : on envoie l'original */ }
  finally { if (img) img.close(); }
  reduites.set(fichier, envoi);
  return envoi;
}

// Nom de fichier accepté par le serveur (il juge sur l'extension) : un fichier sans extension d'image reçoit « .jpg ».
function nomEnvoi(f, repli) {
  const nom = f.name || repli;
  return EXTENSION_IMAGE.test(nom) ? nom : `${nom}.jpg`;
}

async function ajouterPhotos(fichiers, remplacer) {
  const liste = [...fichiers].filter(estImage);
  if (!liste.length) { if (fichiers.length) messagePhotoRefusee(); return; }
  if (remplacer) etat.photos = [];
  for (const f of liste) {
    f.noirEtBlanc = await noirEtBlanc(f);
    f.colorier = f.noirEtBlanc;            // proposé d'office pour un film à plusieurs photos, décochable
    etat.photos.push(f);
    retenir("souvenir", f);
  }
  majPhotos();
}

// --- « Tes photos » : les dernières photos utilisées, gardées DANS le téléphone (IndexedDB), jamais envoyées ---
// Trois familles (souvenir, visage, corps), 8 par famille, la plus récente d'abord. Si le navigateur refuse
// IndexedDB, rien ne casse : la rangée reste cachée.
const RESERVE_MAX = 8;
const reserve = { souvenir: [], visage: [], corps: [] };      // éléments { cle, famille, nom, blob, quand, url }
let baseReserve = null;

function ouvrirBase() {
  if (!baseReserve) baseReserve = new Promise((ok, ko) => {
    try {
      const r = indexedDB.open("stillive", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("photos", { keyPath: "cle" });
      r.onsuccess = () => ok(r.result);
      r.onerror = () => ko(r.error);
      r.onblocked = () => ko(new Error("bloquee"));
    } catch (e) { ko(e); }
  });
  return baseReserve;
}

async function chargerReserve() {
  try {
    const base = await ouvrirBase();
    const tout = await new Promise((ok, ko) => {
      const q = base.transaction("photos").objectStore("photos").getAll();
      q.onsuccess = () => ok(q.result);
      q.onerror = () => ko(q.error);
    });
    // Empreinte manquante (anciens enregistrements) : calculée et réenregistrée.
    for (const p of tout) {
      if (p.emp === undefined) {
        p.emp = await empreinte(p.blob);
        try { base.transaction("photos", "readwrite").objectStore("photos").put(p); } catch { /* tant pis */ }
      }
    }
    const magasin = base.transaction("photos", "readwrite").objectStore("photos");
    for (const famille of Object.keys(reserve)) {
      const gardees = [];
      for (const p of tout.filter((x) => x.famille === famille).sort((a, b) => b.quand - a.quand)) {
        if (gardees.some((g) => memePhoto(g, p))) magasin.delete(p.cle);   // doublon plus ancien : effacé
        else gardees.push(p);
      }
      reserve[famille] = gardees.slice(0, RESERVE_MAX);
    }
  } catch { /* IndexedDB refusé : les rangées restent cachées */ }
  rendreReserve();
}

// Empreinte visuelle (dHash 64 bits, 16 caractères hexadécimaux) : insensible au nom, à la taille et à la recompression.
async function empreinte(blob) {
  let img = null;
  try {
    img = await createImageBitmap(blob);
    const c = document.createElement("canvas");
    c.width = 9;
    c.height = 8;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0, 9, 8);
    const d = ctx.getImageData(0, 0, 9, 8).data;
    const gris = (i) => d[i * 4] * 0.299 + d[i * 4 + 1] * 0.587 + d[i * 4 + 2] * 0.114;
    let hex = "";
    for (let y = 0; y < 8; y++) {
      let octet = 0;
      for (let x = 0; x < 8; x++) octet = (octet << 1) | (gris(y * 9 + x) > gris(y * 9 + x + 1) ? 1 : 0);
      hex += octet.toString(16).padStart(2, "0");
    }
    return hex;
  } catch { return null; }
  finally { if (img) img.close(); }
}

function distanceEmpreintes(a, b) {
  let n = 0;
  for (let i = 0; i < 16; i++) {
    let v = parseInt(a[i], 16) ^ parseInt(b[i], 16);
    while (v) { n += v & 1; v >>= 1; }
  }
  return n;
}

// Même famille et (même clé, ou empreintes proches : 6 bits d'écart au plus).
function memePhoto(a, b) {
  if (a.famille !== b.famille) return false;
  if (a.cle === b.cle) return true;
  return !!(a.emp && b.emp && distanceEmpreintes(a.emp, b.emp) <= 6);
}

// Garde la version réduite de la photo (même nom + même taille = déjà là : elle remonte en tête).
async function retenir(famille, fichier) {
  try {
    const envoi = await reduire(fichier);
    const nom = nomEnvoi(envoi, "photo.jpg");
    const cle = `${famille}|${nom}|${envoi.size}`;
    const base = await ouvrirBase();
    const emp = await empreinte(envoi);
    const neuf = { cle, famille, nom, blob: envoi, quand: Date.now(), emp };
    const anciens = reserve[famille].filter((p) => memePhoto(p, neuf));
    const liste = reserve[famille].filter((p) => !memePhoto(p, neuf));
    liste.unshift(neuf);
    const oublies = liste.splice(RESERVE_MAX);
    for (const p of [...anciens, ...oublies]) if (p.url) URL.revokeObjectURL(p.url);
    reserve[famille] = liste;
    const magasin = base.transaction("photos", "readwrite").objectStore("photos");
    for (const p of anciens) if (p.cle !== cle) magasin.delete(p.cle);
    magasin.put({ cle, famille, nom, blob: envoi, quand: neuf.quand, emp });
    for (const p of oublies) magasin.delete(p.cle);
    rendreReserve();
  } catch { /* stockage refusé ou plein : la photo reste utilisable, sans être gardée */ }
}

async function oublier(p) {
  reserve[p.famille] = reserve[p.famille].filter((x) => x !== p);
  if (p.url) URL.revokeObjectURL(p.url);
  rendreReserve();
  try {
    const base = await ouvrirBase();
    base.transaction("photos", "readwrite").objectStore("photos").delete(p.cle);
  } catch { /* rien à effacer si la base est refusée */ }
}

// Un toucher sur une vignette remet la photo dans la case, comme un choix normal.
function reprendre(cible, p) {
  const fichier = new File([p.blob], p.nom, { type: p.blob.type || "image/jpeg" });
  if (cible === "souvenir") ajouterPhotos([fichier], true);
  else if (cible === "image") choisirUne([fichier], (f) => { etat.image = f; }, "souvenir");
  else if (cible === "moi") choisirUne([fichier], (f) => { etat.moi = f; }, "visage");
  else if (cible === "corps") choisirUne([fichier], (f) => { etat.corps = f; }, "corps");
}

function rendreReserve() {
  document.querySelectorAll(".reserve").forEach((zone) => {
    const liste = reserve[zone.dataset.famille] || [];
    zone.hidden = !liste.length;
    const box = zone.querySelector(".reserve-liste");
    box.innerHTML = "";
    for (const p of liste) {
      if (!p.url) p.url = URL.createObjectURL(p.blob);
      const item = document.createElement("div");
      item.className = "reserve-item";
      const b = document.createElement("button");
      b.type = "button";
      b.className = "reprendre";
      b.setAttribute("aria-label", t("reserve.reprendre"));
      const img = document.createElement("img");
      img.src = p.url;
      img.alt = "";
      b.append(img);
      b.onclick = () => reprendre(zone.dataset.cible, p);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "oublier";
      x.textContent = "×";
      x.setAttribute("aria-label", t("reserve.oublier"));
      x.onclick = () => oublier(p);
      item.append(b, x);
      box.append(item);
    }
  });
}

// Noir et blanc ou sépia : peu de couleur ET une seule teinte dominante. Même règle que
// stillive/retouche.py (mesurée sur les photos de démo), calculée ici sur une miniature.
async function noirEtBlanc(fichier) {
  let img = null;
  try {
    img = await createImageBitmap(fichier);
    const c = document.createElement("canvas");
    const k = 128 / Math.max(img.width, img.height);
    c.width = Math.max(1, Math.round(img.width * k));
    c.height = Math.max(1, Math.round(img.height * k));
    const ctx = c.getContext("2d");
    ctx.drawImage(img, 0, 0, c.width, c.height);
    const px = ctx.getImageData(0, 0, c.width, c.height).data;
    let satTotal = 0, n = 0, comptes = 0;
    const teintes = new Array(12).fill(0);
    for (let i = 0; i < px.length; i += 4) {
      const r = px[i] / 255, g = px[i + 1] / 255, b = px[i + 2] / 255;
      const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min;
      const s = max ? d / max : 0;
      satTotal += s; n++;
      if (s > 0.1 && max > 0.16) {
        let h = d === 0 ? 0 : max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
        h = ((h * 60) + 360) % 360;
        teintes[Math.floor(h / 30) % 12]++; comptes++;
      }
    }
    if (comptes < 50) return true;
    const top = teintes.indexOf(Math.max(...teintes));
    const proches = teintes[(top + 11) % 12] + teintes[top] + teintes[(top + 1) % 12];
    return satTotal / n < 0.19 && 1 - proches / comptes < 0.1;
  } catch { return false; }
  finally { if (img) img.close(); }
}

// Met à jour tout ce qui dépend des photos : cases, grille du film, texte du gros bouton.
function majPhotos() {
  const n = etat.photos.length;
  const multi = n > 1;
  $("bloc-simple").hidden = multi;
  $("bloc-film").hidden = !multi;
  $("avance-simple").hidden = multi;
  $("creer-souvenir").textContent = t(multi ? "film.creer" : "souvenir.creer");
  majCase("case-souvenir", etat.photos[0] || null);
  majCase("case-image", etat.image);
  majCase("case-moi", etat.moi);
  majCase("case-corps", etat.corps);
  $("mot-bloc-s").hidden = multi;                 // le petit mot ne concerne qu'un film à une photo
  if (etat.idees.pour && etat.idees.pour !== etat.photos[0]) viderIdees(false);   // autre photo : plus d'idées
  $("souvenir-message").hidden = true;
  $("entrer-message").hidden = true;
  if (multi) majGrille();
}

function majGrille() {
  const n = etat.photos.length;
  $("nb-photos").textContent = t("film.photos", { n });
  $("duree-film").textContent = t("film.duree", { min: Math.max(1, Math.round(n * MINUTES_PAR_PHOTO)) });
  const grille = $("grille");
  grille.innerHTML = "";
  etat.photos.forEach((f, i) => {
    const v = document.createElement("div");
    v.className = "vignette";
    v.style.setProperty("--r", `${((i * 37) % 7) - 3}deg`);
    const img = document.createElement("img");
    img.src = adresse(f);
    img.alt = "";
    const x = document.createElement("button");
    x.type = "button";
    x.className = "fermer";
    x.textContent = "×";
    x.setAttribute("aria-label", t("film.retirer"));
    x.onclick = () => { etat.photos.splice(i, 1); majPhotos(); };
    v.append(img, x);
    if (f.noirEtBlanc) {                  // étiquette sur le polaroïd : un toucher pour changer
      const tag = document.createElement("button");
      tag.type = "button";
      tag.className = "etiquette";
      tag.setAttribute("aria-pressed", f.colorier);
      tag.textContent = f.colorier ? t("film.couleur") : t("film.couleur_non");
      tag.onclick = () => { f.colorier = !f.colorier; majGrille(); };
      v.append(tag);
    }
    grille.append(v);
  });
  const plus = document.createElement("label");
  plus.className = "vignette ajout";
  plus.htmlFor = "choisir-plus";
  plus.textContent = `+ ${t("film.ajouter")}`;
  rendreAccessible(plus);
  grille.append(plus);
}

// --- Durée : deux boutons (Court, Long), le choix est gardé dans le téléphone ---
function majDuree() {
  document.querySelectorAll(".durees").forEach((box) => {
    box.innerHTML = "";
    for (const n of DUREES) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pastille";
      b.textContent = t(`duree.${n}`);
      b.setAttribute("aria-pressed", etat.secondes === n);
      b.onclick = () => { etat.secondes = n; localStorage.setItem("stillive.secondes", n); majDuree(); };
      box.append(b);
    }
  });
}

// --- Cartes de la porte Faire revivre (effets et gestes en une seule grille) et de la porte Entrer ---
// La précision d'un geste (année, lieu, deux choix) est un seul élément, glissé dans la grille juste sous la
// ligne de la carte choisie ; le texte libre « À toi de raconter » suit la grille (en dernier).
const boxPrecision = document.createElement("div");
boxPrecision.className = "geste-precision";
boxPrecision.id = "geste-precision";
boxPrecision.hidden = true;

function rendreGestes() {
  $("aide-calin").hidden = etat.gestes.entrer !== "calin_enfant";
  for (const [porte, id, liste] of [["souvenir", "gestes-s", GESTES_SOUVENIR], ["entrer", "gestes-e", GESTES_ENTRER]]) {
    const box = $(id);
    box.innerHTML = "";
    const boutons = [];
    for (const g of liste) {
      const cle = g.cle || g.code;
      const tit = g.tit || `geste.${cle}`;
      const choisie = porte === "souvenir" ? etat.choix === cle : etat.gestes.entrer === g.code;
      const b = document.createElement("button");
      b.type = "button";
      b.className = "carte-geste" + (g.large ? " large" : "") + (g.vedette ? " vedette" : "");
      b.setAttribute("aria-pressed", choisie);
      b.innerHTML = `<svg viewBox="0 0 100 64" aria-hidden="true"><use href="#g-${cle}"/></svg>`
        + `<strong></strong><span></span>`;
      b.querySelector("strong").textContent = t(tit);
      b.querySelector("span").textContent = t(`${tit}_d`);
      b.onclick = () => {                      // un toucher choisit, un second toucher décoche (retour à « Automatique »)
        if (porte === "souvenir") etat.choix = choisie ? "auto" : cle;
        else etat.gestes.entrer = choisie ? null : g.code;
        $("souvenir-message").hidden = true;
        rendreGestes();
        if (etat.gestes.entrer === "calin_enfant") $("aide-calin").scrollIntoView({ block: "nearest", behavior: "smooth" });
        if (porte === "souvenir") {
          const cible = PRECISIONS[carteSouvenir().geste] ? boxPrecision : etat.choix === "scene" ? $("bloc-scene") : null;
          if (cible) cible.scrollIntoView({ block: "nearest", behavior: "smooth" });
        }
      };
      box.append(b);
      boutons.push(b);
      if (cle === "auto" && porte === "entrer" && choisie) {   // Entrer en automatique : Naturelle ou Surprends-moi (29/10)
        const prec = document.createElement("div");
        prec.className = "geste-precision";
        const label = document.createElement("label");
        label.textContent = t("geste.entrer_label");
        const groupe = document.createElement("div");
        groupe.className = "pastilles";
        groupe.setAttribute("role", "group");
        for (const c of ["naturel", "surprise"]) {
          const p = document.createElement("button");
          p.type = "button";
          p.className = "pastille";
          p.textContent = t(`geste.animer_${c}`);
          p.setAttribute("aria-pressed", etat.gesteValeur.entrer === c);
          p.onclick = () => { etat.gesteValeur.entrer = c; rendreGestes(); };
          groupe.append(p);
        }
        prec.append(label, groupe);
        box.append(prec);
      }
      if (cle === "auto") {   // la suite est en bonus : un petit titre les sépare
        const bonus = document.createElement("p");
        bonus.className = "gestes-bonus";
        bonus.textContent = t("geste.bonus");
        box.append(bonus);
      }
    }
    if (porte !== "souvenir") continue;
    const carte = carteSouvenir();
    $("bloc-scene").hidden = carte.cle !== "scene";
    if (PRECISIONS[carte.geste]) {                 // fin de la ligne de la carte choisie (cartes larges seules, autres par 2)
      let i = liste.indexOf(carte);
      if (!carte.large) {
        let debut = i;                              // début de la suite de cartes non larges qui contient la carte
        while (debut > 0 && !liste[debut - 1].large) debut--;
        if ((i - debut) % 2 === 0 && liste[i + 1] && !liste[i + 1].large) i++;   // 1re de la ligne : la précision passe après la 2e
      }
      // Animer ma photo : ses deux choix juste sous la carte, avant le titre « en bonus ».
      box.insertBefore(boxPrecision, carte.cle === "auto" ? boutons[0].nextSibling : boutons[i + 1] || null);
    } else boxPrecision.remove();
  }
  rendrePrecision();
}

// Précision du geste choisi (porte Faire revivre) : année, lieu, ou deux choix ; refaite seulement au changement de geste
function rendrePrecision() {
  const box = boxPrecision;
  const geste = carteSouvenir().geste;
  const p = PRECISIONS[geste];
  box.innerHTML = "";
  box.hidden = !p;
  if (!p) return;
  const label = document.createElement("label");
  label.textContent = t(`geste.${geste}_label`);
  box.append(label);
  let pastillesPresets = null;                     // pré-réglages (époques, lieux) puis champ libre dessous
  if (p.presets) {
    const groupe = document.createElement("div");
    groupe.className = "pastilles";
    groupe.setAttribute("role", "group");
    pastillesPresets = [];
    for (const c of p.presets) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pastille";
      b.textContent = t(`geste.${geste}_${c}`);
      b.setAttribute("aria-pressed", etat.gesteValeur[geste] === c);
      b.onclick = () => { etat.gesteValeur[geste] = c; $("souvenir-message").hidden = true; rendrePrecision(); };
      pastillesPresets.push(b);
      groupe.append(b);
    }
    box.append(groupe);
    const ou = document.createElement("small");
    ou.textContent = t(`geste.${geste}_ou`);
    box.append(ou);
  }
  if (p.choix) {
    const groupe = document.createElement("div");
    groupe.className = "pastilles";
    groupe.setAttribute("role", "group");
    for (const c of p.choix) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pastille";
      b.textContent = t(`geste.${geste}_${c}`);
      b.setAttribute("aria-pressed", etat.gesteValeur[geste] === c);
      b.onclick = () => { etat.gesteValeur[geste] = c; rendrePrecision(); };
      groupe.append(b);
    }
    return box.append(groupe);
  }
  const champ = document.createElement("input");
  champ.id = "geste-valeur";
  champ.type = "text";
  champ.autocomplete = "off";
  champ.maxLength = p.type === "annee" ? 4 : 60;
  if (p.type === "annee") champ.inputMode = "numeric";
  champ.placeholder = t(`geste.${geste}_exemple`);
  const valeur = etat.gesteValeur[geste] || "";
  champ.value = p.presets && p.presets.includes(valeur) ? "" : valeur;
  champ.oninput = () => {
    etat.gesteValeur[geste] = champ.value;
    $("souvenir-message").hidden = true;
    if (pastillesPresets) for (const b of pastillesPresets) b.setAttribute("aria-pressed", false);
  };
  champ.enterKeyHint = "done";                     // « OK » du clavier : il se ferme, le bouton Faire revivre reste en vue
  champ.onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); champ.blur(); } };
  label.htmlFor = champ.id;
  box.append(champ);
}

// Clavier du téléphone ouvert : le bouton collé en bas (Faire revivre, Entrer) remonte juste au-dessus du clavier
// au lieu de rester caché dessous (--clavier = hauteur cachée par le clavier, 0 sinon).
if (window.visualViewport) {
  const majClavier = () => {
    const cache = Math.max(0, Math.round(innerHeight - visualViewport.height - visualViewport.offsetTop));
    document.documentElement.style.setProperty("--clavier", `${cache}px`);
  };
  visualViewport.addEventListener("resize", majClavier);
  visualViewport.addEventListener("scroll", majClavier);
}

// --- « Pas d'idée ? » : trois idées proposées par l'IA d'après la photo ---
function viderIdees(tout = true) {
  etat.idees.liste = [];
  etat.idees.vus = [];
  etat.idees.pour = null;
  if (tout) etat.idees.choisie = null;
  $("idees-message").hidden = true;
  rendreIdees();
}

function rendreIdees() {
  const attente = etat.idees.attente;
  $("idees-attente").hidden = !attente;
  // Une vidéo se fabrique : les idées passent devant les autres, juste après l'étape en cours (dit honnêtement).
  $("idees-attente").textContent = t(galerie.films.some(enCours) ? "idees.attente_file" : "idees.attente");
  $("idees").hidden = !etat.idees.liste.length;
  $("idees-demander").hidden = !!etat.idees.liste.length;
  $("idees-demander").disabled = attente;
  $("idees-autres").disabled = attente;
  const box = $("idees-liste");
  box.innerHTML = "";
  for (const p of etat.idees.liste) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "carte-idee";
    b.setAttribute("aria-pressed", $("scene").value.trim() === p.resume.trim());
    const emoji = document.createElement("span");
    emoji.className = "idee-emoji";
    emoji.textContent = EMOJI_TON[p.ton] || "💡";
    emoji.setAttribute("aria-hidden", "true");
    const titre = document.createElement("strong");
    titre.textContent = p.titre;
    const resume = document.createElement("span");
    resume.textContent = p.resume;
    b.append(emoji, titre, resume);
    b.onclick = () => {
      $("scene").value = p.resume;
      etat.idees.choisie = { resume: p.resume, souhait: p.souhait };
      $("souvenir-message").hidden = true;
      rendreIdees();
    };
    box.append(b);
  }
}

async function demanderIdees() {
  if (etat.idees.attente) return;
  if (!etat.photos.length) return avertir("souvenir-message", "souvenir.pas_de_photo");
  const photo = etat.photos[0];
  etat.idees.attente = true;
  $("idees-message").hidden = true;
  rendreIdees();
  try {
    const corps = new FormData();
    const envoi = await reduire(photo);
    corps.append("photo", envoi, nomEnvoi(envoi, "photo.jpg"));
    corps.append("langue", langue);
    corps.append("secondes", String(etat.secondes));
    corps.append("deja", JSON.stringify(etat.idees.vus));
    const { d } = await appelerApi("/api/propositions", { method: "POST", body: corps });
    if (d.erreur || !Array.isArray(d.propositions) || !d.propositions.length) throw new Error(texteErreur(d.erreur || "propositions_illisibles"));
    if (etat.photos[0] !== photo) return;          // la photo a changé pendant l'attente : ces idées ne sont plus les siennes
    etat.idees.pour = photo;
    etat.idees.liste = d.propositions.slice(0, 3);
    for (const p of etat.idees.liste) if (!etat.idees.vus.includes(p.titre)) etat.idees.vus.push(p.titre);
    notifier(t("idees.pret_titre"), t("idees.pret_notif"), "idees");   // page cachée : la personne est prévenue
  } catch (e) {
    $("idees-message").textContent = e instanceof TypeError ? t("erreur.reseau") : e.message;
    $("idees-message").hidden = false;
  } finally {
    etat.idees.attente = false;
    rendreIdees();
  }
}

// Nouvelle création après un film : plus de texte de scène de l'ancien souvenir.
function nouvelleCreation() {
  $("scene").value = "";
  $("mot-s").value = "";
  $("mot-e").value = "";
  viderIdees();
  $("souvenir-message").hidden = true;
}

// --- Fabrication ---
function texteErreur(code) {
  return aTexte(`erreur.${code}`) ? t(`erreur.${code}`) : t("erreur.inconnue");
}

// Clics en rafale : un seul envoi à la fois, et le même souvenir relancé dans la minute demande confirmation
// (la personne a pu toucher « Créer », revenir en arrière et toucher encore).
const dernierEnvoi = { encours: false, photos: [], options: "", quand: 0 };

async function envoyer(photos, options, extra, bouton) {
  if (dernierEnvoi.encours) return;
  const sig = JSON.stringify(options);
  const meme = [...photos, extra.moi_visage].every((f, i) => f === [...dernierEnvoi.photos][i]) && sig === dernierEnvoi.options;
  dernierEnvoi.encours = true;
  if (meme && Date.now() - dernierEnvoi.quand < 60000 && !(await confirmer(t("envoi.deja"), t("envoi.deja_oui")))) {
    dernierEnvoi.encours = false;
    return;
  }
  const texteBouton = $(bouton).textContent;
  $(bouton).disabled = true;
  $(bouton).textContent = t("envoi.en_cours");      // l'envoi peut durer : la personne voit que ça avance
  try {
    const corps = new FormData();
    for (const f of photos) {                     // une par une : pas dix grosses photos décodées en même temps
      const envoi = await reduire(f);
      corps.append("photos", envoi, nomEnvoi(envoi, "photo.jpg"));
    }
    if (extra.moi_visage) { const f = await reduire(extra.moi_visage); corps.append("moi_visage", f, nomEnvoi(f, "visage.jpg")); }
    if (extra.moi_corps) { const f = await reduire(extra.moi_corps); corps.append("moi_corps", f, nomEnvoi(f, "corps.jpg")); }
    corps.append("options", JSON.stringify({ ...options, langue, porte: etat.porte }));
    const { d } = await appelerApi("/api/films", { method: "POST", body: corps });
    Object.assign(dernierEnvoi, { photos: [...photos, extra.moi_visage], options: sig, quand: Date.now() });
    etat.nb = photos.some((f) => f.noirEtBlanc && f.colorier !== false) && etat.couleurs !== "non";   // le conseil sur le noir et blanc ne sert qu'à ces photos
    etat.film = d.id;
    localStorage.setItem("stillive.film", d.id);
    localStorage.setItem("stillive.porte", etat.porte);
    ajouterAuxFilms(d.id);
    nouvelleCreation();                  // le prochain souvenir repart d'une scène et d'une idée vides
    commencerAttente();
  } catch (e) {
    if (e instanceof TypeError) {      // réponse perdue (écran verrouillé, Wi-Fi faible) : le film est peut-être parti
      await chargerGalerie();
      erreur(t("erreur.reseau_envoi"));
    } else erreur(e.message);
  } finally {
    dernierEnvoi.encours = false;
    $(bouton).disabled = false;
    $(bouton).textContent = texteBouton;
  }
}

function creerSouvenir() {
  if (!etat.photos.length) return avertir("souvenir-message", "souvenir.pas_de_photo");
  if (etat.photos.length > 1) {          // film à plusieurs photos : le serveur fait le film long
    return envoyer(etat.photos, {
      titre: $("titre").value.trim(), couleurs: etat.photos.map((f) => !!f.colorier), ambiance: etat.ambiance,
    }, {}, "creer-souvenir");
  }
  const carte = carteSouvenir();
  const options = { recette: carte.recette, secondes: etat.secondes, mouvement: "auto", look: etat.look };
  if (carte.cle === "scene") {
    let scene = $("scene").value.trim();             // geste et texte vides = Automatique : la scène vient de la photo
    const idee = etat.idees.choisie;
    if (idee && scene && scene === idee.resume.trim()) scene = idee.souhait;     // idée de l'IA non retouchée : le souhait anglais part
    if (scene) options.scene = scene;
  }
  const geste = carte.geste || null;            // seulement pour les cartes de geste
  if (geste) {
    options.geste = geste;
    const p = PRECISIONS[geste];
    if (p) {
      const v = String(etat.gesteValeur[geste] || "").trim();
      const codeEpoque = !!p.presets && p.presets.includes(v);
      if (p.type === "annee" && !codeEpoque && !(/^\d{4}$/.test(v) && +v >= 1000 && +v <= 3000)) {
        $("geste-valeur").focus();
        return avertir("souvenir-message", "souvenir.pas_d_annee");
      }
      if (p.type === "lieu" && !v) { $("geste-valeur").focus(); return avertir("souvenir-message", "souvenir.pas_de_lieu"); }
      options.geste_valeur = v;
    }
  }
  const mot = $("mot-s").value.trim();
  if (mot) options.mot = mot;
  if (etat.couleurs !== "auto") options.couleurs = [etat.couleurs === "oui"];
  return envoyer(etat.photos, options, {}, "creer-souvenir");
}

function creerEntrer() {
  if (!etat.image) return avertir("entrer-message", "entrer.pas_d_image");
  if (!etat.moi) return avertir("entrer-message", "entrer.pas_de_moi");
  const options = { recette: "revit", secondes: etat.secondes, mouvement: "auto", look: etat.look };
  const phrase = $("phrase").value.trim();
  if (phrase) options.scene = phrase;
  if (etat.gestes.entrer) options.geste = etat.gestes.entrer;
  else Object.assign(options, { geste: "entrer", geste_valeur: etat.gesteValeur.entrer });   // automatique (29/10)
  const mot = $("mot-e").value.trim();
  if (mot) options.mot = mot;
  if (etat.couleurs !== "auto") options.couleurs = [etat.couleurs === "oui"];
  return envoyer([etat.image], options, { moi_visage: etat.moi, moi_corps: etat.corps }, "creer-entrer");
}

// --- Attente ---
// La photo de la personne se « développe » comme un polaroïd au fil de la fabrication ; la barre avance
// d'après l'estimation du serveur (jamais jusqu'au bout avant la fin) ; des phrases tournent pendant
// l'analyse, puis ce que l'analyse a vu sur la photo s'écrit à la main.
const attente = { p: 0, etape: null, faites: [], anecdote: null, minAstuce: null, frappe: null, fin: null, minCompte: null };

// Petites phrases qui font patienter (clés patience.* du fichier de langue, plus patience_nb.* pour une photo
// en noir et blanc). Tirées au hasard dans un « sac » : aucune ne revient avant que toutes soient passées.
const phrases = { sac: [], derniere: null };

function phraseSuivante() {
  if (!phrases.sac.length) {
    const cles = Object.keys(textes).filter((k) => k.startsWith("patience.") || (etat.nb && k.startsWith("patience_nb.")));
    for (let i = cles.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [cles[i], cles[j]] = [cles[j], cles[i]];
    }
    if (cles.length > 1 && cles[cles.length - 1] === phrases.derniere) cles.unshift(cles.pop());   // pas deux fois de suite
    phrases.sac = cles;
  }
  phrases.derniere = phrases.sac.pop();
  return phrases.derniere ? t(phrases.derniere) : "";
}

function commencerAttente(historique = true) {
  montrer("attente", historique);
  ajouterAuxFilms(etat.film);
  $("tirages").innerHTML = "";
  $("etapes-faites").innerHTML = "";
  for (const id of ["anecdote", "idee-attente", "file-attente"]) $(id).hidden = true;
  $("etape").textContent = "";
  $("restant").textContent = "";
  Object.assign(attente, { p: 0, etape: null, faites: [], anecdote: null });
  majBarre(0.02);
  majPrevenir();
  clearInterval(etat.minuterie);
  suivre();
  etat.minuterie = setInterval(suivre, 4000);
  clearInterval(attente.minAstuce);
  clearInterval(attente.minCompte);
  attente.fin = null;
  montrerAstuce();
  attente.minAstuce = setInterval(montrerAstuce, 6500);
  attente.minCompte = setInterval(afficherCompte, 1000);
}

function arreterAttente() {
  clearInterval(etat.minuterie);
  clearInterval(attente.minAstuce);
  clearInterval(attente.minCompte);
  clearInterval(attente.frappe);
}

function montrerAstuce() {
  const el = $("astuce");
  el.classList.remove("visible");
  setTimeout(() => {
    el.textContent = phraseSuivante();
    el.classList.add("visible");
  }, 400);
}

// Compte à rebours d'après l'estimation du serveur (recalée à chaque relevé) : décompté chaque seconde ici.
// Jamais « dans quelques minutes » : un chiffre précis rassure, et « presque fini » prend le relais à zéro.
function afficherCompte() {
  const el = $("restant");
  if (attente.fin == null) { el.textContent = ""; return; }
  const s = Math.round((attente.fin - Date.now()) / 1000);
  el.textContent = s <= 0 ? t("attente.presque")
    : t("attente.restant", { t: `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}` });
}

function majBarre(p) {
  attente.p = Math.max(attente.p, Math.min(0.97, p));   // ne recule jamais, ne finit jamais avant le film
  $("barre-attente").firstElementChild.style.width = `${(attente.p * 100).toFixed(1)}%`;
  document.querySelectorAll("#tirages .developpe").forEach((img) => img.style.setProperty("--p", attente.p.toFixed(3)));
}

// Avancement d'après le serveur : temps écoulé / (écoulé + reste estimé). Le reste ne descend pas sous
// 15 s : si la fabrication dépasse l'estimation, la barre ralentit au lieu de s'arrêter pleine.
function avancement(f) {
  if (f.etape === "attente" || f.ecoule == null || f.restant == null) return 0.02;
  const reste = Math.max(15, f.restant - (f.depuis_estimation || 0));
  return 0.03 + 0.94 * f.ecoule / (f.ecoule + reste);
}

async function suivre() {
  const id = etat.film;
  let f;
  try {
    const { r, d } = await appelerApi(`/api/films/${id}`, {}, [404]);
    if (r.status === 404) {          // film inconnu (effacé, ou serveur relancé à neuf) : retour à l'accueil
      localStorage.removeItem("stillive.film");
      retirerDesFilms(id);
      return montrer("accueil");
    }
    f = d;
  } catch { return; }                // réseau coupé un instant : on réessaie au prochain tour
  if (etat.ecran !== "attente" || etat.film !== id) return;   // la personne est partie ailleurs entre-temps
  majGalerie(f);
  if (f.etape === "erreur") { localStorage.removeItem("stillive.film"); return erreur(texteFilmRate(f), f.erreur, f.id, "remplacer", f.code); }
  if (f.etape === "fini") { annoncerPret(f); return resultat(f, "remplacer"); }
  const n = f.total || 1;                 // une seule photo : « ta photo », « ton souvenir »
  $("sous-titre-attente").textContent = t("attente.sous_titre", { n });

  const file = $("file-attente");
  file.hidden = !(f.etape === "attente" && f.avant > 0);
  if (!file.hidden) file.textContent = t("attente.file", { n: f.avant });

  const cocher = (etape) => {
    if (!aTexte(`etape_fait.${etape}`) || attente.faites.includes(etape)) return;
    attente.faites.push(etape);
    const li = document.createElement("li");
    li.textContent = t(`etape_fait.${etape}`, { n });
    $("etapes-faites").append(li);
  };
  if (!attente.etape) {                // page rouverte en cours de route : les étapes déjà passées
    if (["plans", "montage"].includes(f.etape)) cocher("analyse");
    if (f.etape === "montage") cocher("plans");
  } else if (attente.etape !== f.etape) cocher(attente.etape);
  attente.etape = f.etape;
  $("etape").textContent = aTexte(`etape.${f.etape}`) ? t(`etape.${f.etape}`, { n }) : "";
  attente.fin = f.restant == null ? null : Date.now() + Math.max(0, f.restant - (f.depuis_estimation || 0)) * 1000;
  afficherCompte();
  dessinerTirages(f);
  majBarre(avancement(f));
  majLecteur("attente.prepare", f.photo);

  if (f.idee && f.idee.titre) {
    const box = $("idee-attente");
    box.hidden = false;
    box.querySelector("strong").textContent = f.idee.titre;
    box.querySelector("span").textContent = f.idee.resume || "";
  }
  if (f.anecdotes.length) {
    const i = Math.floor(Date.now() / 12000) % f.anecdotes.length;
    const texte = (f.anecdotes_langues?.[i] || {})[langue] || f.anecdotes[i];   // suit la langue affichée, même changée pendant l'attente
    if (texte !== attente.anecdote) ecrireAnecdote(texte);
  }
}

// L'anecdote s'écrit lettre après lettre, comme à la main.
function ecrireAnecdote(texte) {
  attente.anecdote = texte;
  const mot = $("anecdote");
  const span = mot.querySelector(".manuscrit");
  mot.hidden = false;
  clearInterval(attente.frappe);
  let i = 0;
  span.textContent = "";
  attente.frappe = setInterval(() => {
    span.textContent = texte.slice(0, ++i);
    if (i >= texte.length) clearInterval(attente.frappe);
  }, 35);
}

function dessinerTirages(f) {
  const box = $("tirages");
  box.classList.toggle("seul", f.total === 1);
  const vus = box.querySelectorAll(".pol").length;
  const montrer3 = Math.min(f.total, f.plans.length + 1);
  for (let i = vus; i < montrer3; i++) box.append(tirage(i === 0 ? f.photo : null));
  box.querySelectorAll(".pol").forEach((p, i) => {
    if (f.plans[i] && !p.querySelector("video")) {
      p.firstElementChild.replaceWith(Object.assign(document.createElement("video"),
        { src: f.plans[i], autoplay: true, muted: true, loop: true, playsInline: true }));
      p.querySelector("figcaption").textContent = f.total > 1 ? t("attente.photo_n", { i: i + 1, n: f.total }) : t("etape.montage");
    }
  });
  // Trois tirages visibles à la fois : les plus récents.
  box.querySelectorAll(".pol").forEach((p, i, all) => { p.hidden = i < all.length - 3; });
}

function tirage(photo) {
  const p = document.createElement("figure");
  p.className = "pol";
  let vide;
  if (photo) {                        // la vraie photo, qui se révèle peu à peu
    vide = Object.assign(document.createElement("img"), { src: photo, alt: "", className: "developpe" });
    vide.style.setProperty("--p", attente.p.toFixed(3));
  } else {
    vide = document.createElement("div");
    vide.className = "revele";
  }
  const cap = document.createElement("figcaption");
  cap.textContent = t("attente.revele");
  p.append(vide, cap);
  return p;
}

// --- Prévenir quand c'est prêt ---
// Une notification demande une page sécurisée (https) : sur l'adresse locale en http, le bouton reste
// caché et la page prévient autrement (titre de l'onglet, vibration) quand elle est encore ouverte.
function peutNotifier() {
  return window.isSecureContext && "Notification" in window && "serviceWorker" in navigator;
}

// Choix de la personne (cloche en haut) : les notifications sont actives si elle les a autorisées
// ET ne les a pas coupées ici. Absent = oui (l'accord du navigateur suffit).
const CLE_NOTIFS = "stillive.notifications";
const notifsVoulues = () => localStorage.getItem(CLE_NOTIFS) !== "non";
const notifsActives = () => peutNotifier() && Notification.permission === "granted" && notifsVoulues();

function majCloche() {
  const b = $("notifs");
  b.hidden = !peutNotifier();
  if (b.hidden) return;
  const actives = notifsActives();
  b.setAttribute("aria-pressed", actives);
  b.title = t(actives ? "notifs.actives" : "notifs.coupees");
}

function messageBref(texte) {
  let m = document.querySelector(".message-bref");
  if (!m) { m = document.createElement("p"); m.className = "message-bref"; m.setAttribute("role", "status"); document.body.append(m); }
  m.textContent = texte;
  m.hidden = false;
  clearTimeout(messageBref.minuterie);
  messageBref.minuterie = setTimeout(() => { m.hidden = true; }, 3200);
}

async function basculerNotifs() {
  if (Notification.permission === "denied") return messageBref(t("notifs.bloquees"));
  if (notifsActives()) {
    localStorage.setItem(CLE_NOTIFS, "non");
  } else {
    localStorage.setItem(CLE_NOTIFS, "oui");
    if (Notification.permission !== "granted") await demanderPrevenir();
  }
  majCloche();
  majPrevenir();
  messageBref(t(notifsActives() ? "notifs.actives" : "notifs.coupees"));
}

// Une notification seulement si la page est cachée (sinon la personne voit déjà le résultat).
// data.film : le souvenir à ouvrir quand on touche la notification (vide pour les idées).
async function notifier(titre, corps, tag, film = "", image = undefined) {
  if (!document.hidden) return;
  document.title = `✓ ${titre} · Stillive`;
  if (!notifsActives()) return;
  try {
    const reg = await navigator.serviceWorker.getRegistration("/web/");
    await reg?.showNotification(titre, { body: corps, icon: image, tag, data: { film } });
  } catch { /* la page montrera le résultat au retour */ }
}

function majPrevenir() {
  const ok = peutNotifier() && Notification.permission !== "denied";
  const accorde = ok && notifsActives();
  $("prevenir").hidden = !ok || accorde;
  $("prevenu").hidden = !accorde;
}

async function demanderPrevenir() {
  localStorage.setItem(CLE_NOTIFS, "oui");          // « Me prévenir » rallume aussi la cloche
  try {
    await navigator.serviceWorker.register("/web/sw.js", { scope: "/web/" });
    await Notification.requestPermission();
  } catch { /* refus ou navigateur sans notifications : rien à faire de plus */ }
  majPrevenir();
  majCloche();
}

// Le lecteur multimédia que le téléphone affiche dans ses notifications (les vidéos qui tournent pendant
// l'attente le font apparaître) sert de suivi : « se prépare » puis « est prêt ». Ne demande pas https.
function majLecteur(cle, photo, album = "") {
  if (!("mediaSession" in navigator) || typeof MediaMetadata === "undefined") return;
  const signature = `${langue}|${cle}|${photo || ""}|${album}`;
  if (majLecteur.derniere === signature) return;      // déjà affiché : pas de clignotement toutes les 4 s
  majLecteur.derniere = signature;
  try {
    const meta = { title: t(cle), artist: t("app.nom"), album };
    if (photo) meta.artwork = [{ src: new URL(photo, location.href).href }];
    navigator.mediaSession.metadata = new MediaMetadata(meta);
  } catch { /* lecteur absent ou image refusée : sans importance */ }
}

const annonces = new Set();
async function annoncerPret(f) {
  if (annonces.has(f.id)) return;
  annonces.add(f.id);
  majLecteur("attente.pret_titre", f.photo);
  if (navigator.userActivation?.hasBeenActive) navigator.vibrate?.([180, 90, 180]);
  await notifier(t("attente.pret_titre"), t("attente.pret_notif"), f.id, f.id, f.photo || undefined);
}

// --- Mes souvenirs : la liste des films de CE téléphone ---
// Le téléphone garde seulement les identifiants de ses films ; un autre téléphone ne les voit pas.
const CLE_GALERIE = "stillive.mes_films";
const GALERIE_MAX = 30;
const galerie = { films: [] };
const enCours = (f) => !["fini", "erreur"].includes(f.etape);

function mesFilms() {
  try {
    const l = JSON.parse(localStorage.getItem(CLE_GALERIE) || "[]");
    return Array.isArray(l) ? l.filter((x) => typeof x === "string") : [];
  } catch { return []; }
}
function garderFilms(l) { localStorage.setItem(CLE_GALERIE, JSON.stringify(l.slice(0, GALERIE_MAX))); }
function ajouterAuxFilms(id) { if (id && !mesFilms().includes(id)) garderFilms([id, ...mesFilms()]); }
function retirerDesFilms(id) {
  garderFilms(mesFilms().filter((x) => x !== id));
  galerie.films = galerie.films.filter((f) => f.id !== id);
  rendreGalerie();
}

function majGalerie(f) {
  const i = galerie.films.findIndex((x) => x.id === f.id);
  if (i >= 0) galerie.films[i] = f; else galerie.films.unshift(f);
}

async function chargerGalerie() {
  const ids = mesFilms();
  if (!ids.length) { galerie.films = []; return rendreGalerie(); }
  let d;
  try {
    d = (await appelerApi(`/api/films?ids=${ids.join(",")}`)).d;
    if (!Array.isArray(d.films)) return;
  } catch { return; }                  // ordinateur injoignable : la galerie garde son dernier état
  const connus = new Map(d.films.map((f) => [f.id, f]));
  for (const f of d.films) {           // un film fini pendant que la personne regardait ailleurs
    const avant = galerie.films.find((x) => x.id === f.id);
    if (avant && enCours(avant) && f.etape === "fini") annoncerPret(f);
  }
  garderFilms(ids.filter((i) => connus.has(i)));     // films effacés de l'ordinateur : retirés
  galerie.films = ids.filter((i) => connus.has(i)).map((i) => connus.get(i));
  rendreGalerie();
}

// Deux vues des mêmes films : la frise de l'accueil et la page « Mes souvenirs » (grandes vignettes).
function rendreGalerie() {
  $("mes-films").hidden = !galerie.films.length;
  $("galerie-vide").hidden = !!galerie.films.length;
  for (const boite of ["bande-accueil", "mur"]) {
    const liste = galerie.films;
    const box = $(boite);
    box.innerHTML = "";
    liste.forEach((f, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `pol mini ${enCours(f) ? "en-cours" : ""} ${f.etape === "erreur" ? "rate" : ""}`;
      b.style.setProperty("--r", `${((i * 37) % 7) - 3}deg`);
      b.setAttribute("aria-label", t("galerie.ouvrir"));
      const img = document.createElement("img");
      img.alt = "";
      img.loading = "lazy";
      if (f.photo) img.src = f.photo;
      const cap = document.createElement("span");
      cap.className = "legende";
      cap.textContent = f.etape === "fini" ? dateCourte(f.cree) : f.etape === "erreur" ? t("galerie.erreur")
        : f.etape === "attente" ? t("galerie.file") : t("galerie.en_cours");
      b.append(img, cap);
      b.onclick = () => ouvrirFilm(f);
      const carte = document.createElement("div");      // le bouton « Annuler » ne peut pas être dans le bouton du film
      carte.className = "mini-case";
      carte.append(b);
      if (enCours(f)) {
        const a = document.createElement("button");
        a.type = "button";
        a.className = "mini-annuler";
        a.textContent = t("galerie.annuler");
        a.onclick = () => annulerFilm(f.id);
        carte.append(a);
      }
      box.append(carte);
    });
  }
}

function dateCourte(s) {
  return s ? new Date(s * 1000).toLocaleDateString(langue, { day: "numeric", month: "short" }) : "";
}

function ouvrirFilm(f) {
  if (f.porte) etat.porte = f.porte;
  if (f.etape === "fini") return resultat(f);
  if (f.etape === "erreur") return erreur(texteFilmRate(f), f.erreur, f.id, true, f.code);
  etat.film = f.id;
  localStorage.setItem("stillive.film", f.id);
  commencerAttente();
}

// Notification « prêt » touchée : la page ouvre ce souvenir (lu sur l'ordinateur s'il n'est pas encore connu ici).
async function ouvrirFilmParId(id) {
  ajouterAuxFilms(id);
  await chargerGalerie();
  const f = galerie.films.find((x) => x.id === id);
  if (f) ouvrirFilm(f);
}
navigator.serviceWorker?.addEventListener("message", (e) => { if (e.data?.ouvrirFilm) ouvrirFilmParId(e.data.ouvrirFilm); });

// Boîte de confirmation dans le style de l'app (remplace confirm()) : rend une promesse vraie/fausse.
function confirmer(texte, libelleOui) {
  return new Promise((fin) => {
    const voile = $("confirmation");
    const precedent = document.activeElement;
    $("confirmation-texte").textContent = texte;
    $("confirmation-oui").textContent = libelleOui;
    voile.hidden = false;
    $("confirmation-non").focus();
    const clore = (reponse) => {
      voile.hidden = true;
      voile.onclick = null;
      document.removeEventListener("keydown", touche, true);
      precedent?.focus?.();
      fin(reponse);
    };
    const touche = (e) => { if (e.key === "Escape") clore(false); };
    document.addEventListener("keydown", touche, true);
    $("confirmation-oui").onclick = () => clore(true);
    $("confirmation-non").onclick = () => clore(false);
    voile.onclick = (e) => { if (e.target === voile) clore(false); };
  });
}

// DELETE : un film fini ou raté est effacé ; un film en attente est retiré tout de suite ; un film en
// fabrication est marqué annulé et s'arrête à la prochaine étape (le serveur répond pareil dans tous les cas).
async function effacerFilm(id) {
  await appelerApi(`/api/films/${id}`, { method: "DELETE" }, [404]);
  retirerDesFilms(id);
  if (localStorage.getItem("stillive.film") === id) localStorage.removeItem("stillive.film");
  if (etat.resultat?.id === id) etat.resultat = null;
  if (etat.film === id) etat.film = null;
}

async function supprimerFilm(id) {
  if (!id || !(await confirmer(t("resultat.supprimer_confirmer"), t("confirmer.supprimer")))) return;
  try {
    await effacerFilm(id);
  } catch (e) {
    return erreur(e instanceof TypeError ? t("erreur.reseau") : e.message);
  }
  montrer("accueil");
}

async function annulerFilm(id) {
  if (!id || !(await confirmer(t("galerie.annuler_confirmer"), t("confirmer.annuler")))) return;
  try {
    await effacerFilm(id);
  } catch (e) {
    return erreur(e instanceof TypeError ? t("erreur.reseau") : e.message);
  }
  if (etat.ecran === "attente") montrer("accueil");
}

async function toutEffacer() {
  if (!galerie.films.length || !(await confirmer(t("galerie.tout_effacer_confirmer"), t("confirmer.tout_effacer")))) return;
  const ids = galerie.films.map((f) => f.id);
  let echec = null;
  for (const id of ids) {            // un par un : les films en cours sont annulés, les autres effacés
    try { await effacerFilm(id); } catch (e) { echec = e; }
  }
  if (echec) return erreur(echec instanceof TypeError ? t("erreur.reseau") : echec.message);
  if (etat.ecran === "attente" || etat.ecran === "resultat") montrer("accueil");
}

// --- Résultat ---
function resultat(f, historique = true) {
  etat.resultat = f;
  if (localStorage.getItem("stillive.film") === f.id) localStorage.removeItem("stillive.film");
  ajouterAuxFilms(f.id);
  majGalerie(f);
  if (etat.ecran === "resultat") historique = "remplacer";   // passer d'un film à l'autre n'empile pas l'historique
  montrer("resultat", historique);
  rendreGalerie();
  majNavigation();
  const box = $("formats");
  box.innerHTML = "";
  for (const k of ["vertical", "horizontal"]) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "format";            // petit sélecteur à icônes (29/10)
    b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><use href="#ic-${k === "vertical" ? "telephone" : "tele"}"/></svg><span></span>`;
    b.querySelector("span").textContent = t(`resultat.${k}`);
    b.setAttribute("aria-pressed", etat.format === k);
    b.onclick = () => { etat.pleinAuto = false; changerFormat(k, false); };   // choix manuel : fin du mode auto
    box.append(b);
  }
  const notes = [];       // codes renvoyés par le serveur : repli d'effet, paroles coupées…
  if (f.repli && aTexte(`repli.${f.repli}`)) notes.push(t(`repli.${f.repli}`));
  if (f.message && aTexte(`message.${f.message}`)) notes.push(t(`message.${f.message}`));
  $("resultat-notes").textContent = notes.join(" ");
  $("resultat-notes").hidden = !notes.length;
  afficherFilm();
}

function afficherFilm() {
  const url = etat.resultat.films[etat.format];
  $("film").src = url;
  $("film").loop = true;   // films courts : tous lus en boucle, comme un cadre animé (choix du 29/10)
  majLecteur("attente.pret_titre", etat.resultat.photo, dateCourte(etat.resultat.cree));
  lancerFilm();
  $("telecharger").href = url;
  $("telecharger").setAttribute("download", `stillive_${etat.format}.mp4`);
  majPartagerFilm();
  document.querySelectorAll("#formats .format").forEach((b, i) => {
    b.setAttribute("aria-pressed", ["vertical", "horizontal"][i] === etat.format);
  });
}

// Partage natif (feuille de partage du téléphone) : seulement si le navigateur sait partager une vidéo
// (page en https). Alors « Partager » devient le bouton principal et « Télécharger » passe au second plan.
function peutPartagerFichier() {
  try {
    return !!(navigator.share && navigator.canShare && navigator.canShare({ files: [new File([""], "s.mp4", { type: "video/mp4" })] }));
  } catch { return false; }
}

function majPartagerFilm() {
  const ok = peutPartagerFichier();
  $("partager-film").hidden = !ok;
  $("telecharger").classList.toggle("gros-bouton-clair", ok);
}

async function partagerFilm() {
  const bouton = $("partager-film");
  if (bouton.disabled || !etat.resultat) return;
  bouton.disabled = true;
  bouton.textContent = t("resultat.partager_prepare");
  try {
    const cle = cleSecrete();
    const r = await fetch(etat.resultat.films[etat.format], cle ? { headers: { "X-Stillive-Cle": cle } } : {});
    if (!r.ok) throw new Error("film");
    const fichier = new File([await r.blob()], "stillive.mp4", { type: "video/mp4" });
    await navigator.share({ files: [fichier], title: "Stillive", text: t("resultat.partager_texte") });
  } catch (e) {
    if (e?.name !== "AbortError") {
      $("resultat-notes").textContent = t("resultat.partager_echec");
      $("resultat-notes").hidden = false;
    }
  } finally {
    bouton.disabled = false;
    bouton.textContent = t("resultat.partager");
  }
}

// Le film démarre seul, avec le son. Si le navigateur refuse (NotAllowedError), il démarre sans le son et un
// bouton bien visible remet le son.
async function lancerFilm() {
  const v = $("film");
  v.muted = false;
  try {
    await v.play();
  } catch (e) {
    if (e?.name === "NotAllowedError") {
      v.muted = true;
      try { await v.play(); } catch { /* lecture refusée aussi sans le son : les commandes restent là */ }
    }
  }
  majBoutonSon();
}

function majBoutonSon() {
  const v = $("film");
  $("remettre-son").hidden = !(v.muted && !v.paused);
  if ("mediaSession" in navigator) navigator.mediaSession.playbackState = v.src && !v.paused ? "playing" : "paused";
}

// Hors de l'écran du résultat, le lecteur est vidé (et pas seulement mis en pause) : sinon le lecteur des
// notifications du téléphone relançait le son d'une vidéo cachée.
function viderFilm() {
  const v = $("film");
  if (!v || !v.getAttribute("src")) return;
  v.pause();
  v.removeAttribute("src");
  v.load();
}

// Plein écran dans le sens du film (retour du 28/10) : paysage pour un film 4:3 ou 16:9, portrait pour un 9:16.
// Sans ça, un film paysage restait affiché en hauteur, minuscule. Tourner le téléphone en paysage pendant qu'un
// film paysage est à l'écran le passe aussi en plein écran (comme les lecteurs vidéo habituels).
function sensDuFilm() {
  const v = $("film");
  const o = screen.orientation;
  if (!o?.lock) return;   // iPhone : le lecteur natif tourne déjà tout seul
  // Retour du 29/10 (Pixel 9) : tout verrou empêchait de revenir dans l'autre sens. Plus aucun verrou : le téléphone
  // décide, et surRotation choisit la version (portrait = verticale, paysage = horizontale). Déverrouillage refait
  // un peu plus tard : Chrome Android verrouille de lui-même une vidéo paysage mise en plein écran.
  const libre = () => { try { o.unlock(); } catch { /* rien à libérer */ } };
  libre();
  if (v.getAttribute("src")) setTimeout(libre, 400);
}
document.addEventListener("fullscreenchange", sensDuFilm);
document.addEventListener("webkitfullscreenchange", sensDuFilm);
$("film").addEventListener("loadedmetadata", sensDuFilm);   // film suivant (glisser) alors qu'on est en plein écran
// Changer de version (vertical / horizontal) en gardant le moment de lecture et l'état lecture / pause.
function changerFormat(k, auto) {
  const v = $("film");
  etat.reprise = v.getAttribute("src") ? { t: v.currentTime, lecture: !v.paused && !v.ended } : null;
  etat.format = k;
  etat.formatAuto = auto;
  afficherFilm();
}
$("film").addEventListener("loadedmetadata", () => {
  const r = etat.reprise;
  if (!r) return;
  etat.reprise = null;
  const v = $("film");
  try { v.currentTime = r.t; } catch { /* durée inconnue : on reste au début */ }
  if (r.lecture) v.play?.().catch(() => {}); else v.pause();
});
// Rotation du téléphone sur l'écran résultat : paysage = version horizontale (plein écran tenté), portrait = retour.
// matchMedia marche partout (iPhone compris), screen.orientation peut manquer.
function surRotation() {
  const v = $("film");
  if (etat.ecran !== "resultat" || !etat.resultat || !v.getAttribute("src")) return;
  const films = etat.resultat.films || {};
  if (matchMedia("(orientation: landscape)").matches) {
    if (etat.format === "vertical" && films.horizontal) changerFormat("horizontal", true);
    if (!document.fullscreenElement && v.requestFullscreen) {
      v.requestFullscreen().then(() => { etat.pleinAuto = true; }).catch(() => {});
    }
    setTimeout(() => v.scrollIntoView({ block: "center" }), 250);   // repli si le plein écran est refusé
  } else {
    // En plein écran, le film suit toujours le téléphone ; hors plein écran, seul un changement automatique est défait.
    const plein = document.fullscreenElement || document.webkitFullscreenElement;
    if ((etat.formatAuto || plein) && etat.format === "horizontal" && films.vertical) changerFormat("vertical", false);
    etat.formatAuto = false;
    if (etat.pleinAuto && document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
    etat.pleinAuto = false;
  }
}
matchMedia("(orientation: landscape)").addEventListener?.("change", surRotation);

// --- Passer d'un souvenir à l'autre : glisser du doigt, flèches, ou lecteur des notifications ---
const filmsFinis = () => galerie.films.filter((f) => f.etape === "fini");

function filmVoisin(sens) {
  const liste = filmsFinis();
  const i = liste.findIndex((f) => f.id === etat.resultat?.id);
  return i < 0 ? null : liste[i + sens] || null;
}

function majNavigation() {
  const liste = filmsFinis();
  const i = liste.findIndex((f) => f.id === etat.resultat?.id);
  $("film-precedent").hidden = !filmVoisin(-1);
  $("film-suivant").hidden = !filmVoisin(1);
  $("film-position").hidden = liste.length < 2 || i < 0;
  $("film-position").textContent = t("resultat.position", { i: i + 1, n: liste.length });
}

function changerFilm(sens) {
  const f = filmVoisin(sens);
  if (!f) return;
  const lecteur = $("lecteur");
  lecteur.classList.remove("vers-gauche", "vers-droite");
  void lecteur.offsetWidth;                       // relance l'animation de glissement
  lecteur.classList.add(sens > 0 ? "vers-gauche" : "vers-droite");
  resultat(f);
}

(function glisser() {
  const zone = $("lecteur");
  let x0 = null, y0 = 0;
  zone.addEventListener("touchstart", (e) => { x0 = e.touches[0].clientX; y0 = e.touches[0].clientY; }, { passive: true });
  zone.addEventListener("touchend", (e) => {
    if (x0 == null) return;
    const dx = e.changedTouches[0].clientX - x0, dy = e.changedTouches[0].clientY - y0;
    x0 = null;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy)) changerFilm(dx < 0 ? 1 : -1);
  }, { passive: true });
})();

// Le lecteur des notifications pilote toujours la vidéo visible de Stillive : « lecture » hors du résultat
// ouvre le dernier souvenir fini, « suivant » et « précédent » passent d'un souvenir à l'autre.
(function lecteurNotifications() {
  if (!("mediaSession" in navigator)) return;
  const ms = navigator.mediaSession;
  const regle = (action, fn) => { try { ms.setActionHandler(action, fn); } catch { /* action inconnue ici */ } };
  regle("play", () => {
    if (etat.ecran === "resultat" && $("film").getAttribute("src")) { lancerFilm(); return; }
    const f = etat.resultat && filmsFinis().some((x) => x.id === etat.resultat.id) ? etat.resultat : filmsFinis()[0];
    if (f) resultat(f);
  });
  regle("pause", () => { $("film").pause(); });
  regle("stop", () => { $("film").pause(); });
  regle("nexttrack", () => { if (etat.ecran === "resultat") changerFilm(1); });
  regle("previoustrack", () => { if (etat.ecran === "resultat") changerFilm(-1); });
})();

// Va chercher sur l'ordinateur la photo d'un film (ouvert depuis la galerie : elle n'est plus dans etat).
async function photoDuFilm(f) {
  if (!f.photo) return null;
  try {
    const cle = cleSecrete();
    const r = await fetch(f.photo, cle ? { headers: { "X-Stillive-Cle": cle } } : {});
    if (!r.ok) return null;
    const blob = await r.blob();
    return new File([blob], `photo_${f.id}.jpg`, { type: blob.type.startsWith("image/") ? blob.type : "image/jpeg" });
  } catch { return null; }
}

// « Refaire avec un autre effet » : rouvre la porte du film avec ses réglages (scène, geste, petit mot, durée),
// la personne n'a qu'à changer ce qu'elle veut.
async function refaire() {
  const f = etat.resultat;
  const porte = f?.porte === "entrer" || f?.porte === "souvenir" ? f.porte : etat.porte;
  nouvelleCreation();
  const garde = !!f && etat.film === f.id;           // les photos de etat sont bien celles de ce film
  const reglages = () => {
    if (DUREES.includes(f.secondes)) etat.secondes = f.secondes;
    if (LOOKS.includes(f.look)) etat.look = f.look;
    majDuree();
    rendreAvance();
  };
  if (f && porte === "souvenir" && (f.scene || f.geste || f.recette === "temps" || f.recette === "boucle")) {
    const parGeste = GESTES_SOUVENIR.find((c) => c.geste && c.geste === f.geste);   // geste retiré ou inconnu : on passe au reste
    etat.choix = parGeste ? parGeste.cle : f.recette === "temps" ? "temps" : f.recette === "boucle" ? "boucle" : f.scene ? "scene" : "auto";
    if (etat.choix === "scene") {
      $("scene").value = f.scene || "";
      if (f.idee?.resume && f.scene) {                 // idée de l'IA non retouchée : le souhait anglais repartira tel quel
        $("scene").value = f.idee.resume;
        etat.idees.choisie = { resume: f.idee.resume, souhait: f.scene };
      }
    }
    if (parGeste && PRECISIONS[parGeste.geste] && f.geste_valeur) etat.gesteValeur[parGeste.geste] = String(f.geste_valeur);
    rendreGestes();
    $("mot-s").value = f.mot || "";
    reglages();
    rendreIdees();
    ouvrirPorte("souvenir");
    if (!(garde && etat.photos.length === 1)) {
      const fichier = await photoDuFilm(f);
      if (fichier) await ajouterPhotos([fichier], true);
    }
    return;
  }
  if (f && porte === "entrer") {
    $("phrase").value = f.scene || "";
    etat.gestes.entrer = codeGeste(GESTES_ENTRER, f.geste || null);
    if (f.geste === "entrer" && ["naturel", "surprise"].includes(f.geste_valeur)) etat.gesteValeur.entrer = f.geste_valeur;
    rendreGestes();
    $("mot-e").value = f.mot || "";
    reglages();
    ouvrirPorte("entrer");
    if (!(garde && etat.image)) {
      const fichier = await photoDuFilm(f);
      if (fichier) { etat.image = fichier; majPhotos(); retenir("souvenir", fichier); }
    }
    return;
  }
  ouvrirPorte(porte);
}

// Phrase claire pour un film raté : selon le code du serveur, sinon le texte général.
function texteFilmRate(f) {
  return f.code && aTexte(`erreur.${f.code}`) ? t(`erreur.${f.code}`) : t("erreur.film");
}

function erreur(message, detail, filmId, historique = true, code = null) {
  if (etat.cleRequise) return montrer("cle", false);       // sans le code, rien d'autre à dire
  $("erreur-texte").textContent = message || t("erreur.inconnue");
  const technique = String(detail || "");               // détail pour le dépannage, replié et raccourci
  $("erreur-detail-texte").textContent = technique.length > 300 ? `${technique.slice(0, 300)}…` : technique;
  $("erreur-detail").hidden = !technique;
  $("erreur-detail").open = false;
  etat.filmRate = filmId || null;       // film raté : on peut l'enlever de « Mes souvenirs »
  $("erreur-supprimer").hidden = !filmId;
  $("erreur-forcer").hidden = !(filmId && code === "pas_enfant");   // « Lancer quand même » : saute la vérification de l'enfant
  montrer("erreur", historique);
}

// --- Scan d'une photo papier ---
// Le serveur trouve les 4 coins (stillive/scan.py) ; l'utilisateur les ajuste au doigt, puis le tirage
// redressé entre dans la liste des photos par le chemin normal (ajouterPhotos).
const scan = { id: null, coins: [], alertes: [], poignees: [], remplacer: true };

function scanTracer() {
  $("scan-polygone").setAttribute("points", scan.coins.map(([x, y]) => `${x},${y}`).join(" "));
  scan.poignees.forEach((p, i) => {
    p.style.left = `${scan.coins[i][0] * 100}%`;
    p.style.top = `${scan.coins[i][1] * 100}%`;
  });
}

function scanPoignees() {
  const cadre = $("scan-cadre");
  cadre.querySelectorAll(".poignee").forEach((p) => p.remove());
  scan.poignees = scan.coins.map((_, i) => {
    const p = document.createElement("div");
    p.className = "poignee";
    p.onpointerdown = (e) => { p.setPointerCapture(e.pointerId); e.preventDefault(); };
    p.onpointermove = (e) => {
      if (!p.hasPointerCapture(e.pointerId)) return;
      const r = cadre.getBoundingClientRect();
      scan.coins[i] = [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
                       Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))];
      scanTracer();
    };
    cadre.append(p);
    return p;
  });
}

function scanAfficherAlertes(alertes) {
  $("scan-alertes").hidden = !alertes.length;
  $("scan-alerte-texte").textContent = alertes.map((a) => t(`scan.alerte_${a}`)).join(" ");
}

async function scanEnvoyer(fichier) {
  if (!fichier) return;
  montrer("scan-cadre");
  $("scan-etat").textContent = t("scan.analyse");
  for (const id of ["scan-cadre", "scan-alertes", "scan-aide", "scan-valider"]) $(id).hidden = true;
  try {
    const corps = new FormData();
    corps.append("photo", fichier, fichier.name || "scan.jpg");
    const { d } = await appelerApi("/api/scan", { method: "POST", body: corps });
    Object.assign(scan, { id: d.id, coins: d.coins, alertes: d.alertes });
    const img = $("scan-image");
    await new Promise((ok, ko) => { img.onload = ok; img.onerror = () => ko(new Error(t("erreur.scan_illisible"))); img.src = `/api/scan/${d.id}/image`; });
    $("scan-etat").textContent = "";
    $("scan-cadre").hidden = false;
    $("scan-aide").hidden = false;
    $("scan-valider").hidden = false;
    scanPoignees();
    scanTracer();
    scanAfficherAlertes(d.alertes);
  } catch (e) {
    erreur(e instanceof TypeError ? t("erreur.reseau") : e.message);
  }
}

async function scanValider() {
  $("scan-valider").disabled = true;
  $("scan-etat").textContent = t("scan.redresse");
  try {
    const { r } = await appelerApi(`/api/scan/${scan.id}/redresser`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ coins: scan.coins }),
    });
    const blob = await r.blob();
    scan.id = null;
    await ajouterPhotos([new File([blob], `scan_${Date.now()}.jpg`, { type: "image/jpeg" })], scan.remplacer);
    ouvrirPorte("souvenir");
  } catch (e) {
    erreur(e instanceof TypeError ? t("erreur.reseau") : e.message);
  } finally {
    $("scan-valider").disabled = false;
  }
}

// --- Stillive pas prêt : l'installation se fait sur l'ordinateur (web/ordinateur.html) ---
// Le téléphone montre seulement une carte et se rafraîchit seul ; si la route échoue, il ne bloque rien.
const prep = { minuterie: null, bloque: false };

function majPrep(d) {
  const impossible = !!d && !!d.compat && d.compat.ok === false;
  const manque = !!d && !!d.h3 && Number(d.h3.a_telecharger_go) > 0;
  prep.bloque = !!d && (impossible || d.pret === false || manque);
  prep.impossible = impossible;
  prep.entrerManque = !!d && !!d.h3 && Number(d.h3.a_telecharger_ref2va_go) > 0;   // « Entrer dans une image » pas installée
  rendrePrep();
}

function rendrePrep() {
  $("carte-prep").hidden = !prep.bloque;
  $("prep-titre").textContent = t(prep.impossible ? "prep.titre_impossible" : "prep.titre");
  $("prep-aide").textContent = t(prep.impossible ? "prep.aide_impossible" : "prep.aide");
  document.querySelector(".portes").hidden = prep.bloque;
  document.querySelector("#ecran-accueil .sous-titre").hidden = prep.bloque;
  for (const id of ["porte-souvenir", "creer-souvenir", "creer-entrer"]) $(id).disabled = prep.bloque;
  $("porte-entrer").disabled = prep.bloque || !!prep.entrerManque;     // grisée, avec son explication
  $("porte-entrer").querySelector(".porte-texte").textContent = t(prep.entrerManque ? "porte.entrer.manque" : "porte.entrer.texte");
}

async function verifierPrep() {
  try {
    majPrep((await appelerApi("/api/moteur")).d);
  } catch { majPrep(null); }          // réseau ou route en échec : on ne bloque pas
}

// --- Démarrage ---
document.querySelectorAll("[data-aller]").forEach((b) => { b.onclick = () => montrer(b.dataset.aller); });
$("porte-souvenir").onclick = () => ouvrirPorte("souvenir");
$("porte-entrer").onclick = () => ouvrirPorte("entrer");

$("choisir").onchange = (e) => { const f = premiereImage(e.target.files); ajouterPhotos(f ? [f] : e.target.files, true); e.target.value = ""; };
$("choisir-plus").onchange = (e) => { ajouterPhotos(e.target.files, false); e.target.value = ""; };
$("choisir-image").onchange = (e) => { choisirUne(e.target.files, (f) => { etat.image = f; }, "souvenir"); e.target.value = ""; };
$("choisir-moi").onchange = (e) => { choisirUne(e.target.files, (f) => { etat.moi = f; }, "visage"); e.target.value = ""; };
// « ou choisir une photo » est DANS la case du selfie : ce bouton ouvre la galerie au lieu de l'appareil photo
$("case-moi-galerie").onclick = (e) => { e.preventDefault(); e.stopPropagation(); $("choisir-moi").click(); };
$("choisir-selfie").onchange = (e) => { choisirUne(e.target.files, (f) => { etat.moi = f; }, "visage"); e.target.value = ""; };
$("choisir-corps").onchange = (e) => { choisirUne(e.target.files, (f) => { etat.corps = f; }, "corps"); e.target.value = ""; };
$("prendre-corps").onchange = (e) => { choisirUne(e.target.files, (f) => { etat.corps = f; }, "corps"); e.target.value = ""; };
// Chaque champ photo est remis à zéro AVANT son ouverture : choisir deux fois de suite la même photo (ou
// revenir sur une case déjà remplie) déclenche toujours le choix, sans recharger la page.
document.querySelectorAll('input[type="file"]').forEach((champ) => { champ.addEventListener("click", () => { champ.value = ""; }); });
// « × » d'une case remplie : la vide.
document.querySelectorAll(".case-retirer").forEach((b) => {
  b.onclick = () => {
    if (b.dataset.case === "souvenir") etat.photos = []; else etat[b.dataset.case] = null;
    majPhotos();
  };
});
$("film-seul").onclick = () => { etat.photos = etat.photos.slice(0, 1); majPhotos(); };

$("scanner").onclick = () => { scan.remplacer = true; montrer("scan-conseils"); };
$("scanner-autre").onclick = () => { scan.remplacer = false; montrer("scan-conseils"); };
$("scan-retour").onclick = () => ouvrirPorte("souvenir");
$("scan-annuler").onclick = () => ouvrirPorte("souvenir");
$("scan-prise").onchange = (e) => { scanEnvoyer(e.target.files[0]); e.target.value = ""; };
$("scan-garder").onclick = () => { $("scan-alertes").hidden = true; };
$("scan-valider").onclick = scanValider;

$("creer-souvenir").onclick = creerSouvenir;
$("idees-demander").onclick = demanderIdees;
$("idees-autres").onclick = demanderIdees;
$("partager-film").onclick = partagerFilm;
$("scene").addEventListener("input", rendreIdees);
$("creer-entrer").onclick = creerEntrer;
$("film-precedent").onclick = () => changerFilm(-1);
$("film-suivant").onclick = () => changerFilm(1);
// Accueil : toucher l'appareil photo ou un polaroïd fait briller les deux portes (rien ne reste sans réponse).
$("scene-accueil").onclick = () => {
  const portes = document.querySelector(".portes");
  portes.classList.remove("appel");
  void portes.offsetWidth;
  portes.classList.add("appel");
  portes.scrollIntoView({ block: "center", behavior: "smooth" });
};
$("refaire").onclick = refaire;
$("vers-accueil").onclick = () => montrer("accueil");
$("remettre-son").onclick = () => { $("film").muted = false; $("film").volume = 1; majBoutonSon(); };
["volumechange", "play", "pause"].forEach((n) => $("film").addEventListener(n, majBoutonSon));
document.querySelectorAll(".tout-effacer:not(.tout-voir)").forEach((b) => { b.onclick = toutEffacer; });
document.querySelectorAll("label[for]").forEach((l) => {      // choix de photo : atteignables au clavier
  const champ = document.getElementById(l.getAttribute("for"));
  if (champ && champ.type === "file") rendreAccessible(l);
});
$("supprimer").onclick = () => supprimerFilm(etat.resultat?.id);
$("erreur-supprimer").onclick = () => supprimerFilm(etat.filmRate);
$("reessayer").onclick = () => ouvrirPorte(etat.porte);
$("erreur-forcer").onclick = async () => {
  const id = etat.filmRate;
  if (!id) return;
  try {
    await appelerApi(`/api/films/${id}/forcer`, { method: "POST" });
    etat.film = id;                     // le film repart en attente : on le suit comme à sa création
    localStorage.setItem("stillive.film", id);
    commencerAttente();
  } catch (e) {
    erreur(e instanceof TypeError ? t("erreur.reseau") : e.message, "", id, true, "pas_enfant");
  }
};
$("prevenir").onclick = demanderPrevenir;
$("notifs").onclick = basculerNotifs;
$("attente-autre").onclick = () => montrer("accueil");
$("logo").onclick = (e) => { e.preventDefault(); montrer("accueil"); };
$("nav-accueil").onclick = () => montrer("accueil");
// « Retour » : l'écran d'avant (comme le bouton du téléphone), sinon l'accueil
$("nav-retour").onclick = () => { if ((history.state?.i || 0) > 0) history.back(); else montrer("accueil"); };

// « La suite plus bas » : visible quand l'écran continue sous le bord, tant qu'on n'a pas fait défiler
function majPlusBas() {
  const reste = document.documentElement.scrollHeight - (window.scrollY + window.innerHeight);
  $("plus-bas").hidden = reste < 160 || window.scrollY > 60 || ["attente", "cle"].includes(etat.ecran);
}
window.addEventListener("scroll", majPlusBas, { passive: true });
window.addEventListener("resize", majPlusBas);
$("plus-bas").onclick = () => window.scrollBy({ top: window.innerHeight * 0.7, behavior: "smooth" });
// Bouton « Partager » à côté de la langue : code QR pour qu'une autre personne ouvre Stillive (PC ou téléphone)
function fermerPartage() {
  $("partage-voile").hidden = true;
  document.removeEventListener("keydown", touchePartage, true);
}
const touchePartage = (e) => { if (e.key === "Escape") fermerPartage(); };
$("partage").onclick = async () => {
  const voile = $("partage-voile");
  $("partage-qr").hidden = true;
  $("partage-absent").hidden = true;
  voile.hidden = false;
  document.addEventListener("keydown", touchePartage, true);
  $("partage-fermer").focus();
  let adresse = null;
  try {
    const r = await fetch("/api/partage");
    adresse = r.ok ? (await r.json()).adresse : null;
  } catch { /* adresse absente : message affiché ci-dessous */ }
  let hote = "";
  try { hote = new URL(adresse).hostname; } catch { /* adresse absente ou invalide */ }
  if (!adresse || !hote || hote === "localhost" || hote.startsWith("127.") || hote === "[::1]") {
    $("partage-absent").hidden = false;
    return;
  }
  $("partage-qr").replaceChildren(qrSvg(adresse, t("partage.alt")));
  $("partage-qr").hidden = false;
};
$("partage-fermer").onclick = fermerPartage;
$("partage-voile").onclick = (e) => { if (e.target === $("partage-voile")) fermerPartage(); };
$("langue").onclick = () => chargerLangue(LANGUES[(LANGUES.indexOf(langue) + 1) % LANGUES.length]);
// Retour sur la page (téléphone rallumé, onglet repris) : tout se remet à jour tout de suite.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) return;
  document.title = "Stillive";
  if (etat.ecran === "attente") suivre(); else chargerGalerie();
});
history.replaceState({ ecran: "accueil", i: 0 }, "");
etat.ecran = "accueil";
document.body.dataset.ecran = "accueil";
chargerReserve();
chargerLangue(langue).then((ok) => {
  if (!ok) return;                                        // textes absents : l'écran de secours est affiché
  const encours = localStorage.getItem("stillive.film");   // page rouverte pendant une fabrication
  if (encours) { etat.film = encours; ajouterAuxFilms(encours); commencerAttente(); }
  const demande = new URLSearchParams(location.search).get("film");   // ouverte par la notification
  if (demande) ouvrirFilmParId(demande); else chargerGalerie();
  verifierPrep();
});
// Toutes les 10 s : l'ordinateur a-t-il fini de se préparer ? un film de la galerie a-t-il fini ?
prep.minuterie = setInterval(() => {
  verifierPrep();
  if (etat.ecran !== "attente" && galerie.films.some(enCours)) chargerGalerie();
}, 10000);
