// Code QR en SVG net (noir sur blanc, avec marge), partagé par la page du téléphone et celle de l'ordinateur.
// Bibliothèque qrcode-generator (MIT, web/vendor/qrcode.js) à charger avant ce fichier.
function qrSvg(texte, alt) {
  const q = qrcode(0, "M");
  q.addData(texte);
  q.make();
  const n = q.getModuleCount(), marge = 4, taille = n + 2 * marge;
  let chemin = "";
  for (let r = 0; r < n; r++) for (let col = 0; col < n; col++) if (q.isDark(r, col)) chemin += `M${col + marge} ${r + marge}h1v1h-1z`;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${taille} ${taille}`);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", alt);
  const fond = document.createElementNS(ns, "rect");
  fond.setAttribute("width", taille); fond.setAttribute("height", taille); fond.setAttribute("fill", "#fff");
  const modules = document.createElementNS(ns, "path");
  modules.setAttribute("d", chemin); modules.setAttribute("fill", "#000");
  svg.append(fond, modules);
  return svg;
}
