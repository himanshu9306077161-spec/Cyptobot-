"""
ViraLab Crypto Futures Bot v7
==============================
BUILT FROM TOP BOT CODE ANALYSIS

IMPROVEMENTS FROM REAL BOT CODE:
  1. Crossover signals (Freqtrade) — wait for signal to CROSS not just be at level
  2. Heikin Ashi candles (Hephyrius bot) — smoother trends, less noise
  3. Trailing Stop Loss (Freqtrade + Hephyrius) — locks profits as price moves
  4. Auto-reversal (Hephyrius bot) — flips position when signal reverses
  5. Parabolic SAR (Freqtrade) — extra trend confirmation

SIGNALS (need 4/7):
  1. EMA 20/50 crossover (not just position)
  2. MACD histogram crossover
  3. Heikin Ashi trend (3 consecutive bull/bear candles)
  4. Parabolic SAR direction
  5. ADX trend strength
  6. Taker Buy Ratio
  7. VWAP position

TRAILING STOP:
  Entry: Fixed 5% initial stop
  After +3%: Trail 3% below peak
  After +7%: Trail 2% below peak
  After +15%: Trail 1.5% below peak

AUTO-REVERSAL:
  If position is LONG and new signals say SHORT → close LONG, open SHORT
  Works in both directions

F&G RULES:
  F&G < 30: SHORT only
  F&G > 70: LONG only
  F&G 30-70: Both directions allowed
"""

import os, sys, json, time, math, hmac, hashlib, urllib3
import smtplib, ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ── CREDENTIALS ───────────────────────────────────────────────────
API_KEY      = os.environ.get("BINANCE_API_KEY","")
API_SECRET   = os.environ.get("BINANCE_API_SECRET","")
EMAIL_PASS   = os.environ.get("EMAIL_APP_PASSWORD","")
EMAIL_FROM   = os.environ.get("EMAIL_FROM","")
EMAIL_TO     = "Himanshu.r.garg@icloud.com"
LIVE_TRADING = os.environ.get("LIVE_TRADING","false").lower()=="true"

# ── CONFIG ────────────────────────────────────────────────────────
FUTURES_URL   = "https://fapi.binance.com"
MIN_SIGNALS   = 4           # Need 4/7
MAX_POSITIONS = 10          # Max 10 simultaneous
LEVERAGE      = 10          # 10x hard cap
TP_PCT        = 0.10        # 10% take profit
SL_PCT        = 0.05        # 5% initial stop loss
TRAIL_PCT     = 0.03        # 3% trailing after +3% profit
MIN_ADX       = 20          # Skip choppy markets
MIN_VOL       = 1_000_000   # $1M daily volume
MAX_COINS     = 200         # Top 200 coins
EMAIL_EVERY   = 6*3600      # Email every 6 hours
STATE_FILE    = "futures_state.json"
SPOT_THRESH   = 1000.0      # Transfer 50% to spot at $1000+

# ── PROXY ─────────────────────────────────────────────────────────
def get_proxy():
    s = os.environ.get("WEBSHARE_PROXY","")
    if not s: return None
    try:
        p = s.strip().split(":")
        url = f"http://{p[2]}:{p[3]}@{p[0]}:{p[1]}" if len(p)==4 else f"http://{p[0]}:{p[1]}"
        prx = {"http":url,"https":url}
        r = requests.get(f"{FUTURES_URL}/fapi/v1/time",proxies=prx,timeout=8,verify=False)
        if r.status_code==200:
            print(f"✅ Proxy: {p[0]}:{p[1]}")
            return prx
    except: pass
    return None

print("[PROXY] Connecting...")
PROXY = get_proxy()

# ── LOGGING ───────────────────────────────────────────────────────
def log(msg):
    print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] {msg}")

# ── BINANCE API ───────────────────────────────────────────────────
def sign(p):
    p["timestamp"]=int(time.time()*1000)
    q=urlencode(p)
    p["signature"]=hmac.new(API_SECRET.encode(),q.encode(),hashlib.sha256).hexdigest()
    return p

def bget(path,params=None,signed=False):
    if params is None: params={}
    if signed: params=sign(params)
    try:
        r=requests.get(f"{FUTURES_URL}{path}",params=params,
                      headers={"X-MBX-APIKEY":API_KEY},
                      proxies=PROXY,timeout=15,verify=False)
        if r.status_code==200: return r.json()
        log(f"GET {path} {r.status_code}: {r.text[:150]}")
        return None
    except Exception as e:
        log(f"GET error: {e}"); return None

def bpost(path,params):
    params=sign(params)
    try:
        r=requests.post(f"{FUTURES_URL}{path}",params=params,
                       headers={"X-MBX-APIKEY":API_KEY},
                       proxies=PROXY,timeout=15,verify=False)
        if r.status_code==200: return r.json()
        log(f"POST {path} {r.status_code}: {r.text[:200]}")
        return None
    except Exception as e:
        log(f"POST error: {e}"); return None

# ── MARKET DATA ───────────────────────────────────────────────────
def klines(symbol,interval="1h",limit=100):
    d=bget("/fapi/v1/klines",{"symbol":symbol,"interval":interval,"limit":limit})
    if not d or len(d)<30: return None
    return {
        "o":[float(k[1]) for k in d],
        "h":[float(k[2]) for k in d],
        "l":[float(k[3]) for k in d],
        "c":[float(k[4]) for k in d],
        "v":[float(k[5]) for k in d],
        "tb":[float(k[9]) for k in d],
        "tv":[float(k[7]) for k in d],
    }

def get_price(sym):
    d=bget("/fapi/v1/ticker/price",{"symbol":sym})
    return float(d["price"]) if d else None

def get_fng():
    try:
        r=requests.get("https://api.alternative.me/fng/?limit=1",
                      proxies=PROXY,timeout=8,verify=False)
        if r.status_code==200:
            return int(r.json()["data"][0]["value"])
    except: pass
    return 50

def get_balance():
    d=bget("/fapi/v2/balance",signed=True)
    if not d: return 0.0
    for a in d:
        if a.get("asset")=="USDT":
            return float(a.get("availableBalance",0))
    return 0.0

def get_symbols():
    d=bget("/fapi/v1/ticker/24hr")
    if not d:
        return ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT"]
    pairs=[(t["symbol"],float(t.get("quoteVolume",0)))
           for t in d
           if t["symbol"].endswith("USDT")
           and float(t.get("quoteVolume",0))>=MIN_VOL]
    pairs.sort(key=lambda x:-x[1])
    result=[p[0] for p in pairs[:MAX_COINS]]
    log(f"Found {len(result)} coins >${MIN_VOL/1e6:.0f}M daily volume")
    return result

def get_exchange_info(symbol):
    d=bget("/fapi/v1/exchangeInfo")
    if not d: return 2,0.001
    for s in d.get("symbols",[]):
        if s["symbol"]==symbol:
            pp=s.get("pricePrecision",2)
            for f in s.get("filters",[]):
                if f["filterType"]=="LOT_SIZE":
                    return pp,float(f["stepSize"])
    return 2,0.001

def transfer_to_spot(amount):
    if not LIVE_TRADING: return
    r=bpost("/sapi/v1/futures/transfer",{
        "asset":"USDT","amount":str(round(amount,2)),"type":"2"})
    if r and r.get("tranId"):
        log(f"✅ Transferred ${amount:.2f} to Spot")

# ── INDICATORS ────────────────────────────────────────────────────
def ema(c,p):
    if len(c)<p: return c[-1]
    k=2/(p+1); v=sum(c[:p])/p
    for x in c[p:]: v=x*k+v*(1-k)
    return v

def ema_series(c,p):
    """Returns full EMA series — needed for crossover detection."""
    if len(c)<p: return [c[-1]]*len(c)
    k=2/(p+1); v=sum(c[:p])/p
    result=[v]
    for x in c[p:]:
        v=x*k+v*(1-k)
        result.append(v)
    # Pad beginning
    pad=[result[0]]*(len(c)-len(result))
    return pad+result

def calc_adx(h,l,c,p=14):
    if len(c)<p*2: return 15
    dp,dm,tr=[],[],[]
    for i in range(1,len(c)):
        u=h[i]-h[i-1]; d=l[i-1]-l[i]
        dp.append(u if u>d and u>0 else 0)
        dm.append(d if d>u and d>0 else 0)
        tr.append(max(h[i]-l[i],abs(h[i]-c[i-1]),abs(l[i]-c[i-1])))
    def sm(v,n):
        s=sum(v[:n]); r=[s]
        for x in v[n:]: s=s-s/n+x; r.append(s)
        return r
    ts=sm(tr,p); ps=sm(dp,p); ms=sm(dm,p)
    dip=[100*ps[i]/ts[i] if ts[i]>0 else 0 for i in range(len(ts))]
    dim=[100*ms[i]/ts[i] if ts[i]>0 else 0 for i in range(len(ts))]
    dx=[100*abs(dip[i]-dim[i])/(dip[i]+dim[i]) if (dip[i]+dim[i])>0 else 0 for i in range(len(dip))]
    return sum(dx[-p:])/p if len(dx)>=p else 15

def calc_macd_series(c):
    """Returns MACD histogram series for crossover detection."""
    if len(c)<26: return [0]*len(c)
    e12=ema_series(c,12)
    e26=ema_series(c,26)
    macd=[e12[i]-e26[i] for i in range(len(c))]
    # Signal = EMA of MACD
    sig=ema_series(macd,9)
    hist=[macd[i]-sig[i] for i in range(len(c))]
    return hist

def calc_vwap(c,v):
    s=c[-20:]; vv=v[-20:]
    tv=sum(vv)
    return sum(s[i]*vv[i] for i in range(len(s)))/tv if tv>0 else c[-1]

def calc_tbr(tb,tv):
    rtb=sum(tb[-3:]); rtv=sum(tv[-3:])
    return rtb/rtv if rtv>0 else 0.5

def heikin_ashi(o,h,l,c):
    """
    Heikin Ashi candles — from Hephyrius bot code.
    Smooths price action and makes trends clearer.
    ha_close = (o+h+l+c)/4
    ha_open  = (prev_ha_open + prev_ha_close)/2
    ha_high  = max(h, ha_open, ha_close)
    ha_low   = min(l, ha_open, ha_close)
    """
    n=len(c)
    ha_c=[0.0]*n; ha_o=[0.0]*n
    ha_h=[0.0]*n; ha_l=[0.0]*n
    ha_c[0]=(o[0]+h[0]+l[0]+c[0])/4
    ha_o[0]=(o[0]+c[0])/2
    ha_h[0]=h[0]; ha_l[0]=l[0]
    for i in range(1,n):
        ha_c[i]=(o[i]+h[i]+l[i]+c[i])/4
        ha_o[i]=(ha_o[i-1]+ha_c[i-1])/2
        ha_h[i]=max(h[i],ha_o[i],ha_c[i])
        ha_l[i]=min(l[i],ha_o[i],ha_c[i])
    return ha_o,ha_h,ha_l,ha_c

def calc_sar(h,l,af_start=0.02,af_max=0.2):
    """
    Parabolic SAR — from Freqtrade sample strategy.
    Returns 'BULL' when price above SAR, 'BEAR' when below.
    """
    if len(h)<3: return "NEUTRAL"
    # Simplified SAR
    sar=[0.0]*len(h)
    bull=True
    ep=l[0]; af=af_start
    sar[0]=h[0]
    for i in range(1,len(h)):
        prev_sar=sar[i-1]
        if bull:
            sar[i]=prev_sar+af*(ep-prev_sar)
            sar[i]=min(sar[i],l[i-1],l[max(0,i-2)])
            if l[i]<sar[i]:
                bull=False; sar[i]=ep
                ep=l[i]; af=af_start
            else:
                if h[i]>ep:
                    ep=h[i]; af=min(af+af_start,af_max)
        else:
            sar[i]=prev_sar+af*(ep-prev_sar)
            sar[i]=max(sar[i],h[i-1],h[max(0,i-2)])
            if h[i]>sar[i]:
                bull=True; sar[i]=ep
                ep=h[i]; af=af_start
            else:
                if l[i]<ep:
                    ep=l[i]; af=min(af+af_start,af_max)
    return "BULL" if bull else "BEAR"

def calc_trailing_sl(pos, current_price):
    """
    Trailing stop loss — from Freqtrade + Hephyrius bot.
    Moves stop loss up as price rises to lock in profits.
    """
    entry    = pos["entry"]
    direction= pos["direction"]
    peak     = pos.get("peak", entry)
    current_sl = pos.get("sl", 0)

    if direction=="LONG":
        peak = max(peak, current_price)
        pnl_pct = (peak - entry) / entry

        if pnl_pct >= 0.15:    trail = 0.015  # 1.5% trail after 15%
        elif pnl_pct >= 0.07:  trail = 0.02   # 2% trail after 7%
        elif pnl_pct >= 0.03:  trail = 0.03   # 3% trail after 3%
        else:
            return current_sl, peak  # Keep original SL

        new_sl = peak * (1 - trail)
        new_sl = max(new_sl, current_sl)  # Never move SL down
        new_sl = max(new_sl, entry * (1 - SL_PCT))  # Never below original
    else:
        peak = min(peak, current_price)
        pnl_pct = (entry - peak) / entry

        if pnl_pct >= 0.15:    trail = 0.015
        elif pnl_pct >= 0.07:  trail = 0.02
        elif pnl_pct >= 0.03:  trail = 0.03
        else:
            return current_sl, peak

        new_sl = peak * (1 + trail)
        new_sl = min(new_sl, current_sl) if current_sl > 0 else new_sl
        new_sl = min(new_sl, entry * (1 + SL_PCT))

    return round(new_sl, 8), peak

# ── SIGNAL ENGINE ─────────────────────────────────────────────────
def analyse(symbol, fng):
    """
    7 signals using CROSSOVER detection (Freqtrade style)
    + Heikin Ashi candles (Hephyrius style)
    + Parabolic SAR (Freqtrade)
    Need 4/7 signals to trade.
    """
    # Always define d first
    d = {"symbol":symbol,"price":0,"adx":0,"fng":fng,
         "bull":0,"bear":0,"skip":""}

    try:
        k1h = klines(symbol,"1h",100)
        if not k1h:
            d["skip"]="No data"; return "NEUTRAL",0,d

        c=k1h["c"]; h=k1h["h"]; l=k1h["l"]
        o=k1h["o"]; v=k1h["v"]
        tb=k1h["tb"]; tv=k1h["tv"]
        price=c[-1]
        d["price"]=price

        # ADX — skip choppy
        adx_val=calc_adx(h,l,c)
        d["adx"]=round(adx_val,1)
        if adx_val < MIN_ADX:
            d["skip"]=f"ADX={adx_val:.0f} choppy"
            return "NEUTRAL",0,d

        # Heikin Ashi candles
        ha_o,ha_h,ha_l,ha_c = heikin_ashi(o,h,l,c)

        # F&G direction gate
        only_short = fng < 30
        only_long  = fng > 70

        bull=bear=0

        # ── Signal 1: EMA 20/50 CROSSOVER ────────────────────────
        # Freqtrade style: detect the moment of crossing, not just position
        e20s=ema_series(c,20)
        e50s=ema_series(c,50)
        e20_now=e20s[-1]; e20_prev=e20s[-2]
        e50_now=e50s[-1]; e50_prev=e50s[-2]
        d["ema20"]=round(e20_now,4)
        d["ema50"]=round(e50_now,4)

        # Golden cross: EMA20 just crossed above EMA50
        if e20_now>e50_now and e20_prev<=e50_prev:
            bull+=1; d["s1"]="🟢 EMA golden cross"
        # Death cross: EMA20 just crossed below EMA50
        elif e20_now<e50_now and e20_prev>=e50_prev:
            bear+=1; d["s1"]="🔴 EMA death cross"
        # Already in trend — less strong signal
        elif e20_now>e50_now:
            bull+=1; d["s1"]="🟢 EMA uptrend"
        elif e20_now<e50_now:
            bear+=1; d["s1"]="🔴 EMA downtrend"
        else:
            d["s1"]="⚪ EMA flat"

        # ── Signal 2: MACD CROSSOVER ──────────────────────────────
        # Freqtrade style: histogram crosses zero
        hist=calc_macd_series(c)
        hist_now=hist[-1]; hist_prev=hist[-2]

        if hist_now>0 and hist_prev<=0:
            bull+=1; d["s2"]="🟢 MACD crossed up"
        elif hist_now<0 and hist_prev>=0:
            bear+=1; d["s2"]="🔴 MACD crossed down"
        elif hist_now>0:
            bull+=1; d["s2"]="🟢 MACD positive"
        elif hist_now<0:
            bear+=1; d["s2"]="🔴 MACD negative"
        else:
            d["s2"]="⚪ MACD flat"

        # ── Signal 3: Heikin Ashi trend ───────────────────────────
        # Hephyrius style: 3 consecutive same-colored HA candles = trend confirmed
        ha_bull_3 = all(ha_c[-i]>ha_o[-i] for i in range(1,4))
        ha_bear_3 = all(ha_c[-i]<ha_o[-i] for i in range(1,4))
        if ha_bull_3:   bull+=1; d["s3"]="🟢 HA 3 bull candles"
        elif ha_bear_3: bear+=1; d["s3"]="🔴 HA 3 bear candles"
        else:                    d["s3"]="⚪ HA no trend"

        # ── Signal 4: Parabolic SAR ───────────────────────────────
        # Freqtrade strategy uses SAR as trend confirmation
        sar=calc_sar(h,l)
        if sar=="BULL":    bull+=1; d["s4"]="🟢 SAR bullish"
        elif sar=="BEAR":  bear+=1; d["s4"]="🔴 SAR bearish"
        else:                       d["s4"]="⚪ SAR neutral"

        # ── Signal 5: ADX strength ────────────────────────────────
        if adx_val>30:
            if e20_now>e50_now: bull+=1; d["s5"]=f"🟢 Strong uptrend ADX={adx_val:.0f}"
            else:               bear+=1; d["s5"]=f"🔴 Strong downtrend ADX={adx_val:.0f}"
        else:
            d["s5"]=f"⚪ Moderate ADX={adx_val:.0f}"

        # ── Signal 6: Taker Buy Ratio ─────────────────────────────
        tbr=calc_tbr(tb,tv)
        d["tbr"]=round(tbr,3)
        if tbr>0.60:    bull+=1; d["s6"]=f"🟢 Buyers {tbr*100:.0f}%"
        elif tbr<0.40:  bear+=1; d["s6"]=f"🔴 Sellers {(1-tbr)*100:.0f}%"
        else:                    d["s6"]=f"⚪ Balanced TBR"

        # ── Signal 7: VWAP ────────────────────────────────────────
        vw=calc_vwap(c,v)
        d["vwap"]=round(vw,4)
        if price>vw*1.001:    bull+=1; d["s7"]="🟢 Above VWAP"
        elif price<vw*0.999:  bear+=1; d["s7"]="🔴 Below VWAP"
        else:                           d["s7"]="⚪ At VWAP"

        # ── Signal 8: Volume spike confirms move ──────────────────
        # From top bots: volume must confirm signal direction
        avg_vol = sum(v[-21:-1])/20 if len(v)>20 else v[-1]
        vol_spike = v[-1] > avg_vol * 1.3
        if vol_spike:
            if c[-1]>c[-2]: bull+=1; d["s8"]="🟢 Volume spike UP"
            else:           bear+=1; d["s8"]="🔴 Volume spike DOWN"
        else:
            d["s8"]="⚪ Normal volume"

        d["bull"]=bull; d["bear"]=bear

        # Apply F&G filter
        if only_short and bull>=MIN_SIGNALS:
            d["skip"]=f"F&G={fng}<30 SHORT only"
            return "NEUTRAL",bull,d
        if only_long and bear>=MIN_SIGNALS:
            d["skip"]=f"F&G={fng}>70 LONG only"
            return "NEUTRAL",bear,d

        if bull>=MIN_SIGNALS: return "LONG",bull,d
        if bear>=MIN_SIGNALS: return "SHORT",bear,d
        return "NEUTRAL",max(bull,bear),d

    except Exception as e:
        log(f"  analyse error {symbol}: {e}")
        return "NEUTRAL",0,d

# ── TRADE SIZING ──────────────────────────────────────────────────
def get_trade_config(balance):
    if balance<2:     return 0,0
    if balance<10:
        num=max(1,int(balance/2)); cap=round(balance*0.60/num,2)
    elif balance<20:  num,cap=10,1.0
    elif balance<30:  num,cap=10,2.0
    elif balance<50:  num,cap=10,3.0
    elif balance<100: num,cap=10,5.0
    elif balance<200: num,cap=10,10.0
    elif balance<500: num,cap=10,20.0
    elif balance<1000:num,cap=10,50.0
    else:
        t=balance*0.50; num,cap=10,round(t/10,2)
    return num,cap

# ── PLACE TRADE ───────────────────────────────────────────────────
def place_trade(symbol,direction,balance,score,capital_each):
    try:
        price=get_price(symbol)
        if not price: return None

        if direction=="LONG":
            sl=round(price*(1-SL_PCT),8)
            tp=round(price*(1+TP_PCT),8)
        else:
            sl=round(price*(1+SL_PCT),8)
            tp=round(price*(1-TP_PCT),8)

        capital=min(capital_each,balance*0.20)
        capital=max(capital,0.50)
        pos_usd=capital*LEVERAGE
        if pos_usd<20:
            capital=20/LEVERAGE; pos_usd=capital*LEVERAGE
        if capital>balance*0.50:
            log(f"  ⚠ {symbol}: not enough balance")
            return None

        pp,step=get_exchange_info(symbol)
        qty=round(math.floor((pos_usd/price)/step)*step,8)
        if qty<=0: return None
        sl=round(sl,pp); tp=round(tp,pp)

        log(f"  📊 {score}/7 | Cap=${capital:.2f}×{LEVERAGE}x=${pos_usd:.2f}")
        log(f"  📍 {direction} @ ${price:.4f} SL=${sl:.4f}(-{SL_PCT*100:.0f}%) TP=${tp:.4f}(+{TP_PCT*100:.0f}%)")

        record={
            "symbol":symbol,"direction":direction,
            "entry":price,"sl":sl,"tp":tp,
            "qty":qty,"capital":capital,"notional":pos_usd,
            "score":score,"peak":price,
            "open_time":time.time(),
            "open_date":datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
            "paper":not LIVE_TRADING,
        }

        if LIVE_TRADING:
            bpost("/fapi/v1/marginType",{"symbol":symbol,"marginType":"ISOLATED"})
            bpost("/fapi/v1/leverage",{"symbol":symbol,"leverage":LEVERAGE})
            side="BUY" if direction=="LONG" else "SELL"
            order=bpost("/fapi/v1/order",{
                "symbol":symbol,"side":side,
                "type":"MARKET","quantity":str(qty),
            })
            if not order:
                log(f"  ❌ Entry failed"); return None
            record["order_id"]=order.get("orderId")
            log(f"  ✅ Entry: {direction} {qty} {symbol} @ ${price:.4f}")
            time.sleep(2)

            sl_side="SELL" if side=="BUY" else "BUY"
            sl_r=bpost("/fapi/v1/algoOrder",{
                "algoType":"CONDITIONAL","symbol":symbol,
                "side":sl_side,"type":"STOP_MARKET",
                "triggerPrice":str(sl),"closePosition":"true",
                "workingType":"CONTRACT_PRICE",
            })
            if sl_r and sl_r.get("algoId"):
                log(f"  ✅ SL @ ${sl}"); record["sl_id"]=sl_r["algoId"]
            else:
                log(f"  ❌ SL failed: {sl_r}")
            time.sleep(0.5)
            tp_r=bpost("/fapi/v1/algoOrder",{
                "algoType":"CONDITIONAL","symbol":symbol,
                "side":sl_side,"type":"TAKE_PROFIT_MARKET",
                "triggerPrice":str(tp),"closePosition":"true",
                "workingType":"CONTRACT_PRICE",
            })
            if tp_r and tp_r.get("algoId"):
                log(f"  ✅ TP @ ${tp}"); record["tp_id"]=tp_r["algoId"]
            else:
                log(f"  ❌ TP failed: {tp_r}")
        else:
            log(f"  📝 PAPER: {direction} {qty} {symbol} @ ${price:.4f}")
            log(f"     SL=${sl} TP=${tp}")

        return record

    except Exception as e:
        log(f"  ❌ place_trade error: {e}")
        import traceback; traceback.print_exc()
        return None

def close_position(pos, reason, fng):
    """Close a position and optionally reverse it."""
    sym=pos["symbol"]; dir=pos["direction"]
    qty=pos["qty"]
    if LIVE_TRADING:
        cls="SELL" if dir=="LONG" else "BUY"
        bpost("/fapi/v1/order",{
            "symbol":sym,"side":cls,
            "type":"MARKET","quantity":str(qty),
            "reduceOnly":"true"
        })
    px=get_price(sym) or pos["entry"]
    pnl=((px-pos["entry"])/pos["entry"]) if dir=="LONG" else ((pos["entry"]-px)/pos["entry"])
    pnl_usd=pnl*pos["capital"]*LEVERAGE
    pos.update({
        "status":"closed","exit":px,
        "exit_date":datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC"),
        "pnl_pct":round(pnl*100,2),
        "pnl_usd":round(pnl_usd,3),
        "reason":reason,
    })
    return pos, pnl_usd

# ── MONITOR ───────────────────────────────────────────────────────
def monitor(state, fng):
    if not state["positions"]: return state, []
    log(f"\n📋 Monitoring {len(state['positions'])} positions...")
    to_close=[]; reversals=[]

    for pos in state["positions"]:
        sym=pos["symbol"]; entr=pos["entry"]
        sl=pos["sl"]; tp=pos["tp"]
        dir=pos["direction"]; qty=pos["qty"]
        hrs=(time.time()-pos.get("open_time",time.time()))/3600

        px=get_price(sym)
        if not px: continue

        # Update trailing stop loss
        new_sl, new_peak = calc_trailing_sl(pos, px)
        if new_sl != sl:
            log(f"  📈 {sym}: Trailing SL moved ${sl:.4f}→${new_sl:.4f}")
            pos["sl"]=new_sl; pos["peak"]=new_peak
            # Update on Binance
            if LIVE_TRADING and new_sl != sl:
                bpost("/fapi/v1/cancelAllOpenOrders",{"symbol":sym})
                time.sleep(0.3)
                sl_side="SELL" if dir=="LONG" else "BUY"
                bpost("/fapi/v1/algoOrder",{
                    "algoType":"CONDITIONAL","symbol":sym,
                    "side":sl_side,"type":"STOP_MARKET",
                    "triggerPrice":str(round(new_sl,2)),
                    "closePosition":"true","workingType":"CONTRACT_PRICE",
                })

        pnl=((px-entr)/entr) if dir=="LONG" else ((entr-px)/entr)
        pnl_usd=pnl*pos["capital"]*LEVERAGE

        log(f"  {sym} {dir}: ${entr:.4f}→${px:.4f} "
            f"PnL={pnl*100:+.2f}% SL=${pos['sl']:.4f} TP=${tp:.4f} {hrs:.1f}h")

        reason=None

        # SL/TP check
        if dir=="LONG":
            if px<=pos["sl"]: reason=f"🛡 SL {pnl*100:.1f}%"
            elif px>=tp:      reason=f"🎯 TP +{pnl*100:.1f}%"
        else:
            if px>=pos["sl"]: reason=f"🛡 SL {pnl*100:.1f}%"
            elif px<=tp:      reason=f"🎯 TP +{pnl*100:.1f}%"

        # Auto-reversal check (Hephyrius bot feature)
        # If signals have fully reversed against position — close and flip
        if not reason and hrs>=1:
            new_dir,score,_=analyse(sym,fng)
            if new_dir=="SHORT" and dir=="LONG" and score>=MIN_SIGNALS:
                reason=f"🔄 Reversal: signals flipped to SHORT"
                reversals.append((sym,"SHORT",score))
                log(f"  ⚡ {sym}: AUTO-REVERSAL → SHORT")
            elif new_dir=="LONG" and dir=="SHORT" and score>=MIN_SIGNALS:
                reason=f"🔄 Reversal: signals flipped to LONG"
                reversals.append((sym,"LONG",score))
                log(f"  ⚡ {sym}: AUTO-REVERSAL → LONG")

        # Stuck check: 4h open, less than 10% progress
        if not reason and hrs>=4:
            prog=((px-entr)/(tp-entr)) if dir=="LONG" and tp!=entr else \
                 ((entr-px)/(entr-tp)) if tp!=entr else 0
            if prog<0.10:
                reason=f"🔄 Stuck {hrs:.0f}h"

        # 24h timeout
        if not reason and hrs>=24:
            reason=f"⏰ 24h timeout {pnl*100:.1f}%"

        if reason:
            log(f"  Closing {sym}: {reason} ${pnl_usd:+.2f}")
            pos, pnl_usd = close_position(pos, reason, fng)
            state["closed_trades"].append(pos)
            state["total_pnl"]=state.get("total_pnl",0)+pnl_usd
            state["daily_loss"]=state.get("daily_loss",0)+min(pnl_usd,0)
            if pnl_usd>0: state["wins"]=state.get("wins",0)+1
            else:         state["losses"]=state.get("losses",0)+1
            state["total_trades"]=state.get("total_trades",0)+1
            to_close.append(pos)

    state["positions"]=[p for p in state["positions"] if p not in to_close]
    return state, reversals

# ── EMAIL ─────────────────────────────────────────────────────────
def send_email(state,bal):
    if not EMAIL_PASS:
        log("⚠ No EMAIL_APP_PASSWORD"); return
    now=datetime.now(timezone.utc)+timedelta(hours=5,minutes=30)
    today=now.strftime("%d %b %Y")
    wins=state.get("wins",0); losses=state.get("losses",0)
    total=state.get("total_trades",0); pnl=state.get("total_pnl",0)
    wrate=wins/max(total,1)*100
    start=state.get("start_balance",bal); gain=bal-start
    open_p=state.get("positions",[])
    today_closed=[t for t in state.get("closed_trades",[])
                  if today in t.get("exit_date","")]

    def row(k,v,c=""):
        s=f' style="color:{c};font-weight:bold"' if c else ""
        return f"<tr><td style='padding:8px 14px;color:#666'>{k}</td><td{s} style='padding:8px 14px'>{v}</td></tr>"

    def trow(t):
        p=t.get("pnl_usd",0); col="#27ae60" if p>0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f5f5f5'>
            <td style='padding:8px'>{'✅' if p>0 else '❌'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold'>{t['symbol']}</td>
            <td style='padding:8px'>${t.get('entry',0):,.4f}</td>
            <td style='padding:8px'>${t.get('exit',0):,.4f}</td>
            <td style='padding:8px;color:{col};font-weight:bold'>{t.get('pnl_pct',0):+.1f}% (${p:+.2f})</td>
            <td style='padding:8px;font-size:11px'>{t.get('reason','')}</td></tr>"""

    def orow(t):
        px=get_price(t["symbol"]) or t["entry"]
        pct=(px-t["entry"])/t["entry"] if t["direction"]=="LONG" else (t["entry"]-px)/t["entry"]
        pusd=pct*t["capital"]*LEVERAGE
        col="#27ae60" if pusd>0 else "#e74c3c"
        return f"""<tr style='border-bottom:1px solid #f5f5f5'>
            <td style='padding:8px'>{'📈' if t['direction']=='LONG' else '📉'} {t['direction']}</td>
            <td style='padding:8px;font-weight:bold'>{t['symbol']}</td>
            <td style='padding:8px'>${t['entry']:,.4f}</td>
            <td style='padding:8px'>${px:,.4f}</td>
            <td style='padding:8px;color:{col};font-weight:bold'>{pct*100:+.1f}% (${pusd:+.2f})</td>
            <td style='padding:8px;font-size:11px'>SL=${t['sl']:,.4f} TP=${t['tp']:,.4f}</td></tr>"""

    mode="🔴 LIVE" if LIVE_TRADING else "📝 PAPER"
    html=f"""<!DOCTYPE html><html><body style='font-family:Arial,sans-serif;background:#f0f2f5;margin:0;padding:16px'>
<div style='max-width:700px;margin:0 auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,.08)'>
<div style='background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);padding:32px;text-align:center'>
<h1 style='color:#f0c040;margin:0;font-size:26px'>⚡ ViraLab FuturesBot v7</h1>
<p style='color:#aaa;margin:8px 0 4px'>Daily Report — {today}</p>
<span style='background:{"#c0392b" if LIVE_TRADING else "#2c3e50"};color:#fff;padding:5px 16px;border-radius:20px;font-size:12px;font-weight:bold'>{mode}</span>
</div>
<div style='padding:24px'>
<h2 style='color:#1a1a2e;border-left:4px solid #f0c040;padding-left:12px'>💰 Account</h2>
<table width='100%' style='border-collapse:collapse;background:#f8f9fa;border-radius:10px'>
{row("Balance",f"${bal:.2f} USDT")}
{row("Gain/Loss",f"${gain:+.2f} USDT","#27ae60" if gain>0 else "#e74c3c")}
{row("Total PnL",f"${pnl:+.2f} USDT","#27ae60" if pnl>0 else "#e74c3c")}
{row("Win Rate",f"{wrate:.0f}% ({wins}W/{losses}L)","#27ae60" if wrate>=50 else "#e74c3c")}
{row("Open",str(len(open_p)))}
</table></div>
<div style='padding:0 24px 24px'>
<h2 style='color:#1a1a2e;border-left:4px solid #27ae60;padding-left:12px'>📊 Closed Today ({len(today_closed)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f2f5'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Exit</th><th style='padding:8px;text-align:left'>PnL</th><th style='padding:8px;text-align:left'>Reason</th></tr>"+"".join(trow(t) for t in today_closed)+"</table>" if today_closed else "<p style='color:#aaa'>None today.</p>"}
</div>
<div style='padding:0 24px 24px'>
<h2 style='color:#1a1a2e;border-left:4px solid #3498db;padding-left:12px'>📈 Open ({len(open_p)})</h2>
{"<table width='100%' style='border-collapse:collapse'><tr style='background:#f0f2f5'><th style='padding:8px;text-align:left'>Side</th><th style='padding:8px;text-align:left'>Symbol</th><th style='padding:8px;text-align:left'>Entry</th><th style='padding:8px;text-align:left'>Now</th><th style='padding:8px;text-align:left'>PnL</th><th style='padding:8px;text-align:left'>Levels</th></tr>"+"".join(orow(t) for t in open_p)+"</table>" if open_p else "<p style='color:#aaa'>None.</p>"}
</div>
<div style='background:#f8f9fa;padding:20px;text-align:center;border-top:1px solid #eee'>
<p style='color:#aaa;font-size:12px;margin:0'>ViraLab FuturesBot v7 • {now.strftime("%d %b %Y %I:%M %p IST")}</p>
<p style='color:#ccc;font-size:11px;margin:4px 0 0'>Heikin Ashi · EMA Crossover · MACD Crossover · Parabolic SAR · Trailing SL · Auto-Reversal</p>
</div></div></body></html>"""

    try:
        msg=MIMEMultipart("alternative")
        msg["Subject"]=f"⚡ FuturesBot v7 — {today} | PnL: ${pnl:+.2f} | WR: {wrate:.0f}%"
        msg["From"]=EMAIL_FROM; msg["To"]=EMAIL_TO
        msg.attach(MIMEText(html,"html"))
        ctx=ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com",465,context=ctx) as srv:
            srv.login(EMAIL_FROM,EMAIL_PASS)
            srv.sendmail(EMAIL_FROM,EMAIL_TO,msg.as_string())
        log(f"✅ Email sent to {EMAIL_TO}")
        state["last_email_time"]=time.time()
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
            "daily_loss":0.0,"last_email_time":0,"last_daily_reset":""}

def save_state(s):
    if len(s.get("closed_trades",[]))>300:
        s["closed_trades"]=s["closed_trades"][-300:]
    with open(STATE_FILE,"w") as f:
        json.dump(s,f,indent=2,ensure_ascii=False)

def daily_reset(state):
    today=datetime.now(timezone.utc).strftime("%d%b%Y")
    if state.get("last_daily_reset","")!=today:
        state["daily_loss"]=0.0
        state["last_daily_reset"]=today
        log("📅 New day — daily loss reset")
    return state

# ── MAIN ──────────────────────────────────────────────────────────
def main():
    log("="*60)
    log("  VIRALAB CRYPTO FUTURES BOT v7")
    log(f"  {datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')}")
    log(f"  Mode: {'🔴 LIVE' if LIVE_TRADING else '📝 PAPER'}")
    log(f"  NEW: Heikin Ashi + EMA Crossover + Parabolic SAR")
    log(f"  NEW: Trailing SL + Auto-Reversal")
    log(f"  Signals: 7 | Min: {MIN_SIGNALS} | Leverage: {LEVERAGE}x")
    log("="*60)

    if not API_KEY or not API_SECRET:
        log("❌ Missing API credentials"); sys.exit(1)

    state=load_state()
    state=daily_reset(state)

    log(f"\nPositions: {len(state['positions'])} | "
        f"Trades: {state.get('total_trades',0)} | "
        f"W/L: {state.get('wins',0)}/{state.get('losses',0)} | "
        f"PnL: ${state.get('total_pnl',0):+.2f}")

    # Get F&G first (needed for monitor reversal check)
    fng=get_fng()
    mood=("🔴 SHORT ONLY" if fng<30 else
          "🟢 LONG ONLY" if fng>70 else "⚪ NEUTRAL")
    log(f"Fear & Greed: {fng} — {mood}")

    # Monitor + get any reversal signals
    state,reversals=monitor(state,fng)
    save_state(state)

    # Get balance
    bal=get_balance()
    log(f"Balance: ${bal:.2f} USDT")
    if state.get("start_balance",0)==0 and bal>0:
        state["start_balance"]=bal

    if bal<2.0:
        log("⚠ Balance too low"); save_state(state); return

    # Circuit breaker
    daily_loss=abs(state.get("daily_loss",0))
    if bal>0 and daily_loss/bal>=0.25:
        log(f"⛔ Circuit breaker: ${daily_loss:.2f} lost today")
        save_state(state); return

    # Email every 6 hours
    if time.time()-state.get("last_email_time",0)>=EMAIL_EVERY:
        log("\n📧 Sending email...")
        send_email(state,bal)
        save_state(state)

    # Spot transfer at $1000
    if bal>=SPOT_THRESH and LIVE_TRADING:
        amt=round(bal*0.50,2)
        log(f"\n💸 Transferring ${amt:.2f} to Spot")
        transfer_to_spot(amt)
        bal-=amt

    # Dynamic trade config
    max_trades,capital_each=get_trade_config(bal)
    if max_trades==0:
        log("⚠ Balance too low"); save_state(state); return

    log(f"\n💼 {max_trades} trades × ${capital_each:.2f} = ${max_trades*capital_each:.2f} total")

    # Handle auto-reversals first (Hephyrius bot feature)
    if reversals:
        log(f"\n🔄 Processing {len(reversals)} auto-reversals...")
        for sym,new_dir,score in reversals:
            if sym not in {p["symbol"] for p in state["positions"]}: # already closed
                rec=place_trade(sym,new_dir,bal,score,capital_each)
                if rec:
                    state["positions"].append(rec)
                    bal-=rec["capital"]
                    save_state(state)

    # Check max positions
    if len(state["positions"])>=max_trades:
        log(f"Max {max_trades} positions open")
        save_state(state); return

    # Scan coins
    symbols=get_symbols()
    log(f"\n🔍 Scanning {len(symbols)} coins...")

    open_syms={p["symbol"] for p in state["positions"]}
    signals=[]
    slots=max_trades-len(state["positions"])

    for sym in symbols:
        if sym in open_syms: continue
        try:
            direction,score,details=analyse(sym,fng)
            skip=details.get("skip","")
            if score>0 or skip:
                log(f"  {sym}: {direction} ({score}/7) "
                    f"ADX={details.get('adx',0):.0f} "
                    f"TBR={details.get('tbr',0.5):.2f}"
                    f"{' — '+skip if skip else ''}")
            if direction!="NEUTRAL":
                signals.append((sym,direction,score))
        except Exception as e:
            log(f"  {sym}: error — {e}")
        time.sleep(0.1)

    if not signals:
        log("\n⏳ No signals this run")
        save_state(state); return

    signals.sort(key=lambda x:-x[2])
    log(f"\n🎯 {len(signals)} signals | Top: {[(s[0],s[1],s[2]) for s in signals[:3]]}")

    opened=0
    for sym,direction,score in signals[:slots]:
        log(f"\n{'='*60}")
        log(f"  {direction} {sym} — {score}/7")
        record=place_trade(sym,direction,bal,score,capital_each)
        if record:
            state["positions"].append(record)
            opened+=1
            bal-=record["capital"]
            save_state(state)

    log(f"\n✅ Done — opened {opened} | Open: {len(state['positions'])}")
    save_state(state)

if __name__ == "__main__":
    main()
