// Stillive dans ComfyUI : aucune icône, aucun menu. Si Stillive est à préparer, un seul petit message
// (fermable) propose d'ouvrir la page de préparation dans un nouvel onglet. Une fois tout prêt : plus rien.
// L'état vient de GET /api/stillive_etat (comfy_nodes/__init__.py).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const TEXTES = {
  fr: { message: "Stillive se prépare : il reste quelques éléments à installer.", ouvrir: "Préparer Stillive", fermer: "Plus tard" },
  en: { message: "Stillive is getting ready: a few things are left to install.", ouvrir: "Set up Stillive", fermer: "Later" },
};
const texte = TEXTES[(navigator.language || "fr").toLowerCase().startsWith("fr") ? "fr" : "en"];

function afficherMessage(port) {
  const boite = document.createElement("div");
  boite.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9999;max-width:320px;padding:12px 14px;"
    + "background:#FFF4E2;color:#2b2118;border:1px solid #d9c3a0;border-radius:10px;font:14px sans-serif;"
    + "box-shadow:0 4px 14px rgba(0,0,0,.3);";
  const phrase = document.createElement("p");
  phrase.textContent = texte.message;
  phrase.style.cssText = "margin:0 0 10px;";
  const ouvrir = document.createElement("a");
  ouvrir.href = `${location.protocol}//${location.hostname}:${port}/web/ordinateur.html`;
  ouvrir.target = "_blank";
  ouvrir.rel = "noopener";
  ouvrir.textContent = texte.ouvrir;
  ouvrir.style.cssText = "display:inline-block;padding:6px 12px;margin-right:8px;background:#c8412b;color:#fff;"
    + "border-radius:16px;text-decoration:none;";
  ouvrir.onclick = () => boite.remove();
  const fermer = document.createElement("button");
  fermer.type = "button";
  fermer.textContent = texte.fermer;
  fermer.style.cssText = "padding:6px 10px;border:0;background:none;color:#2b2118;cursor:pointer;text-decoration:underline;";
  fermer.onclick = () => boite.remove();
  boite.append(phrase, ouvrir, fermer);
  document.body.append(boite);
}

app.registerExtension({
  name: "Stillive.Message",
  setup() {
    // Sans attendre : le message est facultatif et ne doit jamais retarder le chargement de ComfyUI.
    setTimeout(async () => {
      try {
        const r = await api.fetchApi("/stillive_etat");
        if (!r.ok) return;
        const d = await r.json();
        if (d.a_preparer) afficherMessage(Number(d.port) || 8765);
      } catch { /* Stillive absent ou éteint : on ne dérange pas */ }
    }, 3000);
  },
});
