from __future__ import annotations

import asyncio

from app.infrastructure.zadania_wyceny import Postep, ZadaniaWyceny


async def test_ponowne_klikniecie_zwraca_to_samo_zadanie() -> None:
    zadania = ZadaniaWyceny()
    start = asyncio.Event()
    koniec = asyncio.Event()

    async def praca(postep: Postep) -> None:
        postep("Otomoto", 0.3)
        start.set()
        await koniec.wait()

    pierwsze = zadania.uruchom(7, praca)
    await start.wait()
    assert zadania.uruchom(7, praca) is pierwsze
    assert (pierwsze.etap, pierwsze.udzial, pierwsze.gotowe) == ("Otomoto", 0.3, False)
    assert zadania.zakoncz(7) is None  # w toku — nie zdejmujemy

    koniec.set()
    assert pierwsze.zadanie is not None
    await pierwsze.zadanie
    assert pierwsze.gotowe and pierwsze.udzial == 1.0 and pierwsze.blad is None
    assert zadania.zakoncz(7) is pierwsze
    assert zadania.stan(7) is None


async def test_blad_pracy_trafia_do_stanu_i_postep_nie_cofa_sie() -> None:
    zadania = ZadaniaWyceny()

    async def praca(postep: Postep) -> None:
        postep("Model AI", 0.8)
        postep("cofka", 0.2)
        raise RuntimeError("nie udało się dopasować modelu")

    stan = zadania.uruchom(1, praca)
    assert stan.zadanie is not None
    await stan.zadanie
    assert stan.gotowe and stan.blad == "nie udało się dopasować modelu"

    async def druga(postep: Postep) -> None:
        return None

    assert zadania.uruchom(1, druga) is not stan  # po końcu wolno od nowa
