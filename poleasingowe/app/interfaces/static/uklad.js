/* Układ listy: kafelki albo wiersze.
 *
 * Wybór trzymamy w ciasteczku, a nie w `localStorage`, bo serwer musi go znać
 * przy renderowaniu: inaczej każde wejście na listę zaczynałoby się od
 * mignięcia kafelków. `path=/`, bo pod Ingressem aplikacja żyje pod prefiksem
 * z tokenem, a wybór ma przetrwać jego zmianę.
 */
(function () {
  "use strict";

  var ROK_S = 365 * 86400;

  document.addEventListener("click", function (zdarzenie) {
    var przycisk = zdarzenie.target.closest("[data-uklad]");
    if (!przycisk) return;

    var uklad = przycisk.dataset.uklad;
    var lista = document.querySelector(".lista");
    if (lista) lista.classList.toggle("lista-wiersze", uklad === "lista");

    document.querySelectorAll("[data-uklad]").forEach(function (element) {
      var wybrany = element === przycisk;
      element.classList.toggle("aktywny", wybrany);
      element.setAttribute("aria-pressed", wybrany ? "true" : "false");
    });

    document.cookie =
      "poleasingowe_uklad=" + uklad + "; path=/; max-age=" + ROK_S + "; samesite=lax";
  });
})();
