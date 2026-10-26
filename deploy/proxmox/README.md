# Proxmox — wdrożenie standalone

Ten katalog uruchamia ten sam kod co add-on Home Assistant, ale bez Ingressu,
s6 i bashio. Uwierzytelnienie zapewnia Cloudflare Access. W danej chwili tylko
jedna instalacja może odpytywać źródła.

## 1. VM i katalogi

Zalecany punkt startowy to minimalny Debian stable, 1 vCPU, 1 GiB RAM,
16 GiB dysku i statyczny adres prywatny. Po przydzieleniu VM host Proxmoxa
powinien nadal mieć przynajmniej 1 GiB wolnej pamięci. Zainstaluj Docker Engine
z wtyczką Compose, sklonuj repozytorium, a następnie:

```bash
sudo install -d -o 10001 -g 10001 \
  /srv/poleasingowe/data /srv/poleasingowe/share
sudo install -d /srv/poleasingowe/postgres
sudo install -d -m 700 /etc/poleasingowe/secrets
sudo cp deploy/proxmox/options.example.json /etc/poleasingowe/options.json
sudo chown 10001:10001 /etc/poleasingowe/options.json
sudo chmod 600 /etc/poleasingowe/options.json

openssl rand -hex 32 | sudo tee \
  /etc/poleasingowe/secrets/postgres_admin_password >/dev/null
openssl rand -hex 32 | sudo tee \
  /etc/poleasingowe/secrets/poleasingowe_app_password >/dev/null
openssl rand -hex 32 | sudo tee \
  /etc/poleasingowe/secrets/grafana_ro_password >/dev/null
sudo chmod 600 /etc/poleasingowe/secrets/*
sudo chown 10001:10001 \
  /etc/poleasingowe/secrets/poleasingowe_app_password

cd deploy/proxmox
cp .env.example .env
chmod 600 .env
```

Ustaw w `.env` statyczny adres VM i właściwe ścieżki. W
`/etc/poleasingowe/options.json` pozostaw `db_password` pusty — podczas startu
zastąpi go Docker Secret. Klucze AI i poświadczenia źródeł można wpisać do
tego pliku; nie wolno dodawać go do Git.

Plik `poleasingowe_app_password` musi pozostać w trybie `0600` i należeć do
UID/GID `10001`, aby bezrootowa aplikacja mogła go odczytać. PostgreSQL kopiuje
wszystkie trzy sekrety przy starcie do prywatnych plików należących do jego
użytkownika, a następnie uruchamia standardowy entrypoint obrazu.

## 2. Czysty start

```bash
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
curl --fail http://ADRES_VM:8099/zdrowie
```

Na pierwszy start pozostaw `sources: []`. Po sprawdzeniu diagnostyki wpisz
docelowe źródła do `options.json` i odtwórz kontener aplikacji:

```bash
docker compose up -d --force-recreate app
```

PostgreSQL nie ma opublikowanego portu. Skrypt pierwszej inicjalizacji tworzy
wyłącznie bazę `poleasingowe`, role `poleasingowe_app` i `grafana_ro`, schematy
`app` i `reporting` oraz ograniczone uprawnienia wymagane przez aplikację.

## 3. Migracja z Home Assistant

1. Zaktualizuj add-on HA do wersji 0.30.0.
2. Ustaw `sources: []`, zrestartuj add-on i w Diagnostyce wybierz
   „Utwórz pakiet migracyjny”.
3. Skopiuj plik z `/share/poleasingowe/migracja` na VM i zatrzymaj add-on HA
   wraz z jego autostartem.
4. Uruchom tylko bazę i odtwórz pakiet:

   ```bash
   cd deploy/proxmox
   docker compose up -d postgres
   ./scripts/restore-package.sh /ścieżka/poleasingowe-migracja-*.tar
   docker compose up -d app
   ```

5. Porównaj diagnostykę, liczbę aukcji, najnowsze daty i zdjęcia. Dopiero potem
   włącz źródła na Proxmoxie.

Skrypt weryfikuje manifest i SHA-256, wymaga zatrzymanej aplikacji i odmawia
nadpisania niepustej bazy lub archiwum. `--replace` jest świadomą, destrukcyjną
zgodą na zastąpienie istniejących danych.

Jeżeli obok archiwum znajduje się plik `.tar.sha256`, skrypt sprawdza również
sumę całego pakietu przed otwarciem. Skrypty inicjalizacyjne PostgreSQL działają
tylko przy pierwszym tworzeniu pustego katalogu danych; późniejsza podmiana
plików sekretów wymaga także kontrolowanej zmiany haseł odpowiednich ról.

Powrót do HA wykonuje się w odwrotnej kolejności: źródła Proxmoxa wyłączyć,
utworzyć pakiet, zatrzymać aplikację, odtworzyć dump i archiwum w HA, a dopiero
potem uruchomić add-on. Dwa dispatchery nigdy nie mogą działać jednocześnie.

## 4. Cloudflare i zapora

W Cloudflare Tunnel ustaw publiczny hostname `poleasingowe.mazurowski.xyz`
z originem `http://ADRES_VM:8099` oraz obowiązkową polityką Cloudflare Access.
Nie używaj adresu IP kontenera Docker — jest nietrwały.

Na firewallu Proxmoxa dopuść TCP/8099 wyłącznie ze statycznego adresu VM/LXC,
na której działa `cloudflared`. Regułę zastosuj na vNIC VM, ponieważ reguły
Dockera potrafią omijać prostą konfigurację UFW wewnątrz gościa.

## 5. Backup, aktualizacja i awaria

- Dobowe dumpy są w `/srv/poleasingowe/share/poleasingowe/backup` i mają
  retencję siedmiu kopii.
- Backup VM/PBS musi objąć katalogi `postgres`, `data` i `share`.
- Co najmniej raz po wdrożeniu odtwórz pakiet na środowisku testowym.
- Aktualizacja idzie automatycznie z CI (§6). Ręcznie — zawsze z konkretnym
  tagiem:

  ```bash
  # Zmień POLEASINGOWE_VERSION w .env, potem:
  docker compose pull app
  docker compose up -d app
  docker compose ps
  ```

- W razie błędu wróć do poprzedniego tagu obrazu. PostgreSQL pozostaje na
  wersji głównej 17; jego aktualizacji nie łącz z aktualizacją aplikacji.
- Po pierwszej publikacji ustaw pakiet `ghcr.io/maz00r/poleasingowe` jako
  publiczny w ustawieniach GitHub Packages; VM nie powinna przechowywać
  tokenu do pobierania publicznego obrazu.
- Po wdrożeniu zmierz RSS/CPU aplikacji i pamięć całej VM. Limity aplikacji
  pozostają takie jak dla HA: 170 MB w spoczynku i 250 MB w szczycie.

## 6. Automatyczny deploy z GitHub Actions

Po wypchnięciu tagu `vX.Y.Z` CI publikuje obraz, a job `deploy-proxmox`
łączy się po SSH przez Cloudflare Tunnel z LXC i uruchamia
`scripts/deploy-wersji.sh X.Y.Z`. Skrypt podmienia `POLEASINGOWE_VERSION`
w `.env`, pobiera obraz, odtwarza kontener aplikacji i czeka, aż `/zdrowie`
zgłosi dostępną bazę. Gdy się nie doczeka, wraca do poprzedniej wersji, a job
w CI kończy się błędem. Bez skonfigurowanych sekretów job się pomija.

Klucz CI ma w `authorized_keys` **wymuszoną komendę**: nie da się nim
otworzyć powłoki, przekierować portu ani uruchomić niczego poza deployem
wersji w formacie `X.Y.Z`.

### W LXC (jednorazowo)

```bash
apt install -y openssh-server curl util-linux
git -C /opt/poleasingowe pull --ff-only   # skrypt deploy-wersji.sh
SSH_ORIGINAL_COMMAND=0.32.6 /opt/poleasingowe/deploy/proxmox/scripts/deploy-wersji.sh
```

Na swoim komputerze utwórz osobny klucz tylko dla CI:

```bash
ssh-keygen -t ed25519 -N "" -C github-deploy -f ~/.ssh/poleasingowe-deploy
```

Skopiuj wynik `cat ~/.ssh/poleasingowe-deploy.pub` i w LXC dopisz go
z wymuszoną komendą (`WKLEJ_KLUCZ` zamień na skopiowaną linię, zostaw
pojedyncze cudzysłowy):

```bash
install -d -m 700 /root/.ssh
printf 'command="/opt/poleasingowe/deploy/proxmox/scripts/deploy-wersji.sh",restrict %s\n' \
  'WKLEJ_KLUCZ' >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

### Cloudflare Zero Trust

1. Trasa `ssh-poleasingowe.mazurowski.xyz` → `ssh://ADRES_LXC:22`.
   Tunel z dodatku Cloudflared w Home Assistant: w opcjach dodatku dopisz
   do `additional_hosts` pozycję `hostname: ssh-poleasingowe.mazurowski.xyz`,
   `service: ssh://ADRES_LXC:22` i zrestartuj dodatek — DNS utworzy sam
   (HA musi widzieć LXC na porcie 22; na firewallu Proxmoxa dopuść TCP/22
   z adresu HA).
   Tunel zarządzany z panelu: Tunnel → Public hostname. Inny tunel zarządzany
   lokalnie (panel mówi „locally managed”): w `config.yml` cloudflared dopisz
   w `ingress:` — **przed** końcowym `- service: http_status:404`:

   ```yaml
     - hostname: ssh-poleasingowe.mazurowski.xyz
       service: ssh://ADRES_LXC:22
   ```

   potem `cloudflared tunnel ingress validate`,
   `cloudflared tunnel route dns NAZWA_TUNELU ssh-poleasingowe.mazurowski.xyz`
   i `systemctl restart cloudflared`.
2. Access → Service Auth → utwórz token usługi (Client ID i Client Secret).
3. Access → Applications → Self-hosted dla tego hostname'u, polityka
   z akcją **Service Auth** obejmująca tylko ten token.

Linia `known_hosts` dla CI (w LXC):

```bash
awk '{print "ssh-poleasingowe.mazurowski.xyz", $1, $2}' \
  /etc/ssh/ssh_host_ed25519_key.pub
```

### GitHub

Settings → Environments → `proxmox` (można dodać wymóg zatwierdzenia).

- Variables: `DEPLOY_SSH_HOST` = `ssh-poleasingowe.mazurowski.xyz`,
  `DEPLOY_SSH_USER` = `root`.
- Secrets: `DEPLOY_SSH_KEY` (zawartość `~/.ssh/poleasingowe-deploy`),
  `DEPLOY_KNOWN_HOSTS` (linia z `awk` wyżej), `CF_ACCESS_CLIENT_ID`,
  `CF_ACCESS_CLIENT_SECRET`.

Job działa tylko na tagach z tego repozytorium — pull requesty (także
z forków) nie dostają sekretów środowiska i nie uruchamiają deployu.
