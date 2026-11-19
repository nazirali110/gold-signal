"""XAUUSD M30 signal -> Telegram bot."""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

DATA_URL = "https://biquote.io/api/XAUUSD/ohlc?interval=30m&limit=300"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TEST = os.environ.get("TEST_MESSAGE", "").strip() == "1"
TREND, SL_MULT, RR = 100, 1.5, 1.5
UA = {"User-Agent": "Mozilla/5.0 (gold-signal-bot)"}


def http_json(url, data=None):
    last = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=UA, data=data)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if body else None
        except Exception as ex:
            last = ex
            print("Koshish %d nakam: %s" % (attempt, ex))
            time.sleep(4 * attempt)
    raise last


def ema(v, p):
    k, e, out = 2 / (p + 1), v[0], []
    for i, x in enumerate(v):
        e = x if i == 0 else x * k + e * (1 - k)
        out.append(e)
    return out


def rsi(c, p=14):
    out, g, l = [50.0] * len(c), 0.0, 0.0
    for i in range(1, len(c)):
        d = c[i] - c[i - 1]
        u, w = max(d, 0), max(-d, 0)
        if i <= p:
            g += u / p
            l += w / p
        else:
            g = (g * (p - 1) + u) / p
            l = (l * (p - 1) + w) / p
        if i >= p:
            out[i] = 100.0 if l == 0 else 100 - 100 / (1 + g / l)
    return out


def atr(b, p=14):
    out, a = [0.0] * len(b), 0.0
    for i in range(1, len(b)):
        tr = max(b[i]["high"] - b[i]["low"],
                 abs(b[i]["high"] - b[i - 1]["close"]),
                 abs(b[i]["low"] - b[i - 1]["close"]))
        a = a + tr / p if i <= p else (a * (p - 1) + tr) / p
        out[i] = a
    return out


def post(text, silent=False):
    if not TOKEN or not CHAT_ID:
        print("TOKEN ya CHAT_ID set nahi hai:\n" + text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_notification": silent}
    http_json(url, json.dumps(payload).encode())
    print("Telegram par bhej diya.")


def main():
    data = http_json(DATA_URL)
    bars = [b for b in reversed(data["bars"]) if not b.get("isOpen")]
    if len(bars) < 80:
        print("Data kam hai.")
        return

    c = [b["close"] for b in bars]
    f, s, t = ema(c, 20), ema(c, 50), ema(c, TREND)
    r, a = rsi(c), atr(bars)
    i = len(bars) - 1

    buy = f[i] > s[i] and f[i - 1] <= s[i - 1] and c[i] > t[i] and 50 < r[i] < 70
    sell = f[i] < s[i] and f[i - 1] >= s[i - 1] and c[i] < t[i] and 30 < r[i] < 50

    opened = datetime.fromisoformat(bars[i]["openTime"].replace("Z", "+00:00"))
    closed = opened + timedelta(minutes=30)
    age_min = (datetime.now(timezone.utc) - closed).total_seconds() / 60

    if TEST:
        post("✅ Test message: bot theek chal raha hai. Gold price: %.2f" % c[i])
        return

    if not (buy or sell):
        print("Koi signal nahi. Price %.2f RSI %.1f" % (c[i], r[i]))
        if closed.minute == 0:
            trend = "UPAR (price EMA100 se upar)" if c[i] > t[i] else "NEECHE (price EMA100 se neeche)"
            cross = "EMA20 upar hai EMA50 se" if f[i] > s[i] else "EMA20 neeche hai EMA50 se"
            post(
                "📊 <b>XAUUSD M30 report</b>\n"
                f"Price: <code>{c[i]:.2f}</code>\n"
                f"Trend: {trend}\n"
                f"{cross}\n"
                f"RSI: {r[i]:.1f}\n"
                "Signal: abhi koi nahi, bot nigrani kar raha hai.",
                silent=True,
            )
        return
    if age_min > 25:
        print("Signal purana hai (%.0f min), skip." % age_min)
        return

    e = c[i]
    sl = e - a[i] * SL_MULT if buy else e + a[i] * SL_MULT
    tp = e + (e - sl) * RR if buy else e - (sl - e) * RR
    icon, side = ("🟢", "BUY") if buy else ("🔴", "SELL")
    post(
        f"{icon} <b>XAUUSD M30 — {side}</b>\n"
        f"Entry: <code>{e:.2f}</code>\nStop Loss: <code>{sl:.2f}</code>\nTarget: <code>{tp:.2f}</code>\n"
        f"RSI: {r[i]:.1f}\n"
        f"Candle band hui: {closed.strftime('%d %b %H:%M')} UTC\n"
        f"<i>Entry se bohat door chala gaya ho to trade na lein. Demo par test karein.</i>"
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Error:", ex)
        sys.exit(1)
