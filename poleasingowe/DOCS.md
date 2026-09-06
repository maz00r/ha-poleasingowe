# Aukcje poleasingowe — dokumentacja add-onu

Add-on zbiera oferty z polskich serwisów aukcji samochodów poleasingowych,
trzyma pełną historię cen i udostępnia interfejs operacyjny przez Ingress.

**Add-on jest wyłącznie do odczytu.** Nigdy nie licytuje, nie składa ofert
i nie zakłada kont.

Home Assistant jest tu wyłącznie nośnikiem: daje kontener, panel w bocznym
menu i uwierzytelnienie przez Ingress. Add-on **nie wystawia żadnych encji**,
nie używa MQTT i nie zapisuje się do rejestru urządzeń.

## Gdzie leżą dane — przeczytaj przed pierwszym backupem

**Dane trwałe nie leżą w `/data` tego add-onu.** Są w bazie PostgreSQL,
czyli w wolumenie **add-onu PostgreSQL**, a nie tego.

W `/data` tego add-onu mieszkają wyłącznie: sesje (`/data/sessions/`),
zrzuty debugowe (`/data/debug/`) i cache. Skasowanie ich nie powoduje utraty
historii cen; skasowanie wolumenu PostgreSQL — powoduje.

**Odtworzenie działającej instalacji wymaga obu.** Snapshot samego tego
add-onu nie wystarczy.

Dodatkowo add-on wykonuje własny `pg_dump -Fc` **wyłącznie swojej bazy**
raz na dobę do `/share/poleasingowe/backup/` i trzyma 7 kopii. Ma to sens,
bo snapshot dodatku PostgreSQL obejmuje wszystkie bazy naraz — odtworzenie
z niego cofnęłoby też inne aplikacje korzystające z tego serwera.

## Współdzielony PostgreSQL

Add-on łączy się **wyłącznie** do swojej bazy i nigdy do żadnej innej na tym
samym serwerze. Nie tworzy ani nie usuwa baz, nie zmienia parametrów
globalnych, nie dotyka autovacuum i nie wykonuje `VACUUM FULL`.

### Klucz blokady migracji

Migracje wykonują się pod blokadą doradczą PostgreSQL, żeby dwa procesy nie
weszły sobie w drogę. Blokady doradcze są **wspólne dla całej instancji**,
a nie dla pojedynczej bazy — dlatego klucz musi być na tyle nietypowy, żeby
nie zderzyć się z blokadami migracji innych aplikacji na tym samym serwerze
(np. migracji Ecto).

Wybrana wartość, stała i nigdy niezmieniana:

```
5211429344620168909        (0x4852b760aa21c6cd)
```

Wyprowadzona raz jako:

```python
int.from_bytes(sha256(b"poleasingowe.migrations.v1").digest()[:8], "big", signed=True)
```

Blokada jest **transakcyjna** (`pg_advisory_xact_lock`), więc zwalnia się sama
przy commicie albo rollbacku. Proces ubity w połowie migracji nie zostawia
zawieszonej blokady na współdzielonym serwerze.

Definicja w kodzie: `app/infrastructure/persistence/migrations.py`,
stała `KLUCZ_BLOKADY_MIGRACJI`.
