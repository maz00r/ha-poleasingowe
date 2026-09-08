"""Prefiks Ingressu Home Assistanta (SPEC.md §7.1).

Osobny moduł, bo potrzebują go i `app.py`, i `web/widoki.py` — a wzajemny
import między nimi jest cyklem.
"""

from __future__ import annotations

from fastapi import Request

NAGLOWEK_INGRESS = "X-Ingress-Path"


def prefiks_ingress(request: Request) -> str:
    """Prefiks, pod którym Home Assistant zamontował add-on.

    Czytamy go **z nagłówka przy każdym żądaniu**, a nie ze `scope`, i to
    jest poprawka po błędzie, który dwa razy z rzędu wyglądał jak „GUI nie
    działa".

    Wcześniej middleware wpisywał prefiks do `scope["root_path"]`. Wygląda to
    naturalnie, ale łamie umowę ASGI: `root_path` ma być **początkiem**
    `scope["path"]`, a Home Assistant prefiks już zdjął, więc `path` go nie
    zawiera. Starlette 0.38 liczy trasę jako `path` z odjętym `root_path`
    i przekazuje `root_path` dalej do podaplikacji — przez co `StaticFiles`
    szukał pliku pod `<katalog>/static/styl.css` i zwracał **404 na arkusz
    stylów i na HTMX-a**, choć zwykłe trasy odpowiadały normalnie. Efekt:
    strona bez stylów i bez JavaScriptu, czyli panel wyglądający na zepsuty,
    w którym gwiazdka obserwacji nic nie robi.

    Nie dotykamy więc `scope` w ogóle. Wszystkie adresy w tej aplikacji
    powstają przez `web.widoki.sciezka`, która bierze prefiks stąd.

    Pusty łańcuch znaczy „uruchomione poza Ingressem" i jest poprawnym
    stanem — wtedy adresy są zwykłymi ścieżkami od korzenia.
    """
    return (request.headers.get(NAGLOWEK_INGRESS) or "").rstrip("/")
