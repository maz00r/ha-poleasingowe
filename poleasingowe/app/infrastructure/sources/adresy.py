"""Wybór adresu strony aukcji (SPEC.md §12).

Adaptery umieją **złożyć** adres szczegółów z samego identyfikatora, ale
w bazie mamy adres, który serwis podał nam sam — z listy. Ten drugi jest
pewniejszy i to on ma pierwszeństwo.

Powód nie jest teoretyczny. Adapter EFL składał `/Auction/x-id<id>`,
zakładając, że serwis routuje po samej końcówce identyfikatora niezależnie
od sluga. Dla poleasingowe.pl taki skrót **sprawdzono** w rekonesansie
i opisano w kodzie; dla EFL nie ma o nim ani słowa w RECON.md — a to właśnie
przy EFL nie działały zdjęcia. Błąd pobrania galerii jest połykany (brak
zdjęć nie ma prawa zepsuć karty aukcji), więc taka pomyłka nie daje żadnego
objawu poza pustym miejscem na zdjęcia.

Adres z bazy pochodzi z naszego parsera listy, nie od przeglądarki — ale
`ten_sam_serwis` i tak go sprawdza. Wiersz z podmienionym adresem kazałby
add-onowi pobrać dowolny host, a moduł zdjęć jest jedynym miejscem, gdzie
add-on pobiera treść wskazaną przez dane, a nie przez kod.
"""

from __future__ import annotations

from urllib.parse import urlsplit


def ten_sam_serwis(url: str | None, bazowy: str) -> bool:
    """Czy adres wskazuje na ten sam host co adres bazowy adaptera."""
    if not url:
        return False
    adres, baza = urlsplit(url), urlsplit(bazowy)
    return adres.scheme in ("http", "https") and adres.netloc == baza.netloc


def strona_aukcji(url: str | None, *, bazowy: str, zapasowa: str) -> str:
    """Adres strony aukcji: z bazy, gdy wiarygodny; inaczej złożony.

    `zapasowa` to ścieżka względna adaptera — nadal potrzebna, bo galeria
    bywa wołana dla aukcji zapisanych, zanim adres trafił do bazy.
    """
    return url if url is not None and ten_sam_serwis(url, bazowy) else zapasowa
