"""
futures_bot.py — ViraLab Crypto Futures Bot v2
================================================
YOUR SETTINGS:
  Stop Loss:   8%
  Take Profit: 10%
  Leverage:    10x (optimal for small capital)
  Coins:       BTC, ETH, BNB, SOL, XRP, DOGE, ADA, AVAX
  Email:       Himanshu.r.garg@icloud.com (daily summary)
  Alerts:      No Telegram — email only at 9 PM IST daily

STRATEGY:
  - LONG when 4/6 signals bullish
  - SHORT when 4/6 signals bearish
  - Max 4 positions open at once
  - Max 15% of balance per trade
  - Skips if ATR volatility too extreme (>8%)
  - Auto SL and TP orders placed immediately

SIGNALS:
  1. RSI (overbought/oversold)
  2. MACD (trend direction)
  3. EMA Cross 20/50 (trend)
  4. Bollinger Bands (breakout)
  5. Fear & Greed Index (sentiment)
  6. Funding Rate (positioning)

EMAIL REPORT (daily at 9 PM IST = 15:30 UTC):
  - All trades opened today
  - All trades closed today with PnL
  - Running positions
  - Total balance and PnL
"""

import os, sys, json, time, math, hmac, hashlib, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

# ── PROXY SETUP ───────────────────────────────────────────────────
# Uses Webshare.io free static residential proxy
# Get 10 free proxies at: https://proxy.webshare.io
# Add WEBSHARE_PROXY secret to GitHub in format: host:port:user:pass

def get_proxy():
    """Get proxy from GitHub secret WEBSHARE_PROXY or env variable."""
    proxy_str = os.environ.get("WEBSHARE_PROXY", "")
    if not proxy_str:
        print("  ⚠ No WEBSHARE_PROXY set — direct connection")
        return None
    
    try:
        parts = proxy_str.strip().split(":")
        if len(parts) == 4:
            host, port, user, pwd = parts
            proxy_url = f"http://{user}:{pwd}@{host}:{port}"
        elif len(parts) == 2:
            host, port = parts
            proxy_url = f"http://{host}:{port}"
        else:
            print(f"  ⚠ Invalid proxy format: {proxy_str}")
            return None
        
        proxy = {"http": proxy_url, "https": proxy_url}
        
        # Test proxy
        r = requests.get("https://fapi.binance.com/fapi/v1/time",
                        proxies=proxy, timeout=10, verify=False)
        if r.status_code == 200:
            print(f"  ✅ Proxy connected: {host}:{port}")
            return proxy
        else:
            print(f"  ⚠ Proxy test failed: {r.status_code}")
            return None
    except Exception as e:
        print(f"  ⚠ Proxy error: {e}")
        return None

print("[PROXY] Connecting via Webshare proxy...")
PROXY = get_proxy()

# ── CREDENTIALS ──────────────────────────────────────────────────
API_KEY      = os.environ.get("BINANCE_API_KEY", "")
API_SECRET   = os.environ.get("BINANCE_API_SECRET", "")
EMAIL_PASS   = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_FROM   = os.environ.get("EMAIL_FROM", "himanshu9306077161@gmail.com")
EMAIL_TO     = "Himanshu.r.garg@icloud.com"
LIVE_TRADING = os.environ.get("LIVE_TRADING", "false").lower() == "true"

# ── CONFIG ────────────────────────────────────────────────────────
FUTURES_URL  = "https://fapi.binance.com"
LEVERAGE     = 10          # 10x leverage
MAX_RISK_PCT = 0.15        # 15% of balance per trade
TAKE_PROFIT  = 0.10        # 10% take profit
STOP_LOSS    = 0.08        # 8% stop loss
MAX_OPEN     = 4           # Max 4 positions
MIN_SIGNALS  = 4           # 4/6 signals needed
MAX_ATR_PCT  = 8.0         # Skip if volatility > 8%
STATE_FILE   = "futures_state.json"
REPORT_HOUR  = 15          # 15:30 UTC = 9 PM IST
REPORT_MIN   = 30

# All coins to trade
SYMBOLS = [
    "BTCUSDT",   # Bitcoin
    "ETHUSDT",   # Ethereum
    "BNBUSDT",   # BNB
    "SOLUSDT",   # Solana
    "XRPUSDT",   # XRP
    "DOGEUSDT",  # Dogecoin
    "ADAUSDT",   # Cardano
    "AVAXUSDT",  # Avalanche
    "LINKUSDT",  # Chainlink
    "MATICUSDT", # Polygon
]

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
                         proxies=PROXY,
                         timeout=15,
                         verify=False)
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
                          proxies=PROXY,
                          timeout=15,
                          verify=False)
        if r.status_code == 200: return r.json()
        log(f"POST {path} error {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        log(f"POST error: {e}"); return None

# ── MARKET DATA ───────────────────────────────────────────────────
def klines(symbol, interval="1h", limit=150):
    data = bget("/fapi/v1/klines",
                {"symbol": symbol, "interval": interval, "limit": limit})
    if not data: return None
    return {
        "c": [float(k[4]) for k in data],
        "h": [float(k[2]) for k in data],
        "l": [float(k[3]) for k in data],
        "v": [float(k[5]) for k in data],
    }

def price(symbol):
    d = bget("/fapi/v1/ticker/price", {"symbol": symbol})
    return float(d["price"]) if d else None

def funding(symbol):
    d = bget("/fapi/v1/premiumIndex", {"symbol": symbol})
    return float(d.get("lastFundingRate", 0)) if d else 0.0

def fear_greed():
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=8, proxies=PROXY)
        if r.status_code == 200:
            return int(r.json()["data"][0]["value"])
    except: pass
    return 50

def balance():
    data = bget("/fapi/v2/balance", signed=True)
    if not data: return 0.0
    for a in data:
        if a.get("asset") == "USDT":
            return float(a.get("availableBalance", 0))
    return 0.0

def open_positions():
    data = bget("/fapi/v2/positionRisk", signed=True)
    if not data: return []
    return [p for p in data if abs(float(p.get("positionAmt", 0))) > 0]

def symbol_info(symbol):
    data = bget("/fapi/v1/exchangeInfo")
    if not data: return None
    for s in data.get("symbols", []):
        if s["symbol"] == symbol:
            return s
    return None

def min_qty_step(symbol):
    info = symbol_info(symbol)
    if not info: return 0.001, 0.001
    for f in info.get("filters", []):
        if f["filterType"] == "LOT_SIZE":
            return float(f["minQty"]), float(f["stepSize"])
    return 0.001, 0.001

# ── INDICATORS ────────────────────────────────────────────────────
def ema(closes, p):
    if len(closes) < p: return closes[-1]
    k   = 2 / (p + 1)
    v   = sum(closes[:p]) / p
    for c in closes[p:]: v = c * k + v * (1 - k)
    return v

def rsi(closes, p=14):
    if len(closes) < p+1: return 50
    g, l = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        g.append(max(d,0)); l.append(max(-d,0))
    ag = sum(g[-p:]) / p
    al = sum(l[-p:]) / p
    return 100 if al == 0 else 100 - 100 / (1 + ag/al)

def macd_vals(closes):
    if len(closes) < 26: return 0, 0
    m = ema(closes, 12) - ema(closes, 26)
    s = m * 0.85
    return m, s

def bollinger(closes, p=20):
    if len(closes) < p: return closes[-1], closes[-1], closes[-1]
    sl  = closes[-p:]
    mid = sum(sl) / p
    std = math.sqrt(sum((x-mid)**2 for x in sl) / p)
    return mid+2*std, mid, mid-2*std

def atr_pct(highs, lows, closes, p=14):
    if len(closes) < p+1: return 0
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i-1]),
               abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    avg = sum(trs[-p:]) / p
    return (avg / closes[-1]) * 100

# ── SIGNAL ENGINE ─────────────────────────────────────────────────
def analyse(symbol, fng):
    k = klines(symbol)
    if not k: return "NEUTRAL", 0, {}

    c = k["c"]; h = k["h"]; l = k["l"]
    px = c[-1]
    bull = bear = 0
    d = {"price": px, "symbol": symbol}

    # 1 RSI
    r = rsi(c)
    d["rsi"] = round(r, 1)
    if r < 35:   bull += 1; d["rsi_s"] = f"LONG (RSI {r:.0f} oversold)"
    elif r > 65: bear += 1; d["rsi_s"] = f"SHORT (RSI {r:.0f} overbought)"
    else:                    d["rsi_s"] = f"NEUTRAL (RSI {r:.0f})"

    # 2 MACD
    m, s = macd_vals(c)
    if m > s:   bull += 1; d["macd_s"] = "LONG (bullish)"
    elif m < s: bear += 1; d["macd_s"] = "SHORT (bearish)"
    else:                   d["macd_s"] = "NEUTRAL"

    # 3 EMA Cross
    e20 = ema(c, 20); e50 = ema(c, 50)
    if e20 > e50:   bull += 1; d["ema_s"] = f"LONG (EMA20>{e20:.0f} > EMA50>{e50:.0f})"
    elif e20 < e50: bear += 1; d["ema_s"] = f"SHORT (EMA20<EMA50)"
    else:                       d["ema_s"] = "NEUTRAL"

    # 4 Bollinger
    up, mid, lo = bollinger(c)
    bb_pct = (px - lo) / (up - lo) if up != lo else 0.5
    if px < lo or bb_pct < 0.15:
        bull += 1; d["bb_s"] = f"LONG (near/below lower band)"
    elif px > up or bb_pct > 0.85:
        bear += 1; d["bb_s"] = f"SHORT (near/above upper band)"
    else:
        d["bb_s"] = f"NEUTRAL (BB {bb_pct*100:.0f}%)"

    # 5 Fear & Greed
    d["fng"] = fng
    if fng <= 30:   bull += 1; d["fng_s"] = f"LONG (Fear {fng})"
    elif fng >= 70: bear += 1; d["fng_s"] = f"SHORT (Greed {fng})"
    elif fng <= 45: bull += 1; d["fng_s"] = f"LONG (mild fear {fng})"
    elif fng >= 55: bear += 1; d["fng_s"] = f"SHORT (mild greed {fng})"
    else:                       d["fng_s"] = f"NEUTRAL (F&G {fng})"

    # 6 Funding Rate
    fund = funding(symbol)
    d["funding"] = round(fund * 100, 4)
    if fund < -0.0002:   bull += 1; d["fund_s"] = f"LONG (neg funding {fund*100:.4f}%)"
    elif fund > 0.0002:  bear += 1; d["fund_s"] = f"SHORT (pos funding {fund*100:.4f}%)"
    else:                            d["fund_s"] = f"NEUTRAL ({fund*100:.4f}%)"

    # Volatility
    vol = atr_pct(h, l, c)
    d["atr"] = round(vol, 2)
    d["bull"] = bull; d["bear"] = bear

    if vol > MAX_ATR_PCT:
        return "NEUTRAL", 0, {**d, "skip": f"High volatility {vol:.1f}%"}

    if bull >= MIN_SIGNALS: return "LONG",  bull, d
    if bear >= MIN_SIGNALS: return "SHORT", bear, d
    return "NEUTRAL", max(bull, bear), d

# ── TRADING ───────────────────────────────────────────────────────
def set_leverage(symbol):
    return bpost("/fapi/v1/leverage",
                 {"symbol": symbol, "leverage": LEVERAGE})

def place_trade(symbol, direction, bal):
    capital  = bal * MAX_RISK_PCT
    pos_val  = capital * LEVERAGE
    px       = price(symbol)
    if not px: return None

    _, step  = min_qty_step(symbol)
    raw_qty  = pos_val / px
    steps    = math.floor(raw_qty / step)
    qty      = round(steps * step, 8)
    if qty <= 0: return None

    side     = "BUY" if direction == "LONG" else "SELL"
    sl_price = px * (1 - STOP_LOSS)  if direction == "LONG" else px * (1 + STOP_LOSS)
    tp_price = px * (1 + TAKE_PROFIT) if direction == "LONG" else px * (1 - TAKE_PROFIT)

    record = {
        "symbol":      symbol,
        "direction":   direction,
        "entry_price": px,
        "sl_price":    round(sl_price, 4),
        "tp_price":    round(tp_price, 4),
        "qty":         qty,
        "capital":     capital,
        "pos_value":   pos_val,
        "leverage":    LEVERAGE,
        "entry_time":  time.time(),
        "entry_date":  datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "paper":       not LIVE_TRADING,
        "status":      "open",
    }

    if LIVE_TRADING:
        set_leverage(symbol)

        # Market entry
        order = bpost("/fapi/v1/order", {
            "symbol": symbol, "side": side,
            "type": "MARKET", "quantity": qty,
        })
        if not order:
            log(f"  ❌ Market order failed for {symbol}")
            return None
        record["order_id"] = order.get("orderId")

        # Stop Loss
        sl_side = "SELL" if side == "BUY" else "BUY"
        bpost("/fapi/v1/order", {
            "symbol": symbol, "side": sl_side,
            "type": "STOP_MARKET",
            "stopPrice": round(sl_price, 2),
            "closePosition": "true",
            "timeInForce": "GTE_GTC",
        })

        # Take Profit
        bpost("/fapi/v1/order", {
            "symbol": symbol, "side": sl_side,
            "type": "TAKE_PROFIT_MARKET",
            "stopPrice": round(tp_price, 2),
            "closePosition": "true",
            "timeInForce": "GTE_GTC",
        })
        log(f"  ✅ LIVE order placed: {direction} {qty} {symbol} @ ${px:,.4f}")
    else:
        log(f"  📝 PAPER: {direction} {qty} {symbol} @ ${px:,.4f}")
        log(f"     SL: ${sl_price:,.4f} | TP: ${tp_price:,.4f}")

    return record

# ── POSITION MONITOR ──────────────────────────────────────────────
def monitor(state):
    if not state["positions"]: return state
    log(f"\n📋 Monitoring {len(state['positions'])} positions...")
    to_close = []
    now = time.time()

    for pos in state["positions"]:
        sym   = pos["symbol"]
        entry = pos["entry_price"]
        sl    = pos["sl_price"]
        tp    = pos["tp_price"]
        qty   = pos["qty"]
        dir   = pos["direction"]

        px = price(sym)
        if not px: continue

        pnl_pct = ((px-entry)/entry) if dir=="LONG" else ((entry-px)/entry)
        pnl_usd = pnl_pct * pos["capital"] * LEVERAGE

        hit_tp = px >= tp if dir=="LONG" else px <= tp
        hit_sl = px <= sl if dir=="LONG" else px >= sl
        expired = (now - pos.get("entry_time",now)) > 48*3600

        reason = None
        if hit_tp:  reason = f"TAKE PROFIT +{pnl_pct*100:.2f}%"
        elif hit_sl: reason = f"STOP LOSS {pnl_pct*100:.2f}%"
        elif expired: reason = "TIME EXIT (48h)"

        if reason:
            log(f"  Closing {sym} {dir}: {reason} PnL=${pnl_usd:+.3f}")
            if LIVE_TRADING:
                close_side = "SELL" if dir == "LONG" else "BUY"
                bpost("/fapi/v1/order", {
                    "symbol": sym, "side": close_side,
                    "type": "MARKET", "quantity": qty, "reduceOnly": "true",
                })
            pos.update({
                "status":     "closed",
                "exit_price": px,
                "exit_date":  datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
                "pnl_pct":    round(pnl_pct*100, 2),
                "pnl_usd":    round(pnl_usd, 3),
                "close_reason": reason,
            })
            state["closed_trades"].append(pos)
            state["total_pnl"] += pnl_usd
            state["wins" if pnl_usd > 0 else "losses"] += 1
            state["total_trades"] += 1
            to_close.append(pos)

    state["positions"] = [p for p in state["positions"] if p not in to_close]
    return state

# ── EMAIL REPORT ──────────────────────────────────────────────────
def should_send_report():
    """Send report once daily at 9 PM IST (15:30 UTC)."""
    now = datetime.now(timezone.utc)
    return now.hour == REPORT_HOUR and now.minute >= REPORT_MIN

def send_daily_email(state, bal):
    """Send daily trading summary to Himanshu's email."""
    if not EMAIL_PASS:
        log("No email password set — skipping email")
        return

    now    = datetime.now(timezone.utc)
    ist    = now + timedelta(hours=5, minutes=30)
    today  = ist.strftime("%d %b %Y")

    wins       = state.get("wins", 0)
    losses     = state.get("losses", 0)
    total_pnl  = state.get("total_pnl", 0)
    total_tr   = state.get("total_trades", 0)
    win_rate   = wins / max(total_tr, 1) * 100
    open_pos   = state.get("positions", [])
    start_bal  = state.get("start_balance", bal)
    total_gain = bal - start_bal

    # Today's closed trades
    today_str  = ist.strftime("%d %b %Y")
    today_closed = [
        t for t in state.get("closed_trades", [])
        if today_str in t.get("exit_date", "")
    ]
    today_opened = [
        t for t in open_pos
        if today_str in t.get("entry_date", "")
    ]

    # HTML email
    def row(label, val, color=""):
        style = f' style="color:{color};font-weight:bold;"' if color else ""
        return f"<tr><td style='padding:6px 12px;color:#666;'>{label}</td><td{style} style='padding:6px 12px;'>{val}</td></tr>"

    def trade_row(t):
        pnl  = t.get("pnl_usd", 0)
        pct  = t.get("pnl_pct", 0)
        col  = "#27ae60" if pnl > 0 else "#e74c3c"
        icon = "✅" if pnl > 0 else "❌"
        return f"""<tr style='border-bottom:1px solid #f0f0f0;'>
            <td style='padding:8px;'>{icon} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold;'>{t['symbol']}</td>
            <td style='padding:8px;'>${t.get('entry_price',0):,.4f}</td>
            <td style='padding:8px;'>${t.get('exit_price',0):,.4f}</td>
            <td style='padding:8px;color:{col};font-weight:bold;'>{pct:+.2f}% (${pnl:+.3f})</td>
            <td style='padding:8px;font-size:11px;color:#999;'>{t.get('close_reason','')}</td>
        </tr>"""

    def open_row(t):
        px  = price(t["symbol"]) or t["entry_price"]
        pct = ((px-t["entry_price"])/t["entry_price"]) if t["direction"]=="LONG" else ((t["entry_price"]-px)/t["entry_price"])
        pnl = pct * t["capital"] * LEVERAGE
        col = "#27ae60" if pnl > 0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f0f0f0;'>
            <td style='padding:8px;'>{'📈' if t['direction']=='LONG' else '📉'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold;'>{t['symbol']}</td>
            <td style='padding:8px;'>${t['entry_price']:,.4f}</td>
            <td style='padding:8px;'>${px:,.4f}</td>
            <td style='padding:8px;color:{col};font-weight:bold;'>{pct*100:+.2f}% (${pnl:+.3f})</td>
            <td style='padding:8px;font-size:11px;'>SL: ${t['sl_price']:,.4f} | TP: ${t['tp_price']:,.4f}</td>
        </tr>"""

    mode_badge = "🔴 LIVE TRADING" if LIVE_TRADING else "📝 PAPER TRADING"

    html = f"""<!DOCTYPE html>
<html><head><meta charset='UTF-8'></head>
<body style='font-family:Arial,sans-serif;background:#f8f9fa;margin:0;padding:20px;'>
<div style='max-width:700px;margin:0 auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 20px rgba(0,0,0,0.1);'>

  <!-- Header -->
  <div style='background:linear-gradient(135deg,#1a1a2e,#16213e,#0f3460);padding:30px;text-align:center;'>
    <h1 style='color:#e2b96f;margin:0;font-size:24px;'>⚡ ViraLab FuturesBot</h1>
    <p style='color:#aaa;margin:8px 0 0;'>Daily Trading Report — {today}</p>
    <span style='background:{"#c0392b" if LIVE_TRADING else "#2c3e50"};color:white;padding:4px 14px;border-radius:20px;font-size:12px;'>{mode_badge}</span>
  </div>

  <!-- Account Summary -->
  <div style='padding:24px;'>
    <h2 style='color:#2c3e50;border-left:4px solid #e2b96f;padding-left:12px;margin-top:0;'>💰 Account Summary</h2>
    <table width='100%' style='border-collapse:collapse;background:#f8f9fa;border-radius:8px;'>
      {row("Current Balance", f"${bal:.2f} USDT")}
      {row("Starting Balance", f"${start_bal:.2f} USDT")}
      {row("Total Gain/Loss", f"${total_gain:+.2f} USDT", "#27ae60" if total_gain>0 else "#e74c3c")}
      {row("Total PnL (all time)", f"${total_pnl:+.3f} USDT", "#27ae60" if total_pnl>0 else "#e74c3c")}
      {row("Total Trades", str(total_tr))}
      {row("Win Rate", f"{win_rate:.1f}% ({wins}W / {losses}L)", "#27ae60" if win_rate>50 else "#e74c3c")}
      {row("Open Positions", str(len(open_pos)))}
      {row("Leverage", f"{LEVERAGE}x")}
      {row("Stop Loss", f"{STOP_LOSS*100:.0f}%")}
      {row("Take Profit", f"{TAKE_PROFIT*100:.0f}%")}
    </table>
  </div>

  <!-- Today's Closed Trades -->
  <div style='padding:0 24px 24px;'>
    <h2 style='color:#2c3e50;border-left:4px solid {"#27ae60" if today_closed else "#95a5a6"};padding-left:12px;'>
      📊 Today's Closed Trades ({len(today_closed)})
    </h2>
    {"<table width='100%' style='border-collapse:collapse;'><tr style='background:#f0f0f0;'><th style='padding:8px;text-align:left;'>Side</th><th style='padding:8px;text-align:left;'>Symbol</th><th style='padding:8px;text-align:left;'>Entry</th><th style='padding:8px;text-align:left;'>Exit</th><th style='padding:8px;text-align:left;'>PnL</th><th style='padding:8px;text-align:left;'>Reason</th></tr>" + "".join(trade_row(t) for t in today_closed) + "</table>" if today_closed else "<p style='color:#999;'>No trades closed today.</p>"}
  </div>

  <!-- Open Positions -->
  <div style='padding:0 24px 24px;'>
    <h2 style='color:#2c3e50;border-left:4px solid #3498db;padding-left:12px;'>
      📈 Currently Open Positions ({len(open_pos)})
    </h2>
    {"<table width='100%' style='border-collapse:collapse;'><tr style='background:#f0f0f0;'><th style='padding:8px;text-align:left;'>Side</th><th style='padding:8px;text-align:left;'>Symbol</th><th style='padding:8px;text-align:left;'>Entry</th><th style='padding:8px;text-align:left;'>Current</th><th style='padding:8px;text-align:left;'>Unrealised PnL</th><th style='padding:8px;text-align:left;'>Levels</th></tr>" + "".join(open_row(t) for t in open_pos) + "</table>" if open_pos else "<p style='color:#999;'>No open positions.</p>"}
  </div>

  <!-- Today Opened -->
  <div style='padding:0 24px 24px;'>
    <h2 style='color:#2c3e50;border-left:4px solid #9b59b6;padding-left:12px;'>
      🆕 Trades Opened Today ({len(today_opened)})
    </h2>
    {"<ul>" + "".join(f"<li style='margin:6px 0;'><b>{t['direction']}</b> {t['symbol']} @ ${t['entry_price']:,.4f} — SL: ${t['sl_price']:,.4f} | TP: ${t['tp_price']:,.4f}</li>" for t in today_opened) + "</ul>" if today_opened else "<p style='color:#999;'>No new trades opened today.</p>"}
  </div>

  <!-- Footer -->
  <div style='background:#f8f9fa;padding:20px;text-align:center;border-top:1px solid #eee;'>
    <p style='color:#999;font-size:12px;margin:0;'>
      ViraLab FuturesBot • Report generated {ist.strftime("%d %b %Y %I:%M %p IST")}
    </p>
    <p style='color:#bbb;font-size:11px;margin:6px 0 0;'>
      This is an automated trading bot report. Past performance does not guarantee future results.
    </p>
  </div>
</div>
</body></html>"""

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"⚡ FuturesBot Daily Report — {today} | PnL: ${total_pnl:+.2f}"
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg.attach(MIMEText(html, "html"))

        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx) as s:
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
    return {"positions":[], "closed_trades":[], "total_trades":0,
            "wins":0, "losses":0, "total_pnl":0.0,
            "start_balance":0.0, "last_report_date":""}

def save_state(s):
    with open(STATE_FILE, "w") as f:
        json.dump(s, f, indent=2, ensure_ascii=False)

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    log("="*60)
    log("  VIRALAB CRYPTO FUTURES BOT v2")
    log(f"  {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    log(f"  Mode: {'🔴 LIVE' if LIVE_TRADING else '📝 PAPER'}")
    log(f"  Leverage: {LEVERAGE}x | SL: {STOP_LOSS*100:.0f}% | TP: {TAKE_PROFIT*100:.0f}%")
    log(f"  Coins: {len(SYMBOLS)} pairs")
    log("="*60)

    if not API_KEY or not API_SECRET:
        log("❌ Missing API credentials"); sys.exit(1)

    state = load_state()
    log(f"\nOpen: {len(state['positions'])} | "
        f"Trades: {state['total_trades']} | "
        f"W/L: {state['wins']}/{state['losses']} | "
        f"PnL: ${state['total_pnl']:+.3f}")

    # Monitor positions
    state = monitor(state)
    save_state(state)

    # Balance
    bal = balance()
    log(f"Balance: ${bal:.2f} USDT")

    if state["start_balance"] == 0 and bal > 0:
        state["start_balance"] = bal

    # Check balance
    if bal < 0.5:
        log("Balance too low"); save_state(state); return

    # Daily email at 9 PM IST
    today_str = (datetime.now(timezone.utc) + timedelta(hours=5,minutes=30)).strftime("%d%b%Y")
    if should_send_report() and state.get("last_report_date") != today_str:
        log("\n📧 Sending daily email report...")
        send_daily_email(state, bal)
        state["last_report_date"] = today_str
        save_state(state)

    # Check max positions
    if len(state["positions"]) >= MAX_OPEN:
        log(f"\nMax {MAX_OPEN} positions open — not opening new trades")
        save_state(state); return

    # Get fear & greed once (applies to all coins)
    fng = fear_greed()
    log(f"\nFear & Greed: {fng}")

    # Find open symbols
    open_syms = {p["symbol"] for p in state["positions"]}
    slots     = MAX_OPEN - len(state["positions"])
    signals   = []

    # Analyse all coins
    log(f"\n🔍 Scanning {len(SYMBOLS)} coins...")
    for sym in SYMBOLS:
        if sym in open_syms:
            log(f"  {sym}: already open — skipping")
            continue
        if len(signals) + len(open_syms) >= MAX_OPEN * 2:
            break
        direction, score, details = analyse(sym, fng)
        log(f"  {sym}: {direction} ({score}/6) | RSI={details.get('rsi',0):.0f} | ATR={details.get('atr',0):.1f}%")
        if direction != "NEUTRAL":
            signals.append((sym, direction, score, details))

    # Sort by signal strength
    signals.sort(key=lambda x: -x[2])

    if not signals:
        log("\n⏳ No signals this hour")
        save_state(state); return

    # Open best trades up to available slots
    opened = 0
    for sym, direction, score, details in signals[:slots]:
        log(f"\n{'='*60}")
        log(f"  🎯 {direction} {sym} — {score}/6 signals")
        log(f"  Price: ${details['price']:,.4f}")
        log(f"  RSI={details.get('rsi',0):.0f} | ATR={details.get('atr',0):.1f}% | F&G={fng}")

        record = place_trade(sym, direction, bal)
        if record:
            state["positions"].append(record)
            opened += 1
            save_state(state)
            bal *= (1 - MAX_RISK_PCT)  # Reduce available balance

    log(f"\n✅ Complete — opened {opened} new trades")
    log(f"   Open positions: {len(state['positions'])}")
    save_state(state)

if __name__ == "__main__":
    main()
