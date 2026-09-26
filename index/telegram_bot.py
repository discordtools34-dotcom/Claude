#!/usr/bin/env python3
"""Bot Telegram do sprawdzania domen — silnikiem jest check_index.py.

Jedyna komenda to /start. Domenę wysyłasz zwykłą wiadomością (albo samą nazwę: „savowin” = savowin.com,
albo kilka domen naraz, każdą w nowej linii). Ustawienia, statystyki, historię, stan kluczy API
i szczegóły wyników obsługujesz przyciskami.

Token bota (od @BotFather) wpisz do .env jako TELEGRAM_BOT_TOKEN — klucze SerpApi/Serper są w tym samym pliku.
Bot odpowiada tylko właścicielowi: pierwszej osobie, która wyśle /start (i osobom z TELEGRAM_ALLOWED_USERS).
Nie wymaga żadnych bibliotek — wystarczy Python 3.9+. Uruchomienie: python3 telegram_bot.py
"""

from __future__ import annotations

import html
import http.client
import json
import os
import queue
import re
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import check_index as ci

ENV_PATH = ci.SCRIPT_DIR / ".env"
STATE_PATH = ci.SCRIPT_DIR / "bot_stan.json"
TELEGRAM_API = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,}:[\w-]{30,}$")
MAX_TEXT = 4096       # limit długości wiadomości Telegrama
MAX_DOMAINS = 50      # najwięcej domen w jednej wiadomości
RESULTS_KEPT = 30     # tyle ostatnich wyników bot pamięta — przyciski starszych wygasają
PAGE_SIZE = 24        # przyciski krajów na stronę (8 wierszy po 3)

DEFAULTS = {
    "mode": "quick",       # quick = sama nazwa, tylko 1. strona (1 zapytanie na kraj); full = site: + pozycja
    "index_fallback": False,  # tryb szybki: gdy brak na 1. stronie, sprawdź site:
    "countries": "main",   # pl / top / main / all / custom
    "custom": [],          # kody krajów przy "custom"
    "market": "pl",
    "brand": "market",     # market / all / off — gdzie sprawdzać pozycję na samą nazwę
    "max_pages": 3,
    "confirm_all": False,  # powtórka „NIE” we wszystkich krajach
    "site": True,
    "reg": True,
    "archive": True,
    "use_api": True,
    "provider": "auto",    # auto / serpapi / serper
    "phrase": "",
    "tld": "com",          # końcówka dla samej nazwy
}
PRESET_NAMES = {"pl": "tylko Polska", "top": "PL, DE, UK, US", "main": "30 głównych", "all": "wszystkie 90"}
PRESET_CODES = {"pl": ["pl"], "top": ["pl", "de", "uk", "us"]}
BRAND_NAMES = {"market": "na głównym rynku", "all": "we wszystkich krajach", "off": "wyłączona"}
BRAND_SHORT = {"market": "rynek", "all": "wszędzie", "off": "wył."}
PROVIDER_NAMES = {"auto": "automatycznie", "serpapi": "SerpApi", "serper": "Serper.dev"}
MODE_NAMES = {"quick": "szybki", "full": "pełny"}
PAGE_OPTIONS = [1, 3, 5, 10]
TLDS = ["com", "pl", "net", "org", "eu", "io"]
VERDICT_ICON = {"OK": "✅", "UWAGA": "⚠️", "PROBLEM": "❌", "WOLNA": "🆓", "BRAK DANYCH": "❔"}
LEVEL_ICON = {ci.BAD: "❌", ci.WARN: "⚠️", ci.OK: "✅", ci.INFO: "ℹ️"}
KIND_SHORT = {"home": "gł.", "subpage": "podstr.", "subdomain": "subd.", "other": "inna"}


def log(text: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)


def esc(text) -> str:
    return html.escape(str(text), quote=False)


def fit(text: str, limit: int = MAX_TEXT) -> str:
    """Przycina wiadomość do limitu Telegrama całymi liniami, domykając otwarty blok <pre>."""
    if len(text) <= limit:
        return text
    kept, size, open_pre = [], 0, False
    for line in text.split("\n"):
        if size + len(line) + 1 > limit - 20:
            break
        kept.append(line)
        size += len(line) + 1
        if "<pre>" in line:
            open_pre = True
        if "</pre>" in line:
            open_pre = False
    return "\n".join(kept) + ("\n…</pre>" if open_pre else "\n…")


def flag(code: str) -> str:
    code = "gb" if code == "uk" else code
    return "".join(chr(0x1F1E6 + ord(ch) - ord("a")) for ch in code) if re.fullmatch(r"[a-z]{2}", code) else ""


def ago(stamp: str) -> str:
    try:
        then = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return "?"
    seconds = (datetime.now(then.tzinfo) - then).total_seconds()
    if seconds < 90:
        return "przed chwilą"
    if seconds < 3600:
        return f"{int(seconds // 60)} min temu"
    if seconds < 86400:
        return f"{int(seconds // 3600)} godz. temu"
    days = int(seconds // 86400)
    return "wczoraj" if days == 1 else f"{days} dni temu"


def kb(*rows) -> dict:
    """Klawiatura z wierszy przycisków: (tekst, dane) albo (tekst, 'url:https://…'); puste wiersze są pomijane."""
    keyboard = []
    for row in rows:
        buttons = [{"text": text, "url": data[4:]} if data.startswith("url:") else {"text": text, "callback_data": data}
                   for text, data in row if text]
        if buttons:
            keyboard.append(buttons)
    return {"inline_keyboard": keyboard}


# --- Telegram Bot API ---------------------------------------------------------------

class TelegramError(Exception):
    def __init__(self, method: str, code, description: str):
        super().__init__(f"{method}: {code} {description}")
        self.code = code
        self.description = description or ""


class Telegram:
    """Minimalny klient Telegram Bot API (JSON przez HTTPS). Token jest tylko w adresie — nigdy w logach."""

    def __init__(self, token: str, base: str = TELEGRAM_API):
        self.url = f"{base}/bot{token}"

    def call(self, method: str, wait: float = 30, **params):
        body = json.dumps({k: v for k, v in params.items() if v is not None}).encode()
        request = urllib.request.Request(f"{self.url}/{method}", data=body,
                                         headers={"Content-Type": "application/json"})
        return self._send(method, request, wait)

    def _send(self, method: str, request: urllib.request.Request, wait: float):
        for attempt in range(1, 6):
            try:
                with urllib.request.urlopen(request, timeout=wait) as response:
                    payload = json.load(response)
            except urllib.error.HTTPError as e:
                try:
                    payload = json.load(e)
                except ValueError:
                    payload = {"ok": False, "error_code": e.code, "description": str(e.reason)}
            except (OSError, http.client.HTTPException, ValueError) as e:
                if attempt == 5:
                    raise TelegramError(method, 0, ci.describe_net_error(e)) from None
                ci.time.sleep(2 * attempt)
                continue
            if payload.get("ok"):
                return payload.get("result")
            retry = (payload.get("parameters") or {}).get("retry_after")
            if payload.get("error_code") == 429 and retry:
                ci.time.sleep(min(float(retry), 60))  # Telegram każe poczekać — czekamy i ponawiamy
                continue
            raise TelegramError(method, payload.get("error_code"), payload.get("description", ""))
        raise TelegramError(method, 429, "za dużo zapytań do Telegrama")

    def send(self, chat_id: int, text: str, keyboard: dict | None = None) -> int:
        result = self.call("sendMessage", chat_id=chat_id, text=fit(text), parse_mode="HTML",
                           link_preview_options={"is_disabled": True}, reply_markup=keyboard)
        return result["message_id"]

    def edit(self, chat_id: int, message_id: int, text: str, keyboard: dict | None = None) -> None:
        try:
            self.call("editMessageText", chat_id=chat_id, message_id=message_id, text=fit(text), parse_mode="HTML",
                      link_preview_options={"is_disabled": True}, reply_markup=keyboard)
        except TelegramError as e:
            if "message is not modified" not in e.description:  # to samo kliknięte drugi raz — nic nie robimy
                raise

    def answer(self, callback_id: str, text: str | None = None, alert: bool = False) -> None:
        try:
            self.call("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=alert or None)
        except TelegramError:
            pass  # stare albo już obsłużone kliknięcie — bez znaczenia

    def send_document(self, chat_id: int, filename: str, data: bytes, caption: str | None = None) -> None:
        boundary = uuid.uuid4().hex
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()]
        if caption:
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{filename}"\r\n'
                     f"Content-Type: text/csv\r\n\r\n".encode() + data + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        request = urllib.request.Request(f"{self.url}/sendDocument", data=b"".join(parts),
                                         headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self._send("sendDocument", request, 60)


# --- Stan bota: właściciel, ustawienia, statystyki ------------------------------------

class Store:
    """Trwały stan w pliku JSON (bot_stan.json obok skryptu)."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key, empty in (("owners", []), ("settings", {}), ("stats", {}), ("domains", {})):
            if not isinstance(data.get(key), type(empty)):
                data[key] = empty
        stats = data["stats"]
        for key, empty in (("checks", 0), ("queries", 0), ("verdicts", {}), ("days", {})):
            if not isinstance(stats.get(key), type(empty)):
                stats[key] = empty
        self.data = data

    def save(self) -> None:
        with self.lock:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)

    @staticmethod
    def extra_users(env: dict) -> set[int]:
        return {int(user) for user in re.findall(r"\d+", env.get("TELEGRAM_ALLOWED_USERS", ""))}

    def allowed(self, user_id, env: dict) -> bool:
        return user_id in self.extra_users(env) or user_id in self.data["owners"]

    def claim(self, user_id: int, env: dict) -> bool:
        """Pierwszy /start zostaje właścicielem — tylko gdy nikt jeszcze nie ma dostępu."""
        with self.lock:
            if self.data["owners"] or self.extra_users(env):
                return False
            self.data["owners"] = [user_id]
            self.save()
            return True

    def settings(self, user_id: int) -> dict:
        with self.lock:
            stored = self.data["settings"].get(str(user_id), {})
            return {**DEFAULTS, **{k: v for k, v in stored.items() if k in DEFAULTS}}

    def update(self, user_id: int, **changes) -> dict:
        with self.lock:
            self.data["settings"].setdefault(str(user_id), {}).update(changes)
            self.save()
            return self.settings(user_id)

    def record(self, reports: list, queries: int, plan=None) -> None:
        """Zapisuje sprawdzenie w statystykach; z `plan` także indeks i miejsca na samą nazwę (bez planu — np. po
        przerwanym sprawdzeniu — zostają poprzednie)."""
        with self.lock:
            stats, now = self.data["stats"], datetime.now().astimezone()
            day = stats["days"].setdefault(now.strftime("%Y-%m-%d"), {"checks": 0, "queries": 0})
            for report in reports:
                stats["checks"] += 1
                day["checks"] += 1
                stats["verdicts"][report.verdict] = stats["verdicts"].get(report.verdict, 0) + 1
                entry = self.data["domains"].setdefault(report.domain, {"checks": 0})
                entry.update(checks=entry.get("checks", 0) + 1, last=now.isoformat(timespec="seconds"),
                             verdict=report.verdict)
                google = google_summary(report, plan) if plan else None
                if google:
                    entry["google"] = google
            stats["queries"] += queries
            day["queries"] += queries
            for old in sorted(stats["days"])[:-90]:  # statystyki dzienne z ostatnich 90 dni
                del stats["days"][old]
            if len(self.data["domains"]) > 500:
                for domain, _ in self.recent(len(self.data["domains"]))[500:]:
                    del self.data["domains"][domain]
            self.save()

    def recent(self, count: int = 10) -> list[tuple[str, dict]]:
        with self.lock:
            return sorted(self.data["domains"].items(), key=lambda kv: kv[1].get("last", ""), reverse=True)[:count]


def google_summary(report, plan) -> dict | None:
    """Do statystyk: w ilu krajach zaindeksowana i na których miejscach (None = sprawdzenie bez Google)."""
    stats = ci.NameStats.of_report(report, plan)
    if not stats.checked:
        return None
    return {"mode": plan.mode, "checked": stats.checked, "indexed": stats.indexed, "first": stats.first,
            "ranked": len(stats.places), "avg": round(stats.average, 1) if stats.average else None,
            "market": stats.market, "place": stats.places.get(stats.market)}


def google_text(g: dict) -> str:
    """„zaindeksowana 28/30 · 1. miejsce w 25/30 · śr. 1.2 · 🇵🇱 1.” z podsumowania zapisanego w statystykach."""
    parts = [f"{'zaindeksowana' if g.get('mode') == 'quick' else 'indeks'} {g.get('indexed', 0)}/{g.get('checked', 0)}"]
    if (g.get("ranked") or 0) > 1:
        parts.append(f"1. miejsce w {g.get('first', 0)}/{g['ranked']}")
        if g.get("avg"):
            parts.append(f"śr. {g['avg']}")
    if isinstance(g.get("place"), int):
        market = str(g.get("market") or "")
        parts.append(f"{flag(market) or market.upper()} {ci.place_text(g['place'])}")
    return " · ".join(parts)


# --- Ustawienia -> plan sprawdzania ---------------------------------------------------

def countries_for(s: dict) -> list:
    if s["countries"] == "all":
        return list(ci.ALL_COUNTRIES)
    if s["countries"] == "main":
        return list(ci.MAIN_COUNTRIES)
    codes = PRESET_CODES.get(s["countries"]) or [c for c in s["custom"] if c in ci.COUNTRY_BY_CODE] or ["pl"]
    return [ci.COUNTRY_BY_CODE[code] for code in dict.fromkeys(codes)]


def countries_label(s: dict) -> str:
    if s["countries"] in PRESET_NAMES:
        return PRESET_NAMES[s["countries"]]
    codes = [c for c in s["custom"] if c in ci.COUNTRY_BY_CODE]
    return f"wybrane ({len(codes)})" if codes else "wybrane (brak → Polska)"


def market_of(s: dict):
    return ci.COUNTRY_BY_CODE.get(s["market"], ci.COUNTRY_BY_CODE["pl"])


def plan_for(s: dict):
    countries, market = countries_for(s), market_of(s)
    if market not in countries:
        countries.insert(0, market)
    return ci.Plan(countries, market, ci.brand_codes_for(s["brand"], countries, market),
                   (s["phrase"] or None) if s["use_api"] else None, s["max_pages"], s["use_api"],
                   s["site"], s["reg"], s["archive"], s["confirm_all"], s["mode"], s["index_fallback"])


def parse_domains(text: str, tld: str) -> tuple[list[str], list[str]]:
    domains, bad = [], []
    for item in re.split(r"[\s,;]+", text):
        if not item:
            continue
        try:
            domain = ci.normalize_domain(item, tld)
        except ValueError:
            bad.append(item)
            continue
        if domain not in domains:
            domains.append(domain)
    return domains[:MAX_DOMAINS], bad


def settings_summary(s: dict) -> str:
    market = market_of(s)
    if s["mode"] == "quick":
        mode = ("⚡ Tryb <b>szybki</b>: sama nazwa w Google (<code>savowin</code>, bez końcówki), tylko 1. strona — "
                "jest na niej = zaindeksowana + miejsce; 1 zapytanie na kraj"
                + (" (+ site:, gdy brak na 1. stronie)" if s["index_fallback"] else ""))
    else:
        mode = (f"⚡ Tryb <b>pełny</b>: site: w każdym kraju + pozycja na samą nazwę {BRAND_NAMES[s['brand']]} "
                f"(do str. {s['max_pages']})")
    lines = [mode, f"🌍 Kraje: <b>{esc(countries_label(s))}</b> · rynek: {flag(market.code)} {esc(market.domain)}",
             f"🔚 Sama nazwa dostaje końcówkę: <b>.{esc(s['tld'])}</b>"]
    if s["phrase"]:
        lines.append(f"🔤 Fraza: „{esc(s['phrase'])}”")
    tests = [name for name, on in (("strona", s["site"]), ("rejestr", s["reg"]), ("archiwum", s["archive"])) if on]
    lines.append(f"🧪 Darmowe testy: {', '.join(tests) or 'wyłączone'}")
    if s["use_api"]:
        lines.append(f"🔌 Google: {PROVIDER_NAMES[s['provider']]}" + ("" if s["mode"] == "quick" else
                     f" · powtórki NIE: {'wszystkie kraje' if s['confirm_all'] else 'główny rynek'}"))
    else:
        lines.append("🔌 Google: <b>wyłączone</b> (tylko darmowe testy, 0 zapytań API)")
    return "\n".join(lines)


# --- Widoki (tekst + przyciski) -------------------------------------------------------

def menu_view(s: dict):
    text = (f"🔎 <b>Sprawdzanie domen</b>\n"
            f"Wyślij domenę albo samą nazwę (<code>savowin</code> = savowin.{esc(s['tld'])}). "
            f"Kilka domen naraz — każdą w nowej linii.\n\n{settings_summary(s)}")
    return text, kb([("⚙️ Ustawienia", "s"), ("📊 Statystyki", "st")],
                    [("🕘 Historia", "h"), ("💳 Konto API", "k")],
                    [("❓ Pomoc", "?")])


def settings_view(s: dict):
    on = {True: "✅", False: "⬜"}
    phrase = ci.clean(s["phrase"], 18) if s["phrase"] else "brak"
    full = s["mode"] == "full"
    return (f"⚙️ <b>Ustawienia</b>\n{settings_summary(s)}\n\nKliknij, żeby zmienić:",
            kb([(f"⚡ Tryb: {MODE_NAMES[s['mode']]}", "md"),
                ("" if full else f"{on[s['index_fallback']]} site: gdy brak na 1. str.", "tg:index_fallback")],
               [(f"🌍 Kraje: {countries_label(s)}", "c"), (f"🎯 Rynek: {market_of(s).domain}", "mk")],
               [(f"🏷 Pozycja: {BRAND_SHORT[s['brand']]}" if full else "", "br"),
                (f"📄 Do strony: {s['max_pages']}" if full or s["phrase"] else "", "pg")],
               [(f"🔁 Powtórki NIE: {'wszędzie' if s['confirm_all'] else 'rynek'}" if full else "", "cf"),
                (f"🔚 Końcówka: .{s['tld']}", "tl")],
               [(f"✏️ Fraza: {phrase}", "ph"), ("✖️ Usuń frazę" if s["phrase"] else "", "phx")],
               [(f"{on[s['site']]} Strona", "tg:site"), (f"{on[s['reg']]} Rejestr", "tg:reg"),
                (f"{on[s['archive']]} Archiwum", "tg:archive")],
               [(f"{on[s['use_api']]} Google (API)", "tg:use_api"), (f"🔌 {PROVIDER_NAMES[s['provider']]}", "pv")],
               [("🏠 Menu", "m")]))


def countries_view(s: dict):
    return (f"🌍 <b>Kraje</b>\nKażdy kraj to 1 zapytanie API na domenę. Teraz: <b>{esc(countries_label(s))}</b>",
            kb([("🇵🇱 Tylko Polska", "c:pl"), ("PL DE UK US", "c:top")],
               [("30 głównych", "c:main"), ("Wszystkie 90", "c:all")],
               [("✍️ Wybierz ręcznie", "cp:0")],
               [("⬅️ Ustawienia", "s")]))


def paged(items: list, page: int) -> tuple[list, int, int]:
    pages = max(1, -(-len(items) // PAGE_SIZE))
    page = min(max(page, 0), pages - 1)
    return items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE], page, pages


def nav_row(prefix: str, page: int, pages: int) -> list:
    return [("◀️" if page > 0 else "", f"{prefix}:{page - 1}"), (f"{page + 1}/{pages}", f"{prefix}:{page}"),
            ("▶️" if page < pages - 1 else "", f"{prefix}:{page + 1}")]


def picker_view(s: dict, page: int):
    selected = [c.code for c in countries_for(s)] if s["countries"] != "custom" else \
        [c for c in s["custom"] if c in ci.COUNTRY_BY_CODE]
    chunk, page, pages = paged([c.code for c in ci.ALL_COUNTRIES], page)
    rows = [[(f"{'✅' if code in selected else '⬜'} {flag(code)} {code.upper()}", f"ct:{code}:{page}")
             for code in chunk[i:i + 3]] for i in range(0, len(chunk), 3)]
    chosen = ", ".join(code.upper() for code in selected[:30]) + (" …" if len(selected) > 30 else "")
    return (f"✍️ <b>Wybierz kraje</b> — zaznaczone: {len(selected)}\n{esc(chosen) or 'brak (będzie Polska)'}",
            kb(*rows, nav_row("cp", page, pages), [("🗑 Wyczyść", f"cx:{page}"), ("✅ Gotowe", "s")]))


def market_view(s: dict, page: int):
    chunk, page, pages = paged([c.code for c in countries_for(s)], page)
    rows = [[(f"{'🎯' if code == s['market'] else ''}{flag(code)} {code.upper()}", f"mk:{code}")
             for code in chunk[i:i + 3]] for i in range(0, len(chunk), 3)]
    return ("🎯 <b>Główny rynek</b>\nTu sprawdzam pozycję na samą nazwę (np. „savowin”) i tu powtarzam niepewne „NIE”.",
            kb(*rows, nav_row("mp", page, pages) if pages > 1 else [], [("⬅️ Ustawienia", "s")]))


def help_view():
    text = ("❓ <b>Jak to działa</b>\n"
            "• Wyślij domenę (<code>savowin.com</code>) albo samą nazwę (<code>savowin</code> — dopiszę końcówkę "
            "z ustawień). Kilka domen: każda w nowej linii.\n"
            "• ⚡ <b>Tryb szybki</b> (domyślny) — w każdym kraju (w jego języku) jedno zapytanie o samą nazwę bez "
            "końcówki (np. „savowin” dla savowin.com) i tylko 1. strona. Jest na niej → <b>zaindeksowana</b> "
            "i <b>miejsce</b> (która z kolei). Gdy jej nie ma, nie wiadomo, czy jest w indeksie dalej "
            "(włącz „site: gdy brak na 1. str.” — 1 zapytanie więcej tylko tam).\n"
            "• 📊 <b>Statystyki</b> — dla każdej domeny: w ilu krajach zaindeksowana, ile razy 1. miejsce, średnie "
            "miejsce i miejsce na głównym rynku (z ostatniego sprawdzenia).\n"
            "• ⚡ <b>Tryb pełny</b> — <code>site:domena</code> w każdym kraju (indeks) + pozycja na nazwę do wybranej strony. "
            "<b>TAK*</b> = znaleziona dopiero w powtórce (nowe domeny Google pokazuje niestabilnie).\n"
            "• 🥇 <b>1. w site:</b> — czy strona główna jest pierwszym wynikiem; przy małych stronach inny "
            "pierwszy wynik to częsty znak filtra.\n"
            "• 🏷 <b>Nazwa</b> — wysyłam do Google samą nazwę (np. „savowin”) i szukam domeny w wynikach: "
            "strona i miejsce.\n"
            "• 🌐 📇 🗄 — darmowe testy: strona WWW, rejestracja domeny, archiwum Wayback.\n"
            "• Werdykt: ✅ OK · ⚠️ UWAGA · ❌ PROBLEM · 🆓 WOLNA (można zarejestrować).\n"
            "• Koszt: tryb szybki 1 zapytanie na kraj; pełny 1 na kraj + 1–3 na pozycję. "
            "Powyżej 100 zapytań pytam o zgodę.")
    return text, kb([("⚙️ Ustawienia", "s"), ("🏠 Menu", "m")])


def stats_view(store: Store):
    with store.lock:
        stats = json.loads(json.dumps(store.data["stats"]))
        domains = len(store.data["domains"])
        google = [info["google"] for info in store.data["domains"].values() if isinstance(info.get("google"), dict)]
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    week = [(datetime.now().astimezone() - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(7)]
    day = stats["days"].get(today, {})
    week_checks = sum(stats["days"].get(d, {}).get("checks", 0) for d in week)
    week_queries = sum(stats["days"].get(d, {}).get("queries", 0) for d in week)
    verdicts = " · ".join(f"{VERDICT_ICON.get(v, '•')} {esc(v)} {n}" for v, n in stats["verdicts"].items()) or "—"
    lines = ["📊 <b>Statystyki</b>",
             f"Sprawdzeń: <b>{stats['checks']}</b> · różnych domen: <b>{domains}</b>",
             f"Dziś: {day.get('checks', 0)} sprawdzeń · {day.get('queries', 0)} zapytań API",
             f"Ostatnie 7 dni: {week_checks} sprawdzeń · {week_queries} zapytań API",
             f"Zapytania API od początku: {stats['queries']}",
             f"Werdykty: {verdicts}"]
    if google:  # ostatnie sprawdzenie każdej domeny z Google
        indexed = sum(1 for g in google if g.get("indexed"))
        top = sum(1 for g in google if g.get("place") == 1)
        lines.append(f"🔎 Zaindeksowane domeny: <b>{indexed}/{len(google)}</b> · 1. miejsce na nazwę na rynku: "
                     f"<b>{top}/{len(google)}</b>")
    recent = store.recent(8)
    if recent:
        lines += ["", "<b>Ostatnio sprawdzane</b> (zaindeksowana · miejsca na samą nazwę · rynek)"]
        for domain, info in recent:
            g = info.get("google")
            detail = f" — {esc(google_text(g))}" if isinstance(g, dict) else ""
            lines.append(f"{VERDICT_ICON.get(info.get('verdict'), '•')} {esc(domain)}{detail} · "
                         f"{ago(info.get('last', ''))}")
    return "\n".join(lines), kb([("🕘 Historia", "h"), ("💳 Konto API", "k")], [("🏠 Menu", "m")])


def short_rank(rank) -> str:
    if rank is None:
        return ""
    if rank.error:
        return "błąd"
    if rank.place:
        return f"{rank.place}." + (f" s{rank.page}" if rank.page and rank.page > 1 else "")
    return "brak" + (" wiad." if rank.stories else "")


# --- Wynik sprawdzenia ----------------------------------------------------------------

@dataclass
class RunResult:
    rid: str
    reports: list
    plan: object
    settings: dict
    provider_name: str
    fatal: str | None
    usage: list = field(default_factory=list)
    events: list = field(default_factory=list)

    @property
    def domains(self) -> list[str]:
        return [report.domain for report in self.reports]


def quick_lines(report, plan) -> list[str]:
    rows = [r for r in report.results if r.indexed is not None and r.brand]
    if not rows:
        return []
    stats = ci.NameStats.of_report(report, plan)
    query, total = rows[0].brand.query, len(stats.places)
    mark = "✅" if stats.indexed == total else "⚠️" if stats.indexed else "❌"
    lines = [f"🔎 <b>Zaindeksowana:</b> {stats.indexed}/{total} {mark} <i>(jest na 1. stronie po wpisaniu "
             f"„{esc(query)}”)</i>"]
    if stats.indexed:
        lines.append(f"🏷 <b>Miejsce:</b> {esc(stats.places_text())}")
    market = next((r for r in rows if r.country == plan.market), None)
    if market:
        lines.append(f"    {esc(market.country.domain)}: {esc(ci.rank_text(market.brand))}")
    missing = [code.upper() for place, codes in stats.groups() if not place for code in codes]
    if missing:
        lines.append(f"    brak na 1. stronie: {esc(', '.join(missing[:10]))}"
                     + (f" i {len(missing) - 10} innych" if len(missing) > 10 else ""))
    checked = [r for r in rows if r.index_source == "site"]
    if checked:
        yes = sum(1 for r in checked if r.indexed)
        lines.append(f"🔎 <b>site: tam, gdzie brak:</b> w indeksie {yes}/{len(checked)}")
    failed = [r.country.domain for r in report.results if r.indexed is None]
    if failed:
        lines.append(f"    nie sprawdzono: {esc(', '.join(failed[:6]))}")
    return lines


def index_lines(report, plan) -> list[str]:
    if not report.results:
        return []
    if plan.mode == "quick":
        return quick_lines(report, plan)
    checked = [r for r in report.results if r.indexed is not None]
    yes = [r for r in checked if r.indexed]
    no = [r for r in checked if r.indexed is False]
    mark = "✅" if yes and not no else "⚠️" if yes else "❌"
    lines = [f"🔎 <b>Indeks Google:</b> {len(yes)}/{len(checked)} {mark}"]
    if no:
        names = [r.country.domain for r in no]
        lines.append(f"    brak w: {esc(', '.join(names[:6]))}" + (f" i {len(names) - 6} innych" if len(names) > 6 else ""))
    star = [r.country.domain for r in yes if r.recheck == "found"]
    if star:
        lines.append(f"    * dopiero w powtórce: {esc(', '.join(star))}")
    failed = [r.country.domain for r in report.results if r.indexed is None]
    if failed:
        lines.append(f"    nie sprawdzono: {esc(', '.join(failed[:6]))}")
    market = next((r for r in report.results if r.country == plan.market), None)
    if market and market.indexed:
        lines.append(f"🥇 <b>1. w site:</b> {esc(ci.FIRST_KIND_TEXT.get(market.first_kind, '—'))} "
                     f"({esc(market.country.domain)})")
    ranked = [r for r in report.results if r.brand]
    if ranked:
        query = ranked[0].brand.query
        if len(ranked) == 1:
            lines.append(f"🏷 <b>„{esc(query)}”</b> w {esc(ranked[0].country.domain)}: "
                         f"{esc(ci.rank_text(ranked[0].brand))}")
        else:
            first = sum(1 for r in ranked if r.brand.place == 1)
            page_one = sum(1 for r in ranked if r.brand.page == 1)
            line = f"🏷 <b>„{esc(query)}”</b>: 1. miejsce w {first}/{len(ranked)}, 1. strona w {page_one}/{len(ranked)}"
            if market and market.brand:
                line += f" · {esc(market.country.domain)}: {esc(ci.rank_text(market.brand))}"
            lines.append(line)
    phrased = [r for r in report.results if r.phrase and not r.phrase.error]
    if plan.phrase and phrased:
        page_one = sum(1 for r in phrased if r.phrase.page == 1)
        line = f"🔤 <b>„{esc(plan.phrase)}”</b>: 1. strona w {page_one}/{len(phrased)}"
        if market and market.phrase:
            line += f" · {esc(market.country.domain)}: {esc(ci.rank_text(market.phrase))}"
        lines.append(line)
    return lines


def site_line(site) -> str | None:
    if site is None:
        return None
    if isinstance(site, ci.Failed):
        return f"🌐 <b>Strona:</b> ❔ {esc(site.message)}"
    if site.error:
        return f"🌐 <b>Strona:</b> ❌ {esc(site.error)}"
    if site.offsite:
        return f"🌐 <b>Strona:</b> ❌ przekierowuje na {esc(site.offsite)}"
    if site.blocked:
        return f"🌐 <b>Strona:</b> 🛡 ochrona przed botami ({esc(site.blocked)})"
    parts = [f"HTTP {site.status}", "HTTPS ✅" if site.https_ok else "HTTPS ❌",
             {True: "robots ✅", False: "robots ❌"}.get(site.robots_allowed, "robots ❔"),
             "noindex ❌" if site.noindex else "bez noindex ✅"]
    if site.page and site.page.kind:
        parts.append(f"⚠️ {site.page.kind}")
    return "🌐 <b>Strona:</b> " + " · ".join(esc(p) for p in parts)


def reg_line(reg) -> str | None:
    if reg is None:
        return None
    if isinstance(reg, ci.Failed):
        return f"📇 <b>Domena:</b> ❔ {esc(reg.message)}"
    if reg.available:
        return "📇 <b>Domena:</b> 🆓 wolna — można ją zarejestrować"
    if reg.error:
        return f"📇 <b>Domena:</b> ❔ {esc(reg.error)}"
    parts = [ci.age_text(ci.days_since(reg.registered)) if reg.registered else "wiek nieznany"]
    if reg.expires:
        parts.append(f"wygasa {ci.fmt_date(reg.expires)}")
    if reg.registrar:
        parts.append(reg.registrar)
    return "📇 <b>Domena:</b> " + esc(" · ".join(parts))


def archive_line(archive) -> str | None:
    if archive is None:
        return None
    if isinstance(archive, ci.Failed):
        return f"🗄 <b>Archiwum:</b> ❔ {esc(archive.message)}"
    if archive.error:
        return f"🗄 <b>Archiwum:</b> ❔ {esc(archive.error)}"
    if not archive.years:
        return "🗄 <b>Archiwum:</b> brak kopii"
    count = len(archive.years)
    flagged = any(note and not note.startswith("nie udało") for _, _, note in archive.snapshots)
    return (f"🗄 <b>Archiwum:</b> {archive.years[0]}–{archive.years[-1]} ({count} {ci.plural(count, 'rok', 'lata', 'lat')})"
            + (" ⚠️ ślady spamu/parkingu" if flagged else ""))


def sorted_findings(report) -> list[tuple[str, str]]:
    order = {ci.BAD: 0, ci.WARN: 1, ci.OK: 2, ci.INFO: 3}
    return sorted(report.findings, key=lambda f: order[f[0]])


def manual_url(report, plan) -> str | None:
    market = next((r for r in report.results if r.country == plan.market and r.indexed is not None), None)
    if not market:
        return None
    if plan.mode == "quick":  # brak na 1. stronie dla samej nazwy — zobacz te same wyniki w przeglądarce
        if market.brand is None or market.brand.place:
            return None
        search = market.brand.query
    elif market.indexed is False:
        search = f"site:{report.domain}"
    else:
        return None
    query = urllib.parse.urlencode({"q": search, "hl": market.country.hl, "gl": market.country.code, "pws": 0})
    return f"https://www.{market.country.domain}/search?{query}"


def footer_lines(res: RunResult) -> list[str]:
    lines = []
    if res.fatal:
        lines.append(f"⛔ <b>Przerwano:</b> {esc(res.fatal)}")
    lines += [f"🔑 {esc(event)}" for event in res.events]
    lines += [f"<i>{esc(line)}</i>" for line in res.usage]
    return lines


def summary_view(res: RunResult, i: int):
    report, plan, rid = res.reports[i], res.plan, res.rid
    icon, description = VERDICT_ICON.get(report.verdict, "•"), ci.VERDICTS[report.verdict][2]
    lines = [f"{icon} <b>{esc(report.verdict)}</b> — <b>{esc(report.domain)}</b>", f"<i>{esc(description)}</i>", ""]
    lines += index_lines(report, plan)
    lines += [line for line in (site_line(report.site), reg_line(report.reg), archive_line(report.archive)) if line]
    if report.previous:
        changes = "; ".join(report.changes) if report.changes else "bez zmian"
        lines.append(f"🔄 <b>Zmiany od {esc(report.previous[:16].replace('T', ' '))}:</b> {esc(changes)}")
    findings = sorted_findings(report)
    if findings:
        lines += ["", "<b>Ocena</b>"] + [f"{LEVEL_ICON[level]} {esc(text)}" for level, text in findings[:8]]
        if len(findings) > 8:
            lines.append(f"… i {len(findings) - 8} więcej — przycisk „Wszystkie uwagi”")
    footer = footer_lines(res)
    if footer:
        lines += [""] + footer
    missing = any(r.indexed is False for r in report.results)
    url = manual_url(report, plan)
    return "\n".join(lines), kb(
        [("🌍 Kraje" if report.results else "", f"rc:{rid}:{i}"),
         ("🌐 Strona" if report.site is not None else "", f"rs:{rid}:{i}"),
         ("📇 Domena" if report.reg is not None else "", f"rd:{rid}:{i}")],
        [("🗄 Archiwum" if report.archive is not None else "", f"ra:{rid}:{i}"),
         ("📋 Wszystkie uwagi", f"rf:{rid}:{i}"), ("📄 CSV", f"rv:{rid}")],
        [("🔁 Sprawdź ponownie", f"rr:{rid}:{i}"),
         ("✅ Potwierdź NIE" if missing and plan.use_api and plan.mode == "full" else "", f"rp:{rid}:{i}")],
        [("🔗 Sprawdź ręcznie w Google" if url else "", f"url:{url}")],
        [("⬅️ Lista" if len(res.reports) > 1 else "", f"rl:{rid}"), ("🏠 Menu", "m")])


def back_row(res: RunResult, i: int) -> list:
    return [("⬅️ Wynik", f"r:{res.rid}:{i}"), ("🏠 Menu", "m")]


def quick_table_view(res: RunResult, i: int):
    report = res.reports[i]
    query = next((r.brand.query for r in report.results if r.brand), ci.brand_name(report.domain))
    rows = [f"{'Kraj':<5}{'Zaindeksowana':<15}Miejsce"]
    for r in report.results:
        rows.append(f"{r.country.code.upper():<5}{ci.quick_index_cell(r):<15}{ci.quick_place_cell(r)}".rstrip())
    notes = [f"Zaindeksowana = {esc(report.domain)} jest na 1. stronie wyników po wpisaniu „{esc(query)}” "
             f"(NIE = nie ma jej na 1. stronie). Miejsce = która z kolei."]
    if any(r.index_source == "site" for r in report.results):
        notes.append("(site:) = nie ma jej na 1. stronie; czy jest w indeksie — z zapytania site:")
    stats = ci.NameStats.of_report(report, res.plan)
    if stats.places:
        notes.append(f"Razem: zaindeksowana {stats.indexed}/{len(stats.places)}"
                     + (f" · {esc(stats.places_text())}" if stats.indexed else ""))
    text = (f"🌍 <b>„{esc(query)}” w krajach — {esc(report.domain)}</b>\n<pre>{esc(chr(10).join(rows))}</pre>\n"
            + "\n".join(notes))
    return text, kb(back_row(res, i))


def countries_table_view(res: RunResult, i: int):
    if res.plan.mode == "quick":
        return quick_table_view(res, i)
    report = res.reports[i]
    phrase = any(r.phrase for r in report.results)
    header = f"{'Kraj':<5}{'Indeks':<7}{'Wyniki':<12}{'1.site':<8}{'Nazwa':<9}" + ("Fraza" if phrase else "")
    rows = [header.rstrip()]
    for r in report.results:
        status = "BŁĄD" if r.indexed is None else ci.yes_no(r.indexed) + ("*" if r.recheck == "found" else "")
        kind = KIND_SHORT.get(r.first_kind, "—") if r.indexed else "—"
        row = f"{r.country.code.upper():<5}{status:<7}{ci.fmt_count(r):<12}{kind:<8}{short_rank(r.brand):<9}"
        rows.append((row + (short_rank(r.phrase) if phrase else "")).rstrip())
    brand = next((r.brand.query for r in report.results if r.brand), ci.brand_name(report.domain))
    notes = [f"Nazwa = miejsce po wpisaniu „{esc(brand)}” (s2 = 2. strona)"]
    if any(r.recheck == "found" for r in report.results):
        notes.append("* TAK dopiero w powtórnym zapytaniu")
    text = (f"🌍 <b>Indeks w krajach — {esc(report.domain)}</b>\n<pre>{esc(chr(10).join(rows))}</pre>\n"
            + "\n".join(notes))
    return text, kb(back_row(res, i))


def site_view(res: RunResult, i: int):
    site, report = res.reports[i].site, res.reports[i]
    lines = [f"🌐 <b>Strona WWW — {esc(report.domain)}</b>"]
    if isinstance(site, ci.Failed):
        lines.append(f"❔ {esc(site.message)}")
    elif site is not None:
        if site.ips:
            lines.append(f"IP: {esc(', '.join(site.ips[:3]))}" + (f" ({esc(site.dns_note)})" if site.dns_note else ""))
        if site.error:
            lines.append(f"❌ {esc(site.error)}")
        else:
            hops = " → ".join(f"{ci.show_url(url, 50)} [{status}]" for status, url in site.entry_chain)
            if site.entry_error:
                hops = (hops + " → " if hops else f"http://{site.host}/ → ") + f"✘ {site.entry_error}"
            lines += [f"Wejście przez http://: {esc(hops)}",
                      f"Strona końcowa: {esc(ci.show_url(site.final_url, 70))} (HTTP {site.status}, {site.seconds:.1f} s)"]
            if site.https_ok:
                cert = f", certyfikat do {ci.fmt_date(site.ssl_expires)}" if site.ssl_expires else ""
                lines.append(f"HTTPS: ✅ działa{esc(cert)}" + (" · ⚠️ http:// nie przekierowuje na https://"
                                                             if site.http_to_https is False else ""))
            else:
                lines.append(f"HTTPS: ❌ {esc(site.https_note)}")
            lines.append(f"robots.txt: {esc(site.robots_note)}")
            if site.blocked:
                lines.append(f"🛡 Ochrona przed botami: {esc(site.blocked)} — treść niedostępna dla automatów")
            else:
                lines.append("Indeksowanie: " + (f"❌ NOINDEX — {esc(site.noindex)}" if site.noindex else "✅ brak noindex"))
            if site.www_note:
                lines.append(f"Adres z www: {esc(site.www_note)}")
            page = site.page
            if page:
                lines.append(f"Tytuł: „{esc(page.title or '(brak)')}” · ~{page.words} słów · H1: {page.h1} · viewport: "
                             f"{'jest' if page.viewport else 'BRAK'}" + (f" · język: {esc(page.lang)}" if page.lang else ""))
                if page.description:
                    lines.append(f"Opis: „{esc(ci.clean(page.description, 150))}”")
                if page.kind:
                    lines.append(f"⚠️ Wygląda na: {esc(page.kind)} („{esc(page.kind_hint)}”)")
    return "\n".join(lines), kb(back_row(res, i))


def reg_view(res: RunResult, i: int):
    reg, report = res.reports[i].reg, res.reports[i]
    lines = [f"📇 <b>Rejestracja — {esc(report.domain)}</b>"]
    if isinstance(reg, ci.Failed):
        lines.append(f"❔ {esc(reg.message)}")
    elif reg is not None:
        if reg.available:
            lines.append("🆓 Brak w rejestrze — domena wygląda na wolną")
        elif reg.error:
            lines.append(f"❔ {esc(reg.error)}")
        else:
            lines.append(f"Źródło: {esc(reg.source)}" + (f" (dane dla {esc(reg.name)})" if reg.name and reg.name != report.domain else ""))
            if reg.registered:
                lines.append(f"Zarejestrowana: {ci.fmt_date(reg.registered)} ({esc(ci.age_text(ci.days_since(reg.registered)))} temu)")
            else:
                lines.append("Data rejestracji niedostępna (rejestr jej nie publikuje)")
            if reg.expires:
                lines.append(f"Wygasa: {ci.fmt_date(reg.expires)} (za {ci.days_until(reg.expires)} dni)")
            if reg.registrar:
                lines.append(f"Rejestrator: {esc(reg.registrar)}")
            if reg.statuses:
                lines.append(f"Status: {esc(', '.join(reg.statuses[:5]))}")
            if reg.nameservers:
                lines.append(f"Serwery DNS: {esc(', '.join(reg.nameservers[:4]))}")
    return "\n".join(lines), kb(back_row(res, i))


def archive_view(res: RunResult, i: int):
    archive, report = res.reports[i].archive, res.reports[i]
    lines = [f"🗄 <b>Archiwum Wayback — {esc(report.domain)}</b>"]
    if isinstance(archive, ci.Failed):
        lines.append(f"❔ {esc(archive.message)}")
    elif archive is not None:
        if archive.error:
            lines.append(f"❔ {esc(archive.error)}")
        elif not archive.years:
            lines.append("Brak kopii strony głównej w archiwum")
        else:
            count = len(archive.years)
            lines.append(f"Kopie z lat: {esc(ci.year_ranges(archive.years))} ({count} {ci.plural(count, 'rok', 'lata', 'lat')})")
            if archive.first:
                lines.append(f"Pierwsza kopia: {ci.fmt_date(archive.first)}")
            if archive.snapshots:
                lines.append("<b>Tytuły w historii</b>")
                for year, title, note in archive.snapshots:
                    lines.append(f"{year} „{esc(ci.clean(title, 70))}”" + (f" ⚠️ {esc(note)}" if note else ""))
    url = f"https://web.archive.org/web/*/{report.domain}"
    return "\n".join(lines), kb([("🔗 Otwórz archiwum", f"url:{url}")], back_row(res, i))


def findings_view(res: RunResult, i: int):
    report = res.reports[i]
    lines = [f"📋 <b>Wszystkie uwagi — {esc(report.domain)}</b>"]
    lines += [f"{LEVEL_ICON[level]} {esc(text)}" for level, text in sorted_findings(report)] or ["brak"]
    return "\n".join(lines), kb(back_row(res, i))


def list_view(res: RunResult):
    quick = res.plan.mode == "quick"
    rows = [f"{'Domena':<24}{'Indeks':<8}{'Nazwa':<9}Werdykt"]
    for report in res.reports:
        stats = ci.NameStats.of_report(report, res.plan)
        index = f"{stats.indexed}/{stats.checked}" if stats.checked else "—"
        market = next((r for r in report.results if r.country == res.plan.market), None)
        brand = short_rank(market.brand) if market and market.brand else "—"
        rows.append(f"{ci.clean(report.domain, 23):<24}{index:<8}{brand:<9}{report.verdict}")
    count = len(res.reports)
    legend = ("Indeks = w ilu krajach zaindeksowana (jest na 1. stronie po wpisaniu samej nazwy), "
              "Nazwa = miejsce na głównym rynku." if quick else
              "Indeks = w ilu krajach jest w wynikach site:, Nazwa = miejsce po wpisaniu samej nazwy na głównym rynku.")
    lines = [f"📋 <b>Wyniki: {count} {ci.plural(count, 'domena', 'domeny', 'domen')}</b>",
             f"<pre>{esc(chr(10).join(rows))}</pre>", legend, "Kliknij domenę, żeby zobaczyć szczegóły."]
    footer = footer_lines(res)
    if footer:
        lines += [""] + footer
    buttons = [(f"{VERDICT_ICON.get(r.verdict, '•')} {ci.clean(r.domain, 28)}", f"r:{res.rid}:{n}")
               for n, r in enumerate(res.reports[:40])]
    domain_rows = [buttons[n:n + 2] for n in range(0, len(buttons), 2)]
    return "\n".join(lines), kb(*domain_rows,
                                [("📄 CSV", f"rv:{res.rid}"), ("🔁 Sprawdź ponownie", f"rr:{res.rid}:-1")],
                                [("🏠 Menu", "m")])


def usage_lines(provider) -> list[str]:
    if provider is None:
        return []
    pool = provider.pool
    line = f"{provider.name}: wysłano {provider.used} {ci.plural(provider.used, 'zapytanie', 'zapytania', 'zapytań')}"
    if len(pool.keys) > 1:
        line += " (" + ", ".join(f"{pool.label(key)}: {pool.used[key]}" for key in pool.keys) + ")"
    lines = [line]
    for key in pool.keys:
        if key in pool.dead:
            lines.append(f"{pool.label(key)}: pominięty — {pool.dead[key]}")
            continue
        try:
            lines.append(f"{pool.label(key)}: {provider.account_text(key)}")
        except ci.ApiError:
            lines.append(f"{pool.label(key)}: stan konta niedostępny")
    return lines


def merged_env(path: Path) -> dict:
    """Zmienne środowiska + .env; pusta wartość w .env (np. „TELEGRAM_BOT_TOKEN=”) niczego nie kasuje."""
    return {**os.environ, **{key: value for key, value in ci.read_env_file(path).items() if value}}


def denied_text(user_id) -> str:
    return (f"⛔ To prywatny bot.\nTwój identyfikator Telegram: <code>{esc(user_id)}</code>\n"
            f"Właściciel może dopisać go do TELEGRAM_ALLOWED_USERS w pliku .env.")


# --- Zadania i główna pętla ----------------------------------------------------------

@dataclass
class Job:
    chat_id: int
    user_id: int
    domains: list
    settings: dict
    message_id: int | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])


def domains_label(domains: list[str]) -> str:
    return domains[0] if len(domains) == 1 else f"{len(domains)} domen ({', '.join(domains[:3])}{', …' if len(domains) > 3 else ''})"


class Ticker:
    """Co kilka sekund wpisuje postęp do wiadomości „⏳ Sprawdzam…” (Telegram nie lubi zbyt częstych edycji)."""

    def __init__(self, bot: "Bot", job: Job, every: float = 3.0):
        self.bot, self.job, self.every = bot, job, every
        self.text, self.shown = "", ""
        self.done = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def update(self, text: str) -> None:
        if text.strip():
            self.text = text.strip()

    def loop(self) -> None:
        while not self.done.wait(self.every):
            if self.text and self.text != self.shown:
                self.shown = self.text
                self.bot.safe_edit(self.job, f"⏳ <b>Sprawdzam</b> {esc(domains_label(self.job.domains))}\n{esc(self.text)}")

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.done.set()
        self.thread.join(timeout=10)


class Bot:
    def __init__(self, tg: Telegram, store: Store, env_path: Path = ENV_PATH, poll_timeout: int = 50,
                 workers: int = 4):
        self.tg, self.store, self.env_path = tg, store, env_path
        self.poll_timeout, self.workers = poll_timeout, workers
        self.jobs: queue.Queue = queue.Queue()
        self.running: Job | None = None
        self.pending: dict[str, Job] = {}          # czekają na potwierdzenie kosztu
        self.results: OrderedDict[str, RunResult] = OrderedDict()
        self.awaiting: dict[int, str] = {}         # czat -> na co czekamy (np. fraza)
        self.history_lists: dict[int, list[str]] = {}
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def env(self) -> dict:
        """Klucze czytamy z .env przy każdym użyciu — dopisany klucz działa bez restartu bota."""
        return merged_env(self.env_path)

    # --- pętla ---

    def run(self) -> None:
        me = self.tg.call("getMe")
        self.tg.call("deleteWebhook")  # przy ustawionym webhooku getUpdates nie działa
        self.tg.call("setMyCommands", commands=[{"command": "start", "description": "Menu bota"}])
        log(f"Bot @{me.get('username')} działa — napisz do niego /start. Ctrl+C kończy.")
        threading.Thread(target=self.work, daemon=True, name="bot-worker").start()
        offset = None
        while not self.stopped.is_set():
            try:
                updates = self.tg.call("getUpdates", wait=self.poll_timeout + 15, offset=offset,
                                       timeout=self.poll_timeout, allowed_updates=["message", "callback_query"])
            except TelegramError as e:
                if e.code in (401, 404):
                    raise  # zły token — nie ma sensu próbować dalej
                log(f"Telegram: {e} — ponawiam za 5 s")
                self.stopped.wait(5)
                continue
            for update in updates or []:
                offset = update["update_id"] + 1
                try:
                    self.dispatch(update)
                except Exception:  # noqa: BLE001 — jedna zła wiadomość nie może zatrzymać bota
                    log("Błąd przy obsłudze wiadomości:\n" + traceback.format_exc())

    def stop(self) -> None:
        self.stopped.set()

    def dispatch(self, update: dict) -> None:
        if "callback_query" in update:
            self.on_callback(update["callback_query"])
        elif "message" in update:
            self.on_message(update["message"])

    def work(self) -> None:
        while not self.stopped.is_set():
            try:
                job = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            with self.lock:
                self.running = job
            try:
                self.execute(job)
            except Exception as e:  # noqa: BLE001
                log("Błąd sprawdzania:\n" + traceback.format_exc())
                self.safe_edit(job, f"❌ Nieoczekiwany błąd: {esc(type(e).__name__)}: {esc(ci.clean(e, 200))}")
            finally:
                with self.lock:
                    self.running = None

    # --- wiadomości ---

    def on_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        chat_id, user_id = chat.get("id"), (message.get("from") or {}).get("id")
        if chat.get("type") != "private":
            return  # bot działa tylko w prywatnym czacie
        text, env = (message.get("text") or "").strip(), self.env()
        if text.startswith("/start"):
            return self.on_start(chat_id, user_id, env)
        if not self.store.allowed(user_id, env):
            self.tg.send(chat_id, denied_text(user_id))
            return
        if not text:
            self.tg.send(chat_id, "Wyślij domenę tekstem, np. <code>savowin</code>.")
            return
        if text.startswith("/"):
            self.tg.send(chat_id, "Jedyna komenda to /start — resztę robisz przyciskami. "
                                  "Domenę wyślij zwykłą wiadomością.", kb([("🏠 Menu", "m")]))
            return
        if self.awaiting.pop(chat_id, None) == "phrase":
            settings = self.store.update(user_id, phrase=ci.clean(text, 100))
            self.tg.send(chat_id, *settings_view(settings))
            return
        settings = self.store.settings(user_id)
        domains, bad = parse_domains(text, settings["tld"])
        if not domains:
            self.tg.send(chat_id, f"🤔 To nie wygląda na domenę: {esc(ci.clean(text, 60))}\n"
                                  f"Wyślij np. <code>savowin</code> albo <code>savowin.com</code>.")
            return
        note = f"⚠️ Pomijam (to nie domeny): {esc(', '.join(bad[:5]))}\n" if bad else ""
        self.start_or_confirm(chat_id, user_id, domains, settings, note)

    def on_start(self, chat_id: int, user_id: int, env: dict) -> None:
        if not self.store.allowed(user_id, env):
            if not self.store.claim(user_id, env):
                self.tg.send(chat_id, denied_text(user_id))
                return
            log(f"Właściciel bota: {user_id}")
        self.awaiting.pop(chat_id, None)
        self.tg.send(chat_id, *menu_view(self.store.settings(user_id)))

    # --- sprawdzanie ---

    def start_or_confirm(self, chat_id: int, user_id: int, domains: list[str], settings: dict, note: str = "",
                         message_id: int | None = None) -> None:
        job = Job(chat_id, user_id, domains, dict(settings))
        plan, worst = plan_for(settings), 0
        if plan.use_api:
            try:
                provider = ci.provider_for(settings["provider"], self.env())
            except ci.ConfigError as e:
                self.tg.send(chat_id, f"❌ {esc(e)} Dopisz klucz do pliku .env albo wyłącz Google w ustawieniach "
                                      f"(zostaną darmowe testy).", kb([("⚙️ Ustawienia", "s")]))
                return
            worst = ci.estimate_queries(len(domains), plan, provider)[1]
        if worst > ci.CONFIRM_ABOVE:
            self.pending[job.id] = job
            text = (f"{note}💸 Sprawdzenie {esc(domains_label(domains))} w {len(plan.countries)} "
                    f"{ci.plural(len(plan.countries), 'kraju', 'krajach', 'krajach')} zużyje do <b>{worst}</b> "
                    f"zapytań API. Sprawdzić?")
            self.tg.send(chat_id, text, kb([("✅ Sprawdź", f"go:{job.id}"), ("❌ Anuluj", f"no:{job.id}")]))
            return
        self.enqueue(job, note, message_id)

    def enqueue(self, job: Job, note: str = "", message_id: int | None = None) -> None:
        with self.lock:
            ahead = self.jobs.qsize() + (1 if self.running else 0)
        label = esc(domains_label(job.domains))
        text = note + (f"⏳ <b>Sprawdzam</b> {label}…" if not ahead else f"🕒 W kolejce ({ahead} przed tym): {label}")
        if message_id:
            self.tg.edit(job.chat_id, message_id, text)
            job.message_id = message_id
        else:
            job.message_id = self.tg.send(job.chat_id, text)
        self.jobs.put(job)

    def execute(self, job: Job) -> None:
        settings, provider = job.settings, None
        plan = plan_for(settings)
        if plan.use_api:
            try:
                provider = ci.provider_for(settings["provider"], self.env())
            except ci.ConfigError as e:
                self.safe_edit(job, f"❌ {esc(e)} Dopisz klucz do pliku .env.", kb([("⚙️ Ustawienia", "s")]))
                return
            provider.prepare()
        self.safe_edit(job, f"⏳ <b>Sprawdzam</b> {esc(domains_label(job.domains))}…")
        site_queries = len(job.domains) * len(plan.countries) if plan.use_api else 0
        with Ticker(self, job) as ticker:
            progress = ci.Progress(site_queries, len(job.domains) * len(ci.extras_of(plan)), listener=ticker.update)
            reports, fatal = ci.run_checks(job.domains, plan, provider, self.workers, progress, save=True)
        result = RunResult(uuid.uuid4().hex[:6], reports, plan, settings, provider.name if provider else "", fatal,
                           usage_lines(provider), list(provider.pool.events) if provider else [])
        with self.lock:
            self.results[result.rid] = result
            while len(self.results) > RESULTS_KEPT:
                self.results.popitem(last=False)
        # Przerwane sprawdzenie nie nadpisuje indeksu i miejsc w statystykach (tak jak nie trafia do historii).
        self.store.record(reports, provider.used if provider else 0, None if fatal else plan)
        log(f"Sprawdzono {domains_label(job.domains)} — zapytań API: {provider.used if provider else 0}")
        view = summary_view(result, 0) if len(reports) == 1 else list_view(result)
        self.safe_edit(job, *view)

    def safe_edit(self, job: Job, text: str, keyboard: dict | None = None) -> None:
        """Edytuje wiadomość zadania; gdy się nie da (np. usunięta), wysyła nową."""
        try:
            if job.message_id:
                self.tg.edit(job.chat_id, job.message_id, text, keyboard)
                return
        except TelegramError as e:
            log(f"Nie udało się edytować wiadomości: {e}")
        try:
            job.message_id = self.tg.send(job.chat_id, text, keyboard)
        except TelegramError as e:
            log(f"Nie udało się wysłać wiadomości: {e}")

    # --- przyciski ---

    def on_callback(self, query: dict) -> None:
        query_id, user_id = query.get("id"), (query.get("from") or {}).get("id")
        message = query.get("message") or {}
        chat_id, message_id = (message.get("chat") or {}).get("id"), message.get("message_id")
        if not self.store.allowed(user_id, self.env()):
            self.tg.answer(query_id, "⛔ Brak dostępu", alert=True)
            return
        toast = None
        try:
            toast = self.route(chat_id, user_id, message_id, query.get("data") or "")
        except (ValueError, IndexError):
            toast = "Nieznany przycisk"
        finally:
            self.tg.answer(query_id, toast)

    def show(self, chat_id: int, message_id: int, view) -> None:
        text, keyboard = view
        try:
            self.tg.edit(chat_id, message_id, text, keyboard)
        except TelegramError:
            self.tg.send(chat_id, text, keyboard)

    def route(self, chat_id: int, user_id: int, message_id: int, data: str) -> str | None:
        s = self.store.settings(user_id)
        cmd, _, arg = data.partition(":")
        update = lambda **changes: self.store.update(user_id, **changes)  # noqa: E731
        if cmd == "m":
            self.awaiting.pop(chat_id, None)
            self.show(chat_id, message_id, menu_view(s))
        elif cmd == "s":
            self.awaiting.pop(chat_id, None)
            self.show(chat_id, message_id, settings_view(s))
        elif cmd == "c":
            if not arg:
                self.show(chat_id, message_id, countries_view(s))
                return None
            if arg not in PRESET_NAMES:
                return "Nieznany zestaw"
            s = update(countries=arg)
            self.show(chat_id, message_id, settings_view(s))
            return f"Kraje: {countries_label(s)}"
        elif cmd == "cp":
            self.show(chat_id, message_id, picker_view(s, int(arg or 0)))
        elif cmd == "ct":
            code, _, page = arg.partition(":")
            if code not in ci.COUNTRY_BY_CODE:
                return "Nieznany kraj"
            current = [c.code for c in countries_for(s)] if s["countries"] != "custom" else list(s["custom"])
            current = [c for c in current if c != code] if code in current else current + [code]
            s = update(countries="custom", custom=current)
            self.show(chat_id, message_id, picker_view(s, int(page or 0)))
        elif cmd == "cx":
            s = update(countries="custom", custom=[])
            self.show(chat_id, message_id, picker_view(s, int(arg or 0)))
        elif cmd == "mk":
            if not arg:
                self.show(chat_id, message_id, market_view(s, 0))
                return None
            if arg not in ci.COUNTRY_BY_CODE:
                return "Nieznany kraj"
            s = update(market=arg)
            self.show(chat_id, message_id, settings_view(s))
            return f"Rynek: {market_of(s).domain}"
        elif cmd == "mp":
            self.show(chat_id, message_id, market_view(s, int(arg or 0)))
        elif cmd in ("br", "pg", "cf", "tl", "pv", "md"):
            cycles = {"br": ("brand", list(BRAND_NAMES)), "pg": ("max_pages", PAGE_OPTIONS), "md": ("mode", list(MODE_NAMES)),
                      "cf": ("confirm_all", [False, True]), "tl": ("tld", TLDS), "pv": ("provider", list(PROVIDER_NAMES))}
            key, options = cycles[cmd]
            current = s[key]
            s = update(**{key: options[(options.index(current) + 1) % len(options)] if current in options else options[0]})
            self.show(chat_id, message_id, settings_view(s))
        elif cmd == "tg":
            if arg not in ("site", "reg", "archive", "use_api", "index_fallback"):
                return "Nieznany przełącznik"
            s = update(**{arg: not s[arg]})
            self.show(chat_id, message_id, settings_view(s))
        elif cmd == "ph":
            self.awaiting[chat_id] = "phrase"
            self.show(chat_id, message_id, ("✏️ Napisz frazę, dla której mam sprawdzać pozycję (np. <i>tanie buty</i>).",
                                            kb([("❌ Anuluj", "s")])))
        elif cmd == "phx":
            s = update(phrase="")
            self.show(chat_id, message_id, settings_view(s))
        elif cmd == "st":
            self.show(chat_id, message_id, stats_view(self.store))
        elif cmd == "h":
            self.show(chat_id, message_id, self.history_list_view(chat_id))
        elif cmd in ("hd", "hc"):
            domains = self.history_lists.get(chat_id) or []
            index = int(arg)
            if not 0 <= index < len(domains):
                return "Lista historii wygasła — otwórz ją ponownie"
            if cmd == "hd":
                self.show(chat_id, message_id, history_view(domains[index], index))
            else:
                self.start_or_confirm(chat_id, user_id, [domains[index]], s)
        elif cmd == "k":
            self.show(chat_id, message_id, ("💳 Sprawdzam stan kont…", None))
            threading.Thread(target=self.show_accounts, args=(chat_id, message_id), daemon=True).start()
        elif cmd == "?":
            self.show(chat_id, message_id, help_view())
        elif cmd == "go":
            job = self.pending.pop(arg, None)
            if not job:
                return "To potwierdzenie wygasło — wyślij domenę jeszcze raz"
            self.enqueue(job, message_id=message_id)
        elif cmd == "no":
            self.pending.pop(arg, None)
            self.show(chat_id, message_id, ("❌ Anulowano.", kb([("🏠 Menu", "m")])))
        elif cmd in ("r", "rc", "rs", "rd", "ra", "rf", "rv", "rr", "rp", "rl"):
            return self.route_result(chat_id, user_id, message_id, cmd, arg)
        else:
            return "Nieznany przycisk"
        return None

    def route_result(self, chat_id: int, user_id: int, message_id: int, cmd: str, arg: str) -> str | None:
        rid, _, index = arg.partition(":")
        with self.lock:
            result = self.results.get(rid)
        if not result:
            return "Ten wynik wygasł — wyślij domenę jeszcze raz"
        i = int(index or 0)
        if not -1 <= i < len(result.reports) or (i == -1 and cmd not in ("rr", "rp")):
            return "Nieznany przycisk"
        views = {"r": summary_view, "rc": countries_table_view, "rs": site_view, "rd": reg_view,
                 "ra": archive_view, "rf": findings_view}
        if cmd in views:
            self.show(chat_id, message_id, views[cmd](result, i))
        elif cmd == "rl":
            self.show(chat_id, message_id, list_view(result))
        elif cmd == "rv":
            threading.Thread(target=self.send_csv, args=(chat_id, result), daemon=True).start()
            return "Wysyłam pliki CSV…"
        else:  # rr / rp: sprawdź jeszcze raz (rp: z powtórką „NIE” we wszystkich krajach)
            domains = result.domains if i == -1 else [result.reports[i].domain]
            settings = dict(result.settings, confirm_all=True) if cmd == "rp" else dict(result.settings)
            self.start_or_confirm(chat_id, user_id, domains, settings)
        return None

    # --- widoki wymagające stanu bota ---

    def history_list_view(self, chat_id: int):
        domains = [domain for domain, _ in self.store.recent(12)]
        if not domains and ci.HISTORY_DIR.exists():  # sprawdzenia z terminala też mają historię
            files = sorted(ci.HISTORY_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
            domains = [p.name.removesuffix(".jsonl") for p in files[:12]]
        self.history_lists[chat_id] = domains
        if not domains:
            return "🕘 <b>Historia</b>\nJeszcze nic nie sprawdzono.", kb([("🏠 Menu", "m")])
        verdicts = {domain: info.get("verdict") for domain, info in self.store.recent(500)}
        rows = [[(f"{VERDICT_ICON.get(verdicts.get(domain), '•')} {ci.clean(domain, 40)}", f"hd:{n}")]
                for n, domain in enumerate(domains)]
        return ("🕘 <b>Historia</b>\nKliknij domenę, żeby zobaczyć, jak zmieniały się indeks i pozycja.",
                kb(*rows, [("🏠 Menu", "m")]))

    def show_accounts(self, chat_id: int, message_id: int) -> None:
        env, lines = self.env(), ["💳 <b>Konto API</b>"]
        try:
            serpapi = ci.provider_for("serpapi", env)
        except ci.ConfigError:
            serpapi = None
        if serpapi:
            for key in serpapi.pool.keys:
                try:
                    state = serpapi.account_text(key)
                except ci.ApiError as e:
                    state = str(e)
                lines.append(f"SerpApi {esc(serpapi.pool.label(key))}: {esc(state)}")
        else:
            lines.append("SerpApi: brak klucza w .env")
        serper = ci.api_keys("SERPER_API_KEY", env=env)
        lines.append(f"Serper.dev: {len(serper)} {ci.plural(len(serper), 'klucz', 'klucze', 'kluczy')} — stan w panelu "
                     f"serper.dev" if serper else "Serper.dev: brak klucza w .env")
        self.show(chat_id, message_id, ("\n".join(lines), kb([("🔄 Odśwież", "k"), ("🏠 Menu", "m")])))

    def send_csv(self, chat_id: int, result: RunResult) -> None:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / f"wyniki_{datetime.now():%Y-%m-%d_%H%M}.csv"
                summary = Path(ci.write_csv(str(path), result.reports, result.plan))
                for file in (path, summary):
                    self.tg.send_document(chat_id, file.name, file.read_bytes())
        except (OSError, TelegramError) as e:
            log(f"CSV: {e}")
            try:
                self.tg.send(chat_id, f"❌ Nie udało się wysłać CSV: {esc(e)}")
            except TelegramError:
                pass


def history_view(domain: str, index: int):
    records = ci.read_history(ci.history_path(domain))[-12:]
    lines = [f"🕘 <b>Historia — {esc(domain)}</b>"]
    if not records:
        lines.append("Brak zapisanych sprawdzeń z Google (zapisują się sprawdzenia z zapytaniami API).")
    for record in reversed(records):
        when = ci.clean(record.get("czas", "?"))[:16].replace("T", " ")
        verdict = ci.clean(record.get("werdykt", "?"))
        lines.append(f"{VERDICT_ICON.get(verdict, '•')} {esc(when)} · {esc(ci.history_summary(record))}")
    places = ci.NameStats.of_record(records[-1]).places if records else {}
    if places:
        lines += ["", "<b>Miejsce na samą nazwę w krajach</b> (ostatnie sprawdzenie, — = brak)",
                  esc(" · ".join(f"{code.upper()} {f'{place}.' if place else '—'}" for code, place in places.items()))]
    return "\n".join(lines), kb([("🔁 Sprawdź teraz", f"hc:{index}")], [("⬅️ Historia", "h"), ("🏠 Menu", "m")])


def main() -> int:
    token = merged_env(ENV_PATH).get("TELEGRAM_BOT_TOKEN", "").strip()
    if not TOKEN_RE.match(token):
        print("Brak poprawnego TELEGRAM_BOT_TOKEN w pliku .env.\n"
              "1. Na Telegramie napisz do @BotFather: /newbot i nadaj botowi nazwę.\n"
              "2. Skopiuj token do pliku .env: TELEGRAM_BOT_TOKEN=123456789:AA…\n"
              "3. Uruchom ponownie: python3 telegram_bot.py", file=sys.stderr)
        return 1
    bot = Bot(Telegram(token), Store(STATE_PATH))
    try:
        bot.run()
    except TelegramError as e:
        print(f"Telegram odrzucił połączenie ({e}). Sprawdź TELEGRAM_BOT_TOKEN w .env.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nZatrzymano.", flush=True)
        os._exit(0)
