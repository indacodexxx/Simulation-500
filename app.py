import os,time,json,threading,urllib.request,urllib.parse,sqlite3,math,traceback,concurrent.futures
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
PORT=int(os.getenv("PORT","10000")); DB=os.getenv("DB_PATH","/tmp/simulation500.sqlite")
ASSETS={"BTC-EUR":("crypto","BTCEUR","EUR"),"ETH-EUR":("crypto","ETHEUR","EUR"),"SOL-EUR":("crypto","SOLEUR","EUR"),"SPY":("etf","SPY","USD"),"QQQ":("etf","QQQ","USD"),"DIA":("etf","DIA","USD"),"IWM":("etf","IWM","USD"),"EWQ":("etf","EWQ","USD"),"EWG":("etf","EWG","USD"),"EWJ":("etf","EWJ","USD"),"GLD":("etf","GLD","USD"),"SLV":("etf","SLV","USD"),"TLT":("etf","TLT","USD"),"AAPL":("stock","AAPL","USD"),"MSFT":("stock","MSFT","USD"),"NVDA":("stock","NVDA","USD"),"TSLA":("stock","TSLA","USD"),"ASML":("stock","ASML","USD"),"MC.PA":("stock","MC.PA","EUR"),"AIR.PA":("stock","AIR.PA","EUR"),"CAC40":("index","^FCHI","EUR"),"SP500":("index","^GSPC","USD"),"NASDAQ100":("index","^NDX","USD"),"DAX":("index","^GDAXI","EUR"),"NIKKEI":("index","^N225","JPY"),"HANGSENG":("index","^HSI","HKD"),"FTSE":("index","^FTSE","GBP"),"VIX":("index","^VIX","USD"),"EURUSD":("fx","EURUSD=X","USD")}
def fee_rate(asset):
 # Conservative simulated taker fee, configurable; never a claim of actual exchange execution.
 return float(os.getenv("CRYPTO_TAKER_FEE","0.008")) if ASSETS[asset][0]=="crypto" else float(os.getenv("EQUITY_SIM_FEE","0.001"))
def db():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;return c
def init():
 Path(DB).parent.mkdir(parents=True,exist_ok=True)
 with db() as c:
  c.executescript("CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,cash REAL,last_run INTEGER,error TEXT);CREATE TABLE IF NOT EXISTS quotes(asset TEXT PRIMARY KEY,price REAL,t INTEGER,source TEXT);CREATE TABLE IF NOT EXISTS samples(t INTEGER,asset TEXT,price REAL,PRIMARY KEY(t,asset));CREATE TABLE IF NOT EXISTS positions(asset TEXT PRIMARY KEY,qty REAL,entry REAL,cost REAL);CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER,asset TEXT,side TEXT,price REAL,amount REAL,pnl REAL,reason TEXT);")
  c.execute("INSERT OR IGNORE INTO state VALUES(1,500,0,'En attente du premier scan')")
  c.execute("CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER,asset TEXT,indicators TEXT,decision TEXT,reason TEXT)")
  c.execute("CREATE TABLE IF NOT EXISTS daily_risk(day TEXT PRIMARY KEY,start_equity REAL)")
  c.execute("CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER,category TEXT,asset TEXT,detail TEXT)")
def audit(c,t,category,asset,detail):
 c.execute("INSERT INTO audit(t,category,asset,detail) VALUES(?,?,?,?)",(t,category,asset,str(detail)[:700]))
 c.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT 500)")
def get(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=8) as r:return json.load(r)
def quote(asset,kind,symbol):
 if kind=="crypto":
  pair={"BTC-EUR":"XXBTZEUR","ETH-EUR":"XETHZEUR","SOL-EUR":"SOLEUR"}[asset]
  d=get("https://api.kraken.com/0/public/Ticker?pair="+pair)
  if d.get("error"):raise ValueError(str(d["error"]))
  return float(next(iter(d["result"].values()))["c"][0]),int(time.time()),"Kraken"
 d=get("https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?interval=5m&range=1d")["chart"]["result"][0]["meta"]
 return float(d["regularMarketPrice"]),int(d["regularMarketTime"]),"Yahoo Finance"
def market_indicators(asset,kind,symbol,now):
 if kind=="crypto":
  pair={"BTC-EUR":"XXBTZEUR","ETH-EUR":"XETHZEUR","SOL-EUR":"SOLEUR"}[asset]
  d=get("https://api.kraken.com/0/public/OHLC?pair="+pair+"&interval=5")
  if d.get("error"):raise ValueError(str(d["error"]))
  candles=next(v for k,v in d["result"].items() if k!="last")
  points=[(int(x[0]),float(x[4]),float(x[6])) for x in candles if int(x[0])<=now-300]
  bars=[(float(x[1]),float(x[2]),float(x[3]),float(x[4])) for x in candles if int(x[0])<=now-300]
 else:
  d=get("https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?interval=5m&range=5d")["chart"]["result"][0]
  q=d["indicators"]["quote"][0]
  points=[(int(t),float(p),float(v or 0)) for t,p,v in zip(d.get("timestamp") or [],q["close"],q.get("volume") or []) if p is not None and math.isfinite(float(p))]
  bars=[(float(o),float(h),float(l),float(cl)) for o,h,l,cl in zip(q["open"],q["high"],q["low"],q["close"]) if all(v is not None and math.isfinite(float(v)) for v in (o,h,l,cl))]
 if len(bars)<4:return None
 a,b,c=bars[-3:]
 def green(x):return x[3]>x[0]
 def red(x):return x[3]<x[0]
 body=abs(c[3]-c[0]);rng=max(c[1]-c[2],1e-12)
 upper=c[1]-max(c[0],c[3]);lower=min(c[0],c[3])-c[2]
 patterns=[]
 if red(b) and green(c) and c[0]<=b[3] and c[3]>=b[0]:patterns.append("bullish_engulfing")
 if green(b) and red(c) and c[0]>=b[3] and c[3]<=b[0]:patterns.append("bearish_engulfing")
 if lower>=2*max(body,1e-12) and upper<=max(body,0.1*rng) and body/rng>=.08:patterns.append("hammer")
 if upper>=2*max(body,1e-12) and lower<=max(body,0.1*rng) and body/rng>=.08:patterns.append("shooting_star")
 if all(green(x) for x in (a,b,c)) and a[3]<b[3]<c[3]:patterns.append("three_green")
 if all(red(x) for x in (a,b,c)) and a[3]>b[3]>c[3]:patterns.append("three_red")
 if len(points)<55 or now-points[-1][0]>900:return None
 prices=[p for _,p,_ in points];volumes=[v for _,_,v in points]
 latest=prices[-1];old=prices[-25]
 if old<=0:return None
 gains=[max(prices[i]-prices[i-1],0) for i in range(-14,0)]
 losses=[max(prices[i-1]-prices[i],0) for i in range(-14,0)]
 avg_gain=sum(gains)/14;avg_loss=sum(losses)/14
 rsi=100 if avg_loss==0 else 100-100/(1+avg_gain/avg_loss)
 def ema(vals,n):
  out=vals[0];k=2/(n+1)
  for v in vals[1:]:out=v*k+out*(1-k)
  return out
 macd=ema(prices[-60:],12)-ema(prices[-60:],26)
 returns=[prices[i]/prices[i-1]-1 for i in range(-24,0) if prices[i-1]>0]
 volatility=math.sqrt(sum((x-sum(returns)/len(returns))**2 for x in returns)/len(returns))*math.sqrt(24) if returns else 0
 vol_base=sum(volumes[-25:-1])/24
 volume_ratio=volumes[-1]/vol_base if vol_base>0 else None
 return {"momentum":latest/old-1,"rsi":round(rsi,2),"macd_pct":round(macd/latest*100,4),"sma20_gap_pct":round((latest/(sum(prices[-20:])/20)-1)*100,3),"volatility_2h_pct":round(volatility*100,3),"volume_ratio":round(volume_ratio,2) if volume_ratio is not None else None,"candles":len(points),"patterns":patterns,"last_candle":"green" if green(c) else ("red" if red(c) else "doji"),"last_ohlc":{"open":c[0],"high":c[1],"low":c[2],"close":c[3]}}

def ai_approve(asset,indicators,price):
 key=os.getenv("OPENAI_API_KEY","")
 if not key:return False,"OPENAI_API_KEY absente"
 model=os.getenv("OPENAI_MODEL","gpt-4.1-mini")
 payload={"model":model,"input":[{"role":"system","content":"You are a risk reviewer for simulated trading ONLY. Evaluate the supplied indicators and actual candlestick patterns, not hypothetical news or fabricated evidence. Return one line formatted APPROVE: short reason or REJECT: short reason. The entry_setup can be trend, reversal, or three_green. A trend setup does not require a named candlestick pattern. Evaluate momentum, RSI, MACD, SMA20, volatility, fees, and pattern only when relevant. Reject contradictory or overbought signals, or risk/reward unlikely to cover trading costs. Do not invent data. No real orders."},{"role":"user","content":json.dumps({"asset":asset,"price_eur":round(price,6),"timeframe":"5-minute candles","indicators":indicators,"stop_loss_pct":1.2,"take_profit_pct":round(max(.018,2*fee_rate(asset)+.014)*100,2),"fee_per_side_pct":round(fee_rate(asset)*100,2),"paper_trading":True})}],"max_output_tokens":70}
 req=urllib.request.Request("https://api.openai.com/v1/responses",data=json.dumps(payload).encode(),headers={"Authorization":"Bearer "+key,"Content-Type":"application/json"},method="POST")
 try:
  with urllib.request.urlopen(req,timeout=18) as r:result=json.load(r)
  outputs=[part.get("text","") for item in result.get("output",[]) for part in item.get("content",[]) if part.get("type")=="output_text"]
  answer=" ".join(outputs).strip().upper()
  verdict=answer.split(":",1)[0].strip()
  return verdict=="APPROVE",answer[:180] or "reponse vide"
 except urllib.error.HTTPError as e:
  detail=e.read(500).decode("utf-8","replace")
  try:detail=json.loads(detail).get("error",{}).get("message",detail)
  except Exception:pass
  return False,"IA HTTP "+str(e.code)+": "+str(detail)[:160]
 except Exception as e:return False,"IA indisponible: "+str(e)[:110]

def scan():
 now=int(time.time());errors=[];fresh={}
 def load_quote(item):
  a,(kind,symbol,cur)=item
  return a,quote(a,kind,symbol)
 with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
  futures={pool.submit(load_quote,item):item[0] for item in ASSETS.items()}
  for future in concurrent.futures.as_completed(futures):
   a=futures[future]
   try:
    _,(p,t,src)=future.result()
    if not math.isfinite(p) or p<=0 or t>now+60:raise ValueError("cours invalide")
    fresh[a]=(p,t,src)
   except Exception as e:errors.append(a+": "+str(e)[:100])
 history={}
 eligible=[(a,kind,symbol) for a,(kind,symbol,cur) in ASSETS.items() if kind in ("crypto","etf","stock") and a in fresh and now-fresh[a][1]<600 and (cur=="EUR" or ("EURUSD" in fresh and now-fresh["EURUSD"][1]<600))]
 def load_history(item):
  a,kind,symbol=item
  return a,market_indicators(a,kind,symbol,now)
 with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
  futures={pool.submit(load_history,item):item[0] for item in eligible}
  for future in concurrent.futures.as_completed(futures):
   a=futures[future]
   try:
    _,ind=future.result()
    history[a]=ind
   except Exception as e:errors.append(a+" history: "+str(e)[:100])
 with db() as c:
  for a,(p,t,src) in fresh.items():
   c.execute("INSERT OR REPLACE INTO quotes VALUES(?,?,?,?)",(a,p,t,src))
   if now-t<600:c.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?)",(now,a,p))
  c.execute("DELETE FROM samples WHERE t<?",(now-86400,))
  s=c.execute("SELECT * FROM state WHERE id=1").fetchone();cash=s["cash"]
  audit(c,now,"SCAN","",f"{len(fresh)}/{len(ASSETS)} cotations reçues; {len(history)} historiques récupérés; {len(errors)} erreurs; collecte parallèle")
  fx=fresh.get("EURUSD")
  def eur(a):
   q=fresh.get(a)
   if not q or now-q[1]>600:return None
   cur=ASSETS[a][2]
   if cur=="EUR":return q[0]
   if cur=="USD" and fx and now-fx[1]<600 and fx[0]>0:return q[0]/fx[0]
   return None
  pos={r["asset"]:dict(r) for r in c.execute("SELECT * FROM positions")}
  day=datetime.fromtimestamp(now,timezone.utc).strftime("%Y-%m-%d")
  c.execute("INSERT OR IGNORE INTO daily_risk VALUES(?,?)",(day,cash+sum(p["cost"] for p in pos.values())))
  day_start=c.execute("SELECT start_equity FROM daily_risk WHERE day=?",(day,)).fetchone()[0]
  c.execute("DELETE FROM daily_risk WHERE day<?",(day,))
  for a,p in list(pos.items()):
   px=eur(a)
   if px is None:continue
   change=px/p["entry"]-1
   fee=fee_rate(a)
   target=max(.018,2*fee+.014)
   if change>=target or change<=-.012:
    fee=fee_rate(a)
    proceeds=p["qty"]*px*(1-fee);cash+=proceeds
    c.execute("INSERT INTO trades(t,asset,side,price,amount,pnl,reason) VALUES(?,?,?,?,?,?,?)",(now,a,"SELL",px,proceeds,proceeds-p["cost"],"Micro take profit" if change>=target else "Micro stop loss"))
    audit(c,now,"SELL",a,f"Sortie simulée: prix EUR={px:.6f}, frais={fee*100:.2f}% par côté, P&L net={proceeds-p['cost']:.4f} EUR")
    c.execute("DELETE FROM positions WHERE asset=?",(a,));del pos[a]
  candidates=[]
  for a,(kind,_,_) in ASSETS.items():
   if kind not in ("crypto","etf","stock") or a in pos:continue
   px=eur(a)
   if px is None:continue
   ind=history.get(a)
   if ind is None:continue
   trend=.002<ind["momentum"]<.04 and 38<=ind["rsi"]<=70 and ind["macd_pct"]>0 and ind["sma20_gap_pct"]>0
   reversal=any(p in ind["patterns"] for p in ("bullish_engulfing","hammer")) and 28<=ind["rsi"]<=65 and ind["momentum"]>-.025 and ind["sma20_gap_pct"]>-.8
   candle_continuation="three_green" in ind["patterns"] and 38<=ind["rsi"]<=72 and ind["momentum"]>0
   ind["entry_setup"]="trend" if trend else ("reversal" if reversal else ("three_green" if candle_continuation else "none"))
   if trend or reversal or candle_continuation:
    last=c.execute("SELECT MAX(t) FROM trades WHERE asset=? AND side='BUY'",(a,)).fetchone()[0]
    if last and now-last<120:
     audit(c,now,"BLOCK",a,"Signal détecté, mais cooldown de 2 minutes")
     continue
    candidates.append((ind["momentum"],a,px,ind))
  audit(c,now,"SIGNALS","",f"{len(candidates)} signaux éligibles; "+", ".join(a for _,a,_,_ in candidates[:8]))
  equity=cash+sum(p["qty"]*(eur(a) or p["entry"]) for a,p in pos.items())
  daily_loss_limit=max(0,day_start*.08)
  if equity<=day_start-daily_loss_limit:
   audit(c,now,"BLOCK","","Coupe-circuit journalier actif: aucune nouvelle position")
  elif not candidates:
   audit(c,now,"BLOCK","","Aucun signal exploitable sur les données disponibles")
  else:
   ranked=sorted(candidates,key=lambda x:(x[3]["entry_setup"]=="trend",x[0]),reverse=True)
   for mom,a,px,ind in ranked:
    if len(pos)>=10 or cash<20:break
    amount=min(cash*.35,max(25,equity*.18))
    fee=fee_rate(a)
    if amount*(.012+2*fee)>5:amount=5/(.012+2*fee)
    if amount<12:continue
    audit(c,now,"SIGNAL",a,f"Signal technique accepté sans contrôle IA; setup={ind['entry_setup']}; patterns={ind['patterns']}")
    qty=amount*(1-fee)/px
    cash-=amount
    c.execute("INSERT INTO positions VALUES(?,?,?,?)",(a,qty,px,amount))
    c.execute("INSERT INTO trades(t,asset,side,price,amount,pnl,reason) VALUES(?,?,?,?,?,?,?)",(now,a,"BUY",px,amount,0,"Signal technique; stratégie "+ind["entry_setup"]))
    audit(c,now,"BUY",a,f"Achat fictif {amount:.2f} EUR; stratégie={ind['entry_setup']}; prix={px:.6f}; frais={fee*100:.2f}%")
    pos[a]={"asset":a,"qty":qty,"entry":px,"cost":amount}
  if errors:audit(c,now,"ERROR","", "; ".join(errors[:6]))
  if equity<=day_start-daily_loss_limit:audit(c,now,"BLOCK","","Coupe-circuit de perte journalière actif")
  c.execute("UPDATE state SET cash=?,last_run=?,error=? WHERE id=1",(cash,now,"; ".join(errors[:6]) if errors else None))
def snapshot():
 now=int(time.time())
 with db() as c:
  s=dict(c.execute("SELECT * FROM state WHERE id=1").fetchone())
  quotes=[dict(x) for x in c.execute("SELECT * FROM quotes ORDER BY asset")]
  q={x["asset"]:x for x in quotes};fx=q.get("EURUSD")
  def eur(a):
   x=q.get(a)
   if not x or now-x["t"]>600:return None
   cur=ASSETS[a][2]
   if cur=="EUR":return x["price"]
   if cur=="USD" and fx and now-fx["t"]<600:return x["price"]/fx["price"]
   return None
  pos=[dict(x) for x in c.execute("SELECT * FROM positions")]
  trades=[dict(x) for x in c.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 100")]
  decisions=[dict(x) for x in c.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 20")]
  audit_events=[dict(x) for x in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 150")]
  total=s["cash"]+sum(p["qty"]*(eur(p["asset"]) or p["entry"]) for p in pos)
  for x in quotes:x["fresh"]=now-x["t"]<600;x["eur_price"]=eur(x["asset"])
  return {"strategy":"paper trading; conservative taker-fee model; aggressive paper mode; no hourly trade cap; up to 10 positions; 2 min cooldown; 8% daily circuit breaker","fee_model":{"crypto_taker_per_side":fee_rate("BTC-EUR"),"equity_sim_per_side":fee_rate("SPY")}, "daily_circuit_breaker_pct":8,"cash":s["cash"],"value":total,"pnl":total-500,"last_run":s["last_run"],"error":s["error"],"quotes":quotes,"positions":pos,"trades":trades,"decisions":decisions,"audit":audit_events,"ai_configured":bool(os.getenv("OPENAI_API_KEY")),"ai_model":os.getenv("OPENAI_MODEL","gpt-4.1-mini"),"simulation_only":True,"markets_total":len(ASSETS),"markets_fresh":sum(x["fresh"] for x in quotes),"engine_recent":bool(s["last_run"] and now-s["last_run"]<900),"last_scan_age_seconds":now-s["last_run"] if s["last_run"] else None,"note":"Simulation uniquement. Les cours Yahoo peuvent etre differes. Analyse relancee 15 secondes apres chaque scan complet. Render gratuit: veille et stockage ephemere; aucune garantie 24/7 ni de conservation des trades."}
HTML=Path(__file__).with_name("index.html").read_bytes()
class Handler(BaseHTTPRequestHandler):
 def do_HEAD(self):
  self.send_response(200);self.send_header("Content-Type","text/html");self.end_headers()
 def do_GET(self):
  if self.path.split("?")[0] in ("/","/index.html"):body=HTML;mime="text/html; charset=utf-8"
  elif self.path.split("?")[0]=="/api/state":body=json.dumps(snapshot()).encode();mime="application/json"
  elif self.path.split("?")[0]=="/health":body=b'{"ok":true}';mime="application/json"
  else:self.send_error(404);return
  self.send_response(200);self.send_header("Content-Type",mime);self.send_header("Cache-Control","no-store");self.send_header("Content-Length",str(len(body)));self.end_headers();self.wfile.write(body)
def worker():
 while True:
  try:scan()
  except Exception as e:
   with db() as c:
    audit(c,int(time.time()),"ERROR","ENGINE","Erreur moteur: "+traceback.format_exc()[-650:])
    c.execute("UPDATE state SET error=? WHERE id=1",("Erreur moteur: "+str(e)[:150],))
  time.sleep(15)
if __name__=="__main__":
 init();threading.Thread(target=worker,daemon=True).start();ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
