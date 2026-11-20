"""XAUUSD signal bot -> Telegram.
Session: Asia / London / New York. M30 pehle (strict), M15 sirf tab jab session mein 2 se kam signal hon.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

BASE_URL = "https://biquote.io/api/XAUUSD/ohlc?interval=%s&limit=300"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TEST = os.environ.get("TEST_MESSAGE", "").strip() == "1"
TREND = 100
QUOTA = 2          # har session mein kam az kam itne signal (M15 se poore kiye jate hain)
# naam, interval, minute, SL (dollar), Target (dollar)
M30 = ("M30", "30m", 30, 2.5, 5.0)
M15 = ("M15", "15m", 15, 1.5, 3.0)
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
    """(naam, shuru) UTC. Asia 00-08, London 08-13, New York 13-22."""
    h = dt.hour
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if h < 8:
        return "Asia", day
    if h < 13:
        return "London", day + timedelta(hours=8)
    if h < 22:
        return "New York", day + timedelta(hours=13)
    return None, None


def side_at(i, bars, f, s, t, r):
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
    return None


def scan(tf):
    """Ek timeframe ke saare signals (purane se naye) aur aakhri halat."""
    name, interval, minutes, sl_pts, tp_pts = tf
    data = http_json(BASE_URL % interval)
    bars = [b for b in reversed(data["bars"]) if not b.get("isOpen")]
    if len(bars) < 100:
        raise RuntimeError("%s data kam hai (%d)" % (name, len(bars)))
    c = [b["close"] for b in bars]
    f, s, t = ema(c, 20), ema(c, 50), ema(c, TREND)
    r = rsi(c)
    events = []
    for i in range(80, len(bars)):
        cur = side_at(i, bars, f, s, t, r)
        prev = side_at(i - 1, bars, f, s, t, r)
        if cur and cur != prev:
            closed = parse_ts(bars[i]["openTime"]) + timedelta(minutes=minutes)
            events.append({"tf": name, "closed": closed, "side": cur[0], "kind": cur[1],
                           "entry": c[i], "rsi": r[i], "sl": sl_pts, "tp": tp_pts})
    i = len(bars) - 1
    last = {"close": c[i], "f": f[i], "s": s[i], "t": t[i], "rsi": r[i],
            "closed": parse_ts(bars[i]["openTime"]) + timedelta(minutes=minutes)}
    return events, last


def apply_quota(events):
    """M30 hamesha qabool. M15 tab qabool jab session mein QUOTA se kam signal hon
    aur pichle 60 minute mein usi taraf ka signal na ho. Har event mein 'ok' aur 'n' lagta hai."""
    events.sort(key=lambda e: (e["closed"], 0 if e["tf"] == "M30" else 1))
    count, key, accepted = 0, None, []
    for e in events:
        sess, start = session_of(e["closed"])
        e["session"] = sess
        if start != key:
            key, count, accepted = start, 0, []
        ok = False
        if e["tf"] == "M30":
            ok = True
        elif sess is not None and count < QUOTA:
            recent_same = any(a["side"] == e["side"] and
                              (e["closed"] - a["closed"]) < timedelta(minutes=60)
                              for a in accepted)
            ok = not recent_same
        e["ok"] = ok
        if ok:
            count += 1
            accepted.append(e)
            e["n"] = count
    return events


def main():
    now = datetime.now(timezone.utc)
    events30, last30 = scan(M30)
    events = list(events30)
    try:
        events15, _ = scan(M15)
        events += events15
    except Exception as ex:
        print("M15 data nahi mila, sirf M30 chalega:", ex)
    events = apply_quota(events)

    if TEST:
        post("✅ Test message: bot theek chal raha hai. Gold price: %.2f" % last30["close"])
        return

    sent = 0
    for e in events:
        age = (now - e["closed"]).total_seconds() / 60
        if not e["ok"] or age > 29 or age < 0:
            continue
        buy = e["side"] == "BUY"
        en = e["entry"]
        sl = en - e["sl"] if buy else en + e["sl"]
        tp = en + e["tp"] if buy else en - e["tp"]
        icon = "🟢" if buy else "🔴"
        warn = "\n⚠️ M15 signal: chhota SL/Target, M30 se kam pakka." if e["tf"] == "M15" else ""
        post(
            f"{icon} <b>XAUUSD {e['tf']} — {e['side']}</b> ({e['kind']})\n"
            f"Session: {e['session'] or 'Off'}, signal #{e.get('n', '-')}\n"
            f"Entry: <code>{en:.2f}</code>\nStop Loss: <code>{sl:.2f}</code>\nTarget: <code>{tp:.2f}</code>\n"
            f"RSI: {e['rsi']:.1f}\n"
            f"Candle band hui: {e['closed'].strftime('%d %b %H:%M')} UTC"
            f"{warn}\n"
            f"<i>Entry se bohat door chala gaya ho to trade na lein. Demo par test karein.</i>"
        )
        sent += 1

    if sent:
        return
    print("Koi naya signal nahi. Price %.2f RSI %.1f" % (last30["close"], last30["rsi"]))
    if last30["closed"].minute == 0:  # har ghante ek khamosh report
        sess, start = session_of(now)
        n = sum(1 for e in events if e["ok"] and e["session"] == sess and session_of(e["closed"])[1] == start)
        sess_line = ("Session: %s, ab tak %d signal (maqsad %d)" % (sess, n, QUOTA)) if sess else "Session: band (22:00 se 24:00 UTC)"
        trend = "UPAR (price EMA100 se upar)" if last30["close"] > last30["t"] else "NEECHE (price EMA100 se neeche)"
        cross = "EMA20 upar hai EMA50 se" if last30["f"] > last30["s"] else "EMA20 neeche hai EMA50 se"
        note = ""
        if last30["rsi"] < 25:
            note = "\n⚠️ RSI bohat neeche (oversold): naya SELL risky, bounce ke baad pullback ka intezar."
        elif last30["rsi"] > 75:
            note = "\n⚠️ RSI bohat upar (overbought): naya BUY risky, giravat ke baad pullback ka intezar."
        post(
            "📊 <b>XAUUSD report</b>\n"
            f"Price: <code>{last30['close']:.2f}</code>\n"
            f"Trend: {trend}\n{cross}\n"
            f"RSI: {last30['rsi']:.1f}\n"
            f"{sess_line}\n"
            "Signal: abhi koi nahi, bot nigrani kar raha hai."
            f"{note}",
            silent=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Error:", ex)
        sys.exit(1)
