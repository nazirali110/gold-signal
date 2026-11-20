"""XAUUSD signal bot -> Telegram.

SAAF MAQSAD: har ghante 2 se 3 signals (HOURLY_MIN aur HOURLY_MAX neeche set hain).
  * Quality signal  : M30 / M15 ke asal setup (EMA cross, pullback, momentum)
  * Scalp signal    : jab ghante mein asal setup na mile, bot trend ki taraf ka chhota
                      signal bana kar ghante ke 2 signal poore karta hai (zyada risky).
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ----------------------------- SETTINGS -----------------------------
HOURLY_MIN = 2     # har ghante kam az kam itne signal (zaroor)
HOURLY_MAX = 3     # har ghante zyada se zyada itne signal
# SL / Target (dollar): M30, M15, Scalp
M30_SL, M30_TP = 2.5, 5.0
M15_SL, M15_TP = 1.5, 3.0
SCALP_SL, SCALP_TP = 1.5, 3.0
TREND = 100
FRESH_MIN = 14.5   # itne minute tak ke naye signal bheje jate hain (bot har 15 minute chalta hai)
# --------------------------------------------------------------------

BASE_URL = "https://biquote.io/api/XAUUSD/ohlc?interval=%s&limit=300"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TEST = os.environ.get("TEST_MESSAGE", "").strip() == "1"
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


def post(text, silent=False):
    if not TOKEN or not CHAT_ID:
        print("TOKEN ya CHAT_ID set nahi hai:\n" + text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
               "disable_notification": silent}
    http_json(url, json.dumps(payload).encode())
    print("Telegram par bhej diya.")


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def session_of(dt):
    """Naam (UTC): Asia 00-08, London 08-13, New York 13-22."""
    h = dt.hour
    if h < 8:
        return "Asia"
    if h < 13:
        return "London"
    if h < 22:
        return "New York"
    return "Off"


def hour_key(closed):
    """Ghante ka group: 14:15, 14:30, 14:45, 15:00 band hone wali candles = ek ghanta."""
    return (closed - timedelta(minutes=1)).replace(minute=0, second=0, microsecond=0)


def side_at(i, bars, f, s, t, r, momentum=False):
    b = bars[i]
    c, h, l = b["close"], b["high"], b["low"]
    o = b.get("open", bars[i - 1]["close"])
    if f[i] > s[i] and f[i - 1] <= s[i - 1] and c > t[i] and 50 < r[i] < 70:
        return "BUY", "EMA cross"
    if f[i] < s[i] and f[i - 1] >= s[i - 1] and c < t[i] and 30 < r[i] < 50:
        return "SELL", "EMA cross"
    if f[i] > s[i] > t[i] and l <= f[i] and c > f[i] and c > o and 45 < r[i] < 75:
        return "BUY", "Trend pullback"
    if f[i] < s[i] < t[i] and h >= f[i] and c < f[i] and c < o and 25 < r[i] < 55:
        return "SELL", "Trend pullback"
    if momentum and i >= 6:
        lo = min(x["low"] for x in bars[i - 6:i])
        hi = max(x["high"] for x in bars[i - 6:i])
        if f[i] < s[i] and c < lo and c < o and 10 < r[i] < 40:
            return "SELL", "Momentum (risky)"
        if f[i] > s[i] and c > hi and c > o and 60 < r[i] < 90:
            return "BUY", "Momentum (risky)"
    return None


def scan(name, interval, minutes, sl, tp, m15=False):
    data = http_json(BASE_URL % interval)
    bars = [b for b in reversed(data["bars"]) if not b.get("isOpen")]
    if len(bars) < 100:
        raise RuntimeError("%s data kam hai (%d)" % (name, len(bars)))
    c = [b["close"] for b in bars]
    f, s, t = ema(c, 20), ema(c, 50), ema(c, TREND)
    r = rsi(c)
    events, info = [], []
    for i in range(80, len(bars)):
        closed = parse_ts(bars[i]["openTime"]) + timedelta(minutes=minutes)
        cur = side_at(i, bars, f, s, t, r, m15)
        prev = side_at(i - 1, bars, f, s, t, r, m15)
        if cur and cur != prev:
            events.append({"tf": name, "closed": closed, "side": cur[0], "kind": cur[1],
                           "entry": c[i], "rsi": r[i], "sl": sl, "tp": tp, "scalp": False})
        o = bars[i].get("open", bars[i - 1]["close"])
        bias = "BUY" if f[i] > s[i] else "SELL"
        agrees = (c[i] > o) if bias == "BUY" else (c[i] < o)
        info.append({"closed": closed, "bias": bias, "agrees": agrees, "entry": c[i], "rsi": r[i]})
    i = len(bars) - 1
    last = {"close": c[i], "f": f[i], "s": s[i], "t": t[i], "rsi": r[i],
            "closed": parse_ts(bars[i]["openTime"]) + timedelta(minutes=minutes)}
    return events, info, last


def build(regular, info15):
    """Har ghante ke liye final signals: asal setup pehle, phir scalp se HOURLY_MIN poora."""
    times = sorted({e["closed"] for e in regular} | {x["closed"] for x in info15})
    by_t_reg, by_t_info = {}, {}
    for e in regular:
        by_t_reg.setdefault(e["closed"], []).append(e)
    for x in info15:
        by_t_info[x["closed"]] = x
    out, count, key = [], 0, None
    for ts in times:
        k = hour_key(ts)
        if k != key:
            key, count = k, 0
        got = False
        for e in sorted(by_t_reg.get(ts, []), key=lambda e: 0 if e["tf"] == "M30" else 1):
            if count < HOURLY_MAX:
                count += 1
                e["n"] = count
                e["session"] = session_of(ts)
                out.append(e)
                got = True
        x = by_t_info.get(ts)
        if x is not None and not got and count < HOURLY_MIN:
            pos = 4 if ts.minute == 0 else ts.minute // 15   # ghante ki 1..4 candle
            bars_left = 4 - pos + 1
            need = HOURLY_MIN - count
            if x["agrees"] or bars_left <= need:
                count += 1
                out.append({"tf": "M15", "closed": ts, "side": x["bias"], "kind": "Scalp",
                            "entry": x["entry"], "rsi": x["rsi"], "sl": SCALP_SL, "tp": SCALP_TP,
                            "scalp": True, "n": count, "session": session_of(ts)})
    return out


def main():
    now = datetime.now(timezone.utc)
    reg30, _, last30 = scan("M30", "30m", 30, M30_SL, M30_TP)
    regular, info15 = list(reg30), []
    try:
        reg15, info15, _ = scan("M15", "15m", 15, M15_SL, M15_TP, m15=True)
        regular += reg15
    except Exception as ex:
        print("M15 data nahi mila, sirf M30 chalega:", ex)
    events = build(regular, info15)

    if TEST:
        post("✅ Test message: bot theek chal raha hai. Gold price: %.2f" % last30["close"])
        return

    sent = 0
    for e in events:
        age = (now - e["closed"]).total_seconds() / 60
        if age > FRESH_MIN or age < 0:
            continue
        buy = e["side"] == "BUY"
        en = e["entry"]
        sl = en - e["sl"] if buy else en + e["sl"]
        tp = en + e["tp"] if buy else en - e["tp"]
        icon = "🟢" if buy else "🔴"
        if e["scalp"]:
            tag = "⚡ Scalp signal (zyada risky, pehle demo par)"
        elif e["tf"] == "M15":
            tag = "⭐ M15 setup (chhota SL/Target)"
        else:
            tag = "⭐ M30 quality signal"
        post(
            f"{icon} <b>XAUUSD {e['tf']} — {e['side']}</b> ({e['kind']})\n"
            f"{tag}\n"
            f"Is ghante ka signal #{e['n']} | Session: {e['session']}\n"
            f"Entry: <code>{en:.2f}</code>\nStop Loss: <code>{sl:.2f}</code>\nTarget: <code>{tp:.2f}</code>\n"
            f"RSI: {e['rsi']:.1f} | Candle band hui {age:.0f} minute pehle\n"
            f"<i>Price entry se door chali gayi ho to trade na lein.</i>"
        )
        sent += 1

    if sent:
        return
    print("Koi naya signal nahi. Price %.2f RSI %.1f" % (last30["close"], last30["rsi"]))
    age30 = (now - last30["closed"]).total_seconds() / 60
    if last30["closed"].minute == 0 and age30 <= FRESH_MIN:  # har ghante ek khamosh report
        trend = "UPAR (price EMA100 se upar)" if last30["close"] > last30["t"] else "NEECHE (price EMA100 se neeche)"
        cross = "EMA20 upar hai EMA50 se" if last30["f"] > last30["s"] else "EMA20 neeche hai EMA50 se"
        post(
            "📊 <b>XAUUSD report</b>\n"
            f"Price: <code>{last30['close']:.2f}</code>\n"
            f"Trend: {trend}\n{cross}\n"
            f"RSI: {last30['rsi']:.1f}\n"
            f"Session: {session_of(now)}\n"
            "Signal: abhi koi naya nahi, bot nigrani kar raha hai.",
            silent=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Error:", ex)
        sys.exit(1)
