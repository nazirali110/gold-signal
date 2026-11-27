"""XAUUSD signal bot -> Telegram  (version 2)

BADA signal  : target 10-15$  -> PEHLE SE bheja jata hai: "yahan BUY/SELL LIMIT laga lo"
CHHOTA signal: SCALP target 5$ -> candle band hone par, "abhi lagao"
Har signal par likha hota hai ke bada hai ya chhota.
Signal ka natija (profit / loss / fill nahi hua) bhi Telegram par aata hai.
Data: biquote, wo band ho to Yahoo (backup).
"""
import json
import os
import sys
import time
import urllib.request
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import datetime, timedelta, timezone

# ============================== SETTINGS ==============================
# --- BADA signal (limit order, pehle se) ---
BIG_SL, BIG_TP1, BIG_TP2 = 10.0, 10.0, 15.0   # dollar
BIG_VALID_H = 4            # order itne ghante valid
BIG_EXPIRE_H = 12          # order lagne ke baad itne ghante tak natija dekha jata hai
BIG_MAX_PER_SESSION = 2    # London / New York mein zyada se zyada itne bade signal
BIG_GAP_MIN = 90           # do bade signal ke beech kam az kam itne minute
BIG_MIN_DIST, BIG_MAX_DIST = 1.5, 8.0    # order ka level price se kitna door (dollar)
BIG_MIN_ATR = 4.0          # M30 mein itni harkat ho tab hi bada signal
BIG_SESSIONS = ("London", "New York")
# --- CHHOTA signal (scalp, abhi lagao) ---
SCALP_SL, SCALP_TP = 3.0, 5.0
SCALP_MIN_ATR = 2.0        # M15 mein itni harkat ho tab hi scalp
SCALP_GAP_MIN = 30         # ek hi taraf ke do scalp ke beech kam az kam itne minute
SCALP_MAX_PER_HOUR = 3
SCALP_MAX_AGE = 6.0        # candle band hue itne minute se zyada ho to signal NAHI bheja jata
SCALP_STALE_MAX = 1.0      # price entry se itni (dollar) door ho gayi ho to signal NAHI bheja jata
SCALP_EXPIRE_H = 3         # itne ghante mein TP/SL na lage to "expire" ka message
# --- natija (profit / loss) ---
RESULTS_ON = True
# --- baqi ---
TREND = 100
RUN_EVERY_MIN = 15         # cron-job.org bot ko kitne minute baad chalata hai
FRESH_MIN = RUN_EVERY_MIN - 0.1
WAIT_SECONDS = 75          # candle ka data final hone tak intezar
# ======================================================================

BASE_URL = "https://biquote.io/api/XAUUSD/ohlc?interval=%s&limit=300"
TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TEST = os.environ.get("TEST_MESSAGE", "").strip() == "1"
UA = {"User-Agent": "Mozilla/5.0 (gold-signal-bot)"}
USED = set()        # backup data use hua to naam yahan aata hai
BQ_DOWN = [False]   # biquote is run mein band mila to dobara koshish nahi
YAHOO_MAP = {"15m": ("15m", "5d", 15), "30m": ("30m", "5d", 30), "1h": ("60m", "1mo", 60)}


def now_utc():
    return datetime.now(timezone.utc)


# ------------------------------ data ------------------------------
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


def yahoo_bars(interval):
    iv, rng, minutes = YAHOO_MAP[interval]
    last = RuntimeError("Yahoo data nahi mila")
    for sym in ("XAUUSD=X", "GC=F"):
        try:
            d = http_json("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s"
                          % (sym, rng, iv))
            res = d["chart"]["result"][0]
            q = res["indicators"]["quote"][0]
            now = time.time()
            out = []
            for i, t in enumerate(res["timestamp"]):
                o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
                if None in (o, h, l, c):
                    continue
                out.append({"openTime": datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z"),
                            "open": o, "high": h, "low": l, "close": c, "isOpen": t + minutes * 60 > now})
            if len(out) >= 100:
                USED.add("Yahoo " + sym)
                return list(reversed(out))
            last = RuntimeError("Yahoo %s: data kam (%d)" % (sym, len(out)))
        except Exception as ex:
            last = ex
    raise last


def get_bars(interval):
    if not BQ_DOWN[0]:
        try:
            return http_json(BASE_URL % interval)["bars"]
        except Exception as ex:
            BQ_DOWN[0] = True
            print("biquote band, Yahoo backup try kar raha hoon:", ex)
    return yahoo_bars(interval)


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# ---------------------------- indicators ----------------------------
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


def atr(h, l, c, p=14):
    out, a = [0.0] * len(c), 0.0
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1]))
        a = a + tr / p if i <= p else (a * (p - 1) + tr) / p
        out[i] = a
    return out


def load(interval, minutes):
    raw = get_bars(interval)
    live = float(raw[0]["close"])                       # sab se taaza (chalti candle ki) price
    bars = [b for b in reversed(raw) if not b.get("isOpen")]
    if len(bars) < 100:
        raise RuntimeError("%s data kam hai (%d)" % (interval, len(bars)))
    c = [b["close"] for b in bars]
    h = [b["high"] for b in bars]
    l = [b["low"] for b in bars]
    o = [b.get("open", c[i - 1] if i else c[0]) for i, b in enumerate(bars)]
    open_t = [parse_ts(b["openTime"]) for b in bars]
    return {"n": len(bars), "live": live, "c": c, "h": h, "l": l, "o": o,
            "f": ema(c, 20), "s": ema(c, 50), "t": ema(c, TREND), "r": rsi(c), "a": atr(h, l, c),
            "open_t": open_t, "closed": [t + timedelta(minutes=minutes) for t in open_t]}


def scan_h1():
    """H1 trend: (band hone ka waqt, EMA20 > EMA50 ?) ki list."""
    bars = [b for b in reversed(get_bars("1h")) if not b.get("isOpen")]
    c = [b["close"] for b in bars]
    f, s = ema(c, 20), ema(c, 50)
    return [(parse_ts(b["openTime"]) + timedelta(hours=1), f[i] > s[i]) for i, b in enumerate(bars)]


def h1_up(h1, ts):
    times = [x[0] for x in h1]
    k = bisect_right(times, ts) - 1
    return h1[k][1] if k >= 0 else None


# ------------------------------ helpers ------------------------------
def session_of(dt):
    h = dt.hour
    if h < 8:
        return "Asia"
    if h < 13:
        return "London"
    if h < 22:
        return "New York"
    return "Off"


def sess_close(ts):
    return session_of(ts - timedelta(minutes=1))


def pk(dt):
    return (dt + timedelta(hours=5)).strftime("%I:%M %p")      # Pakistan waqt


def side_at(i, S):
    c, h, l, o = S["c"][i], S["h"][i], S["l"][i], S["o"][i]
    f, s, t, r = S["f"], S["s"], S["t"], S["r"]
    if f[i] > s[i] and f[i - 1] <= s[i - 1] and c > t[i] and 50 < r[i] < 70:
        return "BUY", "EMA cross"
    if f[i] < s[i] and f[i - 1] >= s[i - 1] and c < t[i] and 30 < r[i] < 50:
        return "SELL", "EMA cross"
    if f[i] > s[i] > t[i] and l <= f[i] and c > f[i] and c > o and 45 < r[i] < 75:
        return "BUY", "Trend pullback"
    if f[i] < s[i] < t[i] and h >= f[i] and c < f[i] and c < o and 25 < r[i] < 55:
        return "SELL", "Trend pullback"
    return None


# ----------------------------- signals -----------------------------
def gen_scalps(S, h1):
    """CHHOTA signal (target 5$): M15 setup + H1 trend + RSI theek + kafi harkat (ATR)."""
    out, last_t, per_hour = [], {}, Counter()
    for i in range(80, S["n"]):
        cur, prev = side_at(i, S), side_at(i - 1, S)
        if not cur or cur == prev:
            continue
        side, kind = cur
        closed, r = S["closed"][i], S["r"][i]
        up = h1_up(h1, closed)
        if up is None or (side == "BUY") != up:
            continue
        if not ((45 < r < 68) if side == "BUY" else (32 < r < 55)):
            continue
        if S["a"][i] < SCALP_MIN_ATR:
            continue
        if side in last_t and (closed - last_t[side]) < timedelta(minutes=SCALP_GAP_MIN):
            continue
        hk = (closed - timedelta(minutes=1)).replace(minute=0, second=0, microsecond=0)
        if per_hour[hk] >= SCALP_MAX_PER_HOUR:
            continue
        per_hour[hk] += 1
        last_t[side] = closed
        out.append({"type": "scalp", "side": side, "kind": kind, "closed": closed, "entry": S["c"][i],
                    "sl": SCALP_SL, "tp": SCALP_TP, "rsi": r, "session": sess_close(closed)})
    return out


def gen_bigs(S, h1):
    """BADA signal (target 10-15$): pullback ke liye LIMIT order ka level (M30 EMA20)."""
    out, cnt, last = [], Counter(), None
    for i in range(80, S["n"]):
        closed = S["closed"][i]
        sess = sess_close(closed)
        if sess not in BIG_SESSIONS:
            continue
        up = h1_up(h1, closed)
        if up is None or S["a"][i] < BIG_MIN_ATR:
            continue
        c, level, r = S["c"][i], S["f"][i], S["r"][i]
        f, s, t = S["f"][i], S["s"][i], S["t"][i]
        if up and f > s > t and 40 < r < 80 and BIG_MIN_DIST <= c - level <= BIG_MAX_DIST:
            side = "BUY"
        elif (not up) and f < s < t and 20 < r < 60 and BIG_MIN_DIST <= level - c <= BIG_MAX_DIST:
            side = "SELL"
        else:
            continue
        key = ((closed - timedelta(minutes=1)).date(), sess)
        if cnt[key] >= BIG_MAX_PER_SESSION:
            continue
        if last and (closed - last) < timedelta(minutes=BIG_GAP_MIN):
            continue
        cnt[key] += 1
        last = closed
        out.append({"type": "big", "side": side, "kind": "Pullback limit", "closed": closed, "level": level,
                    "price": c, "sl": BIG_SL, "tp1": BIG_TP1, "tp2": BIG_TP2, "rsi": r, "session": sess})
    return out


# ------------------------------ natija ------------------------------
def R(kind, time_, pnl):
    return {"kind": kind, "time": time_, "pnl": pnl}


def outcome(ev, S):
    """Signal ke baad ki M15 candles dekh kar natija. SL aur TP ek hi candle mein ho to LOSS maana jata hai."""
    n, ot, cl = S["n"], S["open_t"], S["closed"]
    buy = ev["side"] == "BUY"
    start = bisect_left(ot, ev["closed"])
    if ev["type"] == "scalp":
        entry, sl, tp = ev["entry"], ev["sl"], ev["tp"]
        end = ev["closed"] + timedelta(hours=SCALP_EXPIRE_H)
        for j in range(start, n):
            if ot[j] >= end:
                return [R("expired", cl[j], 0)]
            adv = (entry - S["l"][j]) if buy else (S["h"][j] - entry)
            fav = (S["h"][j] - entry) if buy else (entry - S["l"][j])
            if adv >= sl:
                return [R("loss", cl[j], -sl)]
            if fav >= tp:
                return [R("win", cl[j], tp)]
        return []
    level, sl, tp1, tp2 = ev["level"], ev["sl"], ev["tp1"], ev["tp2"]
    vend = ev["closed"] + timedelta(hours=BIG_VALID_H)
    fill = None
    for j in range(start, n):
        if ot[j] >= vend:
            return [R("nofill", cl[j], 0)]
        if (buy and S["l"][j] <= level) or ((not buy) and S["h"][j] >= level):
            fill = j
            break
    if fill is None:
        return []

    def adv(j):
        return (level - S["l"][j]) if buy else (S["h"][j] - level)

    def fav(j):
        return (S["h"][j] - level) if buy else (level - S["l"][j])

    if adv(fill) >= sl:                      # jis candle mein order laga, usi mein SL
        return [R("loss", cl[fill], -sl)]
    expire = cl[fill] + timedelta(hours=BIG_EXPIRE_H)
    res, stage = [], 0
    for k in range(fill + 1, n):
        if ot[k] >= expire:
            break
        if stage == 0:
            if adv(k) >= sl:
                return [R("loss", cl[k], -sl)]
            if fav(k) >= tp1:
                res.append(R("tp1", cl[k], tp1))
                stage = 1
                if fav(k) >= tp2:
                    res.append(R("tp2", cl[k], tp2))
                    break
        else:
            if fav(k) >= tp2:
                res.append(R("tp2", cl[k], tp2))
                break
            if adv(k) >= 0:                  # SL entry par tha: chup chaap band
                break
    return res


# ------------------------------ messages ------------------------------
def post(text, silent=False):
    if not TOKEN or not CHAT_ID:
        print("TOKEN ya CHAT_ID set nahi hai:\n" + text)
        return
    url = "https://api.telegram.org/bot%s/sendMessage" % TOKEN
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML", "disable_notification": silent}
    http_json(url, json.dumps(payload).encode())
    print("Telegram par bhej diya.")


def warn_line():
    if not USED:
        return ""
    return "\n⚠️ Backup data (%s) se bana. Price broker se thori alag ho sakti hai, MT5 ki price dekh kar hi lagayein." % ", ".join(sorted(USED))


def ident(ev):
    if ev["type"] == "big":
        w = "BUY LIMIT" if ev["side"] == "BUY" else "SELL LIMIT"
        return "%s @ %.2f" % (w, ev["level"])
    return "%s @ %.2f" % (ev["side"], ev["entry"])


def fmt_signal(ev, live, now):
    age = (now - ev["closed"]).total_seconds() / 60
    buy = ev["side"] == "BUY"
    sg = 1 if buy else -1
    icon = "🟢" if buy else "🔴"
    if ev["type"] == "big":
        lv = ev["level"]
        word = "BUY LIMIT" if buy else "SELL LIMIT"
        valid = ev["closed"] + timedelta(hours=BIG_VALID_H)
        return (
            f"🟣 <b>BADA SIGNAL — target {ev['tp1']:g} se {ev['tp2']:g}$</b>\n"
            f"{icon} XAUUSD {word} (pehle se order)\n"
            f"MT5 mein abhi lagayein: <b>{word} @ <code>{lv:.2f}</code></b>\n"
            f"Stop Loss: <code>{lv - sg * ev['sl']:.2f}</code> ({ev['sl']:g}$)\n"
            f"TP1: <code>{lv + sg * ev['tp1']:.2f}</code> ({ev['tp1']:g}$)\n"
            f"TP2: <code>{lv + sg * ev['tp2']:.2f}</code> ({ev['tp2']:g}$)\n"
            f"Abhi price {live:.2f}, level {abs(live - lv):.1f}$ door | Session: {ev['session']}\n"
            f"Valid: {BIG_VALID_H} ghante ({pk(valid)} Pakistan tak). Tab tak na lage to cancel karein.\n"
            f"<i>TP1 par aadha band karein aur SL entry par le aayein.</i>"
            + warn_line()
        )
    en = ev["entry"]
    return (
        f"🔵 <b>CHHOTA SIGNAL (SCALP) — target {ev['tp']:g}$</b>\n"
        f"{icon} XAUUSD {ev['side']} — abhi lagayein ({ev['kind']})\n"
        f"Entry: <code>{en:.2f}</code> | Abhi price: <code>{live:.2f}</code>\n"
        f"Stop Loss: <code>{en - sg * ev['sl']:.2f}</code> ({ev['sl']:g}$)\n"
        f"Target: <code>{en + sg * ev['tp']:.2f}</code> ({ev['tp']:g}$)\n"
        f"Candle band hui {age:.0f} minute pehle ({pk(ev['closed'])} Pakistan) | RSI {ev['rsi']:.0f} | {ev['session']}\n"
        f"<i>MT5 ki price entry se {SCALP_STALE_MAX:g}$ se zyada door ho to chhor dein.</i>"
        + warn_line()
    )


def fmt_result(ev, rs):
    kind = "BADA signal" if ev["type"] == "big" else "CHHOTA signal (scalp)"
    who = f"{kind}: {ident(ev)} ({pk(ev['closed'])} Pakistan)"
    k = rs["kind"]
    if k == "win":
        return f"✅ <b>PROFIT +{rs['pnl']:g}$</b>\n{who}\nTarget lag gaya."
    if k == "tp1":
        return f"✅ <b>PROFIT +{rs['pnl']:g}$ (TP1)</b>\n{who}\nAadha band karein, SL entry par le aayein."
    if k == "tp2":
        return f"✅✅ <b>PROFIT +{rs['pnl']:g}$ (TP2)</b>\n{who}\nPura target lag gaya."
    if k == "loss":
        return f"❌ <b>LOSS {rs['pnl']:g}$</b>\n{who}\nStop Loss lag gaya. Agle signal ka intezar."
    if k == "nofill":
        return f"⏳ <b>Order fill nahi hua</b>\n{who}\n{BIG_VALID_H} ghante mein price level tak nahi aayi. Order cancel kar dein."
    return f"⏱ <b>Time khatam</b>\n{who}\n{SCALP_EXPIRE_H} ghante mein TP/SL tak nahi pahunchi. Band kar dein."


# -------------------------------- main --------------------------------
def main():
    if not TEST:
        time.sleep(WAIT_SECONDS)
    now = now_utc()
    S30 = load("30m", 30)
    try:
        S15 = load("15m", 15)
    except Exception as ex:
        print("M15 data nahi mila, scalp aur natija band:", ex)
        S15 = None
    try:
        h1 = scan_h1()
    except Exception as ex:
        print("H1 data nahi mila, signal band:", ex)
        h1 = []
    live = (S15 or S30)["live"]

    if TEST:
        post("✅ Test message: bot theek chal raha hai. Gold price: %.2f" % live)
        return

    bigs = gen_bigs(S30, h1) if h1 else []
    scalps = gen_scalps(S15, h1) if (S15 and h1) else []
    events = sorted(bigs + scalps, key=lambda e: e["closed"])
    sent = 0

    for ev in events:                                   # 1) naye signal
        age = (now - ev["closed"]).total_seconds() / 60
        if age < 0 or age > FRESH_MIN:
            continue
        if ev["type"] == "big":
            lv = ev["level"]
            if (ev["side"] == "SELL" and live >= lv) or (ev["side"] == "BUY" and live <= lv):
                print("Chhora (bada): price level tak/aage pahunch chuki %.2f vs %.2f" % (live, lv))
                continue
        else:
            if age > SCALP_MAX_AGE or abs(live - ev["entry"]) > SCALP_STALE_MAX:
                print("Chhora (scalp): purana/price door (%.0f min, %.2f vs %.2f)" % (age, live, ev["entry"]))
                continue
        post(fmt_signal(ev, live, now))
        sent += 1

    wins = losses = 0
    if RESULTS_ON and S15:                              # 2) pichle signals ka natija
        for ev in events:
            if now - ev["closed"] > timedelta(hours=24):
                continue
            for rs in outcome(ev, S15):
                if rs["kind"] in ("win", "tp1"):
                    wins += 1
                elif rs["kind"] == "loss":
                    losses += 1
                age = (now - rs["time"]).total_seconds() / 60
                if 0 <= age <= FRESH_MIN:
                    post(fmt_result(ev, rs))
                    sent += 1

    if sent:
        return
    print("Koi naya signal/natija nahi. Price %.2f" % live)
    age30 = (now - S30["closed"][-1]).total_seconds() / 60
    if S30["closed"][-1].minute == 0 and age30 <= FRESH_MIN:      # har ghante khamosh report
        up = S30["c"][-1] > S30["t"][-1]
        post(
            "📊 <b>XAUUSD report</b>\n"
            f"Price: <code>{live:.2f}</code>\n"
            f"Trend: {'UPAR' if up else 'NEECHE'} | RSI {S30['r'][-1]:.0f}\n"
            f"Session: {sess_close(now + timedelta(minutes=1))}\n"
            f"Pichle 24 ghante: ✅ {wins} profit, ❌ {losses} loss\n"
            "Abhi koi naya signal nahi, bot nigrani kar raha hai." + warn_line(),
            silent=True,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        print("Error:", ex)
        if TEST:
            sys.exit(1)
        try:
            if now_utc().minute < 15 and TOKEN and CHAT_ID:
                post("⚠️ Bot ko abhi data nahi mil raha (%s). Signal rukay hue hain, bot khud dobara koshish karta rahega." % str(ex)[:80], silent=True)
        except Exception:
            pass
        sys.exit(0)
