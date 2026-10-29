// Stillive : service de la page, seulement pour la notification « ton souvenir est prêt » (page sécurisée).
// Toucher la notification ramène sur Stillive et ouvre directement ce souvenir.
self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  // data.film : souvenir à ouvrir (vide pour « tes idées sont prêtes ») ; anciennes notifications : le tag.
  const film = e.notification.data ? (e.notification.data.film || "") : (e.notification.tag || "");
  e.waitUntil((async () => {
    const pages = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const page = pages.find((c) => new URL(c.url).origin === self.location.origin);
    if (page) {
      page.postMessage({ ouvrirFilm: film });
      return page.focus();
    }
    return self.clients.openWindow(film ? `/?film=${encodeURIComponent(film)}` : "/");
  })());
});
