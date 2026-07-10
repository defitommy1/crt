"""
CRT (Candle Range Theory) Daily Scanner — v3
Adds: volume filter, signal history, interactive Telegram buttons,
heartbeat, and open subscription (anyone who messages the bot gets alerts).
"""

import os
import json
import time
import requests
from datetime import datetime, timezone

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

WATCHLIST = {
    "Forex Majors": [
        "EUR/USD", "GBP/USD", "USD/JPY", "USD/CHF",
        "USD/CAD", "AUD/USD", "NZD/USD",
    ],
    "Indices": [
        "SPX", "NDX", "DJI", "DAX", "UK100", "JP225",
    ],
    "Crypto": [
        "BTC/USD", "ETH/USD", "SOL/USD", "XRP/USD",
    ],
    "Metals": [
        "XAU/USD",
    ],
}

TWELVE_DATA_URL = "https://api.twelvedata.com/time_series"
TG_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"
HISTORY_FILE = "history.json"


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            h = json.load(f)
    else:
        h = {"last_update_id": 0, "signals": []}
    if "subscribers" not in h:
        h["subscribers"] = [TELEGRAM_CHAT_ID]
    if TELEGRAM_CHAT_ID not in h["subscribers"]:
        h["subscribers"].append(TELEGRAM_CHAT_ID)
    return h


def save_history(history):
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def process_telegram_updates(history):
    offset = history["last_update_id"] + 1
    resp = requests.get(f"{TG_API}/getUpdates", params={"offset": offset, "timeout": 5}, timeout=15)
    data = resp.json()

    if not data.get("ok"):
        return history

    for update in data.get("result", []):
        history["last_update_id"] = max(history["last_update_id"], update["update_id"])

        msg = update.get("message")
        if msg and msg.get("text", "").strip().lower() == "/start":
            chat_id = str(msg["chat"]["id"])
            if chat_id not in history["subscribers"]:
                history["subscribers"].append(chat_id)
                requests.post(f"{TG_API}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "✅ You're subscribed to CRT daily signal alerts.",
                }, timeout=10)
            continue

        cq = update.get("callback_query")
        if not cq:
            continue

        action, signal_id = cq["data"].split(":", 1)
        for sig in history["signals"]:
            if sig["id"] == signal_id:
                sig["status"] = "watching" if action == "watch" else "ignored"

        requests.post(f"{TG_API}/answerCallbackQuery", json={
            "callback_query_id": cq["id"],
            "text": f"Marked as {'Watching' if action == 'watch' else 'Ignored'}",
        }, timeout=10)

    return history


def fetch_daily_candles(symbol: str, outputsize: int = 6):
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": outputsize,
        "apikey": TWELVE_DATA_API_KEY,
    }
    resp = requests.get(TWELVE_DATA_URL, params=params, timeout=20)
    data = resp.json()

    if "values" not in data:
        print(f"  [warn] no data for {symbol}: {data.get('message', data)}")
        return None

    candles = list(reversed(data["values"]))
    parsed = []
    for c in candles:
        parsed.append({
            "datetime": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]) if c.get("volume") not in (None, "") else None,
        })
    return parsed


def detect_crt(candles):
    if not candles or len(candles) < 2:
        return None
    prev, curr = candles[-2], candles[-1]
    bullish = curr["low"] < prev["low"] and curr["close"] > prev["low"]
    bearish = curr["high"] > prev["high"] and curr["close"] < prev["high"]
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    return None


def passes_volume_filter(candles, min_ratio: float = 1.1):
    recent = candles[-5:]
    volumes = [c["volume"] for c in recent if c["volume"] is not None]
    if len(volumes) < len(recent):
        return True

    curr_vol = volumes[-1]
    avg_prior = sum(volumes[:-1]) / len(volumes[:-1])
    if avg_prior == 0:
        return True
    return curr_vol >= avg_prior * min_ratio


def get_ai_summary(symbol, direction, prev, curr):
    if not ANTHROPIC_API_KEY:
        return None
    prompt = (
        f"A Candle Range Theory (CRT) {direction} signal just fired on {symbol} "
        f"(daily timeframe). Previous candle: O{prev['open']} H{prev['high']} "
        f"L{prev['low']} C{prev['close']}. Current candle: O{curr['open']} "
        f"H{curr['high']} L{curr['low']} C{curr['close']}. "
        f"In 2-3 sentences, explain what this means for a trader in plain "
        f"English. No disclaimers, no preamble."
    )
    try:
        resp = requests.post(
            CLAUDE_URL,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-6",
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        return resp.json()["content"][0]["text"].strip()
    except Exception:
        return None


def send_telegram(text, subscribers, signal_id=None):
    payload_base = {
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if signal_id:
        payload_base["reply_markup"] = json.dumps({
            "inline_keyboard": [[
                {"text": "✅ Watching", "callback_data": f"watch:{signal_id}"},
                {"text": "❌ Ignore", "callback_data": f"ignore:{signal_id}"},
            ]]
        })
    for chat_id in subscribers:
        payload = dict(payload_base, chat_id=chat_id)
        requests.post(f"{TG_API}/sendMessage", json=payload, timeout=20)


def main():
    print("Starting CRT daily scan...")
    history = load_history()
    history = process_telegram_updates(history)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    signals_found = []

    for category, symbols in WATCHLIST.items():
        for symbol in symbols:
            print(f"Checking {symbol}...")
            candles = fetch_daily_candles(symbol)
            time.sleep(1)
            if not candles:
                continue

            direction = detect_crt(candles)
            if not direction:
                continue

            if not passes_volume_filter(candles):
                print(f"  {symbol}: CRT pattern found but filtered out by volume.")
                continue

            prev, curr = candles[-2], candles[-1]
            summary = get_ai_summary(symbol, direction, prev, curr)
            if not summary:
                summary = (
                    f"Swept {'low' if direction == 'bullish' else 'high'} of "
                    f"prior candle and closed back inside range at {curr['close']}."
                )

            signal_id = f"{today}-{symbol.replace('/', '')}-{direction}"
            signals_found.append({
                "id": signal_id, "category": category, "symbol": symbol,
                "direction": direction, "summary": summary,
            })
            history["signals"].append({
                "id": signal_id, "date": today, "symbol": symbol,
                "category": category, "direction": direction, "status": "pending",
            })

    if not signals_found:
        send_telegram("📊 CRT Daily Scan: No CRT signals today across watchlist.", history["subscribers"])
    else:
        for sig in signals_found:
            arrow = "🟢 Bullish" if sig["direction"] == "bullish" else "🔴 Bearish"
            msg = (
                f"📊 <b>{sig['symbol']}</b> ({sig['category']}) — {arrow} CRT\n\n"
                f"{sig['summary']}"
            )
            send_telegram(msg, history["subscribers"], signal_id=sig["id"])
            time.sleep(1)

    history["signals"] = [
        s for s in history["signals"]
        if (datetime.now(timezone.utc) - datetime.strptime(s["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)).days <= 60
    ]
    save_history(history)
    print(f"Done. {len(signals_found)} signal(s) sent.")


if __name__ == "__main__":
    main()
