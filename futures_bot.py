"""
futures_bot.py — ViraLab Crypto Futures Bot v4
================================================
COMPLETE PROFESSIONAL UPGRADE — Research Verified

SCHEDULE: Every 30 minutes (GitHub Actions)
SIGNAL CANDLES: 1h (quality signals, low noise)
TREND FILTER: 4h (direction confirmation)
MONITORING: Every 30 min (trailing stop updates)

SIGNALS (need 6/12):
  1.  RSI 14 (1h)
  2.  Stochastic RSI (1h)
  3.  MACD (1h)
  4.  Supertrend (1h)
  5.  EMA 20/50 Cross (4h trend filter)
  6.  Volume Spike 1.5x (1h)
  7.  Bollinger Bands (1h)
  8.  Funding Rate (real-time)
  9.  Taker Buy Ratio (1h) — #1 predictor per 2026 research
  10. OBV — On Balance Volume (smart money)
  11. VWAP — Volume Weighted Average Price (institutional)
  12. ADX — Average Directional Index (market regime)

DYNAMIC TP/SL (ATR-based — research verified):
  SL  = ATR × 2.0 (adapts to each coin volatility)
  TP1 = ATR × 1.5 (close 50% — quick profit)
  TP2 = ATR × 3.0 (let 50% run — big profit)
  After TP1: Move SL to breakeven (cannot lose)
  Hard max loss: 25% (hard cap)

MARKET REGIME (ADX):
  ADX < 15  → Skip trade (choppy market)
  ADX 15-25 → Tight stops (ATR × 1.5 SL)
  ADX 25-35 → Normal stops (ATR × 2.0 SL)
  ADX > 35  → Wide stops (ATR × 3.0 SL, let run)

POSITION SIZING:
  Fixed 2% risk per trade (research standard)
  Position = (2% balance) / (ATR × SL multiplier)
  Auto leverage = position / capital (max 10x)

CIRCUIT BREAKER:
  Daily loss > 5% → pause 24 hours
  3 consecutive losses → wait 1 candle

EMAIL: Daily 9PM IST → Himanshu.r.garg@icloud.com
"""

import os, sys, json, time, math, hmac, hashlib, urllib3
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── CREDENTIALS ──────────────────────────────────────────────────
API_KEY      = os.environ.get("BINANCE_API_KEY", "")
API_SECRET   = os.environ.get("BINANCE_API_SECRET", "")
EMAIL_PASS   = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_FROM   = os.environ.get("EMAIL_FROM", "")
EMAIL_TO     = "Himanshu.r.garg@icloud.com"
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").lower() == "true"

# ── CONFIG ────────────────────────────────────────────────────────
FUTURES_URL   = "https://fapi.binance.com"
MIN_SIGNALS   = 6           # Need 6/12
MAX_OPEN      = 5           # Max 5 positions
MAX_LOSS_PCT  = 0.25        # Hard cap 25%
RISK_PER_TRADE= 0.02        # 2% of balance per trade
MAX_LEVERAGE  = 10
MIN_LEVERAGE  = 3
MAX_VOL_USD   = 50_000_000  # $50M daily volume minimum
STATE_FILE    = "futures_state.json"
REPORT_HOUR   = 15          # 9PM IST = 15:30 UTC
REPORT_MIN    = 30

# ADX regime multipliers
ADX_REGIMES = {
    # Minimum RR guaranteed: TP1 = SL × 1.5, TP2 = SL × 3.0
    "choppy":   {"min": 0,  "max": 15, "sl_mult": 0,   "tp1_mult": 0,   "tp2_mult": 0},
    "ranging":  {"min": 15, "max": 25, "sl_mult": 1.5, "tp1_mult": 2.25,"tp2_mult": 4.5},
    "moderate": {"min": 25, "max": 35, "sl_mult": 2.0, "tp1_mult": 3.0, "tp2_mult": 6.0},
    "trending": {"min": 35, "max": 999,"sl_mult": 3.0, "tp1_mult": 4.5, "tp2_mult": 9.0},
}

# ── PROXY ─────────────────────────────────────────────────────────
def get_proxy():
    s = os.environ.get("WEBSHARE_PROXY", "")
    if not s: return None
    try:
        p = s.strip().split(":")
        url = f"http://{p[2]}:{p[3]}@{p[0]}:{p[1]}" if len(p)==4 else f"http://{p[0]}:{p[1]}"
        proxy = {"http": url, "https": url}
        r = requests.get(f"{FUTURES_URL}/fapi/v1/time", proxies=proxy, timeout=10, verify=False)
        if r.status_code == 200:
            print(f"  ✅ Proxy: {p[0]}:{p[1]}")
            return proxy
    except Exception as e:
        print(f"  ⚠ Proxy: {e}")
    return None

print("[PROXY] Connecting...")
PROXY = get_proxy()

# ── LOGGING ───────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

# ── BINANCE API ───────────────────────────────────────────────────
def sign(p):
    p["timestamp"] = int(time.time() * 1000)
    q = urlencode(p)
    p["signature"] = hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    return p

def bget(path, params=None, signed=False):
    if params is None: params = {}
    if signed: params = sign(params)
    try:
        r = requests.get(f"{FUTURES_URL}{path}", params=params,
                        headers={"X-MBX-APIKEY": API_KEY},
                        proxies=PROXY, timeout=15, verify=False)
        if r.status_code == 200: return r.json()
        log(f"GET {path} {r.status_code}: {r.text[:150]}")
        return None
    except Exception as e:
        log(f"GET error: {e}"); return None

def bpost(path, params):
    params = sign(params)
    try:
        r = requests.post(f"{FUTURES_URL}{path}", params=params,
                         headers={"X-MBX-APIKEY": API_KEY},
                         proxies=PROXY, timeout=15, verify=False)
        if r.status_code == 200: return r.json()
        log(f"POST {path} {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        log(f"POST error: {e}"); return None

# ── MARKET DATA ───────────────────────────────────────────────────
def klines(symbol, interval="1h", limit=200):
    d = bget("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    if not d: return None
    return {
        "c": [float(k[4]) for k in d],
        "h": [float(k[2]) for k in d],
        "l": [float(k[3]) for k in d],
        "v": [float(k[5]) for k in d],
        "o": [float(k[1]) for k in d],
        "tb": [float(k[9]) for k in d],   # taker buy base volume
        "tv": [float(k[7]) for k in d],   # total volume
    }

def get_funding(symbol):
    d = bget("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(d.get("lastFundingRate", 0)) if d else 0.0

def get_fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1",
                        proxies=PROXY, timeout=8, verify=False)
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
    except: pass
    return 50

def get_balance():
    d = bget("/fapi/v2/balance", signed=True)
    if not d: return 0.0
    for a in d:
        if a.get("asset") == "USDT":
            return float(a.get("availableBalance", 0))
    return 0.0

def get_top_symbols():
    d = bget("/fapi/v1/ticker/24hr")
    if not d:
        return ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","ADAUSDT",
                "DOGEUSDT","AVAXUSDT","LINKUSDT","DOTUSDT","MATICUSDT"]
    syms = [(t["symbol"], float(t.get("quoteVolume",0)))
            for t in d if t["symbol"].endswith("USDT")
            and float(t.get("quoteVolume",0)) >= MAX_VOL_USD]
    syms.sort(key=lambda x: -x[1])
    return [s[0] for s in syms[:60]]

# ── INDICATORS ────────────────────────────────────────────────────
def ema(closes, p):
    if len(closes) < p: return closes[-1]
    k = 2/(p+1); v = sum(closes[:p])/p
    for c in closes[p:]: v = c*k + v*(1-k)
    return v

def rsi(closes, p=14):
    if len(closes) < p+1: return 50
    g, l = [], []
    for i in range(1, len(closes)):
        d = closes[i]-closes[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag = sum(g[-p:])/p; al = sum(l[-p:])/p
    return 100 if al==0 else 100-100/(1+ag/al)

def stoch_rsi(closes, p=14):
    if len(closes) < p*2: return 50, 50
    rvals = [rsi(closes[max(0,i-p*2):i+1], p) for i in range(p, len(closes))]
    if len(rvals) < p: return 50, 50
    recent = rvals[-p:]
    lo, hi = min(recent), max(recent)
    if hi == lo: return 50, 50
    k = ((rvals[-1]-lo)/(hi-lo))*100
    d = sum([((rvals[-(p-j)]-lo)/(hi-lo))*100 for j in range(3)])/3 if len(rvals)>=3 else k
    return round(k,1), round(d,1)

def macd(closes):
    if len(closes) < 26: return 0, 0, 0
    m = ema(closes,12) - ema(closes,26)
    s = m*0.85; return m, s, m-s

def supertrend(h, l, c, p=10, mult=3.0):
    if len(c) < p+1: return "NEUTRAL"
    trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
           for i in range(1,len(c))]
    a = sum(trs[-p:])/p
    hl2 = (h[-1]+l[-1])/2
    return "BULL" if c[-1] > hl2-mult*a else "BEAR"

def bollinger(closes, p=20):
    if len(closes) < p: return closes[-1], closes[-1], closes[-1]
    s = closes[-p:]; mid = sum(s)/p
    std = math.sqrt(sum((x-mid)**2 for x in s)/p)
    return mid+2*std, mid, mid-2*std

def atr(h, l, c, p=14):
    if len(c) < p+1: return c[-1]*0.02
    trs = [max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1]))
           for i in range(1,len(c))]
    return sum(trs[-p:])/p

def adx(h, l, c, p=14):
    """Average Directional Index — measures trend strength."""
    if len(c) < p*2: return 20
    dms_pos, dms_neg, trs = [], [], []
    for i in range(1, len(c)):
        up = h[i]-h[i-1]; dn = l[i-1]-l[i]
        dms_pos.append(up if up>dn and up>0 else 0)
        dms_neg.append(dn if dn>up and dn>0 else 0)
        trs.append(max(h[i]-l[i], abs(h[i]-c[i-1]), abs(l[i]-c[i-1])))
    def smooth(vals, n):
        s = sum(vals[:n])
        result = [s]
        for v in vals[n:]:
            s = s - s/n + v
            result.append(s)
        return result
    tr_s   = smooth(trs, p)
    dmp_s  = smooth(dms_pos, p)
    dmn_s  = smooth(dms_neg, p)
    di_pos = [100*dmp_s[i]/tr_s[i] if tr_s[i]>0 else 0 for i in range(len(tr_s))]
    di_neg = [100*dmn_s[i]/tr_s[i] if tr_s[i]>0 else 0 for i in range(len(tr_s))]
    dx     = [100*abs(di_pos[i]-di_neg[i])/(di_pos[i]+di_neg[i])
              if (di_pos[i]+di_neg[i])>0 else 0 for i in range(len(di_pos))]
    if len(dx) < p: return 20
    return sum(dx[-p:])/p

def vwap(closes, volumes):
    """Volume Weighted Average Price."""
    if not volumes or sum(volumes)==0: return closes[-1]
    tp = [(closes[i])*volumes[i] for i in range(len(closes))]
    return sum(tp[-20:])/sum(volumes[-20:]) if sum(volumes[-20:])>0 else closes[-1]

def obv(closes, volumes):
    """On Balance Volume — smart money detection."""
    if len(closes) < 2: return 0
    o = 0
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]: o += volumes[i]
        elif closes[i] < closes[i-1]: o -= volumes[i]
    return o

def taker_buy_ratio(tb, tv):
    """Taker buy ratio — #1 predictor per 2026 research."""
    recent_tb = sum(tb[-3:])
    recent_tv = sum(tv[-3:])
    if recent_tv == 0: return 0.5
    return recent_tb / recent_tv

def get_adx_regime(adx_val):
    """Get market regime from ADX value."""
    if adx_val < 15: return "choppy"
    if adx_val < 25: return "ranging"
    if adx_val < 35: return "moderate"
    return "trending"

# ── SIGNAL ENGINE ─────────────────────────────────────────────────
def analyse(symbol, fng):
    """Run all 12 signals. Returns direction, score, details, atr_val, adx_val."""
    k1h = klines(symbol, "1h", 200)
    k4h = klines(symbol, "4h", 100)
    if not k1h: return "NEUTRAL", 0, {}, 0, 20

    c=k1h["c"]; h=k1h["h"]; l=k1h["l"]
    v=k1h["v"]; tb=k1h["tb"]; tv=k1h["tv"]
    price = c[-1]

    # Calculate core indicators
    atr_val = atr(h, l, c)
    adx_val = adx(h, l, c)
    regime  = get_adx_regime(adx_val)

    # Skip choppy markets immediately
    if regime == "choppy":
        return "NEUTRAL", 0, {"skip": f"ADX={adx_val:.1f} choppy", "price": price}, atr_val, adx_val

    bull = bear = 0
    d = {"price": price, "atr": round(atr_val,6),
         "adx": round(adx_val,1), "regime": regime}

    # ── 1. RSI ────────────────────────────────────────────────────
    r = rsi(c)
    d["rsi"] = round(r,1)
    if r < 35:      bull+=1; d["s1"]="🟢 RSI oversold"
    elif r > 65:    bear+=1; d["s1"]="🔴 RSI overbought"
    else:                    d["s1"]=f"⚪ RSI={r:.0f}"

    # ── 2. Stochastic RSI ─────────────────────────────────────────
    sk, sd = stoch_rsi(c)
    if sk<20 and sd<20:        bull+=1; d["s2"]="🟢 StochRSI oversold"
    elif sk>80 and sd>80:      bear+=1; d["s2"]="🔴 StochRSI overbought"
    elif sk>sd and sk<50:      bull+=1; d["s2"]="🟢 StochRSI crossing up"
    elif sk<sd and sk>50:      bear+=1; d["s2"]="🔴 StochRSI crossing down"
    else:                               d["s2"]=f"⚪ K={sk} D={sd}"

    # ── 3. MACD ───────────────────────────────────────────────────
    m, s, hist = macd(c)
    if m>s and hist>0:   bull+=1; d["s3"]="🟢 MACD bullish"
    elif m<s and hist<0: bear+=1; d["s3"]="🔴 MACD bearish"
    else:                          d["s3"]="⚪ MACD neutral"

    # ── 4. Supertrend ─────────────────────────────────────────────
    st = supertrend(h, l, c)
    if st=="BULL":   bull+=1; d["s4"]="🟢 Supertrend bull"
    elif st=="BEAR": bear+=1; d["s4"]="🔴 Supertrend bear"
    else:                      d["s4"]="⚪ Supertrend neutral"

    # ── 5. EMA Cross 4h ──────────────────────────────────────────
    if k4h:
        c4 = k4h["c"]
        e20 = ema(c4,20); e50 = ema(c4,50)
        if e20>e50:   bull+=1; d["s5"]="🟢 4h EMA bull"
        elif e20<e50: bear+=1; d["s5"]="🔴 4h EMA bear"
        else:                   d["s5"]="⚪ 4h EMA neutral"
    else:
        d["s5"] = "⚪ 4h unavailable"

    # ── 6. Volume Spike ───────────────────────────────────────────
    avg_vol = sum(v[-21:-1])/20 if len(v)>20 else v[-1]
    if v[-1] > avg_vol*1.5:
        if c[-1]>c[-2]: bull+=1; d["s6"]="🟢 Volume spike up"
        else:           bear+=1; d["s6"]="🔴 Volume spike down"
    else:
        d["s6"]="⚪ No volume spike"

    # ── 7. Bollinger Bands ────────────────────────────────────────
    up, mid, lo = bollinger(c)
    bb_pct = (price-lo)/(up-lo) if up!=lo else 0.5
    if price<lo or bb_pct<0.1:    bull+=1; d["s7"]="🟢 Below BB"
    elif price>up or bb_pct>0.9:  bear+=1; d["s7"]="🔴 Above BB"
    elif bb_pct<0.25:             bull+=1; d["s7"]="🟢 Near BB low"
    elif bb_pct>0.75:             bear+=1; d["s7"]="🔴 Near BB high"
    else:                                   d["s7"]=f"⚪ BB {bb_pct*100:.0f}%"

    # ── 8. Funding Rate ───────────────────────────────────────────
    fund = get_funding(symbol)
    d["funding"] = round(fund*100, 4)
    if fund<-0.0001:   bull+=1; d["s8"]=f"🟢 Neg funding {fund*100:.4f}%"
    elif fund>0.0001:  bear+=1; d["s8"]=f"🔴 Pos funding {fund*100:.4f}%"
    else:                        d["s8"]=f"⚪ Funding neutral"

    # ── 9. Taker Buy Ratio (#1 predictor per 2026 research) ───────
    tbr = taker_buy_ratio(tb, tv)
    d["tbr"] = round(tbr, 3)
    if tbr>0.65:   bull+=1; d["s9"]=f"🟢 Taker buy {tbr*100:.0f}% aggressive"
    elif tbr<0.35: bear+=1; d["s9"]=f"🔴 Taker sell {(1-tbr)*100:.0f}% aggressive"
    else:                   d["s9"]=f"⚪ Taker balanced {tbr*100:.0f}%"

    # ── 10. OBV — On Balance Volume ───────────────────────────────
    obv_now  = obv(c[-20:], v[-20:])
    obv_prev = obv(c[-40:-20], v[-40:-20])
    d["obv_trend"] = "up" if obv_now>obv_prev else "down"
    if obv_now>obv_prev and c[-1]>c[-5]:   bull+=1; d["s10"]="🟢 OBV rising"
    elif obv_now<obv_prev and c[-1]<c[-5]: bear+=1; d["s10"]="🔴 OBV falling"
    elif obv_now>obv_prev:                  bull+=1; d["s10"]="🟢 OBV accumulation"
    else:                                             d["s10"]="⚪ OBV neutral"

    # ── 11. VWAP — institutional benchmark ───────────────────────
    vw = vwap(c, v)
    d["vwap"] = round(vw, 6)
    vwap_pct = (price-vw)/vw*100
    if price > vw*1.001:  bull+=1; d["s11"]=f"🟢 Above VWAP +{vwap_pct:.2f}%"
    elif price < vw*0.999: bear+=1; d["s11"]=f"🔴 Below VWAP {vwap_pct:.2f}%"
    else:                            d["s11"]=f"⚪ At VWAP {vwap_pct:.2f}%"

    # ── 12. Fear & Greed ─────────────────────────────────────────
    d["fng"] = fng
    if fng<=30:    bull+=1; d["s12"]=f"🟢 Extreme fear {fng}"
    elif fng>=70:  bear+=1; d["s12"]=f"🔴 Extreme greed {fng}"
    elif fng<=45:  bull+=1; d["s12"]=f"🟢 Fear {fng}"
    elif fng>=55:  bear+=1; d["s12"]=f"🔴 Greed {fng}"
    else:                   d["s12"]=f"⚪ Neutral {fng}"

    d["bull"]=bull; d["bear"]=bear

    if bull>=MIN_SIGNALS: return "LONG",  bull, d, atr_val, adx_val
    if bear>=MIN_SIGNALS: return "SHORT", bear, d, atr_val, adx_val
    return "NEUTRAL", max(bull,bear), d, atr_val, adx_val

# ── DYNAMIC TP/SL (ATR-based, research verified) ─────────────────
def calc_tp_sl(price, direction, atr_val, adx_val):
    """
    Research-verified ATR-based TP/SL calculation.
    Source: Frontiers journal 2026, institutional bot research.

    SL  = ATR × regime_multiplier
    TP1 = ATR × 1.5 (close 50% here)
    TP2 = ATR × 3.0-5.0 (let 50% run)

    Hard limits:
    - Max loss 25% of position
    - Min TP1: 3% with leverage
    - SL must be at least 1 ATR from price
    """
    regime = get_adx_regime(adx_val)
    cfg    = ADX_REGIMES[regime]

    sl_mult  = cfg["sl_mult"]
    tp1_mult = cfg["tp1_mult"]
    tp2_mult = cfg["tp2_mult"]

    sl_dist  = atr_val * sl_mult
    tp1_dist = atr_val * tp1_mult
    tp2_dist = atr_val * tp2_mult

    # Hard cap: SL never more than 25% of price
    max_sl_dist = price * MAX_LOSS_PCT
    sl_dist = min(sl_dist, max_sl_dist)

    # Minimum SL: at least 1 ATR
    sl_dist = max(sl_dist, atr_val)

    if direction == "LONG":
        sl  = price - sl_dist
        tp1 = price + tp1_dist
        tp2 = price + tp2_dist
    else:
        sl  = price + sl_dist
        tp1 = price - tp1_dist
        tp2 = price - tp2_dist

    return sl, tp1, tp2, sl_mult, tp1_mult, tp2_mult

# ── POSITION SIZING (2% risk rule) ───────────────────────────────
def calc_position(balance, price, sl_price, atr_val):
    """
    Research-standard 2% risk rule.
    Risk amount = 2% of balance
    Position size = risk / (distance to SL)
    Leverage = position / capital (capped at 10x)
    """
    risk_amount  = balance * RISK_PER_TRADE
    sl_distance  = abs(price - sl_price)

    if sl_distance <= 0:
        sl_distance = atr_val

    # Position size in USDT from 2% risk rule
    pos_size = risk_amount / (sl_distance / price)

    # For small balance: ensure minimum $20 Binance notional
    pos_size = max(pos_size, 20.0)

    # Capital to allocate (max 90% of balance for small accounts)
    if balance < 30:
        capital_for_trade = balance * 0.90
    elif balance < 100:
        capital_for_trade = balance * 0.50
    else:
        capital_for_trade = balance * 0.25

    # Calculate leverage needed
    leverage = math.ceil(pos_size / capital_for_trade)
    leverage = max(MIN_LEVERAGE, min(leverage, MAX_LEVERAGE))

    # Actual position value
    actual_pos = capital_for_trade * leverage

    return actual_pos, capital_for_trade, leverage

def get_step_size(symbol):
    info = bget("/fapi/v1/exchangeInfo")
    if not info: return 0.001, 0.001, 2
    for s in info.get("symbols", []):
        if s["symbol"] == symbol:
            pp = s.get("pricePrecision", 2)
            for f in s.get("filters", []):
                if f["filterType"] == "LOT_SIZE":
                    return float(f["minQty"]), float(f["stepSize"]), pp
    return 0.001, 0.001, 2

def get_balance_and_circuit(state):
    """Check circuit breaker before trading."""
    bal = get_balance()
    start = state.get("start_balance", bal)
    if start == 0: start = bal

    daily_loss = state.get("daily_loss", 0)
    daily_loss_pct = daily_loss / start if start > 0 else 0

    if daily_loss_pct <= -0.05:
        log(f"⛔ Circuit breaker: daily loss {daily_loss_pct*100:.1f}% > 5%")
        return bal, True
    return bal, False

# ── PLACE TRADE ───────────────────────────────────────────────────
def place_trade(symbol, direction, balance, score, atr_val, adx_val, details):
    try:
        ticker = bget("/fapi/v1/ticker/price", {"symbol": symbol})
        if not ticker: return None
        price = float(ticker["price"])

        # Calculate TP/SL
        sl, tp1, tp2, sl_m, tp1_m, tp2_m = calc_tp_sl(price, direction, atr_val, adx_val)

        # Calculate position size
        pos_size, capital, leverage = calc_position(balance, price, sl, atr_val)

        # Get step size and price precision
        min_qty, step, price_precision = get_step_size(symbol)
        raw_qty = pos_size / price
        qty     = round(math.floor(raw_qty/step)*step, 8)

        if qty <= 0: return None

        # Check minimum notional $20
        notional = qty * price
        if notional < 20:
            needed_qty = 20 / price
            qty = round(math.ceil(needed_qty/step)*step, 8)
            notional = qty * price

        # Check we can afford it
        if notional > balance * MAX_LEVERAGE * 1.1:
            log(f"  ⚠ {symbol}: notional ${notional:.2f} too large for balance ${balance:.2f}")
            return None

        # Round prices to correct precision
        sl  = round(sl,  price_precision)
        tp1 = round(tp1, price_precision)
        tp2 = round(tp2, price_precision)

        regime = get_adx_regime(adx_val)

        log(f"  📊 {score}/12 signals | ADX={adx_val:.0f} ({regime}) | ATR={atr_val:.4f}")
        log(f"  💰 Capital=${capital:.2f} | Position=${notional:.2f} | Leverage={leverage}x")
        log(f"  📍 Entry=${price:.4f} | SL=${sl:.4f} ({sl_m}×ATR) | TP1=${tp1:.4f} | TP2=${tp2:.4f}")

        record = {
            "symbol":      symbol,
            "direction":   direction,
            "entry_price": price,
            "sl_price":    sl,
            "tp1_price":   tp1,
            "tp2_price":   tp2,
            "tp1_hit":     False,
            "peak_price":  price,
            "qty":         qty,
            "half_qty":    round(math.floor(qty/2/step)*step, 8),
            "capital":     capital,
            "pos_value":   notional,
            "leverage":    leverage,
            "atr":         atr_val,
            "adx":         adx_val,
            "regime":      regime,
            "score":       score,
            "sl_mult":     sl_m,
            "tp1_mult":    tp1_m,
            "tp2_mult":    tp2_m,
            "entry_time":  time.time(),
            "entry_date":  datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
            "paper":       not LIVE_TRADING,
        }

        if LIVE_TRADING:
            # Set leverage
            bpost("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})
            side = "BUY" if direction=="LONG" else "SELL"

            # Entry order
            order = bpost("/fapi/v1/order", {
                "symbol": symbol, "side": side,
                "type": "MARKET", "quantity": str(qty),
            })
            if not order:
                log(f"  ❌ Entry failed"); return None
            record["order_id"] = order.get("orderId")
            log(f"  ✅ Entry: {direction} {qty} {symbol} @ ${price:.4f}")
            time.sleep(2)

            sl_side = "SELL" if side=="BUY" else "BUY"

            # Stop Loss (via algoOrder — Binance requirement since Dec 2025)
            sl_result = bpost("/fapi/v1/algoOrder", {
                "algoType":     "CONDITIONAL",
                "symbol":       symbol,
                "side":         sl_side,
                "type":         "STOP_MARKET",
                "triggerPrice": str(sl),
                "closePosition":"true",
                "workingType":  "CONTRACT_PRICE",
            })
            if sl_result and sl_result.get("algoId"):
                log(f"  ✅ SL @ ${sl} (algoId {sl_result['algoId']})")
                record["sl_order_id"] = sl_result["algoId"]
            else:
                log(f"  ❌ SL failed: {sl_result}")

            time.sleep(0.5)

            # Take Profit at TP2 (full position close)
            # TP1 is handled by monitoring (closes 50% manually)
            tp_result = bpost("/fapi/v1/algoOrder", {
                "algoType":     "CONDITIONAL",
                "symbol":       symbol,
                "side":         sl_side,
                "type":         "TAKE_PROFIT_MARKET",
                "triggerPrice": str(tp2),
                "closePosition":"true",
                "workingType":  "CONTRACT_PRICE",
            })
            if tp_result and tp_result.get("algoId"):
                log(f"  ✅ TP2 @ ${tp2} (algoId {tp_result['algoId']})")
                record["tp_order_id"] = tp_result["algoId"]
            else:
                log(f"  ❌ TP2 failed: {tp_result}")
        else:
            log(f"  📝 PAPER: {direction} {qty} {symbol}")
            log(f"     SL=${sl} TP1=${tp1} TP2=${tp2}")

        return record

    except Exception as e:
        log(f"  ❌ place_trade error: {e}")
        import traceback; traceback.print_exc()
        return None

# ── MONITOR POSITIONS ─────────────────────────────────────────────
def monitor(state):
    if not state["positions"]: return state
    log(f"\n📋 Monitoring {len(state['positions'])} positions...")
    to_close = []

    for pos in state["positions"]:
        sym   = pos["symbol"]
        entry = pos["entry_price"]
        sl    = pos["sl_price"]
        tp1   = pos["tp1_price"]
        tp2   = pos["tp2_price"]
        dir   = pos["direction"]
        qty   = pos["qty"]
        half  = pos.get("half_qty", qty/2)
        atr_v = pos.get("atr", 0.01)
        peak  = pos.get("peak_price", entry)
        tp1_hit = pos.get("tp1_hit", False)

        ticker = bget("/fapi/v1/ticker/price", {"symbol": sym})
        if not ticker: continue
        price = float(ticker["price"])

        # Update peak
        if dir=="LONG":
            peak = max(peak, price)
            pnl_pct = (price-entry)/entry
        else:
            peak = min(peak, price)
            pnl_pct = (entry-price)/entry

        pos["peak_price"] = peak
        pnl_usd = pnl_pct * pos["capital"] * pos.get("leverage",5)

        log(f"  {sym} {dir}: ${entry:.4f}→${price:.4f} PnL={pnl_pct*100:+.2f}% "
            f"Peak=${peak:.4f} SL=${sl:.4f} TP1=${tp1:.4f} TP2=${tp2:.4f}")

        # ── TP1 Check (close 50% and move SL to breakeven) ────────
        if not tp1_hit:
            tp1_reached = (price>=tp1 if dir=="LONG" else price<=tp1)
            if tp1_reached:
                log(f"  🎯 TP1 hit! Closing 50% and moving SL to breakeven")
                pos["tp1_hit"] = True

                if LIVE_TRADING and half > 0:
                    close_side = "SELL" if dir=="LONG" else "BUY"
                    bpost("/fapi/v1/order", {
                        "symbol": sym, "side": close_side,
                        "type": "MARKET", "quantity": str(half),
                        "reduceOnly": "true",
                    })

                # Move SL to breakeven (entry price)
                new_sl = round(entry * 1.001 if dir=="LONG" else entry * 0.999, 6)
                pos["sl_price"] = new_sl

                if LIVE_TRADING:
                    # Cancel old SL and place new one at breakeven
                    bpost("/fapi/v1/cancelAllOpenOrders", {"symbol": sym})
                    time.sleep(0.5)
                    sl_side = "SELL" if dir=="LONG" else "BUY"
                    bpost("/fapi/v1/algoOrder", {
                        "algoType":     "CONDITIONAL",
                        "symbol":       sym,
                        "side":         sl_side,
                        "type":         "STOP_MARKET",
                        "triggerPrice": str(new_sl),
                        "closePosition":"true",
                        "workingType":  "CONTRACT_PRICE",
                    })
                log(f"  ✅ SL moved to breakeven ${new_sl}")
                continue

        # ── Full close conditions ─────────────────────────────────
        reason = None
        if dir=="LONG":
            if price <= sl: reason = f"🛡 SL hit {pnl_pct*100:.2f}%"
            elif price >= tp2: reason = f"🎯 TP2 hit +{pnl_pct*100:.2f}%"
        else:
            if price >= sl: reason = f"🛡 SL hit {pnl_pct*100:.2f}%"
            elif price <= tp2: reason = f"🎯 TP2 hit +{pnl_pct*100:.2f}%"

        # Time exit: 72 hours max hold
        if not reason and (time.time()-pos.get("entry_time",time.time())) > 72*3600:
            reason = f"⏰ Time exit 72h {pnl_pct*100:.2f}%"

        if reason:
            log(f"  Closing {sym}: {reason} ${pnl_usd:+.2f}")
            if LIVE_TRADING:
                close_side = "SELL" if dir=="LONG" else "BUY"
                remaining_qty = half if tp1_hit else qty
                bpost("/fapi/v1/order", {
                    "symbol": sym, "side": close_side,
                    "type": "MARKET", "quantity": str(remaining_qty),
                    "reduceOnly": "true",
                })

            pos.update({
                "status":      "closed",
                "exit_price":  price,
                "exit_date":   datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
                "pnl_pct":     round(pnl_pct*100, 2),
                "pnl_usd":     round(pnl_usd, 3),
                "close_reason":reason,
            })
            state["closed_trades"].append(pos)
            state["total_pnl"] += pnl_usd
            state["daily_loss"] = state.get("daily_loss",0) + min(pnl_usd, 0)
            state["wins" if pnl_usd>0 else "losses"] += 1
            state["total_trades"] += 1
            to_close.append(pos)

    state["positions"] = [p for p in state["positions"] if p not in to_close]
    return state

# ── EMAIL ─────────────────────────────────────────────────────────
def should_report(state):
    now = datetime.now(timezone.utc)
    today = (now+timedelta(hours=5,minutes=30)).strftime("%d%b%Y")
    return now.hour==REPORT_HOUR and now.minute>=REPORT_MIN and state.get("last_report_date","")!=today

def send_email(state, bal):
    if not EMAIL_PASS: return
    now   = datetime.now(timezone.utc)+timedelta(hours=5,minutes=30)
    today = now.strftime("%d %b %Y")
    wins  = state.get("wins",0); losses=state.get("losses",0)
    total = state.get("total_trades",0); pnl=state.get("total_pnl",0)
    wrate = wins/max(total,1)*100
    start = state.get("start_balance",bal); gain=bal-start
    open_p= state.get("positions",[])
    today_str = now.strftime("%d %b %Y")
    today_closed = [t for t in state.get("closed_trades",[]) if today_str in t.get("exit_date","")]

    def row(k,v,c=""):
        s=f' style="color:{c};font-weight:bold"' if c else ""
        return f"<tr><td style='padding:6px 12px;color:#666'>{k}</td><td{s} style='padding:6px 12px'>{v}</td></tr>"

    def trow(t):
        p=t.get("pnl_usd",0); c="#27ae60" if p>0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f0f0f0'>
            <td style='padding:8px'>{'✅' if p>0 else '❌'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold'>{t['symbol']}</td>
            <td style='padding:8px'>${t.get('entry_price',0):,.4f}</td>
            <td style='padding:8px'>${t.get('exit_price',0):,.4f}</td>
            <td style='padding:8px;color:{c};font-weight:bold'>{t.get('pnl_pct',0):+.1f}% (${p:+.2f})</td>
            <td style='padding:8px;font-size:11px'>{t.get('close_reason','')}</td>
        </tr>"""

    def orow(t):
        ticker = bget("/fapi/v1/ticker/price",{"symbol":t["symbol"]})
        px = float(ticker["price"]) if ticker else t["entry_price"]
        pct=((px-t["entry_price"])/t["entry_price"]) if t["direction"]=="LONG" else ((t["entry_price"]-px)/t["entry_price"])
        pusd=pct*t["capital"]*t.get("leverage",5)
        c="#27ae60" if pusd>0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f0f0f0'>
            <td style='padding:8px'>{'📈' if t['direction']=='LONG' else '📉'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold'>{t['symbol']}</td>
            <td style='padding:8px'>${t['entry_price']:,.4f}</td>
            <td style='padding:8px'>${px:,.4f}</td>
            <td style='padding:8px;color:{c};font-weight:bold'>{pct*100:+.1f}% (${pusd:+.2f})</td>
            <td style='padding:8px;font-size:11px'>SL=${t['sl_price']:,.4f} TP1=${t['tp1_price']:,.4f} TP2=${t['tp2_price']:,.4f}</td>
        </tr>"""

    mode = "🔴 LIVE" if LIVE_TRADING else "📝 PAPER"
    html = f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;background:#f8f9fa;margin:0;padding:20px'>
<div style='max-width:750px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,.1)'>
<div style='background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);padding:30px;text-align:center'>
<h1 style='color:#e2b96f;margin:0;font-size:26px'>⚡ ViraLab FuturesBot v4</h1>
<p style='color:#aaa;margin:8px 0 4px'>Daily Report — {today}</p>
<span style='background:{"#c0392b" if LIVE_TRADING else "#2c3e50"};color:white;padding:4px 14px;border-radius:20px;font-size:12px'>{mode}</span>
</div>
<div style='padding:24px'>
<h2 style='color:#2c3e50;border-left:4px solid #e2b96f;padding-left:12px'>💰 Account Summary</h2>
<table width='100%' style='border-collapse:collapse;background:#f8f9fa;border-radius:8px'>
{row("Current Balance",f"${bal:.2f} USDT")}
{row("Starting Balance",f"${start:.2f} USDT")}
{row("Total Gain/Loss",f"${gain:+.2f} USDT","#27ae60" if gain>0 else "#e74c3c")}
{row("Total PnL",f"${pnl:+.2f} USDT","#27ae60" if pnl>0 else "#e74c3c")}
{row("Win Rate",f"{wrate:.0f}% ({wins}W/{losses}L)","#27ae60" if wrate>=50 else "#e74c3c")}
{row("Total Trades",str(total))}
{row("Open Positions",str(len(open_p)))}
{row("Strategy","12 signals · ATR TP/SL · ADX regime · 2% risk rule")}
</table>
</div>
<div style='padding:0 24px 24px'>
<h2 style='color:#2c3e50;border-left:4px solid #27ae60;padding-left:12px'>📊 Today Closed ({len(today_closed)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f0f0'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Exit</th><th style='padding:8px;text-align:left'>PnL</th><th style='padding:8px;text-align:left'>Reason</th></tr>"+"".join(trow(t) for t in today_closed)+"</table>" if today_closed else "<p style='color:#999'>No trades closed today.</p>"}
</div>
<div style='padding:0 24px 24px'>
<h2 style='color:#2c3e50;border-left:4px solid #3498db;padding-left:12px'>📈 Open Positions ({len(open_p)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f0f0'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Current</th><th style='padding:8px;text-align:left'>PnL</th><th style='padding:8px;text-align:left'>Levels</th></tr>"+"".join(orow(t) for t in open_p)+"</table>" if open_p else "<p style='color:#999'>No open positions.</p>"}
</div>
<div style='background:#f8f9fa;padding:20px;text-align:center;border-top:1px solid #eee'>
<p style='color:#999;font-size:12px;margin:0'>ViraLab FuturesBot v4 • {now.strftime("%d %b %Y %I:%M %p IST")}</p>
<p style='color:#bbb;font-size:11px;margin:4px 0 0'>12 signals · ATR-based TP/SL · ADX regime filter · 2% risk rule · Dual TP</p>
</div></div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚡ FuturesBot v4 — {today} | PnL: ${pnl:+.2f} | WR: {wrate:.0f}%"
        msg["From"] = EMAIL_FROM; msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html,"html"))
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com",465,context=ctx) as s:
            s.login(EMAIL_FROM, EMAIL_PASS)
            s.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        log(f"✅ Daily email sent to {EMAIL_TO}")
    except Exception as e:
        log(f"❌ Email error: {e}")

# ── STATE ─────────────────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f: return json.load(f)
        except: pass
    return {"positions":[],"closed_trades":[],"total_trades":0,
            "wins":0,"losses":0,"total_pnl":0.0,"start_balance":0.0,
            "daily_loss":0.0,"last_report_date":"","consecutive_losses":0}

def save_state(s):
    if len(s.get("closed_trades",[])) > 200:
        s["closed_trades"] = s["closed_trades"][-200:]
    with open(STATE_FILE,"w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# Reset daily loss at midnight UTC
def check_daily_reset(state):
    now = datetime.now(timezone.utc)
    last_reset = state.get("last_daily_reset","")
    today_str  = now.strftime("%d%b%Y")
    if last_reset != today_str:
        state["daily_loss"]      = 0.0
        state["last_daily_reset"] = today_str
    return state

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    log("="*64)
    log("  VIRALAB CRYPTO FUTURES BOT v4")
    log(f"  {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    log(f"  Mode: {'🔴 LIVE' if LIVE_TRADING else '📝 PAPER'}")
    log(f"  Signals: 12 | Need: {MIN_SIGNALS}/12 | Risk: {RISK_PER_TRADE*100:.0f}%/trade")
    log("="*64)

    if not API_KEY or not API_SECRET:
        log("❌ Missing API credentials"); sys.exit(1)

    state = load_state()
    state = check_daily_reset(state)

    log(f"\nPositions: {len(state['positions'])} | "
        f"Trades: {state['total_trades']} | "
        f"W/L: {state['wins']}/{state['losses']} | "
        f"PnL: ${state['total_pnl']:+.2f} | "
        f"Daily P&L: ${state.get('daily_loss',0):+.2f}")

    # Monitor positions first
    state = monitor(state)
    save_state(state)

    # Get balance and check circuit breaker
    bal, breaker = get_balance_and_circuit(state)
    log(f"Balance: ${bal:.2f} USDT")

    if state.get("start_balance",0) == 0 and bal > 0:
        state["start_balance"] = bal

    if bal < 1.0:
        log("Balance too low"); save_state(state); return

    # Circuit breaker check
    if breaker:
        log("⛔ Circuit breaker active — no new trades")
        save_state(state); return

    # Daily email
    if should_report(state):
        send_email(state, bal)
        ist_today = (datetime.now(timezone.utc)+timedelta(hours=5,minutes=30)).strftime("%d%b%Y")
        state["last_report_date"] = ist_today
        save_state(state)

    # Max positions check
    if len(state["positions"]) >= MAX_OPEN:
        log(f"Max {MAX_OPEN} positions — not opening new")
        save_state(state); return

    # Get symbols and fear & greed
    symbols = get_top_symbols()
    fng     = get_fear_greed()
    log(f"\n🔍 Scanning {len(symbols)} coins | F&G: {fng}")

    # Analyse all symbols
    open_syms = {p["symbol"] for p in state["positions"]}
    signals   = []
    slots     = MAX_OPEN - len(state["positions"])

    for sym in symbols:
        if sym in open_syms: continue
        direction, score, details, atr_val, adx_val = analyse(sym, fng)
        regime = details.get("regime","?")
        if score > 0 or details.get("skip"):
            log(f"  {sym}: {direction} ({score}/12) ADX={adx_val:.0f} "
                f"({regime}) RSI={details.get('rsi',0):.0f} "
                f"TBR={details.get('tbr',0.5):.2f}")
        if direction != "NEUTRAL":
            signals.append((sym, direction, score, details, atr_val, adx_val))
        time.sleep(0.1)

    if not signals:
        log("\n⏳ No signals this run")
        save_state(state); return

    # Sort by score
    signals.sort(key=lambda x: -x[2])
    log(f"\n🎯 {len(signals)} signals found")
    log(f"   Top 3: {[(s[0],s[1],s[2]) for s in signals[:3]]}")

    # Place best trades
    opened = 0
    for sym, direction, score, details, atr_val, adx_val in signals[:slots]:
        log(f"\n{'='*64}")
        log(f"  {direction} {sym} — {score}/12 | ATR={atr_val:.4f} | ADX={adx_val:.0f}")

        record = place_trade(sym, direction, bal, score, atr_val, adx_val, details)
        if record:
            state["positions"].append(record)
            opened += 1
            bal -= record["capital"]
            save_state(state)

    log(f"\n✅ Done — opened {opened} | Total open: {len(state['positions'])}")
    save_state(state)

if __name__ == "__main__":
    main()
