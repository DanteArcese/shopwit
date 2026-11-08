/*
 * UnitPricer client script.
 * web/htmx.min.js is HTMX 2.0.4, vendored unmodified from
 * https://unpkg.com/htmx.org@2.0.4/dist/htmx.min.js; license (0BSD) in HTMX-LICENSE.
 */
"use strict";

// Browsers without <a ping> support get a beacon instead. Never delays navigation.
if (!("ping" in HTMLAnchorElement.prototype)) {
  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[data-offer]");
    if (link && navigator.sendBeacon) {
      navigator.sendBeacon("/click?o=" + encodeURIComponent(link.dataset.offer));
    }
  });
}

// Filters drawer: server renders it open (works without JS); collapse it on narrow screens
// so results come first. Desktop CSS hides the summary, so it stays open there.
(function () {
  var drawer = document.querySelector("details.filters-drawer");
  if (drawer && window.matchMedia && window.matchMedia("(max-width: 47.99rem)").matches) {
    drawer.open = false;
  }
})();
