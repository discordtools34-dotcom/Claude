#!/usr/bin/env python3
"""Sprawdza domenę: indeks Google w wielu krajach i gotowość strony do publikacji.

Dla każdej domeny:
  • indeks Google w wielu krajach — zapytanie site:domena w google.pl, google.de, …
  • czy strona główna jest 1. wynikiem site: (klasyczny test filtra po zakupie domeny),
  • na którym miejscu jest domena po wpisaniu jej nazwy (np. „test.pl”) i dla podanych fraz,
  • strona WWW: DNS, przekierowania, HTTPS i certyfikat, robots.txt, noindex, parking/„na sprzedaż”,
  • rejestracja (RDAP/WHOIS): wiek domeny, wygaśnięcie, status, serwery DNS, czy jest wolna,
  • archiwum Wayback Machine: lata z kopiami i dawne tytuły (ślady spamu, parkingu),
  • zmiany od poprzedniego sprawdzenia (historia w katalogu historia/).

Zapytania do Google idą przez SerpApi albo Serper.dev (klucz w .env), pozostałe testy są darmowe.
Nie wymaga żadnych bibliotek — wystarczy Python 3.9+.
"""

from __future__ import annotations

import argparse
import csv
import functools
import http.client
import json
import os
import re
import shutil
import socket
import ssl
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
HISTORY_DIR = SCRIPT_DIR / "historia"
API_UA = "check-index/2.0"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/128.0.0.0 Safari/537.36")
IANA_RDAP = "https://data.iana.org/rdap/dns.json"
IANA_WHOIS = "whois.iana.org"
CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_URL = "https://web.archive.org/web"
AVAILABLE_URL = "https://archive.org/wayback/available"  # zapasowe API Wayback (inny serwer niż CDX)
CONFIRM_ABOVE = 100  # powyżej tylu zapytań API pytamy o potwierdzenie


@dataclass(frozen=True)
class Country:
    code: str    # parametr gl
    name: str
    domain: str  # lokalna wersja Google
    hl: str      # język interfejsu Google


# Główne rynki — sprawdzane domyślnie.
MAIN_COUNTRIES = [
    Country("pl", "Polska", "google.pl", "pl"),
    Country("de", "Niemcy", "google.de", "de"),
    Country("uk", "Wielka Brytania", "google.co.uk", "en"),
    Country("fr", "Francja", "google.fr", "fr"),
    Country("es", "Hiszpania", "google.es", "es"),
    Country("it", "Włochy", "google.it", "it"),
    Country("nl", "Holandia", "google.nl", "nl"),
    Country("be", "Belgia", "google.be", "nl"),
    Country("at", "Austria", "google.at", "de"),
    Country("ch", "Szwajcaria", "google.ch", "de"),
    Country("se", "Szwecja", "google.se", "sv"),
    Country("no", "Norwegia", "google.no", "no"),
    Country("dk", "Dania", "google.dk", "da"),
    Country("fi", "Finlandia", "google.fi", "fi"),
    Country("ie", "Irlandia", "google.ie", "en"),
    Country("pt", "Portugalia", "google.pt", "pt-pt"),
    Country("cz", "Czechy", "google.cz", "cs"),
    Country("sk", "Słowacja", "google.sk", "sk"),
    Country("hu", "Węgry", "google.hu", "hu"),
    Country("ro", "Rumunia", "google.ro", "ro"),
    Country("gr", "Grecja", "google.gr", "el"),
    Country("ua", "Ukraina", "google.com.ua", "uk"),
    Country("tr", "Turcja", "google.com.tr", "tr"),
    Country("us", "USA", "google.com", "en"),
    Country("ca", "Kanada", "google.ca", "en"),
    Country("mx", "Meksyk", "google.com.mx", "es"),
    Country("br", "Brazylia", "google.com.br", "pt-br"),
    Country("au", "Australia", "google.com.au", "en"),
    Country("jp", "Japonia", "google.co.jp", "ja"),
    Country("in", "Indie", "google.co.in", "en"),
]

# Dodatkowe kraje — z --wszystkie albo wybrane przez --kraje.
EXTRA_COUNTRIES = [
    Country("ru", "Rosja", "google.ru", "ru"),
    Country("bg", "Bułgaria", "google.bg", "bg"),
    Country("hr", "Chorwacja", "google.hr", "hr"),
    Country("si", "Słowenia", "google.si", "sl"),
    Country("rs", "Serbia", "google.rs", "sr"),
    Country("lt", "Litwa", "google.lt", "lt"),
    Country("lv", "Łotwa", "google.lv", "lv"),
    Country("ee", "Estonia", "google.ee", "et"),
    Country("by", "Białoruś", "google.by", "ru"),
    Country("kz", "Kazachstan", "google.kz", "ru"),
    Country("is", "Islandia", "google.is", "is"),
    Country("lu", "Luksemburg", "google.lu", "fr"),
    Country("cy", "Cypr", "google.com.cy", "el"),
    Country("mt", "Malta", "google.com.mt", "en"),
    Country("ba", "Bośnia i Hercegowina", "google.ba", "bs"),
    Country("mk", "Macedonia Płn.", "google.mk", "mk"),
    Country("al", "Albania", "google.al", "sq"),
    Country("md", "Mołdawia", "google.md", "ro"),
    Country("ge", "Gruzja", "google.ge", "ka"),
    Country("am", "Armenia", "google.am", "hy"),
    Country("az", "Azerbejdżan", "google.az", "az"),
    Country("ar", "Argentyna", "google.com.ar", "es"),
    Country("cl", "Chile", "google.cl", "es"),
    Country("co", "Kolumbia", "google.com.co", "es"),
    Country("pe", "Peru", "google.com.pe", "es"),
    Country("ve", "Wenezuela", "google.co.ve", "es"),
    Country("ec", "Ekwador", "google.com.ec", "es"),
    Country("uy", "Urugwaj", "google.com.uy", "es"),
    Country("py", "Paragwaj", "google.com.py", "es"),
    Country("bo", "Boliwia", "google.com.bo", "es"),
    Country("cr", "Kostaryka", "google.co.cr", "es"),
    Country("pa", "Panama", "google.com.pa", "es"),
    Country("do", "Dominikana", "google.com.do", "es"),
    Country("gt", "Gwatemala", "google.com.gt", "es"),
    Country("nz", "Nowa Zelandia", "google.co.nz", "en"),
    Country("kr", "Korea Płd.", "google.co.kr", "ko"),
    Country("id", "Indonezja", "google.co.id", "id"),
    Country("ph", "Filipiny", "google.com.ph", "en"),
    Country("vn", "Wietnam", "google.com.vn", "vi"),
    Country("th", "Tajlandia", "google.co.th", "th"),
    Country("my", "Malezja", "google.com.my", "ms"),
    Country("sg", "Singapur", "google.com.sg", "en"),
    Country("tw", "Tajwan", "google.com.tw", "zh-tw"),
    Country("hk", "Hongkong", "google.com.hk", "zh-tw"),
    Country("pk", "Pakistan", "google.com.pk", "en"),
    Country("bd", "Bangladesz", "google.com.bd", "bn"),
    Country("ae", "ZEA", "google.ae", "ar"),
    Country("sa", "Arabia Saudyjska", "google.com.sa", "ar"),
    Country("il", "Izrael", "google.co.il", "iw"),
    Country("eg", "Egipt", "google.com.eg", "ar"),
    Country("za", "RPA", "google.co.za", "en"),
    Country("ng", "Nigeria", "google.com.ng", "en"),
    Country("ke", "Kenia", "google.co.ke", "en"),
    Country("ma", "Maroko", "google.co.ma", "fr"),
    Country("qa", "Katar", "google.com.qa", "ar"),
    Country("kw", "Kuwejt", "google.com.kw", "ar"),
    Country("jo", "Jordania", "google.jo", "ar"),
    Country("gh", "Ghana", "google.com.gh", "en"),
    Country("tn", "Tunezja", "google.tn", "fr"),
    Country("dz", "Algieria", "google.dz", "fr"),
]

ALL_COUNTRIES = MAIN_COUNTRIES + EXTRA_COUNTRIES
COUNTRY_BY_CODE = {c.code: c for c in ALL_COUNTRIES}
COUNTRY_ALIASES = {"gb": "uk"}
CJK_MARKETS = {"jp", "tw", "hk", "kr"}  # tu znaki CJK w tytułach są normalne


# --- Pomocnicze ----------------------------------------------------------------

CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ
COLORS = {"green": 32, "red": 31, "yellow": 33, "bold": 1, "dim": 2}
NBSP = "\u00a0"  # twarda spacja — zawijanie tekstu jej nie łamie
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}


def clean(text, limit: int = 0) -> str:
    """Usuwa znaki sterujące (np. sekwencje ANSI w cudzych danych), skleja spacje i przycina."""
    text = " ".join(CONTROL_CHARS.sub(" ", str(text)).split())
    return text[: limit - 1] + "…" if limit and len(text) > limit else text


def paint(text: str, color: str) -> str:
    return f"\033[{COLORS[color]}m{text}\033[0m" if USE_COLOR else text


def plural(n: int, one: str, few: str, many: str) -> str:
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def age_text(days: int) -> str:
    if days < 60:
        return f"{days} {plural(days, 'dzień', 'dni', 'dni')}"
    if days < 730:
        months = days // 30
        return f"{months} {plural(months, 'miesiąc', 'miesiące', 'miesięcy')}"
    years = days // 365
    return f"{years} {plural(years, 'rok', 'lata', 'lat')}"


def parse_date(text) -> datetime | None:
    """Rozpoznaje daty w formatach spotykanych w RDAP/WHOIS (2024-01-31, 31.01.2024, 31-Jan-2024)."""
    text = str(text or "")
    patterns = (
        (r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})", "ymd"),
        (r"(\d{1,2})[-./](\d{1,2})[-./](\d{4})", "dmy"),
        (r"(\d{1,2})[- ]([A-Za-z]{3})[a-z]*[- ,]+(\d{4})", "dMy"),
    )
    for pattern, order in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        a, b, c = match.groups()
        try:
            if order == "ymd":
                return datetime(int(a), int(b), int(c), tzinfo=timezone.utc)
            if order == "dmy":
                return datetime(int(c), int(b), int(a), tzinfo=timezone.utc)
            return datetime(int(c), MONTHS[b.lower()], int(a), tzinfo=timezone.utc)
        except (ValueError, KeyError):
            continue
    return None


def fmt_date(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def days_since(value: datetime) -> int:
    return (datetime.now(timezone.utc) - value).days


def days_until(value: datetime) -> int:
    return (value - datetime.now(timezone.utc)).days


def year_ranges(years) -> str:
    """[2009, 2010, 2011, 2015] -> '2009–2011, 2015'"""
    spans: list[list[int]] = []
    for year in sorted(set(years)):
        if spans and year == spans[-1][1] + 1:
            spans[-1][1] = year
        else:
            spans.append([year, year])
    return ", ".join(str(a) if a == b else f"{a}–{b}" for a, b in spans)


def show_url(url: str, limit: int = 0) -> str:
    """Adres do wyświetlenia: %C5%BC -> ż (znaki sterujące i tak usuwa clean)."""
    return clean(urllib.parse.unquote(url), limit)


def yes_no(value) -> str:
    return {True: "TAK", False: "NIE"}.get(value, "—")


def sentence(text: str) -> str:
    """Wielka pierwsza litera bez psucia reszty (str.capitalize zmieniłby „DNS” na „dns”)."""
    return text[:1].upper() + text[1:]


def pages_text(pages: int) -> str:
    return "str. 1" if pages <= 1 else f"str. 1–{pages}"


# --- Domeny i adresy -------------------------------------------------------------

DOMAIN_RE = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+")
HOME_PATH = re.compile(r"/?(?:[a-z]{2}(?:[-_][a-z]{2})?/?)?(?:index\.(?:html?|php|aspx?))?", re.I)


DEFAULT_TLD = "com"  # końcówka dopisywana do samej nazwy: savowin -> savowin.com


def normalize_domain(raw: str, default_tld: str = DEFAULT_TLD) -> str:
    """'https://www.Test.com/abc' -> 'test.com', sama nazwa 'savowin' -> 'savowin.com'. Dla śmieci ValueError."""
    text = raw.strip()
    try:
        host = urllib.parse.urlsplit(text if "://" in text else f"http://{text}").hostname or ""
        host = host.rstrip(".").removeprefix("www.")
        if host and "." not in host and default_tld:
            host = f"{host}.{default_tld.strip('.').lower()}"
        host = host.encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        host = ""
    if len(host) > 253 or not DOMAIN_RE.fullmatch(host):
        raise ValueError(f"to nie wygląda na domenę: {clean(text)!r}")
    return host


def brand_name(domain: str) -> str:
    """Nazwa, którą ludzie wpisują w Google: domena bez końcówki ('savowin.com' -> 'savowin', 'xn--…' -> 'zażółć')."""
    label = domain.split(".", 1)[0]
    try:
        return label.encode("ascii").decode("idna")
    except UnicodeError:
        return label


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").encode("idna").decode("ascii")
    except (ValueError, UnicodeError):
        return ""


def belongs_to(url: str, domain: str) -> bool:
    """Czy URL należy do domeny albo jej subdomeny."""
    host = host_of(url)
    return host == domain or host.endswith("." + domain)


def is_homepage(url: str, domain: str) -> bool:
    """Strona główna: domena lub www.domena, ścieżka „/”, wersja językowa („/pl/”) albo index.*"""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return (host_of(url) in (domain, "www." + domain) and not parts.query
            and bool(HOME_PATH.fullmatch(parts.path or "/")))


FIRST_KIND_TEXT = {"home": "strona główna", "subpage": "podstrona", "subdomain": "subdomena",
                   "other": "inna domena"}


def first_kind(url: str, domain: str) -> str:
    """Czym jest 1. wynik site: — stroną główną, podstroną, subdomeną czy czymś innym."""
    if is_homepage(url, domain):
        return "home"
    if host_of(url) in (domain, "www." + domain):
        return "subpage"
    return "subdomain" if belongs_to(url, domain) else "other"


# --- Sieć ---------------------------------------------------------------------------

class ApiError(Exception):
    """Nieudane pojedyncze zapytanie."""


class FatalApiError(ApiError):
    """Zły klucz, brak kredytów albo limit — dalsze zapytania nie mają sensu."""


SERVER_ERRORS = {500, 502, 503, 504}
RETRY_STATUSES = {429} | SERVER_ERRORS
NO_CERT = "serwer nie obsługuje HTTPS dla tej nazwy (prawdopodobnie brak certyfikatu)"
SSL_ALERTS = {
    "TLSV1_ALERT_INTERNAL_ERROR": NO_CERT, "TLSV1_UNRECOGNIZED_NAME": NO_CERT,
    "TLSV1_ALERT_UNRECOGNIZED_NAME": NO_CERT, "SSLV3_ALERT_HANDSHAKE_FAILURE": NO_CERT,
    "WRONG_VERSION_NUMBER": "pod adresem https:// serwer odpowiada zwykłym HTTP (zła konfiguracja)",
}


def ssl_reason(error) -> str:
    message = str(getattr(error, "verify_message", "") or error).lower()
    if "expired" in message:
        return "certyfikat SSL wygasł"
    if "self-signed" in message or "self signed" in message:
        return "certyfikat SSL jest samopodpisany"
    if "hostname mismatch" in message or "not valid for" in message:
        return "certyfikat SSL wystawiony dla innej domeny"
    if "local issuer" in message:
        return "nieznany wystawca certyfikatu SSL"
    return f"nieprawidłowy certyfikat SSL ({clean(message, 80)})"


def describe_net_error(error) -> str:
    # URLError opakowuje właściwy błąd w .reason; samo ssl.SSLError też ma .reason, ale to tylko kod tekstowy.
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ssl_reason(reason)
    if isinstance(reason, socket.gaierror):
        return "nie znaleziono serwera (DNS)"
    if isinstance(reason, ConnectionRefusedError):
        return "połączenie odrzucone"
    if isinstance(reason, (TimeoutError, socket.timeout)):
        return "przekroczono czas oczekiwania"
    if isinstance(reason, ConnectionResetError):
        return "połączenie zerwane"
    if isinstance(reason, ssl.SSLError):
        code = str(getattr(reason, "reason", None) or "")
        return SSL_ALERTS.get(code, f"błąd SSL ({clean(code or reason, 60)})")
    return clean(reason, 100) or type(reason).__name__


def http_request(url: str, data: bytes | None = None, headers: dict | None = None,
                 timeout: float = 60, attempts: int = 3, retry=RETRY_STATUSES) -> tuple[int, bytes]:
    """Zapytanie do API z ponowieniami (błędy sieci i kody z `retry`). Zwraca (status HTTP, treść)."""
    request = urllib.request.Request(url, data=data, headers={"User-Agent": API_UA, **(headers or {})})
    attempt = 0
    while True:
        attempt += 1
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, body = response.status, response.read()
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                body = e.read()
            except (OSError, http.client.HTTPException):
                body = b""
        except (OSError, http.client.HTTPException) as e:
            if attempt >= attempts:
                raise ApiError(f"błąd sieci: {describe_net_error(e)}") from None
            time.sleep(2 * attempt)
            continue
        if status not in retry or attempt >= attempts:
            return status, body
        time.sleep(2 * attempt)


def http_json(url: str, data: bytes | None = None, headers: dict | None = None,
              timeout: float = 60, attempts: int = 3, retry=RETRY_STATUSES) -> tuple[int, dict]:
    status, body = http_request(url, data, headers, timeout, attempts, retry)
    try:
        payload = json.loads(body)
    except ValueError:
        payload = {}
    return status, payload if isinstance(payload, dict) else {}


# --- Google przez API (SerpApi / Serper) ----------------------------------------

@dataclass(frozen=True)
class Hit:
    link: str
    title: str = ""
    snippet: str = ""


@dataclass(frozen=True)
class SerpPage:
    hits: tuple[Hit, ...]
    total: int | None       # prawdziwy szacunek Google (jeśli go podał)
    has_next: bool          # czy jest kolejna strona wyników
    notices: tuple[str, ...] = ()  # komunikaty o usuniętych wynikach (DMCA, żądania prawne)
    stories: tuple[str, ...] = ()  # linki z bloku „Najważniejsze wiadomości” (poza wynikami organicznymi)


LEGAL_LINK = re.compile(r"lumendatabase|chillingeffects|support\.google\.com/legal|/legal/answer", re.I)


def api_error(status: int, message: str) -> ApiError:
    message = f"HTTP {status}: {clean(message, 200)}"
    if status in (401, 403, 429) or "credit" in message.lower():
        return FatalApiError(message)
    return ApiError(message)


def extract_hits(items) -> tuple[Hit, ...]:
    return tuple(
        Hit(clean(item["link"]), clean(item.get("title", ""), 200), clean(item.get("snippet", ""), 300))
        for item in items or [] if isinstance(item, dict) and item.get("link")
    )


def story_links(block) -> tuple[str, ...]:
    """Linki z bloku „Najważniejsze wiadomości” (lista albo słownik list, zależnie od układu strony)."""
    items = block if isinstance(block, list) else [
        item for group in block.values() if isinstance(group, list) for item in group] if isinstance(block, dict) else []
    return tuple(clean(item["link"]) for item in items if isinstance(item, dict) and item.get("link"))


def removal_notices(data: dict) -> tuple[str, ...]:
    """Komunikaty Google o wynikach usuniętych na żądanie (pomija zwykłe „pominięto podobne wyniki”)."""
    block = data.get("dmca_messages")
    blocks = block if isinstance(block, list) else [block] if isinstance(block, dict) else []
    notices = []
    for item in blocks:
        for message in item.get("messages") or [] if isinstance(item, dict) else []:
            if not isinstance(message, dict):
                continue
            links = " ".join(str(h.get("link", "")) for h in message.get("highlighted_words") or []
                             if isinstance(h, dict))
            content = str(message.get("content", ""))
            if LEGAL_LINK.search(links) or re.search(r"DMCA|Digital Millennium", content, re.I):
                notices.append(clean(content, 200))
    return tuple(notices)


def notify(text: str) -> None:
    """Komunikat w trakcie pracy — w osobnej linii, nie psuje paska postępu."""
    print(("\r\033[K" if sys.stderr.isatty() else "") + paint(text, "yellow"), file=sys.stderr, flush=True)


class KeyPool:
    """Klucze API jednego dostawcy: używa pierwszego działającego, a wyczerpane i nieprawidłowe pomija."""

    def __init__(self, provider: str, keys: list[str]):
        self.provider = provider
        self.keys = list(keys)
        self.dead: dict[str, str] = {}          # klucz -> powód, dla którego go pominięto
        self.used = {key: 0 for key in self.keys}
        self.left: dict[str, int] = {}          # pozostałe zapytania według Account API
        self.events: list[str] = []             # komunikaty o pominiętych kluczach (bot pokazuje je w czacie)
        self.lock = threading.Lock()

    def label(self, key: str) -> str:
        # Tylko numer i 4 ostatnie znaki — pełny klucz nigdy nie trafia na ekran.
        return f"#{self.keys.index(key) + 1}" + (f" (…{key[-4:]})" if len(key) >= 12 else "")

    def current(self) -> str | None:
        with self.lock:
            return next((key for key in self.keys if key not in self.dead), None)

    def retire(self, key: str, reason: str) -> None:
        with self.lock:
            if key in self.dead:
                return
            self.dead[key] = reason
            following = next((k for k in self.keys if k not in self.dead), None)
        tail = f" — przełączam na {self.label(following)}" if following else " — to był ostatni klucz"
        message = f"{self.provider} {self.label(key)}: {reason}{tail}"
        with self.lock:
            self.events.append(message)
        notify(message)

    def count(self, key: str, n: int = 1) -> None:
        with self.lock:
            self.used[key] += n

    def available(self) -> int | None:
        """Suma pozostałych zapytań na działających kluczach (None, gdy nie wszystkie są znane)."""
        live = [key for key in self.keys if key not in self.dead]
        if not live or any(key not in self.left for key in live):
            return None
        return sum(self.left[key] for key in live)

    def exhausted(self) -> FatalApiError:
        reasons = "; ".join(f"{self.label(key)}: {reason}" for key, reason in self.dead.items())
        return FatalApiError(f"wszystkie klucze {self.provider} są wyczerpane albo nieprawidłowe — {reasons}")


class SerpApi:
    name = "SerpApi"
    url = "https://serpapi.com/search.json"
    account_url = "https://serpapi.com/account.json"
    recheck_variants = ("com", "fresh")  # powtórka: google.com z tym samym krajem, potem bez pamięci podręcznej

    def __init__(self, keys: list[str]):
        self.pool = KeyPool(self.name, keys)

    @property
    def used(self) -> int:
        return sum(self.pool.used.values())

    def search(self, query: str, country: Country, page: int, variant: str = "") -> SerpPage:
        params = {
            "engine": "google",
            "q": query,
            "google_domain": "google.com" if variant == "com" else country.domain,
            "gl": country.code,
            "hl": country.hl,
            "nfpr": 1,  # bez automatycznej poprawki pisowni zapytania
            "start": (page - 1) * 10,
        }
        if variant == "fresh":
            params["no_cache"] = "true"  # świeży wynik zamiast kopii z pamięci podręcznej SerpApi (1 h)
        while True:
            key = self.pool.current()
            if key is None:
                raise self.pool.exhausted()
            # 429 nie ponawiamy: to koniec limitu (miesięcznego albo godzinowego) — przechodzimy na kolejny klucz.
            status, data = http_json(f"{self.url}?{urllib.parse.urlencode({**params, 'api_key': key})}",
                                     retry=SERVER_ERRORS)
            error = str(data.get("error", ""))
            if status in (401, 403, 429):
                self.pool.retire(key, self.key_problem(key, status, error))
                continue
            # Brak wyników to dla SerpApi poprawna odpowiedź 200 z polem "error".
            if status == 200 and "hasn't returned any results" in error:
                self.pool.count(key)
                return SerpPage((), None, False, removal_notices(data))
            if status != 200 or error:
                raise api_error(status, error or "nieoczekiwana odpowiedź")
            self.pool.count(key)
            hits = extract_hits(data.get("organic_results"))
            total = (data.get("search_information") or {}).get("total_results")
            # Przy site: Google często nie podaje szacunku i SerpApi zwraca po prostu liczbę wyników.
            total = total if isinstance(total, int) and total > len(hits) else None
            pagination = data.get("serpapi_pagination") or data.get("pagination") or {}
            return SerpPage(hits, total, bool(pagination.get("next")), removal_notices(data),
                            story_links(data.get("top_stories")))

    def account(self, key: str) -> tuple[int, dict]:
        """Stan konta z Account API — to zapytanie jest darmowe i nie zużywa limitu."""
        return http_json(f"{self.account_url}?{urllib.parse.urlencode({'api_key': key})}", timeout=20, attempts=1)

    def account_text(self, key: str) -> str:
        status, data = self.account(key)
        if status != 200:
            detail = f": {clean(data['error'], 100)}" if data.get("error") else ""
            return f"nie udało się pobrać stanu konta (HTTP {status}{detail})"
        return (f"na koncie zostało {data.get('total_searches_left', '?')} z "
                f"{data.get('searches_per_month', '?')} zapytań/mies. "
                f"(odnowienie {clean(data.get('plan_renewal_date') or '?')})")

    @staticmethod
    def account_problem(status: int, data: dict) -> str:
        """Dlaczego według Account API klucz nie nadaje się teraz do użycia (pusty tekst = nadaje się)."""
        if status == 401:
            return "nieprawidłowy klucz"
        if status != 200:
            return ""
        left = data.get("total_searches_left")
        if isinstance(left, int) and left <= 0:
            return f"wyczerpany limit miesięczny (odnowienie {clean(data.get('plan_renewal_date') or '?')})"
        per_hour, this_hour = data.get("account_rate_limit_per_hour"), data.get("this_hour_searches")
        if isinstance(per_hour, int) and isinstance(this_hour, int) and 0 < per_hour <= this_hour:
            return f"wyczerpany limit godzinowy ({per_hour} zapytań/h)"
        return ""

    def key_problem(self, key: str, status: int, error: str) -> str:
        """HTTP 429 to albo koniec limitu miesięcznego, albo godzinowego — Account API mówi który."""
        try:
            reason = self.account_problem(*self.account(key))
        except ApiError:
            reason = ""
        reason = reason or {401: "nieprawidłowy klucz", 403: "brak dostępu"}.get(status, f"HTTP {status}")
        return f"{reason} ({clean(error, 100)})" if error else reason

    def prepare(self) -> None:
        """Przed startem sprawdza każdy klucz (darmowe Account API) i od razu pomija te, które nie zadziałają."""
        for key in self.pool.keys:
            try:
                status, data = self.account(key)
            except ApiError:
                continue  # nie wiadomo — klucz zostaje, najwyżej odpadnie w trakcie
            problem = self.account_problem(status, data)
            if problem:
                self.pool.retire(key, problem + (f" ({clean(data['error'], 100)})" if data.get("error") else ""))
            elif isinstance(data.get("total_searches_left"), int):
                self.pool.left[key] = data["total_searches_left"]


class Serper(SerpApi):
    name = "Serper.dev"
    url = "https://google.serper.dev/search"
    recheck_variants = ("fresh",)  # Serper nie pozwala wybrać domeny Google — powtarzamy to samo zapytanie

    def search(self, query: str, country: Country, page: int, variant: str = "") -> SerpPage:
        body = {"q": query, "gl": country.code, "hl": country.hl, "autocorrect": False, "page": page}
        while True:
            key = self.pool.current()
            if key is None:
                raise self.pool.exhausted()
            status, data = http_json(
                self.url,
                data=json.dumps(body).encode(),
                headers={"X-API-KEY": key, "Content-Type": "application/json"},
            )
            message = str(data.get("message", ""))
            if status in (401, 403) or (status == 400 and "credit" in message.lower()):
                reason = "brak kredytów" if "credit" in message.lower() else "nieprawidłowy klucz"
                self.pool.retire(key, f"{reason} ({clean(message, 100)})" if message else reason)
                continue
            if status != 200:
                raise api_error(status, message or "nieoczekiwana odpowiedź")
            credits = data.get("credits")
            self.pool.count(key, credits if isinstance(credits, int) else 1)
            hits = extract_hits(data.get("organic"))
            return SerpPage(hits, None, bool(hits), (), story_links(data.get("topStories")))

    def account_text(self, key: str) -> str:
        return "stan konta Serpera sprawdzisz w panelu serper.dev"

    def prepare(self) -> None:
        pass  # Serper nie ma API stanu konta — wyczerpany klucz wychodzi przy pierwszym zapytaniu


@dataclass
class Rank:
    query: str
    page: int | None = None   # strona wyników Google (1, 2, …)
    pos: int | None = None    # miejsce na tej stronie
    place: int | None = None  # miejsce licząc od początku wyników organicznych
    url: str = ""
    pages: int = 0            # ile stron przejrzano
    stories: bool = False     # domena jest w bloku „Najważniejsze wiadomości” na 1. stronie
    error: str = ""


@dataclass
class Result:
    domain: str
    country: Country
    indexed: bool | None = None       # None = nie udało się sprawdzić
    total: int | None = None          # szacunek Google (rzadko podawany przy site:)
    hits: tuple[Hit, ...] = ()        # wyniki z tej domeny na 1. stronie site:
    first_url: str = ""               # 1. wynik site:
    first_kind: str = ""              # home / subpage / subdomain / other
    notices: tuple[str, ...] = ()
    brand: Rank | None = None         # pozycja po wpisaniu nazwy domeny
    phrase: Rank | None = None        # pozycja dla --fraza
    recheck: str = ""                 # "found" = TAK dopiero w powtórce, "confirmed" = NIE potwierdzone powtórką
    index_source: str = ""            # "site" = z zapytania site:, "name" = jest w wynikach dla nazwy, "" = nie wiadomo
    error: str = ""


def find_rank(search, query: str, domain: str, country: Country, max_pages: int) -> Rank:
    """Przegląda kolejne strony wyników, aż znajdzie domenę."""
    rank = Rank(query)
    seen = 0  # wyniki organiczne na wcześniejszych stronach (bywa ich mniej niż 10)
    try:
        for page in range(1, max_pages + 1):
            serp = search(query, country, page, "")
            rank.pages = page
            rank.stories = rank.stories or any(belongs_to(link, domain) for link in serp.stories)
            for pos, hit in enumerate(serp.hits, 1):
                if belongs_to(hit.link, domain):
                    rank.page, rank.pos, rank.place, rank.url = page, pos, seen + pos, hit.link
                    return rank
            seen += len(serp.hits)
            if not serp.hits or not serp.has_next:
                break
    except FatalApiError:
        raise
    except ApiError as e:
        rank.error = str(e)
    return rank


def apply_serp(result: Result, serp: SerpPage) -> bool:
    """Wpisuje wynik zapytania site: do Result; zwraca True, gdy domena jest w wynikach."""
    # Liczymy tylko wyniki z tej domeny — gdyby Google pokazał coś innego, to nie jest indeks.
    own = tuple(hit for hit in serp.hits if belongs_to(hit.link, result.domain))
    result.indexed = bool(own)
    result.index_source = "site"
    result.hits = own
    result.total = serp.total if own else 0
    result.notices = serp.notices or result.notices
    if own:
        result.first_url = serp.hits[0].link
        result.first_kind = first_kind(serp.hits[0].link, result.domain)
    return bool(own)


def rank_checks(search, result: Result, plan) -> None:
    """Pozycje na własną nazwę i na frazę — tylko tam, gdzie domena w ogóle jest w indeksie."""
    if result.country.code in plan.brand_codes:
        # Sama nazwa bez końcówki: po wpisaniu „savowin.com” domena prawie zawsze jest pierwsza, więc to nic nie mówi.
        result.brand = find_rank(search, brand_name(result.domain), result.domain, result.country, plan.max_pages)
    if plan.phrase:
        result.phrase = find_rank(search, plan.phrase, result.domain, result.country, plan.max_pages)


def check_country_quick(search, domain: str, country: Country, plan) -> Result:
    """Tryb szybki: do Google idzie sama nazwa („savowin”) i patrzymy tylko na 1. stronę — 1 zapytanie na kraj.

    Gdy domeny nie ma na 1. stronie, nie wiadomo, czy jest w indeksie; z plan.index_fallback dopytujemy
    wtedy site:domena (1 zapytanie więcej, tylko w takich krajach).
    """
    result = Result(domain, country)
    query = brand_name(domain)
    try:
        serp = search(query, country, 1, "")
    except FatalApiError:
        raise
    except ApiError as e:
        result.error = str(e)
        return result
    rank = Rank(query, pages=1, stories=any(belongs_to(link, domain) for link in serp.stories))
    for pos, hit in enumerate(serp.hits, 1):
        if belongs_to(hit.link, domain):
            rank.page, rank.pos, rank.place, rank.url = 1, pos, pos, hit.link
            break
    result.brand, result.notices = rank, serp.notices
    result.hits = tuple(hit for hit in serp.hits if belongs_to(hit.link, domain))
    if rank.place:
        result.indexed, result.index_source = True, "name"  # jest w wynikach, więc jest w indeksie
    elif plan.index_fallback:
        try:
            apply_serp(result, search(f"site:{domain}", country, 1, ""))
        except FatalApiError:
            raise
        except ApiError as e:
            result.indexed, result.recheck = False, f"błąd site: {e}"
    else:
        result.indexed = False  # brak na 1. stronie; czy jest w indeksie — nie sprawdzano (index_source == "")
    if plan.phrase and result.indexed:
        result.phrase = find_rank(search, plan.phrase, domain, country, plan.max_pages)
    return result


def check_country(search, domain: str, country: Country, plan) -> Result:
    if plan.mode == "quick":
        return check_country_quick(search, domain, country, plan)
    result = Result(domain, country)
    try:
        serp = search(f"site:{domain}", country, 1, "")
    except FatalApiError:
        raise
    except ApiError as e:
        result.error = str(e)
        return result
    if apply_serp(result, serp):
        rank_checks(search, result, plan)
    return result


NEW_DOMAIN_DAYS = 90  # młodsze domeny Google pokazuje niestabilnie — „NIE” warto wtedy powtórzyć


def recheck(search, result: Result, plan, variants: tuple[str, ...]) -> None:
    """Powtórka dla „NIE”: inną drogą (google.com z tym samym krajem), potem bez pamięci podręcznej.

    Różne serwery Google mają różny stan indeksu — zwłaszcza dla nowych domen jedno zapytanie
    potrafi nie znaleźć domeny, a następne już tak.
    """
    for variant in variants:
        try:
            serp = search(f"site:{result.domain}", result.country, 1, variant)
        except FatalApiError:
            raise
        except ApiError as e:
            result.recheck = f"błąd powtórki: {e}"
            return
        if apply_serp(result, serp):
            result.recheck = "found"
            rank_checks(search, result, plan)
            return
    result.recheck = "confirmed"


def recheck_targets(results: list[Result], plan, extra_results: dict) -> list[Result]:
    """Które „NIE” powtórzyć: domena widoczna w innym kraju albo nowa; główny rynek (albo wszystkie z --potwierdz)."""
    if plan.mode == "quick":
        return []  # tryb szybki ma własne (opcjonalne) dopytanie site:
    targets = []
    for domain in dict.fromkeys(r.domain for r in results):
        rows = [r for r in results if r.domain == domain]
        reg = extra_results.get(domain, {}).get("reg")
        new = isinstance(reg, RegInfo) and reg.registered is not None and days_since(reg.registered) < NEW_DOMAIN_DAYS
        if new or any(r.indexed for r in rows):
            targets += [r for r in rows if r.indexed is False and (plan.confirm_all or r.country == plan.market)]
    return targets


def run_rechecks(search, results: list[Result], plan, extra_results: dict, workers: int,
                 variants: tuple[str, ...], progress=None) -> str | None:
    """Zwraca komunikat błędu krytycznego albo None."""
    targets = recheck_targets(results, plan, extra_results)
    fatal = None
    if targets:
        if progress:
            progress.note(f"powtarzam niepewne wyniki: {len(targets)}")
        else:
            status_line(f"  powtarzam niepewne wyniki: {len(targets)}")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for future in as_completed([pool.submit(recheck, search, r, plan, variants) for r in targets]):
                try:
                    future.result()
                except FatalApiError as e:
                    fatal = fatal or str(e)
        if not progress:
            status_line("")
    return fatal


# --- Treść strony -------------------------------------------------------------------

SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}

PAGE_KINDS = (
    # (opis, czy to poważny problem, wzorzec)
    ("na sprzedaż / zaparkowana", True, re.compile(
        r"(this|the) domain( name)? (is|may be) for sale|domain( name)? (is )?for sale|buy this domain|"
        r"make an offer|domain parking|parked (free|domain|by)|\bsedo(parking)?\b|\bdan\.com\b|afternic|"
        r"hugedomains|\bbodis\b|parkingcrew|aftermarket\.pl|domena (jest |może być )?na sprzedaż|"
        r"kup (tę|te) domenę|zaparkowan", re.I)),
    ("wygasła", True, re.compile(
        r"domain( name)? (has )?expired|this domain( name)? expired|domena wygasła|"
        r"renew (this|your) domain|expired domain", re.I)),
    ("konto hostingu zawieszone", True, re.compile(
        r"account (has been )?suspended|this account (has been|is) suspended|"
        r"konto (zostało )?zawieszone|strona (została )?zablokowana", re.I)),
    ("domyślna strona serwera/hostingu", True, re.compile(
        r"welcome to nginx|apache2? .{0,20}default page|\bit works!|test page for the (apache|nginx)|"
        r"\bindex of /|default web ?site page|iis windows server|web server'?s default page|"
        r"there is no website configured|future home of|domena (została )?zarejestrowana|"
        r"this domain (has been|was) registered|site not found|domain not configured", re.I)),
    ("w budowie", False, re.compile(
        r"under construction|coming soon|w budowie|w trakcie budowy|strona w przygotowaniu|"
        r"już wkrótce|launching soon|maintenance mode", re.I)),
)
KIND_SEVERE = {label: severe for label, severe, _ in PAGE_KINDS}
KIND_SHORT = {"na sprzedaż / zaparkowana": "parking", "wygasła": "wygasła", "konto hostingu zawieszone": "zawieszona",
              "domyślna strona serwera/hostingu": "pusta (domyślna)", "w budowie": "w budowie"}

SPAM_RE = re.compile(
    r"\b(casino\w*|kasyn\w*|bukmacher\w*|betting|poker\w*|roulette|ruletk\w*|jackpot\w*|viagra|cialis|"
    r"levitra|pharmacy|porn\w*|xxx|escort\w*|payday|chwilówk\w*|replica|hentai|seks\w*|sex)\b", re.I)
CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")  # japońskie kana i chińskie/japońskie znaki


class PageParser(HTMLParser):
    """Wyciąga z HTML tytuł, meta tagi, canonical, język, H1 i widoczny tekst."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.lang = ""
        self.meta: dict[str, str] = {}
        self.canonical = ""
        self.refresh = ""
        self.h1 = 0
        self.text: list[str] = []
        self._skip = 0
        self._in_title = False
        self._first_title = False

    def handle_starttag(self, tag, attrs):
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag in SKIP_TAGS:
            self._skip += 1
        elif self._skip:
            return
        elif tag == "title":
            self._in_title = True
            self._first_title = not self.title  # liczy się pierwszy <title>
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property") or "").lower()
            if name and name not in self.meta:
                self.meta[name] = attr.get("content", "")
            if attr.get("http-equiv", "").lower() == "refresh":
                self.refresh = attr.get("content", "")
        elif tag == "link" and "canonical" in attr.get("rel", "").lower().split():
            self.canonical = self.canonical or attr.get("href", "")
        elif tag == "html":
            self.lang = attr.get("lang", "")
        elif tag == "h1":
            self.h1 += 1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            if self._first_title:
                self.title += data
        elif not self._skip:
            self.text.append(data)


@dataclass
class PageInfo:
    title: str = ""
    lang: str = ""
    description: str = ""
    canonical: str = ""
    refresh: str = ""
    robots: str = ""      # meta robots / googlebot
    viewport: bool = False  # <meta name="viewport"> — strona przygotowana pod telefony
    words: int = 0
    h1: int = 0
    kind: str = ""        # na sprzedaż / wygasła / domyślna strona / w budowie …
    kind_hint: str = ""   # fragment, po którym to rozpoznano
    spam: tuple[str, ...] = ()


def decode_html(body: bytes, content_type: str = "") -> str:
    match = re.search(r"charset=[\"']?([\w.-]+)", content_type or "", re.I)
    charset = match.group(1) if match else ""
    if not charset:
        meta = re.search(rb"<meta[^>]+charset=[\"']?([\w.-]+)", body[:4096], re.I)
        charset = meta.group(1).decode("ascii", "ignore") if meta else "utf-8"
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def spam_words(texts) -> tuple[str, ...]:
    found = {m.group(1).lower() for text in texts for m in SPAM_RE.finditer(text or "")}
    return tuple(sorted(found))[:8]


def detect_kind(title: str, text: str, words: int) -> tuple[str, str]:
    """Rozpoznaje parking, stronę „na sprzedaż”, domyślną stronę serwera itp."""
    for label, _, pattern in PAGE_KINDS:
        # w tytule zawsze, w treści tylko przy krótkich stronach (długie strony mogą o tym pisać)
        match = pattern.search(title) or (pattern.search(text[:3000]) if words < 400 else None)
        if match:
            return label, clean(match.group(0), 60)
    return "", ""


def analyze_html(body: bytes, content_type: str = "") -> PageInfo:
    parser = PageParser()
    try:
        parser.feed(decode_html(body, content_type))
        parser.close()
    except AssertionError:
        pass  # uszkodzony HTML (np. „<![foo[”) — zostają dane zebrane do tego miejsca
    text = " ".join(" ".join(parser.text).split())
    info = PageInfo(
        title=clean(parser.title, 150),
        lang=clean(parser.lang, 20),
        description=clean(parser.meta.get("description", ""), 200),
        canonical=clean(parser.canonical, 200),
        refresh=clean(parser.refresh, 200),
        robots=clean(" ".join(v for k, v in parser.meta.items() if k in ("robots", "googlebot")), 100),
        viewport="viewport" in parser.meta,
        words=len(re.findall(r"\w+", text)),
        h1=parser.h1,
    )
    info.kind, info.kind_hint = detect_kind(info.title, text, info.words)
    info.spam = spam_words([info.title, text[:5000]])
    return info


# --- Strona WWW (darmowe testy) ---------------------------------------------------

MAX_BODY = 1_500_000


@dataclass
class Fetch:
    url: str
    chain: list = field(default_factory=list)  # [(status, url), …] łącznie z ostatnią odpowiedzią
    status: int | None = None
    final_url: str = ""
    headers: dict = field(default_factory=dict)
    x_robots: list = field(default_factory=list)
    body: bytes = b""
    error: str = ""
    ssl_error: str = ""
    seconds: float = 0.0


class _RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.hops: list[tuple[int, str]] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.hops.append((code, req.full_url))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url: str, verify: bool = True, timeout: float = 15, max_bytes: int = MAX_BODY) -> Fetch:
    """Pobiera adres jak przeglądarka i zapisuje łańcuch przekierowań."""
    result = Fetch(url)
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    recorder = _RedirectRecorder()
    opener = urllib.request.build_opener(recorder, urllib.request.HTTPSHandler(context=context))
    request = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pl,en;q=0.8",
    })
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            result.status, result.final_url = response.status, response.geturl()
            result.headers = {k.lower(): v for k, v in response.headers.items()}
            result.x_robots = response.headers.get_all("X-Robots-Tag") or []
            result.body = response.read(max_bytes)
    except urllib.error.HTTPError as e:
        result.status, result.final_url = e.code, e.geturl() or url
        if e.headers is not None:
            result.headers = {k.lower(): v for k, v in e.headers.items()}
            result.x_robots = e.headers.get_all("X-Robots-Tag") or []
        try:
            result.body = e.read(max_bytes)
        except (OSError, http.client.HTTPException):
            pass
    except (OSError, http.client.HTTPException, ValueError) as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, ssl.SSLCertVerificationError):
            result.ssl_error = ssl_reason(reason)
        result.error = describe_net_error(e)
    result.seconds = time.monotonic() - started
    result.chain = recorder.hops + ([(result.status, result.final_url)] if result.status else [])
    return result


def resolve(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return []
    return list(dict.fromkeys(info[4][0] for info in infos))


def check_ssl(host: str) -> tuple[datetime | None, str, str]:
    """Zwraca (data wygaśnięcia certyfikatu, wystawca, błąd)."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=10) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert() or {}
    except ssl.SSLCertVerificationError as e:
        return None, "", ssl_reason(e)
    except OSError as e:
        return None, "", f"brak HTTPS: {describe_net_error(e)}"
    try:
        expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
    except (KeyError, ValueError):
        expires = None
    issuer = dict(item[0] for item in cert.get("issuer", ()) if item).get("organizationName", "")
    return expires, clean(issuer, 60), ""


def parse_robots(text: str) -> tuple[dict[str, list[tuple[bool, str]]], list[str]]:
    """Grupy reguł robots.txt według user-agenta (zasady jak u Google) i adresy map witryny."""
    groups: dict[str, list[tuple[bool, str]]] = {}
    sitemaps: list[str] = []
    agents: list[str] = []
    reading_agents = True
    for raw in text.splitlines():
        field_name, sep, value = raw.split("#", 1)[0].strip().partition(":")
        if not sep:
            continue
        field_name, value = field_name.strip().lower(), value.strip()
        if field_name == "user-agent":
            if not reading_agents:
                agents, reading_agents = [], True
            agent = value.split("/", 1)[0].strip().lower()
            agents.append(agent)
            groups.setdefault(agent, [])
        elif field_name in ("allow", "disallow"):
            reading_agents = False
            for agent in agents:
                groups[agent].append((field_name == "allow", value))
        elif field_name == "sitemap" and value:
            sitemaps.append(value)
    return groups, sitemaps


def robots_match(pattern: str, path: str) -> bool:
    regex = re.escape(pattern).replace(r"\*", ".*")
    if regex.endswith(r"\$"):
        regex = regex[:-2] + "$"
    return re.match(regex, path) is not None


def robots_allowed(groups: dict[str, list[tuple[bool, str]]], path: str = "/",
                   agent: str = "googlebot") -> bool:
    """Najdłuższa pasująca reguła wygrywa, przy remisie Allow (tak ocenia to Google)."""
    rules = groups.get(agent)
    if rules is None:
        rules = groups.get("*", [])
    best = None
    for allow, pattern in rules:
        if pattern and robots_match(pattern, path):
            key = (len(pattern), allow)
            if best is None or key > best:
                best = key
    return best is None or best[1]


def check_robots(origin: str, verify: bool) -> tuple[int | None, bool | None, str, list[str]]:
    """Zwraca (status HTTP, czy Googlebot może wejść na stronę główną, opis, mapy witryny)."""
    got = fetch(f"{origin}/robots.txt", verify=verify, max_bytes=512_000)
    if got.status is None:
        return None, None, f"nie udało się pobrać robots.txt ({got.error})", []
    if got.status == 200:
        groups, sitemaps = parse_robots(decode_html(got.body, got.headers.get("content-type", "")))
        if robots_allowed(groups, "/"):
            return 200, True, "pozwala Googlebotowi indeksować stronę główną", sitemaps
        return 200, False, "BLOKUJE Googlebota (Disallow dla strony głównej)", sitemaps
    if got.status >= 600 or got.status < 100:
        return got.status, None, (f"nietypowy kod HTTP {got.status} (prawdopodobnie ochrona przed botami) "
                                  f"— nie da się sprawdzić"), []
    if got.status == 429 or got.status >= 500:
        return got.status, None, (f"robots.txt zwraca błąd {got.status} — dopóki to trwa, "
                                  f"Google wstrzymuje indeksowanie"), []
    return got.status, True, f"brak pliku (HTTP {got.status}) — Google indeksuje bez ograniczeń", []


WAF_SERVER_RE = re.compile(r"cloudflare|cast-sec|ddos-guard|sucuri|incapsula|imperva|qrator|stormwall|variti|"
                           r"datadome|perimeterx|kasada|wallarm|safeline|bunkerweb|akamaighost", re.I)
# Frazy, które jednoznacznie oznaczają ekran ochrony przed botami.
WAF_BODY_RE = re.compile(r"just a moment|cf-chl|challenge-platform|attention required|captcha|ddos protection|"
                         r"ddos-guard|checking your browser|verify you are (a )?human|pardon our interruption|"
                         r"incapsula incident|sucuri website firewall|enable javascript and cookies|"
                         r"you have been blocked", re.I)
# Ogólne frazy — liczą się tylko wtedy, gdy serwer albo nagłówki wskazują na WAF (zwykły 403 to prawdziwy błąd).
WAF_WEAK_RE = re.compile(r"access denied|request blocked|forbidden", re.I)
WAF_HEADERS = ("cf-mitigated", "x-sucuri-id", "x-iinfo", "x-datadome", "x-amzn-waf-action")


def detect_block(got: Fetch, words: int) -> str:
    """Ekran ochrony przed botami (WAF) zamiast strony — wtedy treści nie da się ocenić automatycznie."""
    status = got.status or 0
    server = WAF_SERVER_RE.search(got.headers.get("server", ""))
    who = server.group(0) if server else "ochrona przed botami"
    if status >= 600 or 0 < status < 100:
        return f"{who}, nietypowy kod HTTP {status}"
    body = got.body[:30000].decode("utf-8", "replace")
    waf = bool(server) or any(h in got.headers for h in WAF_HEADERS)
    challenge = WAF_BODY_RE.search(body) or (waf and WAF_WEAK_RE.search(body)) or \
        any(h in got.headers for h in WAF_HEADERS)
    if challenge and (status >= 400 or words < 30):
        return f"{who}, HTTP {status}"
    return ""


X_ROBOTS_AGENT = re.compile(r"^\s*([\w-]+)\s*:")
# Dyrektywy, które same zawierają dwukropek (max-snippet: 50) — to nie jest nazwa robota.
ROBOTS_DIRECTIVES = {"all", "index", "follow", "noindex", "nofollow", "none", "noarchive", "nocache", "nosnippet",
                     "notranslate", "noimageindex", "indexifembedded", "max-snippet", "max-image-preview",
                     "max-video-preview", "unavailable_after"}


def header_noindex(values: list[str]) -> str:
    """Czy nagłówek X-Robots-Tag zabrania indeksowania Googlebotowi."""
    for value in values:
        agent = X_ROBOTS_AGENT.match(value)
        if agent and agent.group(1).lower() not in ROBOTS_DIRECTIVES | {"googlebot"}:
            continue  # reguła dla innego robota, np. „otherbot: noindex”
        if re.search(r"\b(noindex|none)\b", value, re.I):
            return clean(value, 80)
    return ""


@dataclass
class SiteInfo:
    host: str
    ips: list = field(default_factory=list)
    dns_note: str = ""
    error: str = ""                 # strona w ogóle nie działa
    entry_chain: list = field(default_factory=list)  # wejście przez http:// jak w przeglądarce
    entry_error: str = ""
    status: int | None = None
    final_url: str = ""
    seconds: float = 0.0
    https_ok: bool | None = None
    https_note: str = ""
    http_to_https: bool | None = None
    offsite: str = ""               # przekierowanie na inną domenę
    blocked: str = ""               # ochrona przed botami zasłania stronę (opis)
    page: PageInfo | None = None
    noindex: str = ""
    robots_status: int | None = None
    robots_allowed: bool | None = None
    robots_note: str = ""
    sitemaps: list = field(default_factory=list)
    ssl_expires: datetime | None = None
    ssl_issuer: str = ""
    ssl_error: str = ""
    www_ok: bool | None = None      # czy działa też adres z www (gdy sprawdzamy domenę bez www)
    www_note: str = ""


SLOW_SECONDS = 5


def check_www(domain: str) -> tuple[bool, str]:
    """Czy www.domena ma DNS i odpowiada — ludzie często wpisują adres z www."""
    www = f"www.{domain}"
    if not resolve(www):
        return False, "nie ma rekordu DNS — kto wpisze adres z www, nie wejdzie na stronę"
    got = fetch(f"http://{www}/", max_bytes=30_000)
    if got.status is None:
        return False, f"nie działa ({got.error})"
    blocked = detect_block(got, words=100)
    if blocked:
        return True, f"odpowiada, ale zasłania go ochrona przed botami ({blocked})"
    if got.status >= 400:
        return False, f"zwraca błąd HTTP {got.status}"
    return True, f"działa → {show_url(got.final_url, 70)}"


def check_site(domain: str) -> SiteInfo:
    site = SiteInfo(host=domain)
    site.ips = resolve(domain)
    if not site.ips:
        www = f"www.{domain}"
        site.ips = resolve(www)
        if not site.ips:
            site.error = "domena nie ma rekordów DNS — nie wskazuje na żaden serwer"
            return site
        site.host, site.dns_note = www, f"adres bez www nie działa (brak DNS), działa tylko {www}"

    via_http = fetch(f"http://{site.host}/")
    via_https = fetch(f"https://{site.host}/")
    site.entry_chain, site.entry_error = via_http.chain, via_http.error
    if via_https.ssl_error:
        site.https_ok, site.https_note = False, via_https.ssl_error
    elif via_https.status is None:
        site.https_ok, site.https_note = False, f"HTTPS nie działa ({via_https.error})"
    elif urllib.parse.urlsplit(via_https.final_url).scheme != "https":
        site.https_ok, site.https_note = False, "https:// przekierowuje z powrotem na http://"
    else:
        site.https_ok = True
    if via_http.status is not None:
        site.http_to_https = urllib.parse.urlsplit(via_http.final_url).scheme == "https"

    # Do analizy: HTTPS, jeśli działa; inaczej HTTP; w ostateczności HTTPS bez weryfikacji certyfikatu.
    page = via_https if site.https_ok else via_http
    if page.status is None and via_https.ssl_error:
        page = fetch(f"https://{site.host}/", verify=False)
    if page.status is None:
        site.error = f"strona nie odpowiada ({page.error or via_http.error})"
        return site

    site.status, site.final_url, site.seconds = page.status, page.final_url, page.seconds
    final_host = host_of(page.final_url)
    if final_host and not belongs_to(page.final_url, domain):
        site.offsite = final_host
    content_type = page.headers.get("content-type", "")
    if "html" in content_type.lower() or page.body.lstrip()[:1] == b"<":
        site.page = analyze_html(page.body, content_type)
    site.blocked = detect_block(page, site.page.words if site.page else 0)
    if site.blocked:
        site.page = None  # to ekran ochrony przed botami, a nie prawdziwa strona
    else:
        site.noindex = header_noindex(page.x_robots)
        if site.noindex:
            site.noindex = f"nagłówek X-Robots-Tag: {site.noindex}"
        elif site.page and re.search(r"\b(noindex|none)\b", site.page.robots, re.I):
            site.noindex = f"meta robots: {site.page.robots}"

    # robots.txt i certyfikat sprawdzamy tam, gdzie faktycznie jest strona (jeśli to ta domena).
    origin_host = final_host if final_host and not site.offsite else site.host
    scheme = "https" if site.https_ok else "http"
    site.robots_status, site.robots_allowed, site.robots_note, site.sitemaps = check_robots(
        f"{scheme}://{origin_host}", verify=bool(site.https_ok))
    if site.blocked and site.robots_status != 200:
        site.robots_allowed = None
        site.robots_note = f"zasłonięty ochroną przed botami (HTTP {site.robots_status}) — nie da się sprawdzić"
    if site.https_ok:
        site.ssl_expires, site.ssl_issuer, site.ssl_error = check_ssl(origin_host)
    if site.host == domain and not site.offsite:
        site.www_ok, site.www_note = check_www(domain)
    return site


# --- Rejestracja domeny (RDAP / WHOIS) ---------------------------------------------

PARKING_NS_RE = re.compile(r"sedoparking|bodis|parkingcrew|above\.com|afternic|dan\.com|hugedomains|"
                           r"parklogic|namebrightdns|uniregistrymarket|domainmarket|parking", re.I)
BAD_STATUS_RE = re.compile(r"hold|redemption|pending ?delete|inactive", re.I)


@dataclass
class RegInfo:
    source: str = ""
    name: str = ""                  # nazwa znaleziona w rejestrze (domena główna)
    registered: datetime | None = None
    expires: datetime | None = None
    registrar: str = ""
    statuses: list = field(default_factory=list)
    nameservers: list = field(default_factory=list)
    available: bool = False         # brak w rejestrze — prawdopodobnie wolna
    error: str = ""


def name_candidates(domain: str) -> list[str]:
    """blog.test.co.uk -> [blog.test.co.uk, test.co.uk, co.uk]"""
    labels = domain.split(".")
    return [".".join(labels[i:]) for i in range(len(labels) - 1)]


@functools.lru_cache(maxsize=1)
def rdap_servers() -> dict[str, str]:
    """Oficjalna lista IANA: końcówka domeny -> serwer RDAP."""
    status, data = http_json(IANA_RDAP, timeout=30)
    if status != 200:
        raise ApiError(f"lista serwerów RDAP niedostępna (HTTP {status})")
    return {tld.lower(): urls[0] for tlds, urls in data.get("services", []) if urls for tld in tlds}


def vcard_name(entity: dict) -> str:
    vcard = entity.get("vcardArray")
    if isinstance(vcard, list) and len(vcard) > 1 and isinstance(vcard[1], list):
        for item in vcard[1]:
            if isinstance(item, list) and len(item) > 3 and item[0] == "fn":
                return clean(item[3], 80)
    return ""


def rdap_lookup(domain: str, base: str) -> RegInfo:
    info = RegInfo(source="RDAP")
    for name in name_candidates(domain):
        status, data = http_json(f"{base.rstrip('/')}/domain/{name}",
                                 headers={"Accept": "application/rdap+json"}, timeout=30)
        if status in (400, 404):
            continue  # nie ma takiej nazwy — spróbuj domeny nadrzędnej
        if status != 200:
            info.error = f"RDAP: HTTP {status}"
            return info
        info.name = name
        for event in data.get("events") or []:
            if not isinstance(event, dict):
                continue
            action, when = str(event.get("eventAction", "")).lower(), parse_date(event.get("eventDate"))
            if action == "registration":
                info.registered = when
            elif action == "expiration":
                info.expires = when
        info.statuses = [clean(s, 60) for s in data.get("status") or []]
        info.nameservers = [clean(ns.get("ldhName", ""), 80).lower() for ns in data.get("nameservers") or []
                            if isinstance(ns, dict) and ns.get("ldhName")]
        for entity in data.get("entities") or []:
            if isinstance(entity, dict) and "registrar" in (entity.get("roles") or []):
                info.registrar = vcard_name(entity) or clean(entity.get("handle", ""), 80)
        return info
    info.available = True
    return info


def whois_query(server: str, query: str, timeout: float = 15) -> str:
    with socket.create_connection((server, 43), timeout=timeout) as sock:
        sock.sendall(query.encode("ascii", "ignore") + b"\r\n")
        chunks, size = [], 0
        while size < 200_000:
            chunk = sock.recv(8192)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    return b"".join(chunks).decode("utf-8", errors="replace")


@functools.lru_cache(maxsize=64)
def whois_server(tld: str) -> str:
    match = re.search(r"^(?:whois|refer):[ \t]*(\S+)", whois_query(IANA_WHOIS, tld), re.M | re.I)
    return match.group(1) if match else ""


WHOIS_FIELDS = {
    "registered": re.compile(r"^[ \t]*(?:creation date|created(?: on)?|registered(?: on)?|registration "
                             r"(?:date|time)|domain registration date|registered date)[ \t]*:[ \t]*(\S.*)$",
                             re.I | re.M),
    "expires": re.compile(r"^[ \t]*(?:registry expiry date|registrar registration expiration date|"
                          r"expir\w*(?: date| on)?|paid-till|renewal date)[ \t]*:[ \t]*(\S.*)$", re.I | re.M),
    "registrar": re.compile(r"^[ \t]*registrar(?: name)?[ \t]*:[ \t]*(\S.*)$", re.I | re.M),
    "status": re.compile(r"^[ \t]*(?:domain )?status[ \t]*:[ \t]*(\S.*)$", re.I | re.M),
    "ns": re.compile(r"^[ \t]*(?:name server|nserver|nameserver)s?[ \t]*:[ \t]*(\S+)", re.I | re.M),
}
WHOIS_FREE = re.compile(r"no match|not found|no entries found|no data found|status:[ \t]*(free|available)|"
                        r"is available|domain not registered|object does not exist", re.I)


def whois_section(text: str, header: str) -> list[str]:
    """Wcięte linie pod nagłówkiem bloku, np. „Name servers:” w WHOIS domen .eu."""
    match = re.search(rf"^{header}:[ \t]*\r?\n((?:[ \t]+\S.*(?:\r?\n|$))+)", text, re.I | re.M)
    return [line.strip() for line in match.group(1).splitlines() if line.strip()] if match else []


def whois_lookup(domain: str) -> RegInfo:
    info = RegInfo(source="WHOIS")
    server = whois_server(domain.rsplit(".", 1)[-1])
    if not server:
        info.error = "brak serwera WHOIS dla tej końcówki"
        return info
    for name in name_candidates(domain):
        text = whois_query(server, name)
        if WHOIS_FREE.search(text) and not WHOIS_FIELDS["registered"].search(text):
            continue
        info.name = name
        if match := WHOIS_FIELDS["registered"].search(text):
            info.registered = parse_date(match.group(1))
        if match := WHOIS_FIELDS["expires"].search(text):
            info.expires = parse_date(match.group(1))
        if match := WHOIS_FIELDS["registrar"].search(text):
            info.registrar = clean(match.group(1), 80)
        else:  # format blokowy (.eu): „Registrar:” i pod spodem „Name: …”
            names = [line.split(":", 1)[1] for line in whois_section(text, "registrar")
                     if line.lower().startswith("name:")]
            info.registrar = clean(names[0], 80) if names else ""
        info.statuses = [clean(s, 60) for s in WHOIS_FIELDS["status"].findall(text)][:6]
        servers = WHOIS_FIELDS["ns"].findall(text) or [line.split()[0] for line in whois_section(text, "name servers")]
        info.nameservers = sorted({clean(ns, 80).lower().rstrip(".") for ns in servers})
        return info
    info.available = True
    return info


def check_registration(domain: str) -> RegInfo:
    tld = domain.rsplit(".", 1)[-1]
    try:
        base = rdap_servers().get(tld)
    except ApiError:
        base = None
    if base:
        return rdap_lookup(domain, base)
    try:
        return whois_lookup(domain)
    except OSError as e:
        return RegInfo(source="WHOIS", error=f"WHOIS niedostępny ({describe_net_error(e)})")


# --- Archiwum Wayback Machine ------------------------------------------------------

ARCHIVE_SLOTS = threading.Semaphore(2)  # archive.org szybko odpowiada 429 przy wielu zapytaniach naraz


@dataclass
class ArchiveInfo:
    years: list = field(default_factory=list)
    first: datetime | None = None
    snapshots: list = field(default_factory=list)  # [(rok, tytuł, uwagi)]
    error: str = ""


def spread(items: list, count: int) -> list:
    """Równomiernie rozłożone elementy (zawsze pierwszy i ostatni)."""
    if len(items) <= count:
        return list(items)
    step = (len(items) - 1) / (count - 1)
    return [items[round(i * step)] for i in range(count)]


def wayback_date(timestamp: str) -> datetime | None:
    try:
        return datetime.strptime(timestamp[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def latest_snapshot(domain: str) -> tuple[bool, datetime | None]:
    """Zapasowe API: (czy udało się sprawdzić, data najnowszej kopii albo None, gdy kopii nie ma)."""
    try:
        status, data = http_json(f"{AVAILABLE_URL}?{urllib.parse.urlencode({'url': domain})}",
                                 timeout=20, attempts=1)
    except ApiError:
        return False, None
    if status != 200:
        return False, None
    closest = (data.get("archived_snapshots") or {}).get("closest") or {}
    return True, wayback_date(str(closest.get("timestamp", ""))) if closest.get("available") else None


def check_archive(domain: str, titles: int = 5) -> ArchiveInfo:
    info = ArchiveInfo()
    query = urllib.parse.urlencode({"url": domain, "output": "json", "fl": "timestamp,original",
                                    "filter": "statuscode:200", "collapse": "timestamp:4"})
    try:
        with ARCHIVE_SLOTS:
            status, body = http_request(f"{CDX_URL}?{query}", timeout=40, attempts=2)
    except ApiError as e:
        status, body, reason = None, b"", str(e)
    else:
        reason = "limit zapytań archive.org" if status == 429 else f"HTTP {status}"
    if status != 200:
        # Pełna historia (CDX) niedostępna — zapasowe API poda przynajmniej najnowszą kopię.
        checked, latest = latest_snapshot(domain)
        if checked and latest:
            info.error = f"pełna historia chwilowo niedostępna ({reason}); najnowsza kopia: {fmt_date(latest)}"
        elif checked:
            info.error = f"pełna historia chwilowo niedostępna ({reason}); zapasowe API nie znalazło kopii"
        else:
            info.error = f"archiwum chwilowo niedostępne ({reason})"
        return info
    try:
        rows = json.loads(body) if body.strip() else []
    except ValueError:
        rows = None
    if not isinstance(rows, list):
        info.error = "archiwum: nieczytelna odpowiedź"
        return info
    captures = [(str(r[0]), str(r[1])) for r in rows[1:]
                if isinstance(r, list) and len(r) >= 2 and str(r[0])[:4].isdigit()]
    info.years = sorted({int(ts[:4]) for ts, _ in captures})
    if captures:
        info.first = wayback_date(captures[0][0])
    for timestamp, original in spread(captures, titles):
        with ARCHIVE_SLOTS:
            got = fetch(f"{WAYBACK_URL}/{timestamp}id_/{original}", timeout=25, max_bytes=800_000)
        year = int(timestamp[:4])
        if got.status != 200:
            info.snapshots.append((year, "", f"nie udało się pobrać kopii ({got.status or got.error})"))
            continue
        page = analyze_html(got.body, got.headers.get("content-type", ""))
        notes = [page.kind] if page.kind else []
        if page.spam:
            notes.append("podejrzane słowa: " + ", ".join(page.spam[:3]))
        if CJK_RE.search(page.title):
            notes.append("chińskie/japońskie znaki")
        info.snapshots.append((year, page.title or "(brak tytułu)", "; ".join(notes)))
    return info


# --- Ocena ------------------------------------------------------------------------

BAD, WARN, OK, INFO = "bad", "warn", "ok", "info"
LEVEL_STYLE = {BAD: ("✘", "red"), WARN: ("!", "yellow"), OK: ("✔", "green"), INFO: ("·", "dim")}
VERDICTS = {
    "PROBLEM": ("✘", "red", "są poważne problemy"),
    "UWAGA": ("!", "yellow", "są rzeczy do sprawdzenia"),
    "OK": ("✔", "green", "domena wygląda na zdrową i gotową"),
    "BRAK DANYCH": ("?", "dim", "za mało danych do oceny"),
    "WOLNA": ("○", "green", "domena nie jest zarejestrowana — możesz ją zarejestrować"),
}


@dataclass
class Failed:
    """Test, którego nie udało się wykonać (błąd sieci, limit, nieoczekiwany błąd)."""
    message: str


@dataclass
class Plan:
    countries: list
    market: Country
    brand_codes: set
    phrase: str | None
    max_pages: int
    use_api: bool
    site: bool
    reg: bool
    archive: bool
    confirm_all: bool = False   # --potwierdz: powtórka „NIE” we wszystkich krajach, nie tylko na głównym rynku
    mode: str = "full"          # "full" = site: + pozycja; "quick" = sama nazwa, tylko 1. strona
    index_fallback: bool = False  # tryb szybki: gdy brak na 1. stronie, sprawdź site:


@dataclass
class DomainReport:
    domain: str
    results: list = field(default_factory=list)
    site: object = None
    reg: object = None
    archive: object = None
    findings: list = field(default_factory=list)
    verdict: str = ""
    changes: list = field(default_factory=list)
    previous: str = ""


def index_status(r: Result) -> str:
    """TAK / NIE / ? (tryb szybki: brak na 1. stronie, indeksu nie sprawdzano) / BŁĄD."""
    if r.indexed is None:
        return "BŁĄD"
    if not r.indexed and not r.index_source:
        return "?"
    return yes_no(r.indexed) + ("*" if r.recheck == "found" else "")


def rank_text(rank: Rank | None) -> str:
    if rank is None:
        return ""
    if rank.error:
        return "błąd"
    if rank.place:
        return f"{rank.place}. miejsce" + (f" (str. {rank.page})" if rank.page and rank.page > 1 else "")
    return f"brak ({pages_text(rank.pages)})" + (", jest w wiadomościach" if rank.stories else "")


def market_result(report: DomainReport, plan: Plan) -> Result | None:
    return next((r for r in report.results if r.country == plan.market), None)


def assess_quick(report: DomainReport, plan: Plan, add) -> None:
    """Ocena trybu szybkiego: czy domena jest na 1. stronie po wpisaniu samej nazwy."""
    rows = [r for r in report.results if r.indexed is not None and r.brand]
    if not rows:
        return
    query, market = rows[0].brand.query, plan.market

    def names(rs: list[Result]) -> str:
        return ", ".join(r.country.domain for r in rs[:8]) + (f" i {len(rs) - 8} innych" if len(rs) > 8 else "")

    on_page = [r for r in rows if r.brand.place]
    first = [r for r in on_page if r.brand.place == 1]
    missing = [r for r in rows if not r.brand.place]
    if not on_page:
        add(BAD, f"Po wpisaniu „{query}” domeny nie ma na 1. stronie w żadnym ze sprawdzonych krajów ({len(rows)})")
    elif len(first) == len(rows):
        add(OK, f"1. miejsce po wpisaniu „{query}” we wszystkich sprawdzonych krajach ({len(rows)})")
    else:  # nie wszędzie 1. miejsce — tak jak w trybie pełnym to ostrzeżenie
        add(WARN, f"Na 1. stronie po wpisaniu „{query}” w {len(on_page)}/{len(rows)} krajach, "
                  f"1. miejsce tylko w {len(first)}")
        if missing:
            add(WARN, f"Brak na 1. stronie w: {names(missing)}")
    m = next((r for r in rows if r.country == market), None)
    if m and m.brand.place and m.brand.place > 1:
        add(INFO, f"{market.domain}: {rank_text(m.brand)} po wpisaniu „{query}”")
    stories = [r for r in missing if r.brand.stories]
    if stories:
        add(INFO, f"Tylko w „Najważniejszych wiadomościach”: {names(stories)}")
    checked = [r for r in missing if r.index_source == "site"]
    absent = [r for r in checked if not r.indexed]
    if absent:
        add(BAD if len(absent) == len(rows) else WARN, f"Nie ma jej w indeksie (site:) w: {names(absent)}")
    present = [r for r in checked if r.indexed]
    if present:
        add(INFO, f"Jest w indeksie, ale nie na 1. stronie w: {names(present)}")
    if missing and not checked:
        add(INFO, "Tryb szybki patrzy tylko na 1. stronę wyników dla samej nazwy — tam, gdzie domeny nie ma, "
                  "nie wiadomo, czy jest w indeksie (włącz sprawdzanie site: albo tryb pełny)")
    hits = list({hit.link: hit for r in rows for hit in r.hits}.values())
    spam = spam_words([f"{hit.title} {hit.snippet}" for hit in hits])
    if spam:
        add(WARN, f"Strony domeny w wynikach zawierają podejrzane słowa: {', '.join(spam)}")
    ranked = [r for r in rows if r.phrase and not r.phrase.error]
    if ranked:
        page_one = sum(1 for r in ranked if r.phrase.page == 1)
        add(INFO, f"Fraza „{clean(plan.phrase)}”: 1. strona wyników organicznych w {page_one}/{len(ranked)} krajach")


def assess(report: DomainReport, plan: Plan) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []

    def add(level: str, text: str) -> None:
        findings.append((level, text))

    domain, market = report.domain, plan.market
    reg = report.reg
    age = days_since(reg.registered) if isinstance(reg, RegInfo) and reg.registered else None
    checked = [r for r in report.results if r.indexed is not None]
    if checked and plan.mode == "quick":
        assess_quick(report, plan, add)
    elif checked:
        yes = [r for r in checked if r.indexed]
        unstable = [r for r in checked if r.recheck == "found"]
        if not yes:
            confirmed = " (potwierdzone powtórką)" if any(r.recheck == "confirmed" for r in checked) else ""
            add(BAD, f"Nie ma jej w indeksie Google w żadnym ze sprawdzonych krajów ({len(checked)}){confirmed}")
        elif len(yes) == len(checked):
            add(OK, f"Jest w indeksie Google we wszystkich sprawdzonych krajach ({len(checked)})")
        else:
            missing = [r.country.domain for r in checked if not r.indexed]
            shown = ", ".join(missing[:8]) + (f" i {len(missing) - 8} innych" if len(missing) > 8 else "")
            if unstable or (age is not None and age < NEW_DOMAIN_DAYS):
                why = f"przy nowej domenie ({age_text(age)})" if age is not None and age < NEW_DOMAIN_DAYS \
                    else "przy tak niestabilnych wynikach"
                add(WARN, f"Widoczna tylko w {len(yes)}/{len(checked)} krajach (brak w: {shown}) — {why} to zwykle "
                          f"chwiejny indeks Google, a nie filtr krajowy"
                          + ("" if plan.confirm_all else "; --potwierdz powtórzy sprawdzenie wszystkich krajów z NIE"))
            else:
                add(WARN, f"W indeksie tylko w {len(yes)}/{len(checked)} krajach — brak w: {shown}")
        if unstable:
            add(WARN, f"Wyniki niestabilne: w {', '.join(r.country.domain for r in unstable)} pierwsze zapytanie "
                      f"nie znalazło domeny, a powtórka tak — Google pokazuje ją raz tak, raz nie "
                      f"(typowe w pierwszych tygodniach nowej domeny)")
        m = market_result(report, plan)
        if m and m.indexed:
            if m.first_kind == "home":
                add(OK, f"Strona główna jest 1. wynikiem site:{domain} w {market.domain}")
            elif m.first_kind == "subdomain":
                add(INFO, f"1. wynik site: w {market.domain} to subdomena {host_of(m.first_url)} "
                          f"— przy stronach z wieloma subdomenami to normalne")
            else:
                add(WARN, f"Strona główna NIE jest 1. wynikiem site: w {market.domain} (1. jest "
                          f"{clean(m.first_url, 80)}) — przy małych stronach to częsty znak filtra")
        if m and m.brand:
            b = m.brand
            if b.error:
                add(WARN, f"Nie udało się sprawdzić pozycji na „{b.query}”: {b.error}")
            elif b.place == 1:
                add(OK, f"1. miejsce w {market.domain} po wpisaniu „{b.query}”")
            elif b.place:
                add(WARN, f"Po wpisaniu „{b.query}” w {market.domain} dopiero {rank_text(b)}")
            else:
                add(BAD, f"Po wpisaniu „{b.query}” w {market.domain} domeny nie ma ({pages_text(b.pages)}) "
                         f"— nie wychodzi na własną nazwę (przy unikalnej nazwie to częsty znak filtra, "
                         f"przy popularnym słowie bywa normalne)"
                         + (", pojawia się tylko w „Najważniejszych wiadomościach”" if b.stories else ""))
        others = [r for r in checked if r.brand and not r.brand.error and r.country != market]
        if others:
            first = sum(1 for r in others if r.brand.place == 1)
            add(INFO, f"Na własną nazwę 1. miejsce w {first}/{len(others)} pozostałych krajach")
        ranked = [r for r in checked if r.phrase and not r.phrase.error]
        if ranked:
            page_one = sum(1 for r in ranked if r.phrase.page == 1)
            stories = sum(1 for r in ranked if r.phrase.stories and not r.phrase.place)
            add(INFO, f"Fraza „{clean(plan.phrase)}”: 1. strona wyników organicznych w {page_one}/{len(ranked)} krajach"
                + (f"; tylko w „Najważniejszych wiadomościach” w {stories}" if stories else ""))
        removed = [r for r in checked if r.notices]
        if removed:
            add(WARN, f"Google usunął część wyników z tej domeny (DMCA/żądania prawne) w: "
                      f"{', '.join(r.country.domain for r in removed)} — {removed[0].notices[0]}")
        hits = list({hit.link: hit for r in checked for hit in r.hits}.values())
        spam = spam_words([f"{hit.title} {hit.snippet}" for hit in hits])
        if spam:
            add(WARN, f"Zaindeksowane strony zawierają podejrzane słowa: {', '.join(spam)}")
        if market.code not in CJK_MARKETS and any(CJK_RE.search(hit.title) for hit in hits):
            add(WARN, "W indeksie są tytuły z chińskimi/japońskimi znakami — typowy ślad włamania "
                      "lub dawnego spamu")

    site = report.site
    if isinstance(site, Failed):
        add(INFO, f"Nie udało się sprawdzić strony: {site.message}")
    elif isinstance(site, SiteInfo):
        before = len(findings)
        free = isinstance(report.reg, RegInfo) and report.reg.available
        if site.error:
            add(INFO if free else BAD, f"Strona: {site.error}" + (" (normalne dla wolnej domeny)" if free else ""))
        else:
            if site.dns_note:
                add(WARN, sentence(site.dns_note))
            if site.offsite:
                add(BAD, f"Przekierowuje na inną domenę ({site.offsite}) — ta domena sama nie będzie w wynikach")
            if site.blocked:
                add(WARN, f"Stronę zasłania ochrona przed botami ({site.blocked}) — automatycznie nie da się ocenić "
                          f"treści; w przeglądarce zwykle działa. Upewnij się, że ochrona przepuszcza Googlebota")
            elif site.status and site.status >= 400:
                add(BAD, f"Strona główna zwraca błąd HTTP {site.status}")
            elif site.status and 300 <= site.status < 400:
                add(BAD, f"Pętla albo za dużo przekierowań (HTTP {site.status})")
            if site.noindex:
                add(BAD, f"Strona główna ma noindex ({site.noindex}) — Google jej nie zaindeksuje")
            if site.robots_allowed is False:
                add(BAD, f"robots.txt {site.robots_note}")
            elif site.robots_allowed is None and not site.blocked:
                add(WARN, site.robots_note)
            page = site.page
            if page and page.kind:
                add(BAD if KIND_SEVERE[page.kind] else WARN,
                    f"Strona wygląda na: {page.kind} (znaleziono „{page.kind_hint}”)")
            if site.https_ok is False:
                add(BAD if "certyfikat" in site.https_note else WARN, f"HTTPS: {site.https_note}")
            elif site.https_ok and site.http_to_https is False:
                add(WARN, "Adres http:// nie przekierowuje na https://")
            if site.ssl_expires:
                days = days_until(site.ssl_expires)
                if days < 0:
                    add(BAD, "Certyfikat SSL wygasł")
                elif days < 14:
                    add(WARN, f"Certyfikat SSL wygasa za {days} {plural(days, 'dzień', 'dni', 'dni')}")
            elif site.ssl_error:
                add(WARN, site.ssl_error)
            if site.www_ok is False:
                add(WARN, f"Adres z www (www.{domain}) {site.www_note}")
            if site.seconds > SLOW_SECONDS:
                add(WARN, f"Strona główna ładuje się wolno ({site.seconds:.1f} s)")
            if page and not page.kind:
                if not page.title:
                    add(WARN, "Strona główna nie ma tytułu (<title>)")
                if not page.viewport:
                    add(WARN, "Brak <meta name=\"viewport\"> — strona może źle wyglądać na telefonach "
                              "(Google ocenia wersję mobilną)")
                if page.words < 150:
                    add(WARN, f"Mało treści na stronie głównej (~{page.words} słów) — "
                              f"strona może być pusta albo generowana tylko przez JavaScript")
                canonical = urllib.parse.urljoin(site.final_url, page.canonical) if page.canonical else ""
                if canonical and not belongs_to(canonical, domain):
                    add(WARN, f"Tag canonical wskazuje inną domenę: {clean(canonical, 80)}")
                if page.refresh:
                    add(WARN, f"Strona przekierowuje przez meta refresh: {page.refresh}")
                if page.spam:
                    add(WARN, f"Na stronie głównej są podejrzane słowa: {', '.join(page.spam)}")
            if not site.page and site.status == 200 and not site.blocked:
                add(WARN, "Strona główna nie zwraca HTML")
        if len(findings) == before:
            add(OK, f"Strona działa (HTTP {site.status}), HTTPS w porządku, robots.txt i meta tagi "
                    f"pozwalają indeksować")

    if isinstance(reg, Failed):
        add(INFO, f"Nie udało się sprawdzić rejestracji: {reg.message}")
    elif isinstance(reg, RegInfo):
        if reg.available:
            add(INFO, "Domena wygląda na WOLNĄ — nie ma jej w rejestrze, można ją zarejestrować")
        elif reg.error:
            add(INFO, f"Rejestracja: {reg.error}")
        else:
            if reg.registered:
                add(INFO, f"Zarejestrowana {fmt_date(reg.registered)} ({age_text(days_since(reg.registered))} temu)")
            if reg.expires:
                days = days_until(reg.expires)
                if days < 0:
                    add(BAD, f"Domena wygasła {fmt_date(reg.expires)}")
                elif days < 30:
                    add(WARN, f"Domena wygasa za {days} {plural(days, 'dzień', 'dni', 'dni')} "
                              f"({fmt_date(reg.expires)})")
            bad_statuses = [s for s in reg.statuses if BAD_STATUS_RE.search(s)]
            if bad_statuses:
                add(BAD, f"Status w rejestrze: {', '.join(bad_statuses)} — domena jest zawieszona "
                         f"albo zaraz wypadnie")
            parking = [ns for ns in reg.nameservers if PARKING_NS_RE.search(ns)]
            if parking:
                add(WARN, f"Domena stoi na serwerach DNS parkingu: {', '.join(parking[:2])}")

    archive = report.archive
    if isinstance(archive, Failed):
        add(INFO, f"Nie udało się sprawdzić archiwum: {archive.message}")
    elif isinstance(archive, ArchiveInfo):
        if archive.error:
            add(INFO, sentence(archive.error))
        elif not archive.years:
            add(INFO, "Brak kopii w archiwum Wayback — domena bez historii albo nieużywana")
        else:
            count = len(archive.years)
            add(INFO, f"Archiwum Wayback: {year_ranges(archive.years)} ({count} {plural(count, 'rok', 'lata', 'lat')})")
            if isinstance(reg, RegInfo) and reg.registered and archive.first \
                    and archive.first.year < reg.registered.year:
                add(INFO, f"Historia starsza niż obecna rejestracja ({archive.first.year} < "
                          f"{reg.registered.year}) — domena kiedyś wygasła i ktoś ją zarejestrował ponownie")
            flagged = [f"{year}: {note}" for year, _, note in archive.snapshots
                       if note and not note.startswith("nie udało")]
            if flagged:
                add(WARN, "W historii domeny: " + "; ".join(flagged))
    return findings


def verdict_of(findings: list[tuple[str, str]], available: bool = False) -> str:
    if available:
        return "WOLNA"
    levels = {level for level, _ in findings}
    if BAD in levels:
        return "PROBLEM"
    if WARN in levels:
        return "UWAGA"
    return "OK" if OK in levels else "BRAK DANYCH"


# --- Historia sprawdzeń -------------------------------------------------------------

def history_path(domain: str) -> Path:
    return HISTORY_DIR / f"{domain}.jsonl"


def history_record(report: DomainReport, provider_name: str, plan: Plan) -> dict:
    def place(rank: Rank | None):
        if rank is None or rank.error:
            return None
        return rank.place or 0  # 0 = nie znaleziono

    return {
        "czas": datetime.now().astimezone().isoformat(timespec="seconds"),
        "api": provider_name,
        "rynek": plan.market.code,
        "werdykt": report.verdict,
        "tryb": plan.mode,
        "kraje": {r.country.code: {"i": r.indexed if r.index_source else None, "g": r.first_kind or None,
                                   "n": place(r.brand)}
                  for r in report.results if r.indexed is not None},
    }


def read_history(path: Path) -> list[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    records = []
    for line in lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and isinstance(record.get("kraje"), dict):
            records.append(record)
    return records


def history_summary(record: dict) -> str:
    """„indeks 28/30” (tryb pełny) albo „na 1. str. 3/30” (tryb szybki) — z jednego wpisu historii."""
    countries = [v for v in record["kraje"].values() if isinstance(v, dict)]
    if record.get("tryb") == "quick":
        on_page = sum(1 for v in countries if v.get("n"))
        return f"na 1. str. {on_page}/{len(countries)}"
    yes = sum(1 for v in countries if v.get("i"))
    return f"indeks {yes}/{len(countries)}"


def place_text(value) -> str:
    if value is None:
        return "—"
    return f"{value}. miejsce" if value else "brak"


def history_changes(previous: dict, current: dict) -> list[str]:
    changes = []
    old = previous.get("kraje") or {}
    for code, now in current["kraje"].items():
        before = old.get(code)
        if not isinstance(before, dict):
            continue
        name = COUNTRY_BY_CODE[code].domain if code in COUNTRY_BY_CODE else code
        if before.get("i") is not None and now["i"] is not None and before.get("i") != now["i"]:
            changes.append(f"{name}: indeks {yes_no(before.get('i'))} → {yes_no(now['i'])}")
        if before.get("g") and now["g"] and before.get("g") != now["g"]:
            changes.append(f"{name}: 1. wynik site: {FIRST_KIND_TEXT.get(before.get('g'), '?')} → "
                           f"{FIRST_KIND_TEXT.get(now['g'], '?')}")
        if before.get("n") is not None and now["n"] is not None and before.get("n") != now["n"]:
            changes.append(f"{name}: pozycja na nazwę {place_text(before.get('n'))} → {place_text(now['n'])}")
    if previous.get("werdykt") and previous.get("werdykt") != current["werdykt"]:
        changes.append(f"werdykt: {previous.get('werdykt')} → {current['werdykt']}")
    return changes


def save_history(report: DomainReport, record: dict) -> None:
    HISTORY_DIR.mkdir(exist_ok=True)
    with history_path(report.domain).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def show_history(domains: list[str]) -> int:
    paths = [history_path(d) for d in domains] if domains else sorted(HISTORY_DIR.glob("*.jsonl"))
    if not paths:
        print("Brak zapisanej historii (zapisuje się po każdym sprawdzeniu z zapytaniami do Google).")
        return 0
    for path in paths:
        print()
        print(paint(path.name.removesuffix(".jsonl"), "bold"))
        records = read_history(path)
        if not records:
            print("  brak zapisanych sprawdzeń")
            continue
        for record in records[-20:]:
            market = record["kraje"].get(record.get("rynek")) or {}
            when = clean(record.get("czas", "?"))[:16].replace("T", " ")
            print(f"  {when}  {history_summary(record)}  ·  1. w site: "
                  f"{FIRST_KIND_TEXT.get(market.get('g'), '—')}  ·  na nazwę: {place_text(market.get('n'))}"
                  f"  ·  {clean(record.get('werdykt', '?'))}")
    return 0


# --- Wypisywanie -------------------------------------------------------------------

def term_width() -> int:
    if not sys.stdout.isatty():
        return 160  # wyjście do pliku/potoku — bez przycinania do 80 kolumn
    return max(60, shutil.get_terminal_size().columns)


def say(level: str, text: str, indent: str = "  ") -> None:
    symbol, color = LEVEL_STYLE[level]
    wrapped = textwrap.fill(f"{symbol} {text}", min(term_width(), 130), initial_indent=indent,
                            subsequent_indent=indent + "  ")
    print(paint(wrapped.replace(NBSP, " "), color))


def section(title: str) -> None:
    print()
    print(paint(title, "bold"))


def print_table(headers: list[str], rows: list[list[str]], colors: list[dict] | None = None,
                indent: str = "") -> None:
    """Tabela z kolumnami wyrównanymi do najdłuższej wartości; ostatnia kolumna jest przycinana."""
    widths = [max([len(h)] + [len(row[i]) for row in rows]) for i, h in enumerate(headers)]
    room = term_width() - len(indent) - sum(widths[:-1]) - 2 * (len(widths) - 1)
    widths[-1] = max(20, min(widths[-1], room))
    print(paint(indent + "  ".join(h.ljust(w) for h, w in zip(headers, widths)).rstrip(), "bold"))
    for n, row in enumerate(rows):
        cells = [cell.ljust(width) for cell, width in zip(row[:-1], widths)] + [clean(row[-1], widths[-1])]
        for i, color in (colors[n] if colors else {}).items():
            cells[i] = paint(cells[i], color)
        print(indent + "  ".join(cells))


def fmt_count(r: Result) -> str:
    if not r.indexed:
        return "0" if r.indexed is False else ""
    if r.total:
        return "~" + f"{r.total:,}".replace(",", " ")
    return f"{len(r.hits)}+" if len(r.hits) >= 10 else str(len(r.hits))


def print_index_quick(report: DomainReport, plan: Plan) -> None:
    results = report.results
    fallback = any(r.index_source == "site" for r in results)
    headers = ["Kraj", "Google", f"Na „{brand_name(report.domain)}”"] + (["Indeks (site:)"] if fallback else []) \
        + ["Adres z listy / błąd"]
    rows, colors = [], []
    for r in results:
        rank = r.brand
        row = [f"{r.country.name} ({r.country.code})", r.country.domain,
               "BŁĄD" if r.indexed is None else rank_text(rank)]
        color = {2: "green" if rank and rank.place == 1 else "yellow" if rank and rank.place else "red"}
        if fallback:
            row.append(index_status(r) if r.index_source == "site" else "—")
        row.append(r.error or (rank.url if rank and rank.url else ""))
        if r.error:
            color[len(row) - 1] = "yellow"
        rows.append(row)
        colors.append(color)
    print_table(headers, rows, colors, indent="  ")
    ranked = [r for r in results if r.brand and r.indexed is not None]
    on_page = sum(1 for r in ranked if r.brand.place)
    print()
    say(INFO, f"Tryb szybki: jedno zapytanie „{brand_name(report.domain)}” na kraj, tylko 1. strona wyników — "
              f"domena jest na niej w {on_page}/{len(ranked)} krajach.")
    failed = [r for r in results if r.indexed is None]
    if failed:
        say(WARN, f"Nie udało się sprawdzić ({len(failed)}): {', '.join(r.country.domain for r in failed)}")


def print_index(report: DomainReport, plan: Plan) -> None:
    if plan.mode == "quick":
        print_index_quick(report, plan)
        return
    results = report.results
    show_brand = any(r.brand for r in results)
    show_phrase = any(r.phrase for r in results)
    headers = ["Kraj", "Google", "Indeks", "Wyniki", "1. w site:"]
    if show_brand:
        headers.append("Na nazwę")
    if show_phrase:
        headers.append("Fraza")
    headers.append("Pierwszy wynik site: / błąd")
    rows, colors = [], []
    for r in results:
        status = "BŁĄD" if r.indexed is None else yes_no(r.indexed) + ("*" if r.recheck == "found" else "")
        kind = {"home": "TAK", "subpage": "NIE", "subdomain": "subdomena", "other": "NIE"}.get(r.first_kind, "—")
        row = [f"{r.country.name} ({r.country.code})", r.country.domain, status, fmt_count(r), kind]
        color = {2: {"TAK": "green", "NIE": "red", "TAK*": "yellow"}.get(status, "yellow"),
                 4: {"TAK": "green", "NIE": "yellow"}.get(kind, "dim")}
        for rank, enabled in ((r.brand, show_brand), (r.phrase, show_phrase)):
            if enabled:
                row.append(rank_text(rank))
                color[len(row) - 1] = ("green" if rank and rank.place == 1 else
                                       "red" if rank and not rank.place and not rank.error else "yellow")
        row.append(r.error or show_url(r.first_url))
        if r.error:
            color[len(row) - 1] = "yellow"
        rows.append(row)
        colors.append(color)
    print_table(headers, rows, colors, indent="  ")

    checked = [r for r in results if r.indexed is not None]
    yes = [r for r in checked if r.indexed]
    no = [r for r in checked if r.indexed is False]
    failed = [r for r in results if r.indexed is None]

    def where(rs: list[Result]) -> str:
        # twarde spacje, żeby zawijanie nie rozrywało „Wielka Brytania (google.co.uk)”
        return ", ".join(f"{r.country.name} ({r.country.domain})".replace(" ", NBSP) for r in rs)

    print()
    if yes and not no:
        say(OK, f"Zaindeksowana we wszystkich sprawdzonych krajach ({len(yes)}).")
    elif no and not yes:
        say(BAD, f"Nie ma jej w indeksie w żadnym ze sprawdzonych krajów ({len(no)}).")
    else:
        if yes:
            say(OK, f"W indeksie ({len(yes)}/{len(checked)}): {where(yes)}")
        if no:
            say(BAD, f"Brak w indeksie ({len(no)}/{len(checked)}): {where(no)}")
    if failed:
        say(WARN, f"Nie udało się sprawdzić ({len(failed)}): {where(failed)}")
    found_later = [r.country.domain for r in results if r.recheck == "found"]
    confirmed = [r.country.domain for r in results if r.recheck == "confirmed"]
    if found_later:
        say(WARN, f"* TAK dopiero w powtórnym zapytaniu ({', '.join(found_later)}) — Google pokazuje tę domenę "
                  f"niestabilnie")
    if confirmed:
        say(INFO, f"NIE potwierdzone powtórką w: {', '.join(confirmed)}")
    home = sum(1 for r in yes if r.first_kind == "home")
    manual = next((r for r in results if r.country == plan.market and r.indexed is False), None)
    if manual:  # porównanie z tym, co widać w przeglądarce (pws=0 wyłącza personalizację)
        query = urllib.parse.urlencode({"q": f"site:{report.domain}", "hl": manual.country.hl,
                                        "gl": manual.country.code, "pws": 0})
        say(INFO, f"Sprawdź ręcznie: https://www.{manual.country.domain}/search?{query}")
    if yes:
        say(INFO, f"Strona główna jako 1. wynik site: w {home}/{len(yes)} krajach, w których domena jest w indeksie.")


def print_indexed_pages(report: DomainReport, plan: Plan) -> None:
    source = market_result(report, plan)
    if not source or not source.indexed:
        source = next((r for r in report.results if r.indexed), None)
    if not source:
        return
    section(f"ZAINDEKSOWANE STRONY ({source.country.domain}, 1. strona wyników site:)")
    for n, hit in enumerate(source.hits, 1):
        title = clean(hit.title, 60) or "(bez tytułu)"
        print(f"  {n:>2}. {title.ljust(60)}  {show_url(hit.link, max(20, term_width() - 70))}")


def print_site(site) -> None:
    section("STRONA WWW")
    if isinstance(site, Failed):
        say(INFO, f"nie udało się sprawdzić: {site.message}")
        return
    if site.ips:
        print(f"  IP: {', '.join(site.ips[:4])}" + (f"  ({site.dns_note})" if site.dns_note else ""))
    if site.error:
        say(BAD, site.error)
        return
    hops = " → ".join(f"{clean(url, 60)} [{status}]" for status, url in site.entry_chain)
    if site.entry_error:
        hops = (hops + " → " if hops else f"http://{site.host}/ → ") + f"✘ {site.entry_error}"
    print(f"  Wejście przez http://: {hops}")
    print(f"  Strona końcowa: {clean(site.final_url, 90)} (HTTP {site.status}, {site.seconds:.1f} s)")
    if site.https_ok:
        ssl_line = "działa"
        if site.ssl_expires:
            days = days_until(site.ssl_expires)
            ssl_line += f", certyfikat ważny do {fmt_date(site.ssl_expires)} ({days} dni)"
        if site.ssl_issuer:
            ssl_line += f", wystawca: {site.ssl_issuer}"
        print(f"  HTTPS: {ssl_line}" + ("" if site.http_to_https in (True, None) else
                                        " · http:// NIE przekierowuje na https://"))
    else:
        print(f"  HTTPS: {site.https_note}")
    robots = f"  robots.txt: {site.robots_note}"
    if site.sitemaps:
        robots += f" · mapa witryny: {clean(site.sitemaps[0], 70)}"
    print(robots)
    if site.blocked:
        print(f"  Ochrona przed botami: {site.blocked} — treść strony niedostępna dla automatów")
    else:
        print(f"  Indeksowanie: {'NOINDEX — ' + site.noindex if site.noindex else 'brak noindex (meta i nagłówki)'}")
    if site.www_note:
        print(f"  Adres z www: {site.www_note}")
    page = site.page
    if page:
        details = [f"~{page.words} słów", f"H1: {page.h1}", "viewport: " + ("jest" if page.viewport else "BRAK")]
        if page.lang:
            details.insert(0, f"język: {page.lang}")
        print(f"  Tytuł: „{page.title or '(brak)'}” · " + " · ".join(details))
        if page.description:
            print(f"  Opis: „{clean(page.description, 110)}”")
        if page.canonical:
            print(f"  canonical: {clean(page.canonical, 90)}")
        if page.kind:
            say(BAD if KIND_SEVERE[page.kind] else WARN, f"wygląda na: {page.kind} („{page.kind_hint}”)")


def print_registration(reg, domain: str) -> None:
    section("REJESTRACJA DOMENY" + (f" ({reg.source})" if isinstance(reg, RegInfo) and reg.source else ""))
    if isinstance(reg, Failed):
        say(INFO, f"nie udało się sprawdzić: {reg.message}")
        return
    if reg.available:
        say(INFO, "brak w rejestrze — domena wygląda na WOLNĄ")
        return
    if reg.error:
        say(INFO, reg.error)
        return
    parts = []
    if reg.registered:
        parts.append(f"zarejestrowana {fmt_date(reg.registered)} ({age_text(days_since(reg.registered))} temu)")
    else:
        parts.append("data rejestracji niedostępna (rejestr jej nie publikuje)")
    if reg.expires:
        parts.append(f"wygasa {fmt_date(reg.expires)} (za {days_until(reg.expires)} dni)")
    print("  " + " · ".join(parts))
    if reg.name and reg.name != domain:
        print(f"  (dane dla domeny głównej: {reg.name})")
    if reg.registrar:
        print(f"  Rejestrator: {reg.registrar}")
    if reg.statuses:
        print(f"  Status: {', '.join(reg.statuses[:5])}")
    if reg.nameservers:
        print(f"  Serwery DNS: {', '.join(reg.nameservers[:4])}")


def print_archive(archive, domain: str) -> None:
    section("ARCHIWUM (Wayback Machine)")
    if isinstance(archive, Failed):
        say(INFO, f"nie udało się sprawdzić: {archive.message}")
        return
    if archive.error:
        say(INFO, archive.error)
        return
    if not archive.years:
        print("  brak kopii strony głównej w archiwum")
        return
    count = len(archive.years)
    first = f" · pierwsza kopia: {fmt_date(archive.first)}" if archive.first else ""
    print(f"  Kopie z lat: {year_ranges(archive.years)} ({count} {plural(count, 'rok', 'lata', 'lat')}){first}")
    if archive.snapshots:
        print("  Tytuły strony w historii:")
        for year, title, note in archive.snapshots:
            line = f"    {year}  " + (f"„{clean(title, 70)}”" if title else "")
            if note:
                line += paint(f"  ⚠ {note}", "yellow")
            print(line)
    print(f"  Przeglądaj: https://web.archive.org/web/*/{domain}")


def print_verdict(report: DomainReport) -> None:
    symbol, color, description = VERDICTS[report.verdict]
    section("WERDYKT")
    print(paint(f"  {symbol} {report.verdict} — {description}", color))
    order = {BAD: 0, WARN: 1, OK: 2, INFO: 3}
    for level, text in sorted(report.findings, key=lambda f: order[f[0]]):
        say(level, text, indent="    ")


def print_report(report: DomainReport, plan: Plan, provider_name: str) -> None:
    line = "━" * max(10, min(term_width(), 100) - len(report.domain) - 4)
    print()
    print(paint(f"━━ {report.domain} {line}", "bold"))
    if report.results and plan.mode == "quick":
        section(f"POZYCJA NA „{brand_name(report.domain)}” ({provider_name}, tryb szybki: tylko 1. strona wyników)")
        print_index(report, plan)
    elif report.results:
        section(f"INDEKS GOOGLE ({provider_name}, site:{report.domain}, główny rynek: {plan.market.domain})")
        print_index(report, plan)
        print_indexed_pages(report, plan)
    if report.site is not None:
        print_site(report.site)
    if report.reg is not None:
        print_registration(report.reg, report.domain)
    if report.archive is not None:
        print_archive(report.archive, report.domain)
    if report.previous:
        section(f"ZMIANY OD POPRZEDNIEGO SPRAWDZENIA ({report.previous[:16].replace('T', ' ')})")
        if report.changes:
            for change in report.changes:
                say(WARN, change)
        else:
            print("  bez zmian")
    print_verdict(report)


def site_summary(site) -> str:
    if site is None:
        return "—"
    if isinstance(site, Failed):
        return "błąd testu"
    if site.error:
        return "brak DNS" if "DNS" in site.error else "nie działa"
    if site.offsite:
        return f"→ {site.offsite}"
    if site.blocked:
        return "ochrona (WAF)"
    if site.page and site.page.kind:
        return KIND_SHORT[site.page.kind]
    return f"HTTP {site.status}"


def print_comparison(reports: list[DomainReport], plan: Plan) -> None:
    section("PORÓWNANIE DOMEN")
    quick = plan.mode == "quick"
    headers = ["Domena", "Na 1. stronie" if quick else "Indeks", "1. w site:", "Na nazwę", "Strona", "Wiek",
               "Archiwum", "Werdykt"]
    rows, colors = [], []
    for report in reports:
        checked = [r for r in report.results if r.indexed is not None]
        if quick:
            index = f"{sum(1 for r in checked if r.brand and r.brand.place)}/{len(checked)}" if checked else "—"
        else:
            index = f"{sum(1 for r in checked if r.indexed)}/{len(checked)}" if checked else "—"
        m = market_result(report, plan)
        kind = FIRST_KIND_TEXT.get(m.first_kind, "—") if m and m.first_kind else "—"
        brand = rank_text(m.brand) if m and m.brand else "—"
        reg = report.reg
        age = "—"
        if isinstance(reg, RegInfo):
            age = "wolna" if reg.available else age_text(days_since(reg.registered)) if reg.registered else "?"
        archive = report.archive
        years = f"{archive.years[0]}–{archive.years[-1]}" if isinstance(archive, ArchiveInfo) and archive.years else "—"
        rows.append([report.domain, index, kind, brand, site_summary(report.site), age, years, report.verdict])
        colors.append({7: VERDICTS[report.verdict][1]})
    print_table(headers, rows, colors, indent="  ")


# --- CSV --------------------------------------------------------------------------

def csv_cell(value) -> str:
    text = "" if value is None else str(value)
    # Excel/LibreOffice wykonałyby komórkę zaczynającą się od = + - @ jako formułę.
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def write_csv(path: str, reports: list[DomainReport], plan: Plan) -> str:
    """Zapisuje wyniki per kraj do `path` i podsumowanie domen do `<nazwa>_podsumowanie.csv`."""
    columns = [
        "domena", "kraj", "gl", "google", "w_indeksie", "szacunek_google", "wyniki_domeny_na_1_str",
        "pierwszy_wynik_site", "pierwszy_wynik_typ", "pozycja_na_nazwe", "pozycja_na_nazwe_strona",
        "pozycja_na_nazwe_url", "fraza", "fraza_pozycja", "fraza_strona", "fraza_url",
        "usuniete_wyniki", "powtorka", "blad", "tryb",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for report in reports:
            for r in report.results:
                brand, phrase = r.brand or Rank(""), r.phrase or Rank("")
                writer.writerow(csv_cell(v) for v in (
                    r.domain, r.country.name, r.country.code, r.country.domain,
                    index_status(r).rstrip("*"), r.total, len(r.hits),
                    r.first_url, FIRST_KIND_TEXT.get(r.first_kind, ""), brand.place, brand.page, brand.url,
                    phrase.query, phrase.place, phrase.page, phrase.url, " | ".join(r.notices),
                    {"found": "TAK dopiero w powtórce", "confirmed": "NIE potwierdzone"}.get(r.recheck, r.recheck),
                    r.error or brand.error or phrase.error, "szybki" if plan.mode == "quick" else "pełny",
                ))
    summary_path = str(Path(path).with_name(Path(path).stem + "_podsumowanie.csv"))
    columns = [
        "domena", "werdykt", "w_indeksie_krajow", "sprawdzonych_krajow", "pierwszy_wynik_site_rynek",
        "pozycja_na_nazwe_rynek", "status_http", "strona_koncowa", "https", "robots_txt", "noindex",
        "typ_strony", "tytul", "zarejestrowana", "wygasa", "rejestrator", "status_rejestru", "serwery_dns",
        "wolna", "archiwum_lata", "problemy_i_uwagi",
    ]
    with open(summary_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(columns)
        for report in reports:
            checked = [r for r in report.results if r.indexed is not None]
            m = market_result(report, plan)
            site = report.site if isinstance(report.site, SiteInfo) else None
            reg = report.reg if isinstance(report.reg, RegInfo) else None
            archive = report.archive if isinstance(report.archive, ArchiveInfo) else None
            page = site.page if site else None
            writer.writerow(csv_cell(v) for v in (
                report.domain, report.verdict, sum(1 for r in checked if r.indexed), len(checked),
                FIRST_KIND_TEXT.get(m.first_kind, "") if m else "", rank_text(m.brand) if m else "",
                site.status if site else "", site.final_url if site else "",
                yes_no(site.https_ok) if site else "", site.robots_note if site else "",
                site.noindex if site else "", page.kind if page else "", page.title if page else "",
                fmt_date(reg.registered) if reg and reg.registered else "",
                fmt_date(reg.expires) if reg and reg.expires else "", reg.registrar if reg else "",
                ", ".join(reg.statuses) if reg else "", ", ".join(reg.nameservers) if reg else "",
                yes_no(reg.available) if reg else "", year_ranges(archive.years) if archive else "",
                " | ".join(text for level, text in report.findings if level in (BAD, WARN)),
            ))
    return summary_path


# --- Uruchomienie ------------------------------------------------------------------

def status_line(text: str) -> None:
    if sys.stderr.isatty():
        print(f"\r\033[K{text}", end="", file=sys.stderr, flush=True)


class Progress:
    """Postęp sprawdzania; `listener` dostaje gotowy tekst (w terminalu: linia statusu, w bocie: edycja wiadomości)."""

    def __init__(self, serp_total: int, extra_total: int, listener=None):
        self.done = {"serp": 0, "extra": 0}
        self.total = {"serp": serp_total, "extra": extra_total}
        self.lock = threading.Lock()
        self.listener = listener or status_line

    def text(self) -> str:
        parts = []
        if self.total["serp"]:
            parts.append(f"Google {self.done['serp']}/{self.total['serp']}")
        if self.total["extra"]:
            parts.append(f"testy strony/rejestru/archiwum {self.done['extra']}/{self.total['extra']}")
        return "  sprawdzam… " + " · ".join(parts)

    def step(self, kind: str) -> None:
        with self.lock:
            self.done[kind] += 1
            self.listener(self.text())

    def note(self, text: str) -> None:
        with self.lock:
            self.listener(f"  {text}")

    def clear(self) -> None:
        with self.lock:
            self.listener("")


def run_serp(search, domains: list[str], plan: Plan, workers: int, progress: Progress):
    """Zwraca (wyniki domena × kraj, komunikat błędu krytycznego albo None)."""
    tasks = [(d, c) for d in domains for c in plan.countries]
    stop = threading.Event()
    done: dict[tuple[str, str], Result] = {}
    fatal = None

    def job(domain: str, country: Country):
        if stop.is_set():
            return None
        return check_country(search, domain, country, plan)

    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {pool.submit(job, d, c): (d, c) for d, c in tasks}
        for future in as_completed(futures):
            domain, country = futures[future]
            try:
                result = future.result()
            except FatalApiError as e:
                if fatal is None:
                    fatal = str(e)
                    stop.set()
                continue
            if result:
                done[(domain, country.code)] = result
            progress.step("serp")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    results = [done.get((d, c.code)) or Result(d, c, error="nie sprawdzono (przerwane)") for d, c in tasks]
    return results, fatal


EXTRA_CHECKS = {"site": check_site, "reg": check_registration, "archive": check_archive}


def safe_check(name: str, domain: str):
    """Dodatkowy test nigdy nie przerywa raportu — błąd trafia do wyniku."""
    try:
        return EXTRA_CHECKS[name](domain)
    except ApiError as e:
        return Failed(str(e))
    except Exception as e:  # noqa: BLE001 — pokazujemy typ błędu, żeby nic nie ginęło po cichu
        return Failed(f"nieoczekiwany błąd {type(e).__name__}: {clean(e, 150)}")


def run_extras(domains: list[str], plan: Plan, progress: Progress) -> dict[str, dict]:
    names = [n for n, enabled in (("site", plan.site), ("reg", plan.reg), ("archive", plan.archive)) if enabled]
    out: dict[str, dict] = {d: {} for d in domains}
    if not names:
        return out
    with ThreadPoolExecutor(max_workers=min(8, len(domains) * len(names))) as pool:
        futures = {pool.submit(safe_check, n, d): (d, n) for d in domains for n in names}
        for future in as_completed(futures):
            domain, name = futures[future]
            out[domain][name] = future.result()
            progress.step("extra")
    return out


class ConfigError(Exception):
    """Błąd konfiguracji (np. brak klucza API) — terminal kończy się komunikatem, bot pokazuje go w czacie."""


def read_env_file(path: Path) -> dict[str, str]:
    """KLUCZ=wartość z pliku .env (# to komentarz). Brak pliku = pusty słownik."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    env = {}
    for line in lines:
        key, sep, value = line.strip().removeprefix("export ").partition("=")
        if sep and key.strip() and not key.startswith("#"):
            env[key.strip()] = value.strip().strip("'\"")
    return env


def load_dotenv(path: Path) -> None:
    """Wczytuje .env do zmiennych środowiska; nie nadpisuje już ustawionych."""
    for key, value in read_env_file(path).items():
        os.environ.setdefault(key, value)


def api_keys(*names: str, env=None) -> list[str]:
    """Klucze z .env: NAZWA=k1,k2 oraz NAZWA_2=…, NAZWA_3=… — w tej kolejności, bez powtórzeń."""
    env = os.environ if env is None else env
    keys: list[str] = []
    for base in names:
        numbered = sorted((n for n in env if re.fullmatch(rf"{re.escape(base)}_?\d+", n)),
                          key=lambda n: int(re.search(r"\d+$", n).group()))
        for name in [base, *numbered]:
            for key in re.split(r"[\s,;]+", env.get(name, "")):
                if key and key not in keys:
                    keys.append(key)
    return keys


def provider_for(choice: str, env=None):
    """SerpApi albo Serper z kluczami z `env` (domyślnie zmienne środowiska); brak klucza -> ConfigError."""
    keys = {
        "serpapi": api_keys("SERPAPI_KEY", "SERPAPI_API_KEY", env=env),
        "serper": api_keys("SERPER_API_KEY", env=env),
    }
    if choice == "auto":
        choice = next((name for name, key in keys.items() if key), "serpapi")
    if not keys[choice]:
        raise ConfigError(f"Brak klucza API ({'SERPAPI_KEY' if choice == 'serpapi' else 'SERPER_API_KEY'}).")
    return SerpApi(keys[choice]) if choice == "serpapi" else Serper(keys[choice])


def make_provider(choice: str):
    try:
        return provider_for(choice)
    except ConfigError as e:
        sys.exit(f"{e} Wpisz go do pliku .env (wzór: .env.example) albo użyj --bez-api (tylko darmowe testy).")


def country_code(code: str) -> str:
    code = code.strip().lower()
    return COUNTRY_ALIASES.get(code, code)


def select_countries(args) -> list[Country]:
    if not args.kraje:
        return list(ALL_COUNTRIES if args.wszystkie else MAIN_COUNTRIES)
    codes = [country_code(c) for c in re.split(r"[\s,;]+", args.kraje) if c.strip()]
    if not codes:
        sys.exit("Podaj co najmniej jeden kod kraju, np. --kraje pl,de")
    unknown = [c for c in codes if c not in COUNTRY_BY_CODE]
    if unknown:
        sys.exit(f"Nieznane kody krajów: {', '.join(unknown)}. Lista: python3 check_index.py --lista-krajow")
    return [COUNTRY_BY_CODE[c] for c in dict.fromkeys(codes)]


def select_market(args, countries: list[Country]) -> Country:
    """Główny rynek: z --rynek albo pierwszy kraj z listy; dopisywany do listy, jeśli go tam nie ma."""
    code = country_code(args.rynek) if args.rynek else countries[0].code
    if code not in COUNTRY_BY_CODE:
        sys.exit(f"Nieznany kod rynku: {code}. Lista: python3 check_index.py --lista-krajow")
    market = COUNTRY_BY_CODE[code]
    if market not in countries:
        countries.insert(0, market)
    return market


def collect_domains(args, prompt: bool = True) -> list[str]:
    tld = getattr(args, "koncowka", DEFAULT_TLD)
    raw = list(args.domeny)
    if args.plik:
        try:
            text = sys.stdin.read() if args.plik == "-" else Path(args.plik).read_text(encoding="utf-8")
        except OSError as e:
            sys.exit(f"Nie mogę odczytać pliku {args.plik}: {e.strerror}")
        raw += [line.split("#", 1)[0] for line in text.splitlines()]
    if not raw and prompt and sys.stdin.isatty():
        try:
            raw = [input("Podaj domenę (np. test.pl): ")]
        except EOFError:
            raw = []
    domains = []
    for item in re.split(r"[\s,;]+", " ".join(raw)):
        if not item:
            continue
        try:
            domain = normalize_domain(item, tld)
        except ValueError as e:
            print(paint(f"Pomijam: {e}", "yellow"), file=sys.stderr)
            continue
        if domain not in domains:
            domains.append(domain)
    return domains


def print_countries() -> None:
    for title, countries in (("Domyślne", MAIN_COUNTRIES), ("Dodatkowe (--wszystkie)", EXTRA_COUNTRIES)):
        print(paint(f"{title}: {len(countries)}", "bold"))
        for c in countries:
            print(f"  {c.code}  {c.domain:<15} {c.name}")


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_index.py",
        description="Sprawdza domenę: indeks Google w wielu krajach, pozycję na własną nazwę "
                    "i gotowość strony do publikacji.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            przykłady:
              python3 check_index.py test.pl                     pełny raport (30 krajów)
              python3 check_index.py savowin --kraje pl          sama nazwa = savowin.com; pozycja dla „savowin”
              python3 check_index.py savowin --szybko            tylko „savowin” na 1. stronie: 1 zapytanie na kraj
              python3 check_index.py test.pl --kraje pl,de,uk    tylko wybrane kraje
              python3 check_index.py -f domeny.txt --bez-api     darmowa selekcja wielu domen
              python3 check_index.py test.pl --pozycja           pozycja na nazwę we wszystkich krajach
              python3 check_index.py test.pl --fraza "tanie buty" --kraje pl
              python3 check_index.py test.pl --csv wyniki.csv    wyniki do Excela
              python3 check_index.py --historia test.pl          poprzednie sprawdzenia
              python3 check_index.py test.pl --potwierdz         powtórz każde „NIE” (nowe, chwiejne domeny)
              python3 check_index.py --konto                     ile zostało zapytań na każdym kluczu
        """),
    )
    parser.add_argument("domeny", nargs="*",
                        help="domeny do sprawdzenia, np. test.pl albo sama nazwa: savowin (= savowin.com)")
    parser.add_argument("-f", "--plik", metavar="PLIK",
                        help="plik z domenami, jedna na linię (# to komentarz, - czyta ze stdin)")
    parser.add_argument("--koncowka", metavar="TLD", default=DEFAULT_TLD,
                        help=f"końcówka dopisywana do samej nazwy (domyślnie {DEFAULT_TLD}: savowin -> savowin.{DEFAULT_TLD})")

    google = parser.add_argument_group("Google (zapytania API)")
    google.add_argument("-p", "--provider", choices=["auto", "serpapi", "serper"], default="auto",
                        help="API do użycia (auto: SerpApi, jeśli jest klucz, inaczej Serper)")
    scope = google.add_mutually_exclusive_group()
    scope.add_argument("-k", "--kraje", metavar="KODY", help="kody krajów po przecinku, np. pl,de,uk")
    scope.add_argument("-a", "--wszystkie", action="store_true",
                       help=f"wszystkie {len(ALL_COUNTRIES)} krajów zamiast {len(MAIN_COUNTRIES)} głównych")
    google.add_argument("-r", "--rynek", metavar="KOD",
                        help="główny rynek: tu sprawdzana jest pozycja na własną nazwę "
                             "(domyślnie pierwszy kraj z listy, czyli pl)")
    brand = google.add_mutually_exclusive_group()
    brand.add_argument("--pozycja", action="store_true",
                       help="pozycja na własną nazwę (sama nazwa bez końcówki, np. „savowin”) we WSZYSTKICH "
                            "krajach (domyślnie tylko na głównym rynku)")
    brand.add_argument("--bez-pozycji", action="store_true",
                       help="nie sprawdzaj pozycji na własną nazwę (oszczędza zapytania)")
    google.add_argument("--fraza", metavar="TEKST", help="sprawdź też pozycję dla tej frazy (w każdym kraju)")
    google.add_argument("--szybko", action="store_true",
                        help="tryb szybki: w każdym kraju tylko jedno zapytanie o samą nazwę (np. „savowin”) i tylko "
                             "1. strona wyników — bez site:")
    google.add_argument("--sprawdz-indeks", action="store_true",
                        help="w trybie szybkim: gdy domeny nie ma na 1. stronie, sprawdź site: (1 zapytanie więcej)")
    google.add_argument("--potwierdz", action="store_true",
                        help="powtórz sprawdzenie w KAŻDYM kraju z wynikiem NIE (domyślnie tylko na głównym rynku); "
                             "przydatne przy nowych domenach, kosztuje do 2 zapytań na kraj")
    google.add_argument("--max-stron", type=int, default=3, choices=range(1, 11), metavar="N",
                        help="ile stron wyników przeszukać przy pozycjach (1-10, domyślnie 3)")
    google.add_argument("--bez-api", action="store_true",
                        help="bez zapytań do Google — tylko darmowe testy (strona, rejestr, archiwum)")

    extra = parser.add_argument_group("Darmowe testy dodatkowe")
    extra.add_argument("--bez-strony", action="store_true", help="pomiń test strony WWW (DNS, HTTPS, robots.txt…)")
    extra.add_argument("--bez-whois", action="store_true", help="pomiń dane rejestracji (wiek, wygaśnięcie)")
    extra.add_argument("--bez-archiwum", action="store_true", help="pomiń archiwum Wayback Machine")

    output = parser.add_argument_group("Wyniki i historia")
    output.add_argument("--csv", metavar="PLIK",
                        help="zapisz wyniki do CSV (średnik, UTF-8) + PLIK_podsumowanie.csv")
    output.add_argument("--bez-zapisu", action="store_true", help="nie zapisuj wyniku w historii")
    output.add_argument("--historia", action="store_true",
                        help="pokaż historię sprawdzeń podanych domen (albo wszystkich) i zakończ")
    output.add_argument("--konto", action="store_true", help="pokaż stan konta API i zakończ")

    other = parser.add_argument_group("Inne")
    other.add_argument("-w", "--watki", type=int, default=4, choices=range(1, 17), metavar="N",
                       help="ile zapytań do Google naraz (1-16, domyślnie 4)")
    other.add_argument("-y", "--tak", action="store_true", help="nie pytaj o potwierdzenie przy wielu zapytaniach")
    other.add_argument("--lista-krajow", action="store_true", help="pokaż dostępne kraje i zakończ")
    return parser.parse_args(argv)


def brand_codes_for(mode: str, countries: list[Country], market: Country) -> set[str]:
    """Gdzie sprawdzać pozycję na własną nazwę: "market" (główny rynek), "all" (wszędzie), "off"."""
    if mode == "all":
        return {c.code for c in countries}
    return set() if mode == "off" else {market.code}


def extras_of(plan: Plan) -> list[str]:
    return [name for name, on in (("strona", plan.site), ("rejestr", plan.reg), ("archiwum", plan.archive)) if on]


def estimate_queries(domain_count: int, plan: Plan, provider) -> tuple[int, int]:
    """(najmniej, najwięcej) zapytań API, które zużyje sprawdzenie."""
    if not plan.use_api:
        return 0, 0
    if plan.mode == "quick":
        base = domain_count * len(plan.countries)  # 1 zapytanie o samą nazwę na kraj
        phrase = domain_count * len(plan.countries) * plan.max_pages if plan.phrase else 0
        return base, base * (2 if plan.index_fallback else 1) + phrase
    site_queries = domain_count * len(plan.countries)
    rank_queries = domain_count * plan.max_pages * (len(plan.brand_codes) + (len(plan.countries) if plan.phrase else 0))
    # Powtórka „NIE” to do len(variants) zapytań na kraj; pozycja po powtórce jest już liczona w rank_queries.
    recheck_queries = domain_count * (len(plan.countries) if plan.confirm_all else 1) * len(provider.recheck_variants)
    return site_queries, site_queries + rank_queries + recheck_queries


def run_checks(domains: list[str], plan: Plan, provider, workers: int = 4, progress: Progress | None = None,
               save: bool = True) -> tuple[list[DomainReport], str | None]:
    """Sprawdza domeny i zwraca (raporty, komunikat błędu krytycznego albo None). Nic nie wypisuje."""
    site_queries = len(domains) * len(plan.countries) if plan.use_api else 0
    progress = progress or Progress(site_queries, len(domains) * len(extras_of(plan)))
    # To samo zapytanie (np. ta sama --fraza dla kilku domen) wysyłamy tylko raz.
    search = functools.lru_cache(maxsize=None)(provider.search) if plan.use_api else None
    background = ThreadPoolExecutor(max_workers=1)
    try:
        extras_future = background.submit(run_extras, domains, plan, progress)
        results, fatal = run_serp(search, domains, plan, workers, progress) if plan.use_api else ([], None)
        extra_results = extras_future.result()
        if plan.use_api and not fatal:  # po testach dodatkowych, bo wiek domeny (RDAP) decyduje o powtórkach
            fatal = run_rechecks(search, results, plan, extra_results, workers, provider.recheck_variants, progress)
    finally:
        background.shutdown(wait=False, cancel_futures=True)
        progress.clear()
    if fatal and all(r.indexed is None for r in results):
        results = []  # nic nie zdążyło się sprawdzić — pusta tabela nic nie wnosi

    reports = []
    for domain in domains:
        found = extra_results.get(domain, {})
        report = DomainReport(domain, [r for r in results if r.domain == domain],
                              found.get("site"), found.get("reg"), found.get("archive"))
        report.findings = assess(report, plan)
        report.verdict = verdict_of(report.findings, isinstance(report.reg, RegInfo) and report.reg.available)
        if plan.use_api and any(r.indexed is not None for r in report.results):
            record = history_record(report, provider.name, plan)
            previous = read_history(history_path(domain))
            if previous:
                report.previous = str(previous[-1].get("czas", ""))
                report.changes = history_changes(previous[-1], record)
            if save and not fatal:  # przerwane sprawdzenie zafałszowałoby historię
                try:
                    save_history(report, record)
                except OSError as e:
                    print(paint(f"Nie udało się zapisać historii: {e.strerror}", "yellow"), file=sys.stderr)
        reports.append(report)
    return reports, fatal


def print_usage(provider) -> None:
    """Stopka: ile zapytań wysłano i ile zostało na każdym kluczu."""
    pool = provider.pool
    line = f"{provider.name}: wysłano {provider.used} {plural(provider.used, 'zapytanie', 'zapytania', 'zapytań')}"
    if len(pool.keys) > 1:
        line += " (" + ", ".join(f"{pool.label(key)}: {pool.used[key]}" for key in pool.keys) + ")"
    states = []
    for key in pool.keys:
        if key in pool.dead:
            states.append(f"pominięty — {pool.dead[key]}")
            continue
        try:
            states.append(provider.account_text(key))
        except ApiError:
            states.append("stan konta niedostępny")
    if len(pool.keys) == 1:
        print(paint(f"\n{line} · {states[0]}", "dim"))
        return
    print(paint(f"\n{line}", "dim"))
    for key, state in zip(pool.keys, states):
        print(paint(f"  {pool.label(key)}: {state}", "dim"))


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.lista_krajow:
        print_countries()
        return 0
    for env_path in dict.fromkeys([SCRIPT_DIR / ".env", Path.cwd() / ".env"]):
        load_dotenv(env_path)
    if args.historia:
        return show_history(collect_domains(args, prompt=False))

    use_api = not args.bez_api
    provider = make_provider(args.provider) if use_api or args.konto else None
    if args.konto:
        for key in provider.pool.keys:
            try:
                text = provider.account_text(key)
            except ApiError as e:
                text = str(e)
            print(f"{provider.name} {provider.pool.label(key)}: {text}")
        return 0

    countries = select_countries(args)
    market = select_market(args, countries)
    domains = collect_domains(args)
    if not domains:
        print("Nie podano żadnej poprawnej domeny.", file=sys.stderr)
        return 2
    mode = "all" if args.pozycja else "off" if args.bez_pozycji else "market"
    plan = Plan(countries, market, brand_codes_for(mode, countries, market), args.fraza if use_api else None,
                args.max_stron, use_api, not args.bez_strony, not args.bez_whois, not args.bez_archiwum,
                args.potwierdz, "quick" if args.szybko else "full", args.sprawdz_indeks)
    if not (plan.use_api or plan.site or plan.reg or plan.archive):
        print("Wszystkie testy są wyłączone — nie ma czego sprawdzać.", file=sys.stderr)
        return 2

    if use_api:
        provider.prepare()  # darmowe sprawdzenie kluczy — wyczerpane odpadają od razu
    site_queries, worst = estimate_queries(len(domains), plan, provider)
    if worst > CONFIRM_ABOVE and not args.tak:
        question = f"To zużyje do {worst} zapytań z limitu {provider.name}."
        if not sys.stdin.isatty():
            sys.exit(f"{question} Dodaj --tak, żeby potwierdzić.")
        if input(f"{question} Kontynuować? [t/N] ").strip().lower() not in ("t", "tak", "y", "yes"):
            print("Anulowano.")
            return 0

    extras = extras_of(plan)
    header = [f"domeny: {len(domains)}"]
    if use_api:
        available = provider.pool.available()
        keys = len(provider.pool.keys)
        header.append(f"Google: {provider.name}, {len(countries)} {plural(len(countries), 'kraj', 'kraje', 'krajów')}, "
                      + ("tryb szybki (1. strona), " if plan.mode == "quick" else "")
                      + f"rynek {market.domain}, zapytań {site_queries}–{worst}"
                      + (f", kluczy {keys}" if keys > 1 else "")
                      + (f", dostępnych zapytań {available}" if available is not None else ""))
    if extras:
        header.append("darmowe testy: " + ", ".join(extras))
    print(paint(" · ".join(header), "dim"))

    reports, fatal = run_checks(domains, plan, provider, args.watki, save=not args.bez_zapisu)
    if fatal and not extras and not any(report.results for report in reports):
        sys.stdout.flush()
        print(paint(f"\nPrzerwano — {fatal}\nSprawdź klucze w .env i limity na kontach {provider.name}.", "red"),
              file=sys.stderr)
        return 1
    for report in reports:
        print_report(report, plan, provider.name if provider else "")
    if len(reports) > 1:
        print_comparison(reports, plan)
    if args.csv:
        try:
            summary = write_csv(args.csv, reports, plan)
            print(f"\nZapisano: {args.csv} i {summary}")
        except OSError as e:
            print(paint(f"\nNie udało się zapisać {args.csv}: {e.strerror}", "red"), file=sys.stderr)
            return 1
    if provider and use_api:
        print_usage(provider)
    if fatal:
        sys.stdout.flush()
        print(paint(f"\nPrzerwano — {fatal}\nSprawdź klucze w .env i limity na kontach {provider.name}.", "red"),
              file=sys.stderr)
        return 1
    return 1 if any(r.indexed is None for report in reports for r in report.results) else 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nPrzerwano.", file=sys.stderr)
        sys.stdout.flush()
        os._exit(130)
