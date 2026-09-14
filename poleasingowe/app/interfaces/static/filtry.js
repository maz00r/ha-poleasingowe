/* Interakcje rozwijanych filtrów.
 *
 * Listy są natywnymi <details>, więc bez JavaScriptu nadal pozwalają wybrać
 * kilka wartości. Skrypt dodaje wyłącznie wygodę: kliknięcie poza listą ją
 * zamyka, a kliknięcie wewnątrz nie przerywa wyboru kolejnych pól.
 */
(() => {
  const selektor = "details.wybor-wielu[open]";

  const zamknijPoza = (cel) => {
    document.querySelectorAll(selektor).forEach((wybor) => {
      if (!wybor.contains(cel)) wybor.open = false;
    });
  };

  document.addEventListener("pointerdown", (zdarzenie) => {
    zamknijPoza(zdarzenie.target);
  });

  document.addEventListener("keydown", (zdarzenie) => {
    if (zdarzenie.key !== "Escape") return;
    document.querySelectorAll(selektor).forEach((wybor) => {
      wybor.open = false;
    });
  });
})();
