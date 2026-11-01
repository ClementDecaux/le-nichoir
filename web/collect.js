/* Le Nichoir : bouton « Envoyer au Nichoir » (favori à glisser dans la barre du navigateur).
 *
 * Ce script tourne dans TON navigateur, sur la page d'annonces que tu consultes
 * (LeBonCoin, SeLoger, PAP, Bien'ici, Logic-Immo, Espaces Atypiques, agences...).
 * Il lit les annonces déjà affichées à l'écran et les transmet à ton Nichoir local via une
 * fenêtre http://127.0.0.1:<port>/import.html (postMessage). Aucune requête n'est envoyée
 * au site : seulement ce que tu as déjà sous les yeux.
 */
(function () {
  var NICHOIR = "__NICHOIR_ORIGIN__";
  var PRICE = /(\d{1,3}(?:[\s  .]\d{3})+|\d{5,7})\s*(?:€|euros?)/i;
  var LISTING = /leboncoin\.fr\/(ad\/|vi\/|ventes_immobilieres\/)|seloger\.com\/annonces\/|pap\.fr\/annonces?\/|bienici\.com\/annonce\/|logic-immo\.com\/(detail-vente|annonces?\/)|espaces-atypiques\.com\/.*\d{4,}|\/annonces?[\/-]|\/vente\/|\/bien\/|\/detail/i;

  function txt(el) { return (el.innerText || el.textContent || "").replace(/[ \t ]+/g, " ").trim(); }
  function abs(u) { try { return new URL(u, location.href).href; } catch (e) { return null; } }
  function key(u) { return u.split("?")[0].split("#")[0]; }

  /* 1. Données structurées de LeBonCoin (Next.js) : coordonnées précises. */
  function fromLeboncoin() {
    var el = document.getElementById("__NEXT_DATA__");
    if (!el || location.hostname.indexOf("leboncoin") < 0) return [];
    var ads = [];
    try {
      (function walk(o, depth) {
        if (!o || typeof o !== "object" || depth > 12) return;
        if (Array.isArray(o)) { o.forEach(function (x) { walk(x, depth + 1); }); return; }
        if (o.list_id && o.subject && o.location) { ads.push(o); return; }
        for (var k in o) walk(o[k], depth + 1);
      })(JSON.parse(el.textContent), 0);
    } catch (e) { return []; }
    return ads.map(function (a) {
      var attr = {};
      (a.attributes || []).forEach(function (x) { attr[x.key] = x.value; });
      var loc = a.location || {};
      var imgs = a.images || {};
      return {
        url: a.url || ("https://www.leboncoin.fr/ad/ventes_immobilieres/" + a.list_id),
        title: a.subject, text: [a.subject, a.body, loc.city, loc.zipcode].filter(Boolean).join("\n"),
        price: Array.isArray(a.price) ? a.price[0] : a.price,
        surface: attr.square ? +attr.square : null, rooms: attr.rooms ? +attr.rooms : null,
        land: attr.land_plot_surface ? +attr.land_plot_surface : null,
        city: loc.city || null, postcode: loc.zipcode || null,
        lat: loc.lat || null, lon: loc.lng || null,
        photo: (imgs.urls_large || imgs.urls || [])[0] || imgs.thumb_url || null,
        source: "LeBonCoin"
      };
    });
  }

  /* 2. JSON-LD (schema.org), utilisé par de nombreux sites et agences. */
  function fromJsonLd() {
    var out = [];
    document.querySelectorAll('script[type="application/ld+json"]').forEach(function (s) {
      var data;
      try { data = JSON.parse(s.textContent); } catch (e) { return; }
      [].concat(data).forEach(function (root) {
        [].concat(root["@graph"] || root).forEach(function (d) {
          var items = d.itemListElement ? d.itemListElement.map(function (i) { return i.item || i; }) : [d];
          items.forEach(function (it) {
            var offer = [].concat(it.offers || {})[0] || {};
            var price = offer.price || (offer.priceSpecification || {}).price;
            if (!price || !it.name) return;
            var addr = it.address || (it.itemOffered && it.itemOffered.address) || {};
            var geo = it.geo || addr.geo || {};
            out.push({
              url: abs(it.url || location.href), title: it.name,
              text: [it.name, it.description, addr.addressLocality, addr.postalCode].filter(Boolean).join("\n"),
              price: +price, city: addr.addressLocality || null, postcode: addr.postalCode || null,
              lat: geo.latitude ? +geo.latitude : null, lon: geo.longitude ? +geo.longitude : null,
              photo: [].concat(it.image || [])[0] || null
            });
          });
        });
      });
    });
    return out;
  }

  /* 3. Cartes d'annonces : lien vers une annonce + prix dans le même bloc. */
  function fromCards() {
    var seen = {}, out = [];
    document.querySelectorAll("a[href]").forEach(function (a) {
      var url = abs(a.getAttribute("href"));
      if (!url || !LISTING.test(url) || url.indexOf(location.origin + "/#") === 0) return;
      var k = key(url);
      if (seen[k]) return;
      var card = a, best = null;
      for (var i = 0; i < 8 && card && card !== document.body; i++, card = card.parentElement) {
        var links = card.querySelectorAll("a[href]"), other = false;
        for (var j = 0; j < links.length && !other; j++) {
          var u = abs(links[j].getAttribute("href"));
          other = !!(u && LISTING.test(u) && key(u) !== k);
        }
        if (other) break;  // le bloc contient une autre annonce : on est remonté trop haut
        if (PRICE.test(txt(card))) best = card;
      }
      if (!best) return;
      seen[k] = true;
      var text = txt(best);
      var img = best.querySelector("img[src], img[data-src]");
      out.push({ url: k, title: (txt(a).split("\n")[0] || text).slice(0, 160), text: text.slice(0, 3000),
                 photo: img ? abs(img.getAttribute("src") || img.getAttribute("data-src")) : null });
    });
    return out;
  }

  /* 4. Page d'une seule annonce. */
  function fromDetail() {
    var main = document.querySelector("main, article, [role=main]") || document.body;
    var text = txt(main).slice(0, 6000);
    if (!PRICE.test(text)) return [];
    var img = document.querySelector('meta[property="og:image"]');
    return [{ url: key(location.href), title: document.title, text: text, photo: img ? img.content : null }];
  }

  var listings = fromLeboncoin();
  if (!listings.length) listings = fromJsonLd();
  if (!listings.length) listings = fromCards();
  if (!listings.length) listings = fromDetail();
  if (!listings.length) { alert("Le Nichoir : aucune annonce trouvée sur cette page."); return; }

  var w = window.open(NICHOIR + "/import.html", "nichoir_import");
  if (!w) { alert("Le Nichoir : autorise les fenêtres pop-up pour ce site, puis réessaie."); return; }
  var payload = { type: "nichoir-listings", page: location.href, listings: listings };
  var tries = 0;
  var timer = setInterval(function () {
    try { w.postMessage(payload, NICHOIR); } catch (e) {}
    if (++tries > 60) clearInterval(timer);
  }, 250);
  window.addEventListener("message", function (e) {
    if (e.origin === NICHOIR && e.data === "nichoir-ok") clearInterval(timer);
  });
})();
