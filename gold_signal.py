"""XAUUSD signal bot -> Telegram.

NEW YORK SESSION (13:00-22:00 UTC = Pakistan 6 pm se 3 am):
    zyada se zyada 2 BADE (lambay) signals (SL 10 dollar, TP1, TP2)
    + CHHOTE signals (M30/M15 setup aur scalp) bhi, jaise baaqi sessions mein.
    Bade signals pehle sakht filters se, 2 na bane to NY_RELAX_HOUR ke baad thora dheela.
BAAQI SESSIONS (Asia, London, Off):
    M30 / M15 ke setup + har ghante 2 se 4 SCALP/quality (chhote) signals.
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

# ----------------------------- SETTINGS -----------------------------
# New York quality signals (dollar mein)
NY_MAX_SIGNALS = 2     # NY session mein zyada se zyada itne bade signal
NY_SL = 10.0           # Stop Loss
NY_TP1 = 7.5           # Target 1 (5 se 10 ke beech)
NY_TP2 = 15.0          # Target 2 (10 se 20 ke beech)
NY_GAP_MIN = 45        # do NY signals ke beech kam az kam itne minute
NY_RELAX_HOUR = 16     # is UTC ghante (Pakistan 9 pm) ke baad, agar 2 signal na bane to filter thora dheela
# Chhote signals (har session mein)
HOURLY_MIN = 2         # har ghante kam az kam itne signal
HOURLY_MAX = 4         # har ghante zyada se zyada itne signal (M30 quality is hisaab se bahar hain)
M30_SL, M30_TP = 2.5, 5.0
M15_SL, M15_TP = 1.5, 3.0
M5_SL, M5_TP = 1.0, 2.0
M1_SL, M1_TP = 0.6, 1.2
SCALP_SL, SCALP_TP = 1.5, 3.0
USE_M5 = False         # M5 ke signal (cron-job.org har 5 minute par hona chahiye)
USE_M1 = False         # M1: bohat shor, har 1 minute par chalana parta hai. Abhi band.
TREND = 100
RUN_EVERY_MIN = 15     # cron-job.org bot ko kitne minute baad chalata hai (5 ya 15)
FRESH_MIN = RUN_EVERY_MIN - 0.1   # itne minute tak ke naye signal bheje jate hain
WAIT_SECONDS = 75      # candle ka data aane tak intezar
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


def sess_close(ts):
    """Candle jis session mein bani, uska naam (band hone ke waqt se ek minute pehle)."""
    return session_of(ts - timedelta(minutes=1))


def hour_key(closed):
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


def scan_h1():
    """H1 trend: har band H1 candle par (band hone ka waqt, EMA20 > EMA50 ?)."""
    data = http_json(BASE_URL % "1h")
    bars = [b for b in reversed(data["bars"]) if not b.get("isOpen")]
    c = [b["close"] for b in bars]
    f, s = ema(c, 20), ema(c, 50)
    return [(parse_ts(b["openTime"]) + timedelta(hours=1), f[i] > s[i]) for i, b in enumerate(bars)]


def ny_quality(regular, h1):
    """New York ke bade (lambay) signal, zyada se zyada NY_MAX_SIGNALS.
    Pehle STRICT: M30 setup + H1 trend + RSI band.
    Agar NY_RELAX_HOUR tak 2 na bane to RELAXED: M30/M15 koi bhi setup + H1 trend (RSI 30 se 70)."""
    out, key, accepted = [], None, []
    cands = [e for e in regular if e["tf"] in ("M30", "M15")]
    for e in sorted(cands, key=lambda e: e["closed"]):
        if sess_close(e["closed"]) != "New York":
            continue
        up = None
        for ts, is_up in h1:                      # sab se taaza band H1 jo is waqt tak band ho chuki
            if ts <= e["closed"]:
                up = is_up
        if up is None or (e["side"] == "BUY") != up:
            continue                              # H1 trend ke khilaf signal nahi
        k = (e["closed"] - timedelta(minutes=1)).replace(hour=13, minute=0, second=0, microsecond=0)
        if k != key:
            key, accepted = k, []
        if len(accepted) >= NY_MAX_SIGNALS:
            continue
        if accepted and (e["closed"] - accepted[-1]["closed"]) < timedelta(minutes=NY_GAP_MIN):
            continue
        rsi_ok = (45 < e["rsi"] < 65) if e["side"] == "BUY" else (35 < e["rsi"] < 55)
        strict = e["tf"] == "M30" and e["kind"] in ("EMA cross", "Trend pullback") and rsi_ok
        if not strict:
            late = (e["closed"] - timedelta(minutes=1)).hour >= NY_RELAX_HOUR
            if not (late and 30 < e["rsi"] < 70):
                continue
        e = dict(e, ny=True, relaxed=not strict, n=len(accepted) + 1, session="New York")
        accepted.append(e)
        out.append(e)
    return out


def build(regular, info15, big_keys=frozenset()):
    """Chhote signals (har session mein): asal setup pehle, phir scalp se HOURLY_MIN poora.
    big_keys: wo NY bade signals jo alag se bheje jate hain (dobara nahi bheje jate)."""
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
        order = {"M30": 0, "M15": 1, "M5": 2, "M1": 3}
        for e in sorted(by_t_reg.get(ts, []), key=lambda e: order.get(e["tf"], 9)):
            if (e["tf"], e["closed"], e["side"]) in big_keys:
                count += 1               # ye NY ka bada signal hai, alag se jata hai
                got = True
                continue
            if e["tf"] == "M30" or count < HOURLY_MAX:
                count += 1
                e = dict(e, n=count, session=sess_close(ts), ny=False)
                out.append(e)
                got = True
        x = by_t_info.get(ts)
        if x is not None and not got and count < HOURLY_MIN:
            pos = 4 if ts.minute == 0 else ts.minute // 15
            bars_left = 4 - pos + 1
            need = HOURLY_MIN - count
            if x["agrees"] or bars_left <= need:
                count += 1
                out.append({"tf": "M15", "closed": ts, "side": x["bias"], "kind": "Scalp",
                            "entry": x["entry"], "rsi": x["rsi"], "sl": SCALP_SL, "tp": SCALP_TP,
                            "scalp": True, "n": count, "session": sess_close(ts), "ny": False})
    return out


def fmt_signal(e, now):
    age = (now - e["closed"]).total_seconds() / 60
    buy = e["side"] == "BUY"
    en = e["entry"]
    icon = "🟢" if buy else "🔴"
    sgn = 1 if buy else -1
    if e["ny"]:
        tag = ("⭐ NEW YORK signal (filter thora dheela, TP1 par jaldi nikalein)" if e.get("relaxed")
               else "⭐⭐ NEW YORK QUALITY signal")
        return (
            f"{icon} <b>XAUUSD {e['tf']} — {e['side']}</b> ({e['kind']})\n"
            f"{tag} #{e['n']}/{NY_MAX_SIGNALS}\n"
            f"Entry: <code>{en:.2f}</code>\n"
            f"Stop Loss: <code>{en - sgn * NY_SL:.2f}</code> ({NY_SL:g}$)\n"
            f"TP1: <code>{en + sgn * NY_TP1:.2f}</code> ({NY_TP1:g}$)\n"
            f"TP2: <code>{en + sgn * NY_TP2:.2f}</code> ({NY_TP2:g}$)\n"
            f"RSI: {e['rsi']:.1f} | H1 trend ke saath | Candle band hui {age:.0f} minute pehle\n"
            f"<i>TP1 lagne par aadha band karein aur SL entry par le aayein. "
            f"Price entry se door chali gayi ho to trade na lein.</i>"
        ), age
    sl = en - sgn * e["sl"]
    tp = en + sgn * e["tp"]
    if e["scalp"]:
        tag = "⚡ Scalp signal (zyada risky, pehle demo par)"
    elif e["tf"] == "M30":
        tag = "⭐ M30 setup"
    else:
        tag = "⭐ %s setup (chhota SL/Target, zyada risky)" % e["tf"]
    return (
        f"{icon} <b>XAUUSD {e['tf']} — {e['side']}</b> ({e['kind']})\n"
        f"{tag}\n"
        f"Is ghante ka signal #{e['n']} | Session: {e['session']}\n"
        f"Entry: <code>{en:.2f}</code>\nStop Loss: <code>{sl:.2f}</code>\nTarget: <code>{tp:.2f}</code>\n"
        f"RSI: {e['rsi']:.1f} | Candle band hui {age:.0f} minute pehle\n"
        f"<i>Price entry se door chali gayi ho to trade na lein.</i>"
    ), age


def main():
    if not TEST:
        time.sleep(WAIT_SECONDS)
    now = datetime.now(timezone.utc)
    reg30, _, last30 = scan("M30", "30m", 30, M30_SL, M30_TP)
    regular, info15 = list(reg30), []
    try:
        reg15, info15, _ = scan("M15", "15m", 15, M15_SL, M15_TP, m15=True)
        regular += reg15
    except Exception as ex:
        print("M15 data nahi mila, sirf M30 chalega:", ex)
    extra = []
    if USE_M5:
        extra.append(("M5", "5m", 5, M5_SL, M5_TP))
    if USE_M1:
        extra.append(("M1", "1m", 1, M1_SL, M1_TP))
    for nm, iv, mins, sl_, tp_ in extra:
        try:
            ev, _, _ = scan(nm, iv, mins, sl_, tp_, m15=True)
            regular += ev
        except Exception as ex:
            print("%s data nahi mila, chhor diya: %s" % (nm, ex))
    try:
        h1 = scan_h1()
    except Exception as ex:
        print("H1 data nahi mila, NY signal nahi banenge:", ex)
        h1 = []
    big = ny_quality(regular, h1)
    big_keys = {(e["tf"], e["closed"], e["side"]) for e in big}
    events = build(regular, info15, big_keys) + big

    if TEST:
        post("✅ Test message: bot theek chal raha hai. Gold price: %.2f" % last30["close"])
        return

    sent = 0
    for e in sorted(events, key=lambda e: e["closed"]):
        age = (now - e["closed"]).total_seconds() / 60
        if age > FRESH_MIN or age < 0:
            continue
        text, _ = fmt_signal(e, now)
        post(text)
        sent += 1

    if sent:
        return
    print("Koi naya signal nahi. Price %.2f RSI %.1f" % (last30["close"], last30["rsi"]))
    age30 = (now - last30["closed"]).total_seconds() / 60
    if last30["closed"].minute == 0 and age30 <= FRESH_MIN:  # har ghante ek khamosh report
        sess = sess_close(now + timedelta(minutes=1))
        trend = "UPAR (price EMA100 se upar)" if last30["close"] > last30["t"] else "NEECHE (price EMA100 se neeche)"
        cross = "EMA20 upar hai EMA50 se" if last30["f"] > last30["s"] else "EMA20 neeche hai EMA50 se"
        ny_line = ""
        if sess == "New York":
            start = now.replace(hour=13, minute=0, second=0, microsecond=0)
            n = sum(1 for e in events if e["ny"] and e["closed"] > start)
            ny_line = f"NY bade signal ab tak: {n}/{NY_MAX_SIGNALS}\n"
        post(
            "📊 <b>XAUUSD report</b>\n"
            f"Price: <code>{last30['close']:.2f}</code>\n"
            f"Trend: {trend}\n{cross}\n"
            f"RSI: {last30['rsi']:.1f}\n"
            f"Session: {sess}\n{ny_line}"
            "Signal: abhi koi naya nahi, bot nigrani kar raha hai.",
            silent=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Error:", ex)
        sys.exit(1)
