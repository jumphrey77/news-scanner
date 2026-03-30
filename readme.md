# Finviz News Scanner

A real-time news scanner that monitors Finviz for breaking stories on stocks matching your price threshold, watchlist, and keywords. Alerts fire in the terminal with audio beeps, log to a JSON file, and display in a live browser dashboard.

---

## Features

- Scans Finviz news on a configurable interval
- Four priority alert levels with distinct audio beeps
- Watchlist — always alert on specific tickers regardless of price
- Keyword matching — partnership, FDA approval, merger, deal, etc.
- Linked ticker detection — shows co-mentioned tickers in the same article
- JSON log — single source of truth, auto-rotates at configurable max entries
- Live browser dashboard — auto-refreshes, dark/light theme, filter by priority
- Built-in HTTP server — accessible from any device on your local WiFi
- Hot-reload config — change any setting while the scanner is running

---

## Requirements

- Python 3.10+
- Windows (uses `winsound` for audio alerts)

Install dependencies:

```powershell
pip install requests beautifulsoup4 pandas lxml
```

---

## Quick Start

```powershell
cd news_scanner
python finviz_scanner.py
```

On first run, Windows Firewall will prompt to allow Python network access. Allow on **Private networks** at minimum. This enables the browser dashboard on your local WiFi.

Open the dashboard at:
```
http://localhost:8765/alerts.html
```

The URL is also printed in the terminal on every scan.

---

## File Structure

```
news_scanner/
  ├── finviz_scanner.py    # Main scanner
  ├── config.json          # Configuration (edit anytime — hot reloaded)
  ├── alerts.html          # Browser dashboard (open in browser)
  ├── alerts_log.json      # Live JSON log (written each scan)
  └── README.md            # This file
```

---

## Alert Priority Levels

Alerts fire in descending priority order. Each scan produces **one beep** pitched to the highest priority found.

| Priority | Condition | Beep | Color |
|---|---|---|---|
| `HIGH ★` | Ticker under price threshold **AND** keyword matched | 3× high beeps | Red |
| `WATCH` | Ticker is in your watchlist (any price) | 2× medium beeps | Magenta |
| `PRICE ↑` | Ticker under price threshold, no keyword | 2× low beeps | Yellow |
| `KEYWORD` | Keyword matched, ticker above threshold | 1× soft beep | Cyan |

---

## Console Output Format

Each alert row is displayed as:

```
  Feb 22  01:27 AM ET  [HIGH ★ ]  RXT    $  1.12  Rackspace partners with Palantir…  ↳ partnership  [linked: PLTR]
```

Columns in order: **news time**, **priority**, **ticker**, **price**, **headline**, **keywords**, **linked tickers**

---

## Browser Dashboard (`alerts.html`)

The dashboard reads `alerts_log.json` automatically on each refresh.

- **Auto-refreshes** every 30 seconds
- **Priority filter buttons** — toggle HIGH / WATCH / PRICE / KEYWORD on and off
- **`is_new` highlighting** — new alerts glow amber for one scan cycle
- **Clickable headlines** — opens the original article in a new tab
- **Keyword pills** — matched keywords shown as green badges
- **Linked tickers** — co-mentioned tickers shown as dim badges
- **Dark / Light theme** — toggle button, preference saved in browser
- **Responsive** — readable on phone over local WiFi

**Local WiFi access:** The scanner prints your network IP on startup. Open `http://YOUR-IP:8765/alerts.html` on any device on the same network.

---

## Configuration (`config.json`)

All settings hot-reload — no restart needed. Edit and save while the scanner is running.

### Scanning

| Key | Default | Description |
|---|---|---|
| `scan_interval_seconds` | `120` | How often to scan Finviz news (seconds) |
| `news_max_age_hours` | `4` | Ignore articles older than this many hours |
| `finviz_news_url` | Finviz URL | URL to scrape |

### Alert Filtering

| Key | Default | Description |
|---|---|---|
| `price_threshold_dollars` | `10.0` | Max price for PRICE and HIGH alerts |
| `keywords` | See below | List of keywords to match in headlines (case-insensitive) |
| `watchlist` | `["RXT","NVDA","PLTR"]` | Tickers that always alert regardless of price |
| `keyword_alert_mode` | `"both"` | `"both"` = PRICE+KEYWORD alerts, `"price"` = price only, `"keyword"` = keyword only |

### Output Control

| Key | Default | Description |
|---|---|---|
| `output_keyword` | `true` | Show KEYWORD alerts in console and rolling display |
| `output_watch` | `true` | Show WATCH alerts in console and rolling display |
| `rolling_display_window` | `20` | Number of recent alerts shown in the rolling section |

> **Note:** Setting `output_keyword` or `output_watch` to `false` suppresses those alerts from display but still marks them as seen so they do not re-fire if re-enabled.

### Volume Filters

| Key | Default | Description |
|---|---|---|
| `min_avg_volume` | `500000` | Minimum average daily volume |
| `min_relative_volume` | `2.0` | Minimum relative volume |
| `max_float_million` | `100` | Maximum float in millions |

### Audio

| Key | Default | Description |
|---|---|---|
| `alert_sound_repeat` | `3` | Number of beeps for HIGH alerts |

### Logging

| Key | Default | Description |
|---|---|---|
| `log_file` | `"alerts_log.json"` | JSON log filename |
| `max_log_entries` | `90` | Maximum alerts to keep — oldest removed first when exceeded |

### Web Server

| Key | Default | Description |
|---|---|---|
| `web_server_port` | `8765` | Port for the built-in HTTP server |

### Default Keywords

```json
[
  "partnership", "partners", "fda approval", "fda clearance",
  "merger", "acquisition", "contract awarded", "clinical trial",
  "breakthrough", "exclusive", "deal", "collaboration",
  "license agreement", "strategic"
]
```

---

## JSON Log Schema (`alerts_log.json`)

Written after every scan. The browser dashboard reads this file directly.

```json
{
  "version": "1.0",
  "generated": "2026-02-22 01:27:00",
  "alerts": [
    {
      "id":             "uuid-v4",
      "timestamp":      "2026-02-22 01:27:00",
      "news_time":      "Feb 22  01:27 AM ET",
      "priority":       "HIGH",
      "ticker":         "RXT",
      "price":          "1.12",
      "keywords":       ["partnership"],
      "headline":       "Rackspace partners with Palantir...",
      "source":         "Benzinga",
      "url":            "https://finviz.com/news/...",
      "linked_tickers": ["PLTR"],
      "is_new":         true,
      "sms_sent":       false
    }
  ]
}
```

### Field Reference

| Field | Description |
|---|---|
| `id` | UUID — unique per alert, stable across page refreshes |
| `timestamp` | Wall clock time the alert was generated |
| `news_time` | Estimated ET time parsed from Finviz article age |
| `priority` | `HIGH`, `WATCH`, `PRICE`, or `KEYWORD` |
| `ticker` | Stock ticker symbol |
| `price` | Price at time of scan, or `N/A` if unavailable |
| `keywords` | Keywords matched in the headline |
| `headline` | Full article headline |
| `source` | News source (Benzinga, TheStreet, etc.) |
| `url` | Link to the original article on Finviz |
| `linked_tickers` | Other tickers in the same article that did not trigger their own alert |
| `is_new` | `true` for one scan cycle after the alert fires, then flips to `false` |
| `sms_sent` | Reserved for Twilio SMS feature — `false` until configured |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SCANNER_UNICODE` | `1` | Set to `0` to force ASCII symbols in CMD terminals |

---

## Linked Tickers

When an article mentions multiple tickers, the one that qualifies fires an alert. The others appear as `linked_tickers` — shown inline in the console and as dim badges in the browser. No separate alert fires for linked tickers.

Example: An article mentions NNVC ($1.02), NFLX ($78), and WBD ($28). With a $5 threshold, only NNVC triggers. NFLX and WBD appear as linked context:

```
  Feb 22  01:27 AM ET  [PRICE ↑]  NNVC   $  1.02  Trump targets Netflix board…  [linked: NFLX WBD]
```

---

## Deduplication

Each alert is keyed on `TICKER::headline`. Once seen, that combination never re-fires within a session. If you raise the price threshold mid-session, newly qualifying articles appear as `is_new: true` on the next scan.

---

## Planned Features

- SMS alerts via Twilio (`sms_util.py`)
- React config editor page in momentum-dashboard
- Scheduled start/stop times (`run_start`, `run_stop` in config)
- Setup/install script for new machine deployment

---

## Part of TradingScripts

This scanner is part of a broader trading tools project including a momentum dashboard, signal scanner, simulator, and React frontend. See the project root README for the full architecture.
