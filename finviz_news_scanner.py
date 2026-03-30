"""
finviz_scanner.py
-----------------
Scans Finviz news for tickers matching your price threshold and/or keywords.
Alerts via terminal sound + print. Logs all alerts to file.
Configure via config.json in the same directory.

Alert Priority Levels:
  [HIGH]    Ticker is under price threshold AND headline contains a keyword
  [WATCH]   Ticker is in your watchlist (any price)
  [PRICE]   Ticker is under price threshold (no keyword)
  [KEYWORD] Keyword matched but ticker is above price threshold
"""

import json
import os
import sys
import time
import winsound
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from collections import deque
from io import StringIO
import pandas as pd
import re
import subprocess
import uuid
import threading
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
import logging

# ── Paths ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

# ── Load config ────────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)

# ── Terminal helpers ───────────────────────────────────────────────────────────
RESET   = "\033[0m"
RED     = "\033[91m"
YELLOW  = "\033[93m"
CYAN    = "\033[96m"
GREEN   = "\033[92m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
WHITE   = "\033[97m"

LINES_TO_PRINT = 89   # Terminal line width — edit if your window is wider/narrower

# ── Terminal Unicode capability detection ──────────────────────────────────────
# CMD often can't render Unicode box/arrow chars even with chcp 65001.
# Set SCANNER_UNICODE=0 in your environment to force ASCII mode.
_UNICODE = os.environ.get("SCANNER_UNICODE", "1") != "0"

# Symbols — Unicode when supported, ASCII fallback for CMD
SYM_ARROW = "\u21b3" if _UNICODE else "->"   # ↳
SYM_STAR  = "\u2605" if _UNICODE else "*"    # ★
SYM_UP    = "\u2191" if _UNICODE else "^"    # ↑
SYM_HEAVY = "\u2501" if _UNICODE else "="   # ━
SYM_LIGHT = "\u2500" if _UNICODE else "-"   # ─

def cls():
    subprocess.run("cls" if os.name == "nt" else "clear", shell=True, check=False)

def print_header(cfg):
    now       = datetime.now().strftime("%Y-%m-%d  %H:%M:%S ET")
    threshold = cfg["price_threshold_dollars"]
    interval  = cfg["scan_interval_seconds"]
    watches   = cfg.get("watchlist", [])
    print(f"{BOLD}{CYAN}{LINES_TO_PRINT*SYM_HEAVY}{RESET}")
    print(f"{BOLD}{CYAN}  FINVIZ NEWS SCANNER{RESET}  {DIM}|  Price ≤ ${threshold:.2f}  |  Scan every {interval}s  |  {now}{RESET}")
    print(f"{BOLD}{CYAN}{LINES_TO_PRINT*SYM_HEAVY}{RESET}")
    print(f"  {DIM}Keywords : {', '.join(cfg['keywords'][:6])}{'...' if len(cfg['keywords'])>6 else ''}{RESET}")
    if watches:
        print(f"  {DIM}Watchlist: {', '.join(watches)}{RESET}")
    print(f"{CYAN}{LINES_TO_PRINT*SYM_LIGHT}{RESET}\n")

# ── Beep: ONE beep per scan, pitched by highest priority ──────────────────────
PRIORITY_ORDER = {"HIGH": 0, "WATCH": 1, "PRICE": 2, "KEYWORD": 3}

def beep_scan(cfg, alerts):
    if not alerts:
        return
    highest = min(alerts, key=lambda a: PRIORITY_ORDER.get(a["priority"], 99))["priority"]
    repeat  = cfg.get("alert_sound_repeat", 3)
    if highest == "HIGH":
        for _ in range(repeat):
            winsound.Beep(1200, 200)
            time.sleep(0.1)
    elif highest == "WATCH":
        winsound.Beep(950, 300)
        time.sleep(0.12)
        winsound.Beep(950, 300)
    elif highest == "PRICE":
        winsound.Beep(800, 300)
        time.sleep(0.12)
        winsound.Beep(800, 300)
    else:
        winsound.Beep(600, 450)

def priority_label(priority):
    if priority == "HIGH":
        return f"{RED}{BOLD}[HIGH {SYM_STAR} ]{RESET}"
    elif priority == "WATCH":
        return f"{MAGENTA}{BOLD}[WATCH  ]{RESET}"
    elif priority == "PRICE":
        return f"{YELLOW}{BOLD}[PRICE {SYM_UP}]{RESET}"
    else:
        return f"{CYAN}[KEYWORD]{RESET}"

# ── Age string → estimated ET datetime ────────────────────────────────────────
# Finviz uses these formats depending on article age:
#   Recent  : "12 min", "2 hours"
#   Today   : "10:35AM" (wall clock, no date)
#   Older   : "Feb-21"  (date only, no time — shown after midnight rollover)
#   Ancient : "1 day"
MONTH_MAP = {
    "jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,
    "jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12
}

def age_to_et(age_str):
    now = datetime.now()
    age = age_str.strip().lower()
    if not age:
        return ""   # blank — caller will show nothing rather than spaces

    # "Feb-21" — date only, no time (older articles after midnight rollover)
    match = re.match(r"([a-z]{3})-(\d{1,2})", age)
    if match:
        mon = MONTH_MAP.get(match.group(1))
        day = int(match.group(2))
        if mon:
            # Use current year; if date is in the future roll back a year
            year = now.year
            try:
                dt = datetime(year, mon, day)
                if dt > now:
                    dt = datetime(year - 1, mon, day)
            except ValueError:
                return age_str
            return dt.strftime("%b %d  --:-- -- ET  ")

    # "10:35AM" — wall clock time, same day
    match = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)?", age)
    if match:
        h, m = int(match.group(1)), int(match.group(2))
        meridiem = match.group(3)
        if meridiem == "pm" and h != 12:
            h += 12
        elif meridiem == "am" and h == 12:
            h = 0
        dt = now.replace(hour=h, minute=m, second=0, microsecond=0)
        return dt.strftime("%b %d  %I:%M %p ET")

    # "27 min"
    match = re.match(r"(\d+)\s*min", age)
    if match:
        dt = now - timedelta(minutes=int(match.group(1)))
        return dt.strftime("%b %d  %I:%M %p ET")

    # "2 hours"
    match = re.match(r"(\d+)\s*hour", age)
    if match:
        dt = now - timedelta(hours=int(match.group(1)))
        return dt.strftime("%b %d  %I:%M %p ET")

    # "1 day"
    match = re.match(r"(\d+)\s*day", age)
    if match:
        dt = now - timedelta(days=int(match.group(1)))
        return dt.strftime("%b %d  --:-- -- ET  ")

    # Unknown format — fixed width so columns don't shift
    return f"{age_str:22s}"

# ── Fetch Finviz news page ─────────────────────────────────────────────────────
def fetch_news(cfg):
    headers = {"User-Agent": cfg["user_agent"]}
    try:
        resp = requests.get(cfg["finviz_news_url"], headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"{RED}  [ERROR] Could not fetch news: {e}{RESET}")
        return None

# ── Parse news rows ────────────────────────────────────────────────────────────
def parse_news_rows(html):
    """
    Returns list of dicts:
      { 'age': str, 'headline': str, 'tickers': [str,...], 'source': str }
    Each row can have multiple ticker badges (up to 9+).
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = []

    news_table = soup.find("table", {"id": "news-table"})
    if not news_table:
        news_table = soup.find("table", class_=lambda c: c and "news" in c.lower())
    if not news_table:
        for t in soup.find_all("table"):
            if t.find("a", class_=lambda c: c and "news" in str(c).lower()):
                news_table = t
                break
    if not news_table:
        return rows

    last_age = ""   # carry forward — Finviz omits time on continuation rows

    for tr in news_table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue

        # Time is ALWAYS in the first <td> on Finviz news rows.
        # Only inspect that cell — never the headline/badge cells.
        # Continuation rows (same timestamp, next ticker) have an empty first td
        # so we carry the last known age forward.
        time_text = ""
        if tds:
            first_txt = tds[0].get_text(strip=True)
            if first_txt and ("-" in first_txt or ":" in first_txt
                    or "am" in first_txt.lower() or "pm" in first_txt.lower()
                    or "hour" in first_txt.lower() or "min" in first_txt.lower()
                    or "day" in first_txt.lower()):
                time_text = first_txt
                last_age  = first_txt          # remember for next continuation row
            else:
                time_text = last_age           # inherit from previous row

        headline = ""
        link_tag = tr.find("a", class_=lambda c: c and "nn-tab-link" in str(c))
        if not link_tag:
            link_tag = tr.find("a", class_=lambda c: c and "news-link" in str(c))
        if not link_tag:
            for a in tr.find_all("a"):
                cls_str = " ".join(a.get("class", []))
                if "ticker" not in cls_str and len(a.get_text(strip=True)) > 15:
                    link_tag = a
                    break
        url = ""
        if link_tag:
            headline = link_tag.get_text(strip=True)
            url      = link_tag.get("href", "")
            # Finviz uses relative URLs — prepend base
            if url and url.startswith("/"):
                url = "https://finviz.com" + url

        tickers = []
        for a in tr.find_all("a"):
            cls_val = " ".join(a.get("class", []))
            txt = a.get_text(strip=True)
            if (txt and txt.isupper() and 1 <= len(txt) <= 5
                    and ("ticker" in cls_val.lower() or "tab-link" not in cls_val.lower())):
                if txt not in tickers and txt != headline:
                    tickers.append(txt)

        source = ""
        badge_div = tr.find("div", class_="news-badges-container")
        if badge_div:
            spans = badge_div.find_all("span")
            if spans:
                source = spans[-1].get_text(strip=True)

        if headline and tickers:
            rows.append({
                "age":      time_text,
                "headline": headline,
                "tickers":  tickers,
                "source":   source,
                "url":      url,
            })

    return rows

# ── Screener price fetch (paginated, pandas-based) ─────────────────────────────
SCREENER_VIEW = "111"
SCREENER_COLS = "0,1,2,65"

def _fetch_screener_page(url, ticker_str, start, headers):
    params = {"v": SCREENER_VIEW, "t": ticker_str, "c": SCREENER_COLS, "r": start}
    try:
        resp   = requests.get(url, headers=headers, params=params, timeout=20)
        resp.raise_for_status()
        tables = pd.read_html(StringIO(resp.text))
    except Exception:
        return None

    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if len(t) == 0:
            continue
        required = ["ticker", "price", "change", "volume"]
        if not all(any(req in c for c in cols) for req in required):
            continue
        if len(t) > 20 or len(t.columns) != 11:
            continue
        if not isinstance(t.iloc[0]["Ticker"], str):
            continue
        return t
    return None

def fetch_ticker_prices(tickers, cfg):
    if not tickers:
        return {}
    prices     = {}
    headers    = {"User-Agent": cfg["user_agent"]}
    url        = cfg["finviz_screener_base_url"]
    ticker_str = ",".join(tickers)
    start = 1
    while True:
        df = _fetch_screener_page(url, ticker_str, start, headers)
        if df is None or len(df) == 0:
            break
        for _, row in df.iterrows():
            ticker = str(row.get("Ticker", "")).strip()
            raw    = row.get("Price", None)
            if ticker:
                try:
                    prices[ticker] = float(str(raw).replace(",", ""))
                except (ValueError, TypeError):
                    prices[ticker] = None
        if len(df) < 20:
            break
        start += 20
        time.sleep(0.5)
    return prices

# ── Keyword matching ───────────────────────────────────────────────────────────
def headline_has_keyword(headline, keywords):
    hl = headline.lower()
    return [kw for kw in keywords if kw.lower() in hl]

# ── JSON Log ───────────────────────────────────────────────────────────────────
JSON_LOG_VERSION = "1.0"

def _json_log_path(cfg):
    name = cfg.get("log_file", "alerts_log.json")
    # Always use .json extension regardless of what config says
    name = os.path.splitext(name)[0] + ".json"
    return os.path.join(SCRIPT_DIR, name)

def load_json_log(cfg):
    """Load existing log or return empty structure."""
    path = _json_log_path(cfg)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Validate basic structure
                if "alerts" in data:
                    return data
        except Exception:
            pass
    return {"version": JSON_LOG_VERSION, "generated": "", "alerts": []}

def save_json_log(cfg, log_data):
    """Write log to disk atomically. Embeds scanner_runtime from cfg each save."""
    path = _json_log_path(cfg)
    log_data["generated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_data["scanner_runtime"] = {
        "price_threshold_dollars": cfg.get("price_threshold_dollars", 10.0),
        "watchlist":               cfg.get("watchlist", []),
        "keywords":                cfg.get("keywords", []),
        "keyword_alert_mode":      cfg.get("keyword_alert_mode", "both"),
        "scan_interval_seconds":   cfg.get("scan_interval_seconds", 120),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)   # Atomic replace — no partial reads from browser

def clear_is_new(log_data):
    """Flip is_new=True → False at the start of each scan cycle."""
    for alert in log_data["alerts"]:
        alert["is_new"] = False

def append_alert_to_log(cfg, log_data, alert):
    """
    Add one alert to the JSON log.
    Enforces max_log_entries rotation (oldest removed first).
    """
    max_entries = cfg.get("max_log_entries", 90)
    entry = {
        "id":           str(uuid.uuid4()),
        "timestamp":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "news_time":    alert.get("news_time", ""),
        "priority":     alert["priority"],
        "ticker":       alert["ticker"],
        "price":        alert.get("price", "N/A"),
        "keywords":     alert.get("keywords", []),
        "headline":     alert["headline"],
        "source":       alert.get("source", ""),
        "url":          alert.get("url", ""),
        "linked_tickers": alert.get("linked_tickers", []),
        "is_new":       True,
        "sms_sent":     False,
    }
    log_data["alerts"].append(entry)
    # Rotate — keep only the most recent max_entries
    if len(log_data["alerts"]) > max_entries:
        log_data["alerts"] = log_data["alerts"][-max_entries:]


# ── Web server — serves the scanner folder over HTTP ─────────────────────────
class _QuietHandler(SimpleHTTPRequestHandler):
    """Serve SCRIPT_DIR silently — no request logs cluttering the console."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=SCRIPT_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass  # Suppress HTTP request logs

    def end_headers(self):
        # Allow local network access (CORS for future React integration)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        super().end_headers()

def _get_local_ip():
    """Best-effort local WiFi IP for display purposes."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "localhost"

def start_web_server(port):
    """Start HTTP server in a daemon thread — dies when scanner exits."""
    try:
        server = HTTPServer(("", port), _QuietHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        return True
    except OSError:
        return False  # Port already in use

# ── Shared single-line alert formatter ────────────────────────────────────────
def print_alert_row(a):
    """Single consistent format used in both NEW ALERTS and Recent Alerts."""
    pl      = priority_label(a["priority"])
    tk      = f"{BOLD}{a['ticker']:<5}{RESET}"
    price   = f"${a.get('price', '?')}"
    # Fixed-width 22-char timestamp so columns always align regardless of format
    raw_ts  = a.get("news_time") or a.get("timestamp") or "?"
    # Strip ANSI from length calc — pad the raw string, then colorize
    news_ts = f"{raw_ts:<22}"
    kws     = ", ".join(a.get("keywords", [])) or ""
    hl      = a["headline"][:48] + ("…" if len(a["headline"]) > 48 else "")
    hl      = f"{hl:<49}"  # pad to fixed width so keywords always start at same column
    kw_str    = f"  {GREEN}{SYM_ARROW} {kws}{RESET}" if kws else ""
    linked    = a.get("linked_tickers", [])
    link_str  = f"  {DIM}[linked: {" ".join(linked)}]{RESET}" if linked else ""
    print(f"  {DIM}{news_ts}{RESET}  {pl}  {tk}  {YELLOW}{price:>8}{RESET}  {hl}{kw_str}{link_str}")

# ── Display rolling window — most recent ON TOP ────────────────────────────────
def display_rolling(rolling, cfg):
    window = cfg.get("rolling_display_window", 20)
    recent = list(rolling)[-window:][::-1]
    print(f"\n{BOLD}{WHITE}  Recent Alerts  (showing {len(recent)} of {len(rolling)} total):{RESET}")
    print(f"  {DIM}{(LINES_TO_PRINT-6)*SYM_LIGHT}{RESET}")
    for a in recent:
        print_alert_row(a)
    print()

# ── Main scan loop ─────────────────────────────────────────────────────────────
def main():
    print(f"{BOLD}{CYAN}Starting Finviz News Scanner...{RESET}")
    print(f"Config: {CONFIG_PATH}\n")

    # ── Start web server ──────────────────────────────────────────────────
    _cfg_init  = load_config()
    _port      = _cfg_init.get("web_server_port", 8765)
    _ok        = start_web_server(_port)
    _local_ip  = _get_local_ip()
    if _ok:
        print(f"{GREEN}  Web server started:{RESET}")
        print(f"  {BOLD}http://localhost:{_port}/alerts.html{RESET}  (this PC)")
        print(f"  {BOLD}http://{_local_ip}:{_port}/alerts.html{RESET}  (local WiFi)")
    else:
        print(f"{YELLOW}  Web server port {_port} in use — view may not work{RESET}")
    print()

    # Key = "TICKER::headline_text" — persists across scans to prevent duplicates
    seen_headlines = set()
    rolling        = deque(maxlen=500)
    scan_count     = 0
    log_data       = None   # Loaded fresh each scan from disk

    while True:
        cfg       = load_config()
        threshold = cfg["price_threshold_dollars"]
        keywords  = cfg["keywords"]
        mode      = cfg.get("keyword_alert_mode", "both")
        watchlist = set(t.upper() for t in cfg.get("watchlist", []))

        # Load JSON log and flip is_new flags from previous scan
        log_data = load_json_log(cfg)
        clear_is_new(log_data)

        cls()
        print_header(cfg)

        scan_count += 1
        print(f"  {DIM}Scan #{scan_count}  |  Fetching news...{RESET}")

        html = fetch_news(cfg)
        if not html:
            time.sleep(cfg["scan_interval_seconds"])
            continue

        rows        = parse_news_rows(html)
        all_tickers = list({t for row in rows for t in row["tickers"]})
        print(f"  {DIM}Parsed {len(rows)} rows  |  {len(all_tickers)} unique tickers  |  Fetching prices...{RESET}")

        prices   = fetch_ticker_prices(all_tickers, cfg)
        resolved = sum(1 for v in prices.values() if v is not None)
        print(f"  {DIM}Prices resolved: {resolved}/{len(all_tickers)}{RESET}\n")

        new_alerts = []

        # Finviz returns rows newest-first — process in reverse (oldest first)
        # so the deque appends oldest→newest, and the rolling display
        # slice+reverse shows newest at the top correctly
        for row in reversed(rows):
            headline    = row["headline"]
            tickers     = row["tickers"]
            source      = row["source"]
            age         = row["age"]
            # Compute news time ONCE per row using the age at parse time
            news_time   = age_to_et(age)
            matched_kws = headline_has_keyword(headline, keywords)

            for ticker in tickers:
                # ── Dedup key: ticker + raw headline text (not computed time)
                # This persists across scans so the same story is never re-alerted
                key = f"{ticker}::{headline}"
                if key in seen_headlines:
                    continue

                price           = prices.get(ticker)
                under_threshold = (price is not None and price <= threshold)
                is_watched      = ticker in watchlist

                priority = None
                if under_threshold and matched_kws:
                    priority = "HIGH"
                elif is_watched:
                    priority = "WATCH"
                elif under_threshold and mode in ("both", "price"):
                    priority = "PRICE"
                elif matched_kws and mode in ("both", "keyword"):
                    priority = "KEYWORD"

                if priority:
                    # ── Output filter: suppress high-priced KEYWORD/WATCH if configured
                    if priority == "KEYWORD" and not cfg.get("output_keyword", True):
                        seen_headlines.add(key)
                        continue
                    if priority == "WATCH" and not cfg.get("output_watch", True):
                        seen_headlines.add(key)
                        continue

                    seen_headlines.add(key)   # Mark as seen — will not re-fire

                    # ── Linked tickers: other tickers on same article that
                    # did not qualify on their own (no alert, context only)
                    linked = [
                        t for t in tickers
                        if t != ticker
                        and t not in watchlist
                        and (prices.get(t) is None or prices.get(t) > threshold)
                    ]

                    alert = {
                        "timestamp":      datetime.now().strftime("%H:%M:%S"),
                        "news_time":      news_time,
                        "priority":       priority,
                        "ticker":         ticker,
                        "price":          f"{price:.2f}" if price is not None else "N/A",
                        "keywords":       matched_kws,
                        "headline":       headline,
                        "source":         source,
                        "age":            age,
                        "linked_tickers": linked,
                        "url":            row.get("url", ""),
                    }
                    rolling.append(alert)
                    append_alert_to_log(cfg, log_data, alert)
                    new_alerts.append(alert)

        # ── Save JSON log after all alerts processed this scan ────────────────
        save_json_log(cfg, log_data)

        # ── Fire ONE beep for the whole scan ──────────────────────────────────
        if new_alerts:
            new_alerts.sort(key=lambda a: PRIORITY_ORDER.get(a["priority"], 99))
            beep_scan(cfg, new_alerts)

            print(f"\n{RED}{BOLD}  *** {len(new_alerts)} NEW ALERT(S) THIS SCAN ***{RESET}")
            print(f"  {DIM}{(LINES_TO_PRINT-6)*SYM_LIGHT}{RESET}")
            for alert in new_alerts:
                print_alert_row(alert)
            print()
        else:
            print(f"  {DIM}No new alerts this scan.{RESET}\n")

        if rolling:
            display_rolling(rolling, cfg)

        # ── Footer with next scan time ────────────────────────────────────────
        interval  = cfg["scan_interval_seconds"]
        next_scan = (datetime.now() + timedelta(seconds=interval)).strftime("%I:%M %p")
        print(f"  {DIM}Next scan in {interval}s  @  {next_scan} ET  |  Edit config.json anytime{RESET}")
        _port2   = cfg.get("web_server_port", 8765)
        log_path = _json_log_path(cfg)
        print(f"  {DIM}Log : {log_path}{RESET}")
        print(f"  {DIM}View: http://localhost:{_port2}/alerts.html{RESET}")
        print(f"{CYAN}{LINES_TO_PRINT*SYM_LIGHT}{RESET}")

        time.sleep(interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}Scanner stopped.{RESET}\n")
        sys.exit(0)