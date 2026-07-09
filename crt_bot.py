"""
CRT (Candle Range Theory) Daily Scanner
----------------------------------------
Checks the most recently CLOSED daily candle for every symbol in WATCHLIST.
A "CRT signal" fires when that candle:
  - Bullish CRT: wicks BELOW the previous candle's low, but CLOSES back above it
                 (liquidity sweep of the low, reversal up)
  - Bearish CRT: wicks ABOVE the previous candle's high, but CLOSES back below it
                 (liquidity sweep of the high, reversal down)

For every symbol that fires, it asks Claude for a 2-3 sentence plain-English
summary of what happened, then sends everything to you on Telegram.
"""

import os
import time
import requests

TWELVE_DATA_API_KEY = os.environ["TWELVE_DATA_API_KEY"]
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

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
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
CLAUDE_URL = "https://api.anthropic.com/v1/messages"


def fetch_daily_candles(symbol: str, outputsize: int = 3):
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

    candles = data["values"]
    candles = list(reversed(candles))
    parsed = []
    for c in candles:
        parsed.append({
            "datetime": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
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


def get_ai_summary(symbol: str, direction: str, prev, curr):
    prompt = (
        f"A Candle Range Theory (CRT) {direction} signal just fired on {symbol} "
        f"(daily timeframe). Previous candle: O{prev['open']} H{prev['high']} "
        f"L{prev['low']} C{prev['close']}. Current candle: O{curr['open']} "
        f"H{curr['high']} L{curr['low']} C{curr['close']}. "
        f"In 2-3 sentences, explain what this means for a trader in plain "
        f"English (liquidity swept, reversal direction, what to watch next). "
        f"No disclaimers, no preamble, just the explanation."
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
        data = resp.json()
        return data["content"][0]["text"].strip()
    except Exception as e:
        return f"(AI summary unavailable: {e})"


def send_telegram(text: str):
    requests.post(
        TELEGRAM_URL,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )


def main():
    print("Starting CRT daily scan...")
    signals_found = []

    for category, symbols in WATCHLIST.items():
        for symbol in symbols:
            print(f"Checking {symbol}...")
            candles = fetch_daily_candles(symbol)
            time.sleep(1)

            if not candles:
                continue

            direction = detect_crt(candles)
            if direction:
                prev, curr = candles[-2], candles[-1]
                summary = get_ai_summary(symbol, direction, prev, curr)
                signals_found.append({
                    "category": category,
                    "symbol": symbol,
                    "direction": direction,
                    "summary": summary,
                    "date": curr["datetime"],
                })

    if not signals_found:
        send_telegram("📊 CRT Daily Scan: No CRT signals today across watchlist.")
        print("No signals found.")
        return

    message_lines = ["📊 <b>CRT Daily Scan Results</b>\n"]
    for sig in signals_found:
        arrow = "🟢 Bullish" if sig["direction"] == "bullish" else "🔴 Bearish"
        message_lines.append(
            f"<b>{sig['symbol']}</b> ({sig['category']}) — {arrow} CRT\n"
            f"{sig['summary']}\n"
        )

    full_message = "\n".join(message_lines)

    if len(full_message) <= 4000:
        send_telegram(full_message)
    else:
        chunk = ""
        for line in message_lines:
            if len(chunk) + len(line) > 3800:
                send_telegram(chunk)
                chunk = ""
            chunk += line + "\n"
        if chunk:
            send_telegram(chunk)

    print(f"Sent {len(signals_found)} signal(s) to Telegram.")


if __name__ == "__main__":
    main()
