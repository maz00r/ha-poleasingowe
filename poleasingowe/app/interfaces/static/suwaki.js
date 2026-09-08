/* Suwak zakresu z dwoma uchwytami (SPEC.md §12).
 *
 * Bez biblioteki i bez frameworka — kilkadziesiąt linii na coś, co inaczej
 * ciągnęłoby za sobą zależność większą niż cały interfejs.
 *
 * ZASADA: HTML działa bez tego pliku. W szablonie stoją dwa zwykłe pola
 * liczbowe („od" i „do"); skrypt dorabia do nich tor z uchwytami i trzyma
 * jedno z drugim w zgodzie. Gdy JavaScript nie wstanie — bo Ingress, bo
 * stara przeglądarka, bo cokolwiek — filtr nadal da się ustawić ręcznie
 * i formularz nadal wyśle te same parametry.
 *
 * Druga zasada, mniej oczywista: pole puste znaczy „nie filtruj", a nie
 * „skrajna wartość". Suwak przesunięty do końca CZYŚCI pole, zamiast
 * wpisywać granicę. Inaczej każdy adres niósłby `rocznik_od=2008` i po
 * dojściu nowych, starszych aut lista po cichu by się zawężała.
 */
(function () {
  "use strict";

  function ustaw(suwak) {
    var min = Number(suwak.dataset.min);
    var max = Number(suwak.dataset.max);
    var jednostka = suwak.dataset.jednostka || "";
    var poleOd = suwak.querySelector("[data-suwak-od]");
    var poleDo = suwak.querySelector("[data-suwak-do]");
    var podglad = suwak.querySelector("[data-suwak-podglad]");
    if (!poleOd || !poleDo || !(max > min)) return;

    var tor = document.createElement("div");
    tor.className = "suwak-tor";
    tor.innerHTML =
      '<div class="suwak-zakres"></div>' +
      '<input type="range" class="suwak-uchwyt" aria-hidden="true" tabindex="-1">' +
      '<input type="range" class="suwak-uchwyt" aria-hidden="true" tabindex="-1">';
    var wypelnienie = tor.querySelector(".suwak-zakres");
    var uchwyty = tor.querySelectorAll(".suwak-uchwyt");
    var uchwytOd = uchwyty[0];
    var uchwytDo = uchwyty[1];

    [uchwytOd, uchwytDo].forEach(function (u) {
      u.min = String(min);
      u.max = String(max);
      u.step = "1";
    });
    uchwytOd.value = String(poleOd.value === "" ? min : poleOd.value);
    uchwytDo.value = String(poleDo.value === "" ? max : poleDo.value);

    suwak.insertBefore(tor, suwak.querySelector(".suwak-pola"));
    suwak.classList.add("suwak-gotowy");

    function procent(wartosc) {
      return ((Number(wartosc) - min) / (max - min)) * 100;
    }

    function odswiez() {
      var od = Number(uchwytOd.value);
      var doo = Number(uchwytDo.value);
      wypelnienie.style.left = procent(od) + "%";
      wypelnienie.style.right = 100 - procent(doo) + "%";
      if (podglad) podglad.textContent = od + "–" + doo + jednostka;
    }

    // Uchwyty nie mogą się minąć: „od 200 do 150" nie jest zakresem, tylko
    // pustym wynikiem, którego użytkownik nie umiałby sobie wytłumaczyć.
    function zsynchronizuj(zrodlo) {
      var od = Number(uchwytOd.value);
      var doo = Number(uchwytDo.value);
      if (od > doo) {
        if (zrodlo === uchwytOd) uchwytDo.value = String(od);
        else uchwytOd.value = String(doo);
      }
      poleOd.value = Number(uchwytOd.value) === min ? "" : uchwytOd.value;
      poleDo.value = Number(uchwytDo.value) === max ? "" : uchwytDo.value;
      odswiez();
    }

    [uchwytOd, uchwytDo].forEach(function (u) {
      u.addEventListener("input", function () {
        zsynchronizuj(u);
      });
    });

    // Ręczne wpisanie liczby ma przesunąć uchwyt — pola i tor to jeden
    // filtr pokazany na dwa sposoby, a nie dwa niezależne.
    poleOd.addEventListener("change", function () {
      uchwytOd.value = String(poleOd.value === "" ? min : poleOd.value);
      zsynchronizuj(uchwytOd);
    });
    poleDo.addEventListener("change", function () {
      uchwytDo.value = String(poleDo.value === "" ? max : poleDo.value);
      zsynchronizuj(uchwytDo);
    });

    odswiez();
  }

  function uruchom(korzen) {
    (korzen || document).querySelectorAll("[data-suwak]").forEach(function (s) {
      if (!s.classList.contains("suwak-gotowy")) ustaw(s);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      uruchom(document);
    });
  } else {
    uruchom(document);
  }

  // Filtry przychodzą zwykłym GET-em, ale panel bywa też podmieniany przez
  // HTMX — wtedy nowe suwaki trzeba uzbroić ponownie.
  document.body.addEventListener("htmx:afterSwap", function (zdarzenie) {
    uruchom(zdarzenie.target);
  });
})();
