/* Układ listy: kafelki, wiersze albo tabela.
 *
 * Wybór trzymamy w ciasteczku, a nie w `localStorage`, bo serwer musi go znać
 * przy renderowaniu: inaczej każde wejście na listę zaczynałoby się od
 * mignięcia kafelków. `path=/`, bo pod Ingressem aplikacja żyje pod prefiksem
 * z tokenem, a wybór ma przetrwać jego zmianę.
 */
(function () {
  "use strict";

  var ROK_S = 365 * 86400;
  var KLASY = { lista: "lista-wiersze", tabela: "lista-tabela" };
  var WYSOKOSC_PODGLADU = 190;

  document.addEventListener("click", function (zdarzenie) {
    var przycisk = zdarzenie.target.closest("[data-uklad]");
    if (!przycisk) return;

    var uklad = przycisk.dataset.uklad;
    var lista = document.querySelector(".lista");
    if (lista) {
      Object.keys(KLASY).forEach(function (nazwa) {
        lista.classList.toggle(KLASY[nazwa], nazwa === uklad);
      });
    }

    document.querySelectorAll("[data-uklad]").forEach(function (element) {
      var wybrany = element === przycisk;
      element.classList.toggle("aktywny", wybrany);
      element.setAttribute("aria-pressed", wybrany ? "true" : "false");
    });

    document.cookie =
      "poleasingowe_uklad=" + uklad + "; path=/; max-age=" + ROK_S + "; samesite=lax";
  });

  // Podgląd zdjęcia w tabeli otwiera się w dół; przy dolnej krawędzi okna
  // zostałby ucięty, więc wtedy idzie do góry.
  document.addEventListener("mouseover", function (zdarzenie) {
    var wiersz = zdarzenie.target.closest(".lista-tabela .oferta");
    if (!wiersz) return;
    var dol = wiersz.getBoundingClientRect().bottom;
    wiersz.classList.toggle("podglad-gora", dol + WYSOKOSC_PODGLADU > window.innerHeight);
  });
})();
