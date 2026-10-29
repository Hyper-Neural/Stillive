// Stillive : page « ordinateur » (fenêtre Stillive dans ComfyUI). Vérifie l'ordinateur, installe l'analyse des
// photos (Gemma 4 12B) et les modèles vidéo, puis montre le code QR pour ouvrir Stillive sur le téléphone.
// Tous les textes viennent de web/i18n/<langue>.json (le même fichier que la page téléphone).
// Routes : GET /api/moteur, POST /api/moteur/{installer,utiliser,ollama/telecharger}, GET /api/info.
const LANGUES = ["fr", "en"];
const REF2VA_GO = 23;                 // « Entrer dans l'image » : +23 Go (repli si le serveur ne donne pas la taille)
let textes = {};
let langue = localStorage.getItem("stillive.langue") || (navigator.language || "fr").slice(0, 2);
if (!LANGUES.includes(langue)) langue = "fr";
const $ = (id) => document.getElementById(id);
const ui = { d: null, reseau: true, derniere: null, erreurAction: "", quant: null, sigQuants: "", sigProgs: "",
  adresse: null, adresseCherchee: false, qrPour: null, minuterie: null };

function t(cle, vars = {}) {
  const s = textes[cle] ?? cle;
  return s.replace(/\{(\w+)\}/g, (_, k) => vars[k] ?? "");
}
const aTexte = (cle) => cle in textes;
const nb = (go) => Number(go || 0).toLocaleString(langue, { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const goOctets = (octets) => nb(octets / 1e9);

// Lit le fichier de langue (3 essais). Sans aucun texte, un message de secours écrit en dur et un bouton
// « Recharger » s'affichent au lieu d'une page vide.
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

function afficherSecours() {
  if (document.getElementById("secours")) return;
  const boite = document.createElement("div");
  boite.id = "secours";
  boite.className = "secours";
  boite.setAttribute("role", "alert");
  const p = document.createElement("p");
  p.textContent = "Impossible de charger la page. / The page could not load.";
  const b = document.createElement("button");
  b.type = "button";
  b.className = "gros-bouton";
  b.textContent = "Recharger / Reload";
  b.onclick = () => location.reload();
  boite.append(p, b);
  document.body.prepend(boite);
}

async function chargerLangue(code) {
  const lus = await lireLangue(code);
  if (!lus) {
    if (!Object.keys(textes).length) afficherSecours();
    return false;
  }
  document.getElementById("secours")?.remove();
  textes = lus;
  langue = code;
  document.documentElement.lang = code;
  localStorage.setItem("stillive.langue", code);
  $("langue").textContent = code.toUpperCase();
  document.querySelectorAll("[data-t]").forEach((el) => { el.textContent = t(el.dataset.t); });
  document.querySelectorAll("[data-t-label]").forEach((el) => { el.setAttribute("aria-label", t(el.dataset.tLabel)); });
  ui.sigQuants = ui.sigProgs = "";
  ui.qrPour = null;
  rendre();
  return true;
}

// --- Routes ---
async function lire() {
  const r = await fetch("/api/moteur");
  if (!r.ok) throw new Error("moteur");
  return r.json();
}

async function poster(chemin, corps) {
  try {
    const r = await fetch(`/api/moteur/${chemin}`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(corps || {}),
    });
    const d = await r.json().catch(() => ({}));
    return r.ok || "ok" in d ? d : { ok: false, erreur: d.erreur || "reseau" };
  } catch { return { ok: false, erreur: "reseau" }; }
}

// Codes du serveur : injoignable, modele_absent, deja_en_cours, puis les codes communs (erreur.*).
// Jamais de texte brut d'exception : sans phrase connue, le texte de repli.
function texteMoteur(code, repli) {
  if (code && aTexte(`moteur.code.${code}`)) return t(`moteur.code.${code}`);
  if (code && aTexte(`erreur.${code}`)) return t(`erreur.${code}`);
  return t(repli);
}

// --- Lecture de l'état ---
const compat = (d) => d.compat || { ok: true };
const h3Manque = (d) => Number(d.h3 && d.h3.a_telecharger_go) || 0;
const recommandeDetecte = (d) => (d.pret ? null : (d.detectes || []).find((p) => p.modele_recommande) || null);
const llmGo = (d) => (d.pret || recommandeDetecte(d) ? 0 : Number(d.taille_go) || 0);
const toutPret = (d) => d.pret && !h3Manque(d);
const ref2vaManque = (d) => Number(d.h3 && d.h3.a_telecharger_ref2va_go) > 0;
const quantConseillee = (d) => ((d.quants || []).find((q) => q.recommande) || {}).nom || d.quant || null;

function texteRaison(c) {   // raisons du serveur : pas_nvidia, vram_faible, mac, ram_faible, disque, systeme
  return aTexte(`pc.raison.${c.raison}`) ? t(`pc.raison.${c.raison}`, {
    vram: nb(c.vram_go), ram: nb(c.ram_go), libre: nb(c.disque_libre_go), requis: nb(c.disque_requis_go), carte: c.carte || "",
  }) : t("pc.raison.defaut");
}

// Taille de « Entrer dans l'image » : somme des fichiers marqués ref2va, sinon 23 Go
function tailleRef2va(d) {
  const somme = ((d.h3 && d.h3.fichiers) || []).filter((f) => f.option === "ref2va").reduce((s, f) => s + (Number(f.taille_go) || 0), 0);
  return somme || REF2VA_GO;
}

function montrerTexte(id, texte) {
  const p = $(id);
  p.textContent = texte || "";
  p.hidden = !texte;
}

// --- Affichage ---
function rendre() {
  const d = ui.d;
  $("pc-chargement").hidden = !!d || !ui.reseau;
  for (const id of ["bloc-incompat", "bloc-express", "bloc-progres", "bloc-pret", "bloc-avance", "bloc-desinst", "pc-verif", "pc-reessayer", "pc-erreur-aide"]) $(id).hidden = true;
  if (!d) { montrerTexte("pc-message", ui.reseau ? "" : t("erreur.reseau")); return; }

  const inst = d.installation || { etat: "aucune" };
  const enCours = inst.etat === "en_cours";
  const c = compat(d);
  const ok = c.ok !== false;
  const fini = !enCours && toutPret(d);
  const erreur = !enCours && !fini && inst.etat === "erreur";

  if (ok && !enCours && c.carte) montrerTexte("pc-verif", t("pc.verif_ok", { carte: c.carte, vram: nb(c.vram_go) }));
  else if (ok && !enCours && c.ok === true) montrerTexte("pc-verif", t("pc.verif_ok_sans"));
  if (!ok && !fini && !enCours) {
    montrerTexte("pc-incompat", c.raison === "mac" || c.raison === "systeme" ? texteRaison(c) : t("pc.incompatible", { raison: texteRaison(c) }));
    $("bloc-incompat").hidden = false;
  }
  $("bloc-express").hidden = !(ok && !enCours && !fini && !erreur);
  $("bloc-progres").hidden = !enCours;
  const qr = fini || (ok && !enCours && d.pret);   // le code QR dès que l'analyse est prête, même s'il manque des modèles vidéo
  $("bloc-pret").hidden = !qr;
  $("bloc-avance").hidden = enCours;
  $("bloc-desinst").hidden = enCours;
  rendreDesinstaller();

  if (!$("bloc-express").hidden) rendreExpress(d, c);
  if (enCours) rendreProgres(inst);
  if (qr) {
    rendrePret(d);
    document.querySelector("#bloc-pret .etiquette-titre").textContent = t(fini ? "pc.pret" : "pc.pret_partiel");
  }
  if (!enCours) rendreAvance(d, ok);

  $("pc-reessayer").hidden = !erreur;
  $("pc-erreur-aide").hidden = !erreur;
  if (fini && inst.etat === "erreur" && ref2vaManque(d)) {    // l'ajout de « Entrer dans l'image » s'est arrêté
    montrerTexte("pc-message", t("moteur.erreur", { detail: String(inst.message || "").slice(0, 300) }));
  } else if (enCours || fini) { ui.erreurAction = ""; montrerTexte("pc-message", ""); }
  else if (erreur) montrerTexte("pc-message", t("moteur.erreur", { detail: String(inst.message || "").slice(0, 300) }));
  else montrerTexte("pc-message", ui.erreurAction);
}

function rendreExpress(d, c) {
  const llm = llmGo(d), h3 = h3Manque(d), total = llm + h3;
  const q = recommandeDetecte(d);
  montrerTexte("pc-deja", q ? t("pc.dl.analyse_deja", { nom: q.nom }) : "");
  const liste = $("pc-liste");
  liste.innerHTML = "";
  const lignes = [];
  if (llm > 0) lignes.push(t("pc.dl.analyse", { taille: nb(llm) }));
  if (h3 > 0) lignes.push(t("pc.dl.video", { taille: nb(h3) }));
  for (const l of lignes) { const li = document.createElement("li"); li.textContent = l; liste.append(li); }
  montrerTexte("pc-va-telecharger", lignes.length ? t("pc.va_telecharger") : "");
  const libre = c.disque_libre_go;
  const aLibre = libre !== undefined && libre !== null;
  montrerTexte("pc-total", !lignes.length ? t("pc.rien")
    : t(aLibre ? "pc.total" : "pc.total_sans_disque", { taille: nb(total), libre: nb(libre) }));
  montrerTexte("pc-disque", aLibre && lignes.length && libre < total ? t("pc.disque_juste", { libre: nb(libre), taille: nb(total) }) : "");
}

function rendreProgres(inst) {
  const total = inst.total || 0;
  $("pc-barre").parentElement.classList.toggle("indeterminee", !total);     // taille inconnue : barre animée
  $("pc-barre").style.width = total ? `${Math.min(100, (100 * inst.fait) / total)}%` : "";
  $("pc-chiffres").textContent = total ? t("moteur.go", { fait: goOctets(inst.fait), total: goOctets(total) }) : "";
  $("pc-etape").textContent = aTexte(`moteur.etape.${inst.etape}`) ? t(`moteur.etape.${inst.etape}`) : t("moteur.etape.defaut");
  $("pc-fichier").textContent = inst.message || "";
}

function rendrePret(d) {
  $("pc-actuel").textContent = d.modele ? t("moteur.actuel", { modele: d.modele }) : "";
  // L'avertissement ne paraît que si le serveur dit clairement que le modèle choisi n'est pas le recommandé
  // (champ absent ou modèle inconnu : rien à dire).
  montrerTexte("pc-reco", d.pret && d.modele && d.recommande === false ? t("moteur.pas_recommande") : "");
  const ref2va = ref2vaManque(d);
  $("pc-ref2va").hidden = !ref2va || !toutPret(d);          // « Entrer dans l'image » : quand le reste est prêt
  if (ref2va) $("pc-ref2va").textContent = t("pc.ref2va_ajouter", { taille: nb(tailleRef2va(d)) });
  if (ui.adresse) {
    if (ui.qrPour !== ui.adresse) dessinerQR(ui.adresse);
  } else if (!ui.adresseCherchee) {
    chargerAdresse();
  }
  $("pc-qr-absent").hidden = !!ui.adresse || !ui.adresseCherchee;
}

// 127.x, localhost et ::1 ne marchent jamais sur le téléphone : traités comme « pas d'adresse » (nouvel essai plus tard).
function adresseUtilisable(adresse) {
  if (!adresse) return false;
  try {
    const hote = new URL(adresse).hostname;
    return !(hote === "localhost" || hote === "[::1]" || /^127\./.test(hote));
  } catch { return false; }
}

async function chargerAdresse() {
  ui.adresseCherchee = true;
  try {
    const r = await fetch("/api/info");
    const adresse = r.ok ? (await r.json()).adresse : null;     // adresse complète, avec le code (?cle=) pour le QR
    ui.adresse = adresseUtilisable(adresse) ? adresse : null;
  } catch { ui.adresse = null; }
  if (!ui.adresse) setTimeout(() => { ui.adresseCherchee = false; }, 5000);   // on réessaie un peu plus tard
  rendre();
}

// Code QR : bibliothèque qrcode-generator (MIT, web/vendor/), dessin en SVG net, noir sur blanc avec marge.
function dessinerQR(texte) {
  $("pc-qr").replaceChildren(qrSvg(texte, t("pc.qr_alt")));
  $("pc-qr").hidden = false;
  $("pc-adresse").textContent = texte;
  $("pc-adresse").hidden = false;
  $("pc-qr-ou").hidden = false;
  ui.qrPour = texte;
}

// --- Réglages avancés ---
function rendreAvance(d, ok) {
  const quants = d.quants || [];
  const conseillee = quantConseillee(d);
  if (!ui.quant || (quants.length && !quants.some((q) => q.nom === ui.quant))) ui.quant = conseillee;
  $("av-quant-bloc").hidden = !quants.length || !$("av-llm").checked;
  const sig = JSON.stringify(quants) + ui.quant;
  if (sig !== ui.sigQuants) {
    ui.sigQuants = sig;
    const box = $("av-quants");
    box.innerHTML = "";
    for (const q of quants) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "pastille";
      b.setAttribute("aria-pressed", q.nom === ui.quant);
      b.append(q.nom);
      const petit = document.createElement("small");
      petit.textContent = `${nb(q.taille_go)} Go${q.recommande ? ` · ${t("pc.av.conseille")}` : ""}`;
      b.append(petit);
      b.onclick = () => { ui.quant = q.nom; rendreAvance(d, ok); };
      box.append(b);
    }
  }
  $("av-pas-conseille").hidden = !(quants.length && $("av-llm").checked && conseillee && ui.quant !== conseillee);
  $("av-h3-non").hidden = $("av-h3").checked;
  $("av-ref2va-texte").textContent = t("pc.av.ref2va", { taille: nb(tailleRef2va(d)) });
  $("av-lancer").textContent = t(ok ? "pc.av.lancer" : "pc.av.essayer");
  $("av-essayer-aide").hidden = ok;
  // programmes déjà présents
  const sp = JSON.stringify(d.detectes || []) + d.pret;
  if (sp !== ui.sigProgs) {
    ui.sigProgs = sp;
    const box = $("moteur-detectes");
    box.innerHTML = "";
    for (const p of d.detectes || []) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "bouton-trait";
      if (p.modele_recommande) {
        b.textContent = t("moteur.utiliser", { nom: p.nom });
        b.onclick = () => utiliserProgramme({ programme: p.programme, url: p.url, modele: p.modele_recommande }, b);
      } else if (p.programme === "ollama") {
        b.textContent = t("moteur.ollama_ajouter");
        b.onclick = () => lancer("ollama/telecharger", { url: p.url }, b);
      } else continue;
      box.append(b);
    }
    if (!$("moteur-url").value && (d.detectes || []).length) $("moteur-url").value = d.detectes[0].url;
  }
}

// --- Actions ---
const occupe = (b, oui) => { if (b) b.disabled = oui; };

async function lancer(chemin, corps, bouton) {          // installation ou ajout de modèle côté serveur
  ui.erreurAction = "";
  occupe(bouton, true);
  try {
    const r = await poster(chemin, corps);
    if (r.ok === false) {
      ui.erreurAction = r.erreur === "incompatible" && r.raison
        ? t("pc.incompatible", { raison: texteRaison({ ...compat(ui.d), raison: r.raison }) })
        : texteMoteur(r.erreur, "erreur.reseau");
    }
  } finally { occupe(bouton, false); }
  await rafraichir();
}

function optionsExpress(d) {
  return d.pret || recommandeDetecte(d) ? { llm: false } : {};
}

async function express(bouton) {
  const d = ui.d;
  const q = recommandeDetecte(d);
  if (q) {                    // Gemma 4 12B est déjà dans un programme : on le branche, sans rien retélécharger
    occupe(bouton, true);
    const r = await poster("utiliser", { programme: q.programme, url: q.url, modele: q.modele_recommande });
    occupe(bouton, false);
    if (!r.ok) { ui.erreurAction = texteMoteur(r.erreur, "moteur.test_echec"); return rafraichir(); }
  }
  if (!q && !d.pret) ui.derniere = {};
  else ui.derniere = { llm: false };
  if (h3Manque(d) || (!q && !d.pret)) return lancer("installer", ui.derniere, bouton);
  ui.erreurAction = "";
  return rafraichir();
}

function optionsAvancees(d) {
  const o = { h3: $("av-h3").checked, ref2va: $("av-ref2va").checked, llm: $("av-llm").checked };
  if (o.llm && ui.quant) o.quant = ui.quant;
  if (compat(d).ok === false) o.forcer = true;
  return o;
}

async function utiliserProgramme(corps, bouton) {
  montrerTexte("av-message", "");
  occupe(bouton, true);
  let message = "";
  try {
    const r = await poster("utiliser", corps);
    if (!r.ok) message = texteMoteur(r.erreur, "moteur.test_echec");
    else message = r.recommande === false ? `${t("moteur.reussite_autre")} ${t("moteur.pas_recommande")}` : t("moteur.reussite");
  } finally { occupe(bouton, false); }
  await rafraichir();
  montrerTexte("av-message", message);
}

// --- Désinstaller Stillive (routes : GET et POST /api/moteur/desinstaller) ---
// Rien n'est coché par défaut ; suppression en deux temps (bouton, puis « Oui, supprimer »).
const CATEGORIES_DS = ["llama", "llm", "h3", "donnees", "reglages"];
const ds = { apercu: null, coches: new Set(), confirme: false, occupe: false, resultat: null, sig: "" };
const goDs = (go) => Number(go || 0).toLocaleString(langue, { minimumFractionDigits: go < 0.1 ? 2 : 1, maximumFractionDigits: go < 0.1 ? 2 : 1 });

async function chargerApercuDesinstaller() {
  try {
    const r = await fetch("/api/moteur/desinstaller");
    if (!r.ok) throw new Error("apercu");
    ds.apercu = await r.json();
    for (const c of [...ds.coches]) if (!((ds.apercu.categories || {})[c] || {}).nombre) ds.coches.delete(c);
  } catch { ds.apercu = null; }
  rendreDesinstaller();
}

function totalDesinstaller() {
  const cats = (ds.apercu && ds.apercu.categories) || {};
  return [...ds.coches].reduce((s, c) => s + (Number((cats[c] || {}).taille_go) || 0), 0);
}

function rendreDesinstaller() {
  if ($("bloc-desinst").hidden) return;
  const cats = (ds.apercu && ds.apercu.categories) || {};
  const sig = JSON.stringify([cats, [...ds.coches], ds.confirme, ds.occupe, ds.resultat, langue]);
  if (sig === ds.sig) return;
  ds.sig = sig;
  const liste = $("ds-liste");
  liste.innerHTML = "";
  let presentes = 0;
  for (const c of CATEGORIES_DS) {
    const info = cats[c];
    if (!info || !info.nombre) continue;
    presentes++;
    const label = document.createElement("label");
    label.className = "interrupteur";
    const box = document.createElement("input");
    box.type = "checkbox";
    box.checked = ds.coches.has(c);
    box.disabled = ds.confirme || ds.occupe;
    box.onchange = () => { if (box.checked) ds.coches.add(c); else ds.coches.delete(c); rendreDesinstaller(); };
    const texte = document.createElement("span");
    const titre = document.createElement("span");
    titre.textContent = t(`pc.desinstaller.${c}`, { taille: goDs(info.taille_go) });
    texte.append(titre);
    if ((c === "llm" || c === "h3") && (info.noms || []).length) {   // les fichiers exacts, pour décider en connaissance de cause
      const noms = document.createElement("small");
      noms.textContent = t("pc.desinstaller.fichiers", { noms: info.noms.join(", ") });
      texte.append(noms);
    }
    label.append(box, texte);
    liste.append(label);
  }
  $("ds-rien").hidden = presentes > 0 || !ds.apercu;
  const autresNonCoches = CATEGORIES_DS.some((c) => c !== "reglages" && (cats[c] || {}).nombre && !ds.coches.has(c));
  $("ds-reglages-note").hidden = !(ds.coches.has("reglages") && autresNonCoches && (ds.apercu || {}).inscrit);
  $("ds-comfy").textContent = t("pc.desinstaller.comfy", { dossier: (ds.apercu || {}).dossier || "custom_nodes/stillive_nodes" });
  $("ds-bouton").hidden = ds.confirme;
  $("ds-bouton").disabled = !ds.coches.size || ds.occupe;
  $("ds-confirme").hidden = !ds.confirme;
  $("ds-confirme-texte").textContent = ds.occupe ? t("pc.desinstaller.en_cours") : t("pc.desinstaller.confirme", { taille: goDs(totalDesinstaller()) });
  $("ds-oui").disabled = $("ds-non").disabled = ds.occupe;
  const res = $("ds-resultat");
  res.hidden = !ds.resultat;
  res.innerHTML = "";
  for (const ligne of ds.resultat || []) {
    const p = document.createElement("p");
    p.className = ligne.alerte ? "alerte" : "petit";
    p.textContent = ligne.texte;
    res.append(p);
  }
}

function resultatDesinstaller(r) {
  if (r.ok === false && !(r.retire || []).length && !(r.echecs || []).length) {
    const code = `pc.desinstaller.code.${r.erreur}`;
    return [{ alerte: true, texte: aTexte(code) ? t(code) : t("erreur.reseau") }];
  }
  const lignes = [{ texte: (r.retire || []).length ? t("pc.desinstaller.fini", { taille: goDs(r.libere_go) }) : t("pc.desinstaller.fini_rien") }];
  for (const e of r.echecs || []) {
    const nom = String(e.chemin || "").split(/[\\/]/).pop();
    lignes.push({ alerte: true, texte: t(e.raison === "chemin_refuse" ? "pc.desinstaller.echec_refuse" : "pc.desinstaller.echec", { nom }) });
  }
  return lignes;
}

async function supprimerSelection() {
  const categories = [...ds.coches];
  if (!categories.length || ds.occupe) return;
  ds.occupe = true;
  rendreDesinstaller();
  const r = await poster("desinstaller", { categories });
  ds.occupe = false;
  ds.confirme = false;
  ds.coches.clear();
  ds.resultat = resultatDesinstaller(r);
  await chargerApercuDesinstaller();
  await rafraichir();
}

$("bloc-desinst").addEventListener("toggle", () => { if ($("bloc-desinst").open) { ds.resultat = null; chargerApercuDesinstaller(); } });
$("ds-bouton").onclick = () => { if (ds.coches.size) { ds.confirme = true; ds.resultat = null; rendreDesinstaller(); } };
$("ds-non").onclick = () => { ds.confirme = false; rendreDesinstaller(); };
$("ds-oui").onclick = supprimerSelection;

// --- Démarrage ---
async function rafraichir() {
  clearTimeout(ui.minuterie);
  try { ui.d = await lire(); ui.reseau = true; } catch { ui.reseau = false; }
  rendre();
  const enCours = ui.d && (ui.d.installation || {}).etat === "en_cours";
  ui.minuterie = setTimeout(rafraichir, enCours ? 1000 : 4000);
}

$("pc-preparer").onclick = (e) => express(e.currentTarget);
$("pc-reessayer").onclick = (e) => lancer("installer", ui.derniere || optionsExpress(ui.d), e.currentTarget);
$("pc-ref2va").onclick = (e) => {                 // seulement « Entrer dans l'image » : l'analyse et la vidéo sont déjà là
  ui.derniere = { ref2va: true, llm: false };
  lancer("installer", ui.derniere, e.currentTarget);
};
$("av-lancer").onclick = (e) => {
  ui.derniere = optionsAvancees(ui.d);
  lancer("installer", ui.derniere, e.currentTarget);
};
$("av-llm").onchange = () => rendreAvance(ui.d, compat(ui.d).ok !== false);
$("av-h3").onchange = () => rendreAvance(ui.d, compat(ui.d).ok !== false);
$("moteur-autre-lien").onclick = () => { $("moteur-autre").hidden = !$("moteur-autre").hidden; };
$("moteur-tester").onclick = (e) => {
  const url = $("moteur-url").value.trim(), modele = $("moteur-modele").value.trim();
  const prog = (((ui.d && ui.d.detectes) || []).find((p) => p.url === url) || {}).programme || "openai";
  utiliserProgramme({ programme: prog, url, modele }, e.currentTarget);
};
$("langue").onclick = () => chargerLangue(LANGUES[(LANGUES.indexOf(langue) + 1) % LANGUES.length]);
chargerLangue(langue).then((ok) => { if (ok) rafraichir(); });
