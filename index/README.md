# Sprawdzanie domeny: indeks Google i gotowość strony

`check_index.py` odpowiada na trzy pytania, które zadajesz przy kupnie domeny albo przed publikacją strony:

1. **Czy domena jest w indeksie Google** i w których krajach (zapytanie `site:domena` w google.pl, google.de…).
2. **Czy jest na 1. miejscu**, czyli czy strona główna jest 1. wynikiem `site:` i które miejsce zajmuje domena
   po wpisaniu jej nazwy (np. „test.pl”).
3. **Czy strona jest gotowa dla ludzi**: czy działa, ma HTTPS, nie blokuje Google i nie jest parkingiem.
   Do tego wiek domeny i jej historia.

Pod raportem każdej domeny jest werdykt: **OK**, **UWAGA**, **PROBLEM** albo **WOLNA** (domenę można zarejestrować).

Działa w terminalu (`check_index.py`) i jako **bot Telegram na przyciskach** (`telegram_bot.py`, opis niżej).

## Start

1. Potrzebny jest tylko Python 3.9+, nic nie trzeba instalować.
2. Klucz API w pliku `.env` (wzór: `.env.example`). Możesz podać kilka (patrz „Kilka kluczy API”):
   - SerpApi: https://serpapi.com/manage-api-key (250 darmowych zapytań miesięcznie)
   - Serper.dev: https://serper.dev (2500 darmowych zapytań na start)
3. Uruchom `python3 check_index.py test.pl`.

Możesz wpisać samą nazwę: `savowin` oznacza `savowin.com`. Inną końcówkę ustawisz przez `--koncowka pl`.

Bez klucza też coś sprawdzisz: `--bez-api` uruchamia same darmowe testy (strona, rejestracja, archiwum).

## Najczęstsze użycia

```bash
python3 check_index.py test.pl                          # pełny raport: 30 krajów + strona + rejestracja + archiwum
python3 check_index.py test.pl --kraje pl               # tylko Polska (1–6 zapytań)
python3 check_index.py savowin --kraje pl               # sama nazwa = savowin.com; pozycja dla „savowin”
python3 check_index.py savowin --szybko                 # tryb szybki: tylko „savowin” na 1. stronie, 1 zapytanie na kraj
python3 check_index.py -f domeny.txt --bez-api          # darmowa wstępna selekcja wielu domen (0 zapytań)
python3 check_index.py -f domeny.txt --kraje pl --csv wyniki.csv   # porównanie domen, wyniki do Excela
python3 check_index.py test.pl --pozycja                # pozycja na własną nazwę we wszystkich krajach
python3 check_index.py test.pl --fraza "tanie buty" --kraje pl     # pozycja dla własnej frazy
python3 check_index.py nowa-domena.pl --potwierdz       # powtórz każde „NIE” (nowe domeny są chwiejne)
python3 check_index.py --historia test.pl               # jak zmieniał się indeks przy kolejnych sprawdzeniach
python3 check_index.py --konto                          # ile zostało zapytań na każdym kluczu (za darmo)
python3 check_index.py --help                           # wszystkie opcje
```

Plik z domenami: jedna domena na linię, `#` zaczyna komentarz. Adresy typu `https://www.test.pl/abc`
są skracane do `test.pl`.

## Bot Telegram

Bot robi to samo co skrypt, ale sterujesz nim przyciskami. Jedyna komenda to `/start`, a domenę albo samą nazwę
wysyłasz zwykłą wiadomością. Kilka domen naraz wyślij w osobnych liniach.

Uruchomienie:
1. Na Telegramie napisz do **@BotFather**: `/newbot`, nadaj botowi nazwę i skopiuj token.
2. Wpisz token do `.env`: `TELEGRAM_BOT_TOKEN=123456789:AA…` (klucze API są w tym samym pliku).
3. Uruchom `python3 telegram_bot.py` i zostaw działające. Bot działa, dopóki działa ten program.
   Żeby chodził w tle, użyj np. `tmux` albo usługi systemd.
4. W Telegramie wyślij botowi `/start`. **Pierwsza osoba, która to zrobi, zostaje właścicielem** i tylko ona
   może z niego korzystać. Kolejne osoby dopiszesz do `TELEGRAM_ALLOWED_USERS` (ich ID pokaże im bot).

Przyciski:
- **Ustawienia**:
  - ⚡ tryb: szybki (domyślny, 1 zapytanie na kraj) albo pełny, a w szybkim przełącznik „site: gdy brak na 1. str.”;
  - kraje (tylko PL, PL/DE/UK/US, 30 głównych, wszystkie 90 albo ręczny wybór z flagami);
  - główny rynek;
  - pozycja na nazwę (rynek / wszędzie / wyłączona) i „do strony” (1/3/5/10);
  - powtórki NIE i końcówka dla samej nazwy;
  - fraza, darmowe testy (strona, rejestr, archiwum) oraz Google API (wł./wył. i wybór dostawcy).
- **Wynik**: Kraje (tabela), Strona, Domena, Archiwum, Wszystkie uwagi, CSV (dwa pliki), Sprawdź ponownie,
  Potwierdź NIE i link do ręcznego sprawdzenia w Google.
- **Statystyki**: sprawdzenia i zużyte zapytania (dziś, 7 dni, łącznie), werdykty, ostatnie domeny.
- **Historia**: poprzednie sprawdzenia domeny, wspólne z terminalem. **Konto API**: stan każdego klucza. **Pomoc**.

Przy sprawdzeniu za ponad 100 zapytań bot pyta o zgodę. Kolejne domeny czekają w kolejce. Klucze z `.env` bot czyta
przy każdym sprawdzeniu, więc dopisany klucz działa bez restartu. Ustawienia i statystyki zapisuje w `bot_stan.json`.

## Kilka kluczy API

W `.env` wpisz kolejne klucze:

```
SERPAPI_KEY=pierwszy_klucz
SERPAPI_KEY_2=drugi_klucz
SERPAPI_KEY_3=trzeci_klucz
```

Działa też wersja w jednej linii: `SERPAPI_KEY=pierwszy,drugi`. Dla Serpera to samo: `SERPER_API_KEY_2=…`.

Jak to działa:
- Przed startem skrypt sprawdza każdy klucz przez darmowe Account API SerpApi.
  Wyczerpany albo nieprawidłowy klucz jest pomijany od razu i nie marnuje zapytań.
- Gdy w trakcie pracy SerpApi odpowie `HTTP 429` („Your account has run out of searches.”) albo `401`
  (zły klucz), skrypt przechodzi na następny klucz i powtarza to samo zapytanie.
  Wypisuje przy tym, czy skończył się limit miesięczny, czy godzinowy.
- Serper: przy „Not enough credits” albo złym kluczu przejście na następny klucz działa tak samo.
- Na końcu raportu widać, ile zapytań poszło z każdego klucza i ile na nim zostało.
  Na ekranie pojawia się tylko numer klucza i jego 4 ostatnie znaki.

## Tryb szybki i tryb pełny

| | Tryb szybki (`--szybko`, w bocie domyślny) | Tryb pełny (domyślny w terminalu) |
|---|---|---|
| Co wysyła do Google | samą nazwę („savowin”), tylko 1. strona wyników | `site:savowin.com` + samą nazwę do wybranej strony |
| Co mówi | czy savowin.com jest na 1. stronie i na którym miejscu, w każdym kraju | czy jest w indeksie, czy strona główna jest 1. w `site:`, pozycja na nazwę |
| Koszt | **1 zapytanie na kraj** | 1 na kraj + 1–3 na pozycję |
| Gdy domeny nie ma na 1. stronie | nie wiadomo, czy jest w indeksie; `--sprawdz-indeks` dopyta wtedy `site:` (1 zapytanie więcej, tylko tam) | wiadomo z `site:` |

Przy kupnie domeny tryb szybki od razu odpowiada, czy domena jest na 1. miejscu na swoją nazwę.
Pełny przydaje się, gdy trzeba wiedzieć, dlaczego nie jest.

## Jak czytać raport

### Indeks Google

| Kolumna | Znaczenie |
|---|---|
| **Indeks** | TAK: Google zwrócił strony z tej domeny. NIE: brak wyników. **TAK\***: znaleziona dopiero w powtórnym zapytaniu (wynik niestabilny). BŁĄD: nie udało się sprawdzić. |
| **Wyniki** | Szacunek Google (np. „~56 100 000”), jeśli go podał. „10+” znaczy co najmniej 10 stron. |
| **1. w site:** | TAK: strona główna jest 1. wynikiem (dobrze). NIE: pierwsza jest podstrona, co przy małych stronach często oznacza filtr. „subdomena”: przy portalach to normalne. |
| **Na nazwę** | Do Google idzie **sama nazwa bez końcówki** (np. „savowin”), a skrypt szuka w wynikach domeny savowin.com i podaje stronę oraz miejsce. Pytanie o pełne „savowin.com” prawie zawsze daje 1. miejsce, więc nic nie mówi. Domyślnie tylko na głównym rynku (google.pl), a `--pozycja` sprawdza wszystkie kraje. Najlepiej „1. miejsce”. Komunikat „brak (str. 1–3)” to mocny sygnał filtra. Dopisek „jest w wiadomościach” znaczy, że domena pojawia się tylko w bloku „Najważniejsze wiadomości”. |

Skrypt ostrzega też, gdy:
- Google usunął wyniki z domeny (DMCA, żądania prawne);
- w zaindeksowanych stronach są podejrzane słowa (kasyno, viagra…) albo chińskie/japońskie znaki (ślad włamania albo dawnego spamu).

Gdy na głównym rynku wyjdzie NIE, raport pokazuje link „Sprawdź ręcznie”, żeby porównać wynik z przeglądarką.

### Nowe domeny: powtórki i wyniki niestabilne

W pierwszych tygodniach różne serwery Google mają różny stan indeksu nowej domeny. To samo zapytanie potrafi
raz ją pokazać, a raz nie, a różnice między krajami są wtedy przypadkowe (to nie filtr krajowy).
Dlatego skrypt sam powtarza wynik NIE na głównym rynku, jeśli domena jest widoczna w innym kraju albo ma mniej niż 90 dni:
1. pyta przez google.com z tym samym krajem;
2. potem wysyła świeże zapytanie, pomijając pamięć podręczną SerpApi.

Jeśli powtórka znajdzie domenę, w tabeli pojawia się **TAK\*** i ostrzeżenie o niestabilnych wynikach.
Jeśli nie znajdzie, widać „NIE potwierdzone powtórką”. `--potwierdz` robi to samo dla wszystkich krajów z NIE.
Najpewniejszy obraz nowej domeny daje sprawdzenie jej ponownie za kilka dni.

### Strona WWW (za darmo)

Skrypt sprawdza:
- DNS i przekierowania (wejście przez `http://` jak w przeglądarce);
- HTTPS i datę wygaśnięcia certyfikatu;
- robots.txt (oceniany według zasad Google) oraz noindex w meta i w nagłówku `X-Robots-Tag`;
- działanie adresu z `www`;
- `meta viewport` (telefony) i czas ładowania;
- tytuł, ilość treści, canonical i meta refresh.

Rozpoznaje też stronę zaparkowaną, „na sprzedaż”, wygasłą, zawieszoną, domyślną stronę serwera i stronę „w budowie”.

**Ochrona przed botami.** Część stron (Cloudflare, cast-sec, DDoS-Guard…) pokazuje automatom ekran z zagadką zamiast treści.
Czasem używa przy tym nietypowego kodu, np. HTTP 666. Skrypt to rozpoznaje i pisze, że treści nie da się ocenić
automatycznie, zamiast zgłaszać awarię strony. W przeglądarce taka strona zwykle działa. Warto tylko upewnić się,
że ochrona przepuszcza Googlebota.

### Rejestracja domeny (za darmo)

Oficjalny RDAP (dla .com, .pl, .uk, .fr, .nl, .cz i nowych końcówek), a dla pozostałych WHOIS. Pokazuje:
- datę rejestracji (wiek domeny), datę wygaśnięcia i rejestratora;
- statusy: `hold` czy `redemption` oznaczają domenę zawieszoną albo tuż przed usunięciem;
- serwery DNS parkingu domen.

Domena, której nie ma w rejestrze, dostaje werdykt **WOLNA**.
Rejestry .de i .eu nie publikują daty rejestracji.

### Archiwum Wayback Machine (za darmo)

Skrypt pokazuje lata, z których są kopie strony głównej, i tytuły strony z kilku momentów historii
(z oznaczeniem spamu i parkingu). Wykrywa też domenę, która kiedyś wygasła i została zarejestrowana ponownie:
historia jest wtedy starsza niż obecna rejestracja.

Czasem archive.org ogranicza zapytania (HTTP 429). Wtedy skrypt pokazuje tylko datę najnowszej kopii.
Pełną historię obejrzysz pod linkiem `https://web.archive.org/web/*/domena`.

### Historia i zmiany

Każde sprawdzenie z zapytaniami do Google zapisuje się w `historia/<domena>.jsonl`. Przy następnym uruchomieniu
raport pokazuje, co się zmieniło, np. `google.de: indeks NIE → TAK` albo `pozycja na nazwę 4. miejsce → 1. miejsce`.
Nowo kupioną domenę sprawdzaj co kilka dni, a zobaczysz, kiedy wejdzie do indeksu.
`--bez-zapisu` wyłącza zapis.

## Koszt (zapytania API)

- Tryb szybki: dokładnie 1 zapytanie na kraj (z `--sprawdz-indeks` plus 1 tam, gdzie brak na 1. stronie).
- Tryb pełny: 1 zapytanie na kraj plus 1–3 zapytania o pozycję na własną nazwę na głównym rynku.
- Powtórka „NIE” na głównym rynku kosztuje do 2 zapytań i dotyczy tylko domen nowych albo widocznych gdzie indziej.
  Stara domena bez indeksu nie kosztuje nic dodatkowo.
- Domyślnie jest 30 krajów, czyli 30–35 zapytań na domenę. Z `--kraje pl` to 1–6 zapytań.
- `--pozycja` i `--fraza` dodają do 3 zapytań na każdy kraj, a `--potwierdz` do 2 zapytań na każdy kraj z NIE.
- SerpApi nie liczy tego samego zapytania powtórzonego w ciągu godziny. Serper liczy każde.
- Powyżej 100 zapytań skrypt pyta o potwierdzenie (`--tak` pomija pytanie). W nagłówku raportu widać,
  ile zapytań zostało łącznie na wszystkich kluczach.

Przy wielu domenach najpierw puść je z `--bez-api` (za darmo). Zapytania do Google zostaw dla tych, które wyglądają obiecująco.

## Warto wiedzieć

- Google ma jeden indeks dla całego świata, więc u starszych domen wynik jest zwykle taki sam we wszystkich krajach.
  Stała różnica oznacza filtrowanie w danym kraju. U nowych domen różnice bywają przypadkowe (patrz wyżej).
- Kraj jest symulowany parametrami Google (`gl`, `hl`, google.xx), a nie adresem IP z tego kraju.
  Wynik w przeglądarce może się różnić: inny serwer Google, personalizacja, zalogowane konto.
- `site:` pokazuje tylko próbkę. Dokładny stan indeksu własnej strony zobaczysz w Google Search Console.
- `www.` jest obcinane, bo `site:test.pl` obejmuje też www i wszystkie subdomeny.

## Pliki

| Plik | Co to jest |
|---|---|
| `check_index.py` | skrypt (i silnik bota) |
| `telegram_bot.py` | bot Telegram |
| `bot_stan.json` | stan bota: właściciel, ustawienia, statystyki (tworzy się sam) |
| `.env` | Twoje klucze API (nie udostępniaj, jest w `.gitignore`) |
| `.env.example` | wzór pliku `.env` |
| `historia/` | zapisane sprawdzenia (tworzy się sam) |
