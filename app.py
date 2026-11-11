import os,time,json,threading,urllib.request,urllib.parse,sqlite3,math
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
PORT=int(os.getenv("PORT","10000")); DB=os.getenv("DB_PATH","/tmp/simulation500.sqlite")
ASSETS={"BTC-EUR":("crypto","BTCEUR","EUR"),"ETH-EUR":("crypto","ETHEUR","EUR"),"SOL-EUR":("crypto","SOLEUR","EUR"),"SPY":("etf","SPY","USD"),"QQQ":("etf","QQQ","USD"),"DIA":("etf","DIA","USD"),"IWM":("etf","IWM","USD"),"EWQ":("etf","EWQ","USD"),"EWG":("etf","EWG","USD"),"EWJ":("etf","EWJ","USD"),"GLD":("etf","GLD","USD"),"SLV":("etf","SLV","USD"),"TLT":("etf","TLT","USD"),"AAPL":("stock","AAPL","USD"),"MSFT":("stock","MSFT","USD"),"NVDA":("stock","NVDA","USD"),"TSLA":("stock","TSLA","USD"),"ASML":("stock","ASML","USD"),"MC.PA":("stock","MC.PA","EUR"),"AIR.PA":("stock","AIR.PA","EUR"),"CAC40":("index","^FCHI","EUR"),"SP500":("index","^GSPC","USD"),"NASDAQ100":("index","^NDX","USD"),"DAX":("index","^GDAXI","EUR"),"NIKKEI":("index","^N225","JPY"),"HANGSENG":("index","^HSI","HKD"),"FTSE":("index","^FTSE","GBP"),"VIX":("index","^VIX","USD"),"EURUSD":("fx","EURUSD=X","USD")}
def db():
 c=sqlite3.connect(DB,timeout=15);c.row_factory=sqlite3.Row;return c
def init():
 Path(DB).parent.mkdir(parents=True,exist_ok=True)
 with db() as c:
  c.executescript("CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY,cash REAL,last_run INTEGER,error TEXT);CREATE TABLE IF NOT EXISTS quotes(asset TEXT PRIMARY KEY,price REAL,t INTEGER,source TEXT);CREATE TABLE IF NOT EXISTS samples(t INTEGER,asset TEXT,price REAL,PRIMARY KEY(t,asset));CREATE TABLE IF NOT EXISTS positions(asset TEXT PRIMARY KEY,qty REAL,entry REAL,cost REAL);CREATE TABLE IF NOT EXISTS trades(id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER,asset TEXT,side TEXT,price REAL,amount REAL,pnl REAL,reason TEXT);")
  c.execute("INSERT OR IGNORE INTO state VALUES(1,500,0,'En attente du premier scan')")
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
def historical_momentum(asset,kind,symbol,now):
 # Use actual historical candles, not a one-hour warm-up or invented prices.
 if kind=="crypto":
  pair={"BTC-EUR":"XXBTZEUR","ETH-EUR":"XETHZEUR","SOL-EUR":"SOLEUR"}[asset]
  d=get("https://api.kraken.com/0/public/OHLC?pair="+pair+"&interval=5")
  if d.get("error"):raise ValueError(str(d["error"]))
  candles=next(v for k,v in d["result"].items() if k!="last")
  points=[(int(x[0]),float(x[4])) for x in candles if int(x[0])<=now-300]
 else:
  d=get("https://query1.finance.yahoo.com/v8/finance/chart/"+urllib.parse.quote(symbol,safe="")+"?interval=5m&range=2d")["chart"]["result"][0]
  timestamps=d.get("timestamp") or []
  closes=d["indicators"]["quote"][0]["close"]
  points=[(int(t),float(p)) for t,p in zip(timestamps,closes) if p is not None and math.isfinite(float(p))]
 if len(points)<8:return None
 latest_t,latest=points[-1]
 if now-latest_t>600:return None
 earlier=[(t,p) for t,p in points if now-7800<=t<=now-6000]
 if not earlier:return None
 return latest/earlier[0][1]-1
def scan():
 now=int(time.time());errors=[];fresh={}
 for a,(kind,symbol,cur) in ASSETS.items():
  try:
   p,t,src=quote(a,kind,symbol)
   if not math.isfinite(p) or p<=0 or t>now+60:raise ValueError("cours invalide")
   fresh[a]=(p,t,src)
  except Exception as e:errors.append(a+": "+str(e)[:55])
 with db() as c:
  for a,(p,t,src) in fresh.items():
   c.execute("INSERT OR REPLACE INTO quotes VALUES(?,?,?,?)",(a,p,t,src))
   if now-t<600:c.execute("INSERT OR REPLACE INTO samples VALUES(?,?,?)",(now,a,p))
  c.execute("DELETE FROM samples WHERE t<?",(now-86400,))
  s=c.execute("SELECT * FROM state WHERE id=1").fetchone();cash=s["cash"]
  fx=fresh.get("EURUSD")
  def eur(a):
   q=fresh.get(a)
   if not q or now-q[1]>600:return None
   cur=ASSETS[a][2]
   if cur=="EUR":return q[0]
   if cur=="USD" and fx and now-fx[1]<600 and fx[0]>0:return q[0]/fx[0]
   return None
  pos={r["asset"]:dict(r) for r in c.execute("SELECT * FROM positions")}
  for a,p in list(pos.items()):
   px=eur(a)
   if px is None:continue
   change=px/p["entry"]-1
   if change>=.035 or change<=-.02:
    fee=.004 if ASSETS[a][0]=="crypto" else .001
    proceeds=p["qty"]*px*(1-fee);cash+=proceeds
    c.execute("INSERT INTO trades(t,asset,side,price,amount,pnl,reason) VALUES(?,?,?,?,?,?,?)",(now,a,"SELL",px,proceeds,proceeds-p["cost"],"Take profit" if change>=.035 else "Stop loss"))
    c.execute("DELETE FROM positions WHERE asset=?",(a,));del pos[a]
  candidates=[]
  for a,(kind,_,_) in ASSETS.items():
   if kind not in ("crypto","etf","stock") or a in pos:continue
   px=eur(a)
   if px is None:continue
   try:momentum=historical_momentum(a,kind,ASSETS[a][1],now)
   except Exception as e:
    errors.append(a+" history: "+str(e)[:40]);continue
   if momentum is not None and .004<momentum<.04:candidates.append((momentum,a,px))
  if len(pos)<3 and cash>=80 and candidates:
   mom,a,px=max(candidates);amount=min(100,cash*.25);fee=.004 if ASSETS[a][0]=="crypto" else .001
   qty=amount*(1-fee)/px;cash-=amount
   c.execute("INSERT INTO positions VALUES(?,?,?,?)",(a,qty,px,amount))
   c.execute("INSERT INTO trades(t,asset,side,price,amount,pnl,reason) VALUES(?,?,?,?,?,?,?)",(now,a,"BUY",px,amount,0,"Momentum 2h: "+str(round(mom*100,2))+"%"))
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
  total=s["cash"]+sum(p["qty"]*(eur(p["asset"]) or p["entry"]) for p in pos)
  for x in quotes:x["fresh"]=now-x["t"]<600;x["eur_price"]=eur(x["asset"])
  return {"cash":s["cash"],"value":total,"pnl":total-500,"last_run":s["last_run"],"error":s["error"],"quotes":quotes,"positions":pos,"trades":trades,"markets_total":len(ASSETS),"markets_fresh":sum(x["fresh"] for x in quotes),"note":"Simulation uniquement. Les cours Yahoo peuvent etre differes. Sur Render gratuit, veille et stockage ephemere: pas de garantie 24/7 ni de conservation des trades."}
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
   with db() as c:c.execute("UPDATE state SET error=? WHERE id=1",("Erreur moteur: "+str(e)[:150],))
  time.sleep(300)
if __name__=="__main__":
 init();threading.Thread(target=worker,daemon=True).start();ThreadingHTTPServer(("0.0.0.0",PORT),Handler).serve_forever()
