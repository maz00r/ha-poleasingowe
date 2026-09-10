/* Powrót na listę w to samo miejsce (SPEC.md §12).
 *
 * Add-on siedzi w ramce Home Assistanta, więc przycisk „wstecz" przeglądarki
 * cofa CAŁY panel, a nie zawartość ramki — dlatego dodatek musi mieć własny.
 *
 * Samo cofnięcie to za mało. Lista dokłada kolejne pozycje HTMX-em, więc po
 * powrocie trzeba odtworzyć DWIE rzeczy: ile stron było doładowanych i gdzie
 * stała strona. Bez tego wraca się na pierwsze pięćdziesiąt ofert i na samą
 * górę, czyli tam, gdzie użytkownika nie było.
 *
 * Stan trzymamy w `sessionStorage`, pod kluczem z adresu listy razem
 * z filtrami: wracając do INNEGO zestawu filtrów nie chcemy cudzej pozycji.
 * `sessionStorage` znika razem z kartą przeglądarki i to jest właściwy czas
 * życia takiej informacji — to stan patrzenia, nie dane.
 */
(function () {
  "use strict";

  var KLUCZ = "poleasingowe:powrot";
  var MAKS_STRON = 20; // bezpiecznik: 20 × 50 pozycji

  function adresListy() {
    return location.pathname + location.search;
  }

  function zapisz() {
    try {
      sessionStorage.setItem(
        KLUCZ,
        JSON.stringify({
          adres: adresListy(),
          y: Math.round(window.scrollY),
          strony: document.querySelectorAll(".lista .oferta").length,
        })
      );
    } catch (e) {
      /* Tryb prywatny albo zablokowane dane witryny — trudno. */
    }
  }

  function odczytaj() {
    try {
      var surowe = sessionStorage.getItem(KLUCZ);
      return surowe ? JSON.parse(surowe) : null;
    } catch (e) {
      return null;
    }
  }

  // --- Lista: zapamiętaj miejsce i odtwórz je po powrocie ------------------

  function obsluzListe() {
    var siatka = document.querySelector(".lista");
    if (!siatka) return;

    // Zapisujemy przy wyjściu z listy, a nie przy każdym przewinięciu:
    // jedno zdarzenie zamiast setek.
    siatka.addEventListener("click", function (zdarzenie) {
      if (zdarzenie.target.closest("a[href]")) zapisz();
    });
    window.addEventListener("pagehide", zapisz);

    var stan = odczytaj();
    if (!stan || stan.adres !== adresListy()) return;

    var docelowo = Math.min(stan.strony, MAKS_STRON * 50);
    var prob = 0;

    // Gdy stan wymaga tylko pierwszej strony, lista jest już odtworzona.
    // Nie rejestrujemy wtedy `htmx:afterSwap`: taki globalny nasłuch
    // reagował później na zwykłe kliknięcie „Wczytaj kolejne 50” i cofał
    // widok do starego `stan.y`, często równego zero.
    if (siatka.querySelectorAll(".oferta").length >= docelowo) {
      window.scrollTo(0, stan.y);
      return;
    }

    function poPodmianie() {
      window.scrollTo(0, stan.y);
    }

    function zakonczOdtwarzanie() {
      document.body.removeEventListener("htmx:afterSwap", poPodmianie);
      window.scrollTo(0, stan.y);
    }

    // Ponawianie zamiast jednego kliknięcia: HTMX podpina przycisk we
    // własnym `DOMContentLoaded`, a klik wykonany zanim to zrobi jest
    // zwykłym kliknięciem w martwy przycisk — bez żądania i bez zdarzenia,
    // na które można by czekać. Zmierzone: pierwsza próba potrafi trafić
    // przed inicjalizacją HTMX-a i wtedy lista zostawała na 50 pozycjach.
    function dociagnij() {
      var teraz = siatka.querySelectorAll(".oferta").length;
      var przycisk = document.querySelector("#dalej .wczytaj-wiecej");
      if (teraz >= docelowo || !przycisk || prob >= 40) {
        zakonczOdtwarzanie();
        return;
      }
      prob += 1;
      przycisk.click();
      setTimeout(dociagnij, 250);
    }

    // Każda doładowana strona wywołuje `htmx:afterSwap`; zanim następny takt
    // pobierze ewentualną kolejną, przywracamy zapamiętaną pozycję.
    document.body.addEventListener("htmx:afterSwap", poPodmianie);
    dociagnij();
  }

  // --- Karta aukcji: przycisk wstecz --------------------------------------

  function obsluzKarte() {
    var wstecz = document.querySelector("[data-wstecz]");
    if (!wstecz) return;
    wstecz.addEventListener("click", function (zdarzenie) {
      // `history.back()` jest lepsze niż zwykły link: przeglądarka sama
      // przywraca stronę z pamięci, razem z doładowanymi kafelkami.
      // Gdy nie ma dokąd wracać (karta otwarta z linku), zostaje `href`.
      if (window.history.length > 1) {
        zdarzenie.preventDefault();
        window.history.back();
      }
    });
  }

  function uruchom() {
    obsluzListe();
    obsluzKarte();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", uruchom);
  } else {
    uruchom();
  }
})();
