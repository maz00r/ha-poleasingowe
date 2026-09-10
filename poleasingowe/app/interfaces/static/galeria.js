/* Galeria zdjęć w karcie aukcji.
 *
 * Fragment galerii wstawia HTMX, dlatego kliknięcia obsługuje jeden nasłuch
 * dokumentu. Dzięki temu działa także po odświeżeniu karty aukcji w miejscu.
 */
(() => {
  const selektorOtwarcia = "[data-galeria-otworz]";
  const selektorModalu = "[data-galeria-modal]";

  const kontrolki = (modal) =>
    Array.from(modal.closest("[data-galeria]").querySelectorAll(selektorOtwarcia));

  const pokaz = (modal, indeks) => {
    const zdjecia = kontrolki(modal);
    if (!zdjecia.length) return;

    const biezacy = (indeks + zdjecia.length) % zdjecia.length;
    const przycisk = zdjecia[biezacy];
    const obraz = modal.querySelector("[data-galeria-obraz]");
    const licznik = modal.querySelector("[data-galeria-licznik]");
    const poprzednie = modal.querySelector("[data-galeria-wstecz]");
    const nastepne = modal.querySelector("[data-galeria-dalej]");

    obraz.src = przycisk.dataset.galeriaSrc;
    obraz.alt = przycisk.querySelector("img").alt;
    licznik.textContent = `Zdjęcie ${biezacy + 1} z ${zdjecia.length}`;
    poprzednie.hidden = zdjecia.length < 2;
    nastepne.hidden = zdjecia.length < 2;
    modal.dataset.galeriaIndeks = String(biezacy);
  };

  const otworz = (przycisk) => {
    const galeria = przycisk.closest("[data-galeria]");
    const modal = galeria.querySelector(selektorModalu);
    pokaz(modal, Number(przycisk.dataset.galeriaIndeks));
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("galeria-otwarta");
    modal.querySelector("[data-galeria-zamknij]").focus();
  };

  const zamknij = (modal) => {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    if (!document.querySelector(`${selektorModalu}:not([hidden])`)) {
      document.body.classList.remove("galeria-otwarta");
    }
  };

  document.addEventListener("click", (zdarzenie) => {
    const otwieracz = zdarzenie.target.closest(selektorOtwarcia);
    if (otwieracz) {
      otworz(otwieracz);
      return;
    }

    const modal = zdarzenie.target.closest(selektorModalu);
    if (!modal) return;
    if (zdarzenie.target.closest("[data-galeria-zamknij]")) {
      zamknij(modal);
      return;
    }

    const przesuniecie = zdarzenie.target.closest("[data-galeria-wstecz]")
      ? -1
      : zdarzenie.target.closest("[data-galeria-dalej]")
        ? 1
        : 0;
    if (przesuniecie) {
      pokaz(modal, Number(modal.dataset.galeriaIndeks) + przesuniecie);
    }
  });

  document.addEventListener("keydown", (zdarzenie) => {
    const modal = document.querySelector(`${selektorModalu}:not([hidden])`);
    if (!modal) return;
    if (zdarzenie.key === "Escape") {
      zamknij(modal);
    } else if (zdarzenie.key === "ArrowLeft") {
      pokaz(modal, Number(modal.dataset.galeriaIndeks) - 1);
    } else if (zdarzenie.key === "ArrowRight") {
      pokaz(modal, Number(modal.dataset.galeriaIndeks) + 1);
    }
  });
})();
