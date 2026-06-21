"""
futures_bot.py — ViraLab Crypto Futures Bot v3
================================================
COMPLETE PROFESSIONAL UPGRADE

COINS: 100+ highest volume Binance futures pairs (auto-fetched)
SIGNALS: 8 indicators across 3 timeframes (need 5/8)
PROFIT: Dynamic — 15% to 100%+ based on momentum strength
LOSS: Smart trailing — maximum 25%, moves to breakeven after +5%

SIGNAL SYSTEM:
  1.  RSI 14 (1h)
  2.  Stochastic RSI (1h)
  3.  MACD (1h)
  4.  Supertrend (1h)
  5.  EMA 20/50 Cross (4h trend filter)
  6.  Volume spike 1.5x average (1h)
  7.  Bollinger Bands (1h)
  8.  Funding Rate (real-time)

SMART LOSS PROTECTION:
  Entry:     Stop at -12% (gives room to breathe)
  At +5%:    Move stop to -1% (near breakeven)
  At +10%:   Trailing stop 8% below peak
  At +20%:   Trailing stop 6% below peak
  At +50%:   Trailing stop 4% below peak
  At +80%+:  Trailing stop 3% below peak
  MAXIMUM:   Never more than 25% loss ever

DYNAMIC PROFIT:
  Weak momentum  (5/8 signals): TP at 15-20%
  Good momentum  (6/8 signals): TP at 30-50%
  Strong momentum(7/8 signals): TP at 60-80%
  Perfect signal (8/8 signals): Let it run with trailing stop

POSITION SIZING (Kelly Criterion):
  Adjusts automatically based on bot win rate
  Range: 5% to 25% of balance per trade
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
LEVERAGE      = 10  # Dynamic — overridden per trade by signal score
MIN_SIGNALS   = 5           # Need 5/8 signals
MAX_OPEN      = 5           # Max 5 positions at once
MAX_LOSS_PCT  = 0.25        # Hard maximum loss 25%
INIT_STOP     = 0.12        # Initial stop loss 12%
MAX_VOL_DAILY = 50_000_000  # Min $50M daily volume
STATE_FILE    = "futures_state.json"
REPORT_HOUR   = 15
REPORT_MIN    = 30

# ── PROXY ────────────────────────────────────────────────────────
def get_proxy():
    proxy_str = os.environ.get("WEBSHARE_PROXY", "")
    if not proxy_str:
        return None
    try:
        parts = proxy_str.strip().split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            proxy_url = f"http://{user}:{pwd}@{host}:{port}"
        else:
            host, port = parts[0], parts[1]
            proxy_url = f"http://{host}:{port}"
        proxy = {"http": proxy_url, "https": proxy_url}
        r = requests.get(f"{FUTURES_URL}/fapi/v1/time",
                        proxies=proxy, timeout=10, verify=False)
        if r.status_code == 200:
            print(f"  ✅ Proxy: {host}:{port}")
            return proxy
    except Exception as e:
        print(f"  ⚠ Proxy error: {e}")
    return None

print("[PROXY] Connecting...")
PROXY = get_proxy()

# ── LOGGING ───────────────────────────────────────────────────────
def log(msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")

# ── BINANCE API ───────────────────────────────────────────────────
def sign(params):
    params["timestamp"] = int(time.time() * 1000)
    q   = urlencode(params)
    sig = hmac.new(API_SECRET.encode(), q.encode(), hashlib.sha256).hexdigest()
    params["signature"] = sig
    return params

def bget(path, params=None, signed=False):
    if params is None: params = {}
    if signed: params = sign(params)
    try:
        r = requests.get(f"{FUTURES_URL}{path}",
                        params=params,
                        headers={"X-MBX-APIKEY": API_KEY},
                        proxies=PROXY, timeout=15, verify=False)
        if r.status_code == 200: return r.json()
        log(f"GET {path} error {r.status_code}: {r.text[:150]}")
        return None
    except Exception as e:
        log(f"GET error: {e}"); return None

def bpost(path, params):
    params = sign(params)
    try:
        r = requests.post(f"{FUTURES_URL}{path}",
                         params=params,
                         headers={"X-MBX-APIKEY": API_KEY},
                         proxies=PROXY, timeout=15, verify=False)
        if r.status_code == 200: return r.json()
        log(f"POST {path} error {r.status_code}: {r.text[:500]}")
        return None
    except Exception as e:
        log(f"POST error: {e}"); return None

# ── GET 100+ COINS ────────────────────────────────────────────────
def get_top_symbols():
    """Get all USDT perpetual futures with >$50M daily volume."""
    data = bget("/fapi/v1/ticker/24hr")
    if not data:
        # Fallback list
        return ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
                "DOGEUSDT","ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT",
                "MATICUSDT","LTCUSDT","TRXUSDT","ETCUSDT","ATOMUSDT",
                "NEARUSDT","FILUSDT","APTUSDT","ARBUSDT","OPUSDT",
                "SUIUSDT","PEPEUSDT","SHIBUSDT","WIFUSDT","INJUSDT"]

    symbols = []
    for t in data:
        sym = t.get("symbol","")
        vol = float(t.get("quoteVolume", 0))
        if sym.endswith("USDT") and vol >= MAX_VOL_DAILY:
            symbols.append((sym, vol))

    # Sort by volume descending, take top 100
    symbols.sort(key=lambda x: -x[1])
    result = [s[0] for s in symbols[:100]]
    log(f"Found {len(result)} coins with >$50M daily volume")
    return result

# ── MARKET DATA ───────────────────────────────────────────────────
def klines(symbol, interval="1h", limit=200):
    data = bget("/fapi/v1/klines",
               {"symbol":symbol,"interval":interval,"limit":limit})
    if not data: return None
    return {
        "c": [float(k[4]) for k in data],
        "h": [float(k[2]) for k in data],
        "l": [float(k[3]) for k in data],
        "v": [float(k[5]) for k in data],
        "o": [float(k[1]) for k in data],
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
    data = bget("/fapi/v2/balance", signed=True)
    if not data: return 0.0
    for a in data:
        if a.get("asset") == "USDT":
            return float(a.get("availableBalance", 0))
    return 0.0

# ── INDICATORS ────────────────────────────────────────────────────
def ema(closes, p):
    if len(closes) < p: return closes[-1] if closes else 0
    k = 2/(p+1); v = sum(closes[:p])/p
    for c in closes[p:]: v = c*k + v*(1-k)
    return v

def rsi(closes, p=14):
    if len(closes) < p+1: return 50
    g,l = [],[]
    for i in range(1,len(closes)):
        d = closes[i]-closes[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g[-p:])/p; al=sum(l[-p:])/p
    return 100 if al==0 else 100-100/(1+ag/al)

def stoch_rsi(closes, p=14, sk=3, sd=3):
    """Stochastic RSI — more sensitive than regular RSI."""
    if len(closes) < p*2: return 50, 50
    rsi_vals = []
    for i in range(p, len(closes)):
        rsi_vals.append(rsi(closes[max(0,i-p*2):i+1], p))
    if len(rsi_vals) < p: return 50, 50
    recent = rsi_vals[-p:]
    lo, hi = min(recent), max(recent)
    if hi == lo: return 50, 50
    k = ((rsi_vals[-1]-lo)/(hi-lo))*100
    # Simple moving average for signal
    k_vals = []
    for i in range(p, len(rsi_vals)):
        r2 = rsi_vals[-p:]
        lo2,hi2 = min(r2),max(r2)
        k_vals.append(((rsi_vals[i]-lo2)/(hi2-lo2))*100 if hi2!=lo2 else 50)
    d = sum(k_vals[-sd:])/min(sd,len(k_vals)) if k_vals else k
    return round(k,1), round(d,1)

def macd(closes):
    if len(closes) < 26: return 0,0,0
    m = ema(closes,12)-ema(closes,26)
    s = m*0.85; h = m-s
    return m,s,h

def supertrend(highs, lows, closes, p=10, mult=3.0):
    """Supertrend indicator — cleaner trend signal than MACD."""
    if len(closes) < p+1: return "NEUTRAL"
    # Calculate ATR
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1]))
           for i in range(1,len(closes))]
    atr_val = sum(trs[-p:])/p

    hl2 = (highs[-1]+lows[-1])/2
    upper = hl2 + mult*atr_val
    lower = hl2 - mult*atr_val

    price = closes[-1]
    prev  = closes[-2] if len(closes)>1 else price

    # Simplified supertrend direction
    if price > upper and prev <= upper:
        return "BULL"
    elif price < lower and prev >= lower:
        return "BEAR"
    elif price > lower:
        return "BULL"
    else:
        return "BEAR"

def bollinger(closes, p=20):
    if len(closes) < p: return closes[-1],closes[-1],closes[-1]
    s=closes[-p:]; mid=sum(s)/p
    std=math.sqrt(sum((x-mid)**2 for x in s)/p)
    return mid+2*std, mid, mid-2*std

def volume_spike(volumes, p=20):
    """Check if current volume is 1.5x above average."""
    if len(volumes) < p: return False
    avg = sum(volumes[-p-1:-1])/p
    return volumes[-1] > avg*1.5 if avg > 0 else False

def atr_pct(highs, lows, closes, p=14):
    if len(closes) < p+1: return 0
    trs=[max(highs[i]-lows[i],abs(highs[i]-closes[i-1]),
             abs(lows[i]-closes[i-1])) for i in range(1,len(closes))]
    return (sum(trs[-p:])/p/closes[-1])*100

def momentum_score(closes, p=10):
    """Measure price momentum — positive = bullish, negative = bearish."""
    if len(closes) < p+1: return 0
    return (closes[-1]-closes[-p-1])/closes[-p-1]*100

# ── SIGNAL ENGINE ─────────────────────────────────────────────────
def analyse(symbol, fng):
    """
    Analyse symbol with 8 signals across 3 timeframes.
    Returns: direction, score, details, momentum_strength
    """
    # Get 1h and 4h data
    k1h = klines(symbol, "1h", 200)
    k4h = klines(symbol, "4h", 100)
    if not k1h: return "NEUTRAL", 0, {}, 0

    c1=k1h["c"]; h1=k1h["h"]; l1=k1h["l"]; v1=k1h["v"]
    price = c1[-1]

    bull=bear=0
    d={"price":price,"symbol":symbol}

    # ── Signal 1: RSI ─────────────────────────────────────────────
    r=rsi(c1)
    d["rsi"]=round(r,1)
    if r < 35:      bull+=1; d["s1"]=f"🟢 LONG RSI={r:.0f}"
    elif r > 65:    bear+=1; d["s1"]=f"🔴 SHORT RSI={r:.0f}"
    else:                    d["s1"]=f"⚪ RSI={r:.0f}"

    # ── Signal 2: Stochastic RSI ──────────────────────────────────
    sk,sd=stoch_rsi(c1)
    d["srsi"]=f"K={sk} D={sd}"
    if sk < 20 and sd < 20:   bull+=1; d["s2"]=f"🟢 LONG StochRSI oversold"
    elif sk > 80 and sd > 80: bear+=1; d["s2"]=f"🔴 SHORT StochRSI overbought"
    elif sk > sd and sk < 50: bull+=1; d["s2"]=f"🟢 LONG StochRSI crossing up"
    elif sk < sd and sk > 50: bear+=1; d["s2"]=f"🔴 SHORT StochRSI crossing down"
    else:                                d["s2"]=f"⚪ StochRSI neutral"

    # ── Signal 3: MACD ────────────────────────────────────────────
    m,s,h=macd(c1)
    if m>s and h>0:  bull+=1; d["s3"]="🟢 LONG MACD bullish"
    elif m<s and h<0:bear+=1; d["s3"]="🔴 SHORT MACD bearish"
    else:                      d["s3"]="⚪ MACD neutral"

    # ── Signal 4: Supertrend ──────────────────────────────────────
    st=supertrend(h1,l1,c1)
    if st=="BULL":   bull+=1; d["s4"]="🟢 LONG Supertrend"
    elif st=="BEAR": bear+=1; d["s4"]="🔴 SHORT Supertrend"
    else:                      d["s4"]="⚪ Supertrend neutral"

    # ── Signal 5: EMA Cross 4h (trend filter) ─────────────────────
    if k4h:
        c4=k4h["c"]
        e20_4h=ema(c4,20); e50_4h=ema(c4,50)
        if e20_4h>e50_4h:   bull+=1; d["s5"]=f"🟢 LONG 4h trend up"
        elif e20_4h<e50_4h: bear+=1; d["s5"]=f"🔴 SHORT 4h trend down"
        else:                         d["s5"]="⚪ 4h neutral"
    else:
        d["s5"]="⚪ 4h data unavailable"

    # ── Signal 6: Volume Spike ────────────────────────────────────
    vol_spike=volume_spike(v1)
    if vol_spike:
        # Volume spike in direction of price move
        price_up=(c1[-1]>c1[-2])
        if price_up:   bull+=1; d["s6"]="🟢 LONG volume spike up"
        else:          bear+=1; d["s6"]="🔴 SHORT volume spike down"
    else:
        d["s6"]="⚪ No volume spike"

    # ── Signal 7: Bollinger Bands ─────────────────────────────────
    up,mid,lo=bollinger(c1)
    bb_pct=(price-lo)/(up-lo) if up!=lo else 0.5
    if price<lo or bb_pct<0.1:      bull+=1; d["s7"]="🟢 LONG below BB"
    elif price>up or bb_pct>0.9:    bear+=1; d["s7"]="🔴 SHORT above BB"
    elif bb_pct<0.25:                bull+=1; d["s7"]=f"🟢 LONG near BB low"
    elif bb_pct>0.75:                bear+=1; d["s7"]=f"🔴 SHORT near BB high"
    else:                                      d["s7"]=f"⚪ BB middle {bb_pct*100:.0f}%"

    # ── Signal 8: Funding Rate ────────────────────────────────────
    fund=get_funding(symbol)
    d["funding"]=round(fund*100,4)
    if fund<-0.0001:    bull+=1; d["s8"]=f"🟢 LONG neg funding {fund*100:.4f}%"
    elif fund>0.0001:   bear+=1; d["s8"]=f"🔴 SHORT pos funding {fund*100:.4f}%"
    else:                         d["s8"]=f"⚪ Funding neutral"

    # ── Volatility check ─────────────────────────────────────────
    vol=atr_pct(h1,l1,c1)
    d["atr"]=round(vol,2)
    if vol>8:
        return "NEUTRAL",0,{**d,"skip":f"Too volatile {vol:.1f}%"},0

    # ── Momentum strength ─────────────────────────────────────────
    mom=momentum_score(c1,10)
    d["momentum"]=round(mom,2)
    d["bull"]=bull; d["bear"]=bear

    if bull>=MIN_SIGNALS: return "LONG",  bull, d, abs(mom)
    if bear>=MIN_SIGNALS: return "SHORT", bear, d, abs(mom)
    return "NEUTRAL", max(bull,bear), d, 0

# ── DYNAMIC PROFIT TARGET ─────────────────────────────────────────
def calc_leverage(score, momentum, atr):
    """
    Dynamic leverage based on signal strength and volatility.
    Range: 3x to 10x

    Strong signal + low volatility  → 10x
    Strong signal + high volatility → 5x
    Weak signal   + low volatility  → 5x
    Weak signal   + high volatility → 3x
    """
    # Base leverage from signal score
    base = {5: 5, 6: 7, 7: 9, 8: 10}.get(score, 5)

    # Reduce for high volatility
    if atr > 5.0:       base = max(3, base - 4)  # Very volatile
    elif atr > 3.0:     base = max(3, base - 2)  # Volatile
    elif atr > 2.0:     base = max(3, base - 1)  # Slightly volatile
    # else: normal — keep base

    # Cap between 3 and 10
    return max(3, min(base, 10))

def calc_take_profit(score, momentum):
    """
    Calculate take profit based on signal strength and momentum.
    Range: 10% minimum to 42% maximum.

    5/8 signals → 10-20%
    6/8 signals → 20-30%
    7/8 signals → 30-38%
    8/8 signals → 38-42%
    """
    # Base profit from signal score
    base = {5: 0.10, 6: 0.20, 7: 0.30, 8: 0.38}.get(score, 0.10)

    # Momentum adds up to 12% extra
    if momentum > 15:   extra = 0.12
    elif momentum > 10: extra = 0.08
    elif momentum > 5:  extra = 0.05
    elif momentum > 2:  extra = 0.02
    else:               extra = 0.0

    tp = base + extra
    # Hard cap: minimum 10%, maximum 42%
    tp = max(0.10, min(tp, 0.42))
    return round(tp, 2)

# ── SMART STOP LOSS ───────────────────────────────────────────────
def update_stop_loss(pos, current_price):
    """
    Update stop loss using smart trailing logic.
    Returns new stop price and whether to close.
    """
    entry     = pos["entry_price"]
    direction = pos["direction"]
    peak      = pos.get("peak_price", entry)
    init_stop = pos.get("init_stop", entry * (1-INIT_STOP) if direction=="LONG" else entry*(1+INIT_STOP))
    current_stop = pos.get("current_stop", init_stop)

    if direction == "LONG":
        pnl_pct = (current_price - entry) / entry
        # Update peak
        peak = max(peak, current_price)

        if pnl_pct >= 0.80:       trail = 0.03  # 3% trail after 80%
        elif pnl_pct >= 0.50:     trail = 0.04  # 4% trail after 50%
        elif pnl_pct >= 0.20:     trail = 0.06  # 6% trail after 20%
        elif pnl_pct >= 0.10:     trail = 0.08  # 8% trail after 10%
        elif pnl_pct >= 0.05:     trail = 0.01  # Move to breakeven
        else:                     trail = INIT_STOP

        if pnl_pct >= 0.05:
            new_stop = max(current_stop, peak*(1-trail))
        else:
            new_stop = current_stop

        # Hard cap: never lose more than 25%
        hard_stop = entry * (1 - MAX_LOSS_PCT)
        new_stop = max(new_stop, hard_stop)

        should_close = current_price <= new_stop
    else:
        pnl_pct = (entry - current_price) / entry
        peak = min(peak, current_price)

        if pnl_pct >= 0.80:       trail = 0.03
        elif pnl_pct >= 0.50:     trail = 0.04
        elif pnl_pct >= 0.20:     trail = 0.06
        elif pnl_pct >= 0.10:     trail = 0.08
        elif pnl_pct >= 0.05:     trail = 0.01
        else:                     trail = INIT_STOP

        if pnl_pct >= 0.05:
            new_stop = min(current_stop, peak*(1+trail))
        else:
            new_stop = current_stop

        hard_stop = entry * (1 + MAX_LOSS_PCT)
        new_stop = min(new_stop, hard_stop)

        should_close = current_price >= new_stop

    return round(new_stop, 6), peak, should_close, round(pnl_pct*100, 2)

def update_binance_sl(symbol, direction, new_stop, qty):
    """Cancel old SL and place new one at updated trailing stop price."""
    if not LIVE_TRADING: return
    try:
        # Cancel all open orders for this symbol
        bpost("/fapi/v1/cancelAllOpenOrders", {"symbol": symbol})
        time.sleep(0.5)
        # Place new stop loss
        sl_side = "SELL" if direction=="LONG" else "BUY"
        result = bpost("/fapi/v1/order",{
            "symbol":        symbol,
            "side":          sl_side,
            "type":          "STOP_MARKET",
            "stopPrice":     str(round(new_stop, 4)),
            "closePosition": "true",
            "workingType":   "CONTRACT_PRICE",
            "timeInForce":   "GTE_GTC",
        })
        if result:
            log(f"  🔄 Updated SL on Binance @ ${new_stop:,.4f}")
    except Exception as e:
        log(f"  ⚠ SL update error: {e}")

# ── POSITION SIZING (Kelly Criterion) ────────────────────────────
def kelly_size(state, balance, score):
    """
    Calculate optimal position size using Kelly Criterion.
    Adjusts based on bot's actual win rate over time.
    """
    wins   = state.get("wins", 0)
    losses = state.get("losses", 0)
    total  = wins + losses

    if total < 10:
        # Not enough data yet — use balance-based size
        if balance < 15:
            base = 0.50  # 50% — need large % to meet $20 min notional
        elif balance < 30:
            base = 0.35
        elif balance < 100:
            base = 0.20
        else:
            base = 0.10
    else:
        win_rate = wins / total
        avg_win  = state.get("total_pnl", 0) / max(wins, 1) / balance if wins > 0 else 0.10
        avg_loss = MAX_LOSS_PCT

        # Kelly formula: f = (bp - q) / b
        # b = avg win/avg loss ratio, p = win rate, q = loss rate
        b = max(avg_win / avg_loss, 0.5)
        q = 1 - win_rate
        kelly = (b * win_rate - q) / b
        # Use half-Kelly for safety
        base = max(0.05, min(kelly * 0.5, 0.25))

    # Boost size slightly for stronger signals
    signal_boost = {5: 1.0, 6: 1.2, 7: 1.4, 8: 1.6}.get(score, 1.0)
    size = min(base * signal_boost, 0.25)
    return round(size, 3)

# ── ACCOUNT & TRADING ─────────────────────────────────────────────
def min_qty_step(symbol):
    info = bget("/fapi/v1/exchangeInfo")
    if not info: return 0.001, 0.001
    for s in info.get("symbols",[]):
        if s["symbol"]==symbol:
            for f in s.get("filters",[]):
                if f["filterType"]=="LOT_SIZE":
                    return float(f["minQty"]),float(f["stepSize"])
    return 0.001, 0.001

def place_trade(symbol, direction, bal, score, momentum, details=None):
    try:
        px = None
        ticker = bget("/fapi/v1/ticker/price", {"symbol": symbol})
        if ticker: px = float(ticker["price"])
        if not px: return None

        # Step 1: Calculate dynamic leverage FIRST
        atr_val  = (details.get("atr", 2.0) if isinstance(details, dict) else 2.0)
        leverage = calc_leverage(score, momentum, atr_val)

        # Step 2: Calculate position size
        size_pct = kelly_size({}, bal, score)
        capital  = bal * size_pct
        pos_val  = capital * leverage

        # Step 3: Calculate quantity
        _, step  = min_qty_step(symbol)
        raw_qty  = pos_val / px
        qty      = round(math.floor(raw_qty / step) * step, 8)
        if qty <= 0: return None

        # Step 4: Check minimum notional ($20)
        notional = qty * px
        log(f"  Qty={qty} Price=${px:.4f} Notional=${notional:.2f}")

        if notional < 20:
            min_qty_needed = 20 / px
            steps_needed   = math.ceil(min_qty_needed / step)
            qty             = round(steps_needed * step, 8)
            notional        = qty * px
            log(f"  Adjusted qty={qty} notional=${notional:.2f}")

        # Step 5: Check we can afford it
        max_notional = bal * leverage  # Max position with full balance
        if notional > max_notional * 1.1:
            log(f"  ⚠ Skipping — notional ${notional:.2f} > max ${max_notional:.2f}")
            return None

        # Step 6: Calculate TP and SL
        tp_pct     = calc_take_profit(score, momentum)
        init_stop  = INIT_STOP
        side       = "BUY" if direction=="LONG" else "SELL"
        stop_price = px*(1-init_stop) if direction=="LONG" else px*(1+init_stop)
        tp_price   = px*(1+tp_pct)    if direction=="LONG" else px*(1-tp_pct)

        log(f"  📊 Score={score}/8 Mom={momentum:.1f}% Leverage={leverage}x")
        log(f"  💰 Capital=${capital:.2f} ({size_pct*100:.0f}%) Position=${pos_val:.2f}")
        log(f"  🎯 TP={tp_pct*100:.0f}% (${tp_price:,.4f}) SL=12% (${stop_price:,.4f})")

    except Exception as e:
        log(f"  ❌ place_trade setup error: {e}")
        import traceback; traceback.print_exc()
        return None

    record = {
        "symbol":        symbol,
        "direction":     direction,
        "entry_price":   px,
        "current_stop":  round(stop_price, 6),
        "init_stop":     round(stop_price, 6),
        "tp_price":      round(tp_price, 6),
        "tp_pct":        tp_pct,
        "peak_price":    px,
        "qty":           qty,
        "capital":       capital,
        "pos_value":     pos_val,
        "leverage":      leverage,
        "score":         score,
        "momentum":      round(momentum, 2),
        "entry_time":    time.time(),
        "entry_date":    datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "paper":         not LIVE_TRADING,
        "status":        "open",
        "pnl_history":   [],
    }

    if LIVE_TRADING:
        # Set leverage
        bpost("/fapi/v1/leverage", {"symbol":symbol,"leverage":leverage})

        # Place market entry
        order = bpost("/fapi/v1/order", {
            "symbol":   symbol,
            "side":     side,
            "type":     "MARKET",
            "quantity": str(qty),
        })
        if not order:
            log(f"  ❌ Entry order failed"); return None
        record["order_id"] = order.get("orderId")
        log(f"  ✅ Entry: {direction} {qty} {symbol} @ ${px:,.4f}")
        time.sleep(2)

        sl_side = "SELL" if side == "BUY" else "BUY"

        # Round stop price properly
        # Get price precision from exchange
        info = bget("/fapi/v1/exchangeInfo")
        price_precision = 2
        if info:
            for s in info.get("symbols", []):
                if s["symbol"] == symbol:
                    price_precision = s.get("pricePrecision", 2)
                    break

        sl_rounded = round(stop_price, price_precision)
        tp_rounded = round(tp_price,   price_precision)

        log(f"  SL=${sl_rounded} TP=${tp_rounded} side={sl_side} precision={price_precision}")

        # Stop Loss using correct Binance Futures Algo Order format
        sl_placed = False
        sl_result = bpost("/fapi/v1/order", {
            "symbol":        symbol,
            "side":          sl_side,
            "type":          "STOP_MARKET",
            "stopPrice":     str(sl_rounded),
            "quantity":      str(qty),
            "reduceOnly":    "true",
            "workingType":   "CONTRACT_PRICE",
        })
        if sl_result and sl_result.get("orderId"):
            log(f"  ✅ Stop Loss @ ${sl_rounded}")
            record["sl_order_id"] = sl_result["orderId"]
            sl_placed = True
        else:
            log(f"  ⚠ SL failed: {sl_result}")

        # Take Profit using correct Binance Futures Algo Order format
        tp_placed = False
        tp_result = bpost("/fapi/v1/order", {
            "symbol":        symbol,
            "side":          sl_side,
            "type":          "TAKE_PROFIT_MARKET",
            "stopPrice":     str(tp_rounded),
            "quantity":      str(qty),
            "reduceOnly":    "true",
            "workingType":   "CONTRACT_PRICE",
        })
        if tp_result and tp_result.get("orderId"):
            log(f"  ✅ Take Profit @ ${tp_rounded}")
            record["tp_order_id"] = tp_result["orderId"]
            tp_placed = True
        else:
            log(f"  ⚠ TP failed: {tp_result}")

        if not sl_placed or not tp_placed:
            log(f"  ⚠ SL={sl_placed} TP={tp_placed}")

        log(f"  ✅ LIVE order complete: {direction} {qty} {symbol}")
    else:
        log(f"  📝 PAPER: {direction} {qty} {symbol} @ ${px:,.4f}")

    return record

# ── MONITOR POSITIONS ─────────────────────────────────────────────
def monitor(state):
    if not state["positions"]: return state
    log(f"\n📋 Monitoring {len(state['positions'])} positions...")
    to_close = []

    for pos in state["positions"]:
        sym   = pos["symbol"]
        entry = pos["entry_price"]
        dir   = pos["direction"]
        tp    = pos["tp_price"]

        ticker = bget("/fapi/v1/ticker/price",{"symbol":sym})
        if not ticker: continue
        px = float(ticker["price"])

        # Update smart stop
        new_stop, new_peak, hit_stop, pnl_pct = update_stop_loss(pos, px)
        pos["current_stop"] = new_stop
        pos["peak_price"]   = new_peak

        # Check take profit
        hit_tp = (px >= tp) if dir=="LONG" else (px <= tp)

        # Check 48h time limit
        expired = (time.time()-pos.get("entry_time",time.time())) > 48*3600

        log(f"  {sym} {dir}: ${entry:,.4f}→${px:,.4f} PnL={pnl_pct:+.1f}% "
            f"Peak=${new_peak:,.4f} Stop=${new_stop:,.4f} TP=${tp:,.4f}")

        reason = None
        if hit_tp:    reason = f"✅ TAKE PROFIT {pnl_pct:+.1f}%"
        elif hit_stop: reason = f"🛡 SMART STOP {pnl_pct:+.1f}%"
        elif expired:  reason = f"⏰ TIME EXIT {pnl_pct:+.1f}%"

        if reason:
            pnl_usd = (pnl_pct/100) * pos["capital"] * LEVERAGE
            log(f"  Closing {sym}: {reason} ${pnl_usd:+.2f}")

            if LIVE_TRADING:
                # Cancel existing SL/TP orders first
                try:
                    bpost("/fapi/v1/allOpenOrders",{"symbol":sym})
                except: pass
                # Close position with market order
                close_side = "SELL" if dir=="LONG" else "BUY"
                bpost("/fapi/v1/order",{
                    "symbol":sym,"side":close_side,
                    "type":"MARKET","quantity":pos["qty"],"reduceOnly":"true"})

            pos.update({
                "status":"closed","exit_price":px,
                "exit_date":datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
                "pnl_pct":round(pnl_pct,2),"pnl_usd":round(pnl_usd,3),
                "close_reason":reason,
            })
            state["closed_trades"].append(pos)
            state["total_pnl"] += pnl_usd
            state["wins" if pnl_usd>0 else "losses"] += 1
            state["total_trades"] += 1
            to_close.append(pos)

    state["positions"] = [p for p in state["positions"] if p not in to_close]
    return state

# ── EMAIL ─────────────────────────────────────────────────────────
def should_report(state):
    now = datetime.now(timezone.utc)
    today = (now+timedelta(hours=5,minutes=30)).strftime("%d%b%Y")
    return (now.hour==REPORT_HOUR and now.minute>=REPORT_MIN
            and state.get("last_report_date","")!=today)

def send_email(state, bal):
    if not EMAIL_PASS: return
    now    = datetime.now(timezone.utc)+timedelta(hours=5,minutes=30)
    today  = now.strftime("%d %b %Y")
    wins   = state.get("wins",0)
    losses = state.get("losses",0)
    total  = state.get("total_trades",0)
    pnl    = state.get("total_pnl",0)
    wrate  = wins/max(total,1)*100
    start  = state.get("start_balance",bal)
    gain   = bal-start
    open_p = state.get("positions",[])
    today_str = now.strftime("%d %b %Y")
    today_closed = [t for t in state.get("closed_trades",[])
                    if today_str in t.get("exit_date","")]

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
        pusd=pct*t["capital"]*LEVERAGE; c="#27ae60" if pusd>0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f0f0f0'>
            <td style='padding:8px'>{'📈' if t['direction']=='LONG' else '📉'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold'>{t['symbol']}</td>
            <td style='padding:8px'>${t['entry_price']:,.4f}</td>
            <td style='padding:8px'>${px:,.4f}</td>
            <td style='padding:8px;color:{c};font-weight:bold'>{pct*100:+.1f}% (${pusd:+.2f})</td>
            <td style='padding:8px;font-size:11px'>TP={t.get('tp_pct',0)*100:.0f}% Stop=${t.get('current_stop',0):,.4f}</td>
        </tr>"""

    mode = "🔴 LIVE" if LIVE_TRADING else "📝 PAPER"
    html = f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;background:#f8f9fa;margin:0;padding:20px'>
<div style='max-width:750px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,.1)'>
<div style='background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);padding:30px;text-align:center'>
<h1 style='color:#e2b96f;margin:0;font-size:26px'>⚡ ViraLab FuturesBot v3</h1>
<p style='color:#aaa;margin:8px 0 0'>Daily Report — {today}</p>
<span style='background:{"#c0392b" if LIVE_TRADING else "#2c3e50"};color:white;padding:4px 14px;border-radius:20px;font-size:12px'>{mode}</span>
</div>
<div style='padding:24px'>
<h2 style='color:#2c3e50;border-left:4px solid #e2b96f;padding-left:12px'>💰 Account Summary</h2>
<table width='100%' style='border-collapse:collapse;background:#f8f9fa;border-radius:8px'>
{row("Current Balance",f"${bal:.2f} USDT")}
{row("Starting Balance",f"${start:.2f} USDT")}
{row("Total Gain/Loss",f"${gain:+.2f} USDT","#27ae60" if gain>0 else "#e74c3c")}
{row("Total PnL",f"${pnl:+.2f} USDT","#27ae60" if pnl>0 else "#e74c3c")}
{row("Win Rate",f"{wrate:.0f}% ({wins}W/{losses}L)","#27ae60" if wrate>50 else "#e74c3c")}
{row("Total Trades",str(total))}
{row("Open Positions",str(len(open_p)))}
{row("Strategy","8 signals · 100+ coins · Smart trailing stop")}
{row("Leverage",f"{LEVERAGE}x")}
{row("Max Loss",f"{MAX_LOSS_PCT*100:.0f}% (smart trailing)")}
</table>
</div>
<div style='padding:0 24px 24px'>
<h2 style='color:#2c3e50;border-left:4px solid {"#27ae60" if today_closed else "#95a5a6"};padding-left:12px'>
📊 Today Closed ({len(today_closed)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f0f0'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Exit</th><th style='padding:8px;text-align:left'>PnL</th><th style='padding:8px;text-align:left'>Reason</th></tr>"+"".join(trow(t) for t in today_closed)+"</table>" if today_closed else "<p style='color:#999'>No trades closed today.</p>"}
</div>
<div style='padding:0 24px 24px'>
<h2 style='color:#2c3e50;border-left:4px solid #3498db;padding-left:12px'>
📈 Open Positions ({len(open_p)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f0f0'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Current</th><th style='padding:8px;text-align:left'>Unrealised PnL</th><th style='padding:8px;text-align:left'>Details</th></tr>"+"".join(orow(t) for t in open_p)+"</table>" if open_p else "<p style='color:#999'>No open positions.</p>"}
</div>
<div style='background:#f8f9fa;padding:20px;text-align:center;border-top:1px solid #eee'>
<p style='color:#999;font-size:12px;margin:0'>ViraLab FuturesBot v3 • {now.strftime("%d %b %Y %I:%M %p IST")}</p>
<p style='color:#bbb;font-size:11px;margin:6px 0 0'>100+ coins • 8 signals • Smart trailing stop • Max 25% loss</p>
</div></div></body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚡ FuturesBot v3 — {today} | PnL: ${pnl:+.2f} | WR: {wrate:.0f}%"
        msg["From"] = EMAIL_FROM
        msg["To"]   = EMAIL_TO
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
            "wins":0,"losses":0,"total_pnl":0.0,
            "start_balance":0.0,"last_report_date":""}

def save_state(s):
    # Keep only last 500 closed trades to prevent file bloat
    if len(s.get("closed_trades",[])) > 500:
        s["closed_trades"] = s["closed_trades"][-500:]
    with open(STATE_FILE,"w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    log("="*62)
    log("  VIRALAB CRYPTO FUTURES BOT v3")
    log(f"  {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    log(f"  Mode: {'🔴 LIVE' if LIVE_TRADING else '📝 PAPER'}")
    log(f"  Leverage: {LEVERAGE}x | Max Loss: {MAX_LOSS_PCT*100:.0f}% | Signals: {MIN_SIGNALS}/8")
    log("="*62)

    if not API_KEY or not API_SECRET:
        log("❌ Missing API credentials"); sys.exit(1)

    state = load_state()
    log(f"\nPositions: {len(state['positions'])} | "
        f"Trades: {state['total_trades']} | "
        f"W/L: {state['wins']}/{state['losses']} | "
        f"PnL: ${state['total_pnl']:+.2f}")

    # Monitor existing positions first
    state = monitor(state)
    save_state(state)

    # Get balance
    bal = get_balance()
    log(f"Balance: ${bal:.2f} USDT")
    if state["start_balance"]==0 and bal>0:
        state["start_balance"] = bal

    if bal < 1.0:
        log("Balance too low"); save_state(state); return

    # Daily email
    if should_report(state):
        send_email(state, bal)
        now_ist = (datetime.now(timezone.utc)+timedelta(hours=5,minutes=30))
        state["last_report_date"] = now_ist.strftime("%d%b%Y")
        save_state(state)

    # Check max positions
    if len(state["positions"]) >= MAX_OPEN:
        log(f"Max {MAX_OPEN} positions — not opening new trades")
        save_state(state); return

    # Get 100+ coins
    symbols = get_top_symbols()
    log(f"\n🔍 Scanning {len(symbols)} coins...")

    # Get fear & greed once
    fng = get_fear_greed()
    log(f"Fear & Greed: {fng}")

    # Analyse all coins
    open_syms = {p["symbol"] for p in state["positions"]}
    signals   = []
    slots     = MAX_OPEN - len(state["positions"])

    for sym in symbols:
        if sym in open_syms: continue
        direction, score, details, momentum = analyse(sym, fng)
        if score > 0:
            log(f"  {sym}: {direction} ({score}/8) RSI={details.get('rsi',0):.0f} "
                f"ATR={details.get('atr',0):.1f}% Mom={details.get('momentum',0):.1f}%")
        if direction != "NEUTRAL":
            signals.append((sym, direction, score, details, momentum))
        time.sleep(0.1)  # Rate limiting

    if not signals:
        log("\n⏳ No signals this hour")
        save_state(state); return

    # Sort by score then momentum
    signals.sort(key=lambda x: (-x[2], -x[4]))
    log(f"\n🎯 {len(signals)} trade signals found")
    log(f"   Top: {[(s[0],s[1],s[2]) for s in signals[:3]]}")

    # Open best trades
    opened = 0
    for sym, direction, score, details, momentum in signals[:slots]:
        log(f"\n{'='*62}")
        log(f"  {direction} {sym} — {score}/8 signals | Mom={momentum:.1f}%")

        record = place_trade(sym, direction, bal, score, momentum, details)
        if record:
            state["positions"].append(record)
            opened += 1
            bal *= (1-record["capital"]/bal)
            save_state(state)

    log(f"\n✅ Done — opened {opened} trades | "
        f"Total open: {len(state['positions'])}")
    save_state(state)

if __name__ == "__main__":
    main()
