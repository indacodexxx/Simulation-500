"""Public Polymarket wallet follow in PAPER mode only.
Trades are copied from the public Data API with a 500 EUR independent ledger.
No keys, wallets, on-chain transactions or real orders.
"""
import json,time,sqlite3,threading,urllib.request,urllib.parse
TARGET="0xcE25E214D5cfE4f459cf67F08DF581885AAE7Fdc"
LOCK=threading.Lock()
def fetch(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Simulation500-PaperCopy/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=12) as r:return json.load(r)
def activity(limit=100):
 url="https://data-api.polymarket.com/activity?"+urllib.parse.urlencode({"user":TARGET,"limit":limit,"type":"TRADE","sortBy":"TIMESTAMP","sortDirection":"DESC"})
 data=fetch(url)
 if not isinstance(data,list):raise ValueError("Unexpected activity payload")
 return data
def target_exposure_value():
 data=fetch("https://data-api.polymarket.com/value?"+urllib.parse.urlencode({"user":TARGET}))
 if not isinstance(data,list):raise ValueError("Target position value unavailable")
 value=sum(float(x.get("value") or 0) for x in data)
 if value<=0:raise ValueError("Target open-position value is zero; cannot size proportional trades")
 return value
def usd_per_eur():
 d=fetch("https://api.kraken.com/0/public/Ticker?pair=EURUSD")
 if d.get("error"):raise ValueError(str(d["error"]))
 return float(next(iter(d["result"].values()))["c"][0])
def init(path):
 with sqlite3.connect(path,timeout=15) as c:
  c.execute("CREATE TABLE IF NOT EXISTS copy_state (id INTEGER PRIMARY KEY CHECK(id=1),cash REAL,last_seen INTEGER,started INTEGER,error TEXT)")
  c.execute("INSERT OR IGNORE INTO copy_state VALUES(1,500,0,0,NULL)")
  c.execute("CREATE TABLE IF NOT EXISTS copy_trades (id INTEGER PRIMARY KEY AUTOINCREMENT,trade_key TEXT UNIQUE,t INTEGER,market TEXT,asset TEXT,side TEXT,target_price REAL,paper_price REAL,shares REAL,spent REAL,reason TEXT)")
  c.execute("CREATE TABLE IF NOT EXISTS copy_positions (asset TEXT PRIMARY KEY,market TEXT,shares REAL,cost REAL,last_price REAL)")
  c.execute("CREATE TABLE IF NOT EXISTS copy_health (id INTEGER PRIMARY KEY CHECK(id=1),checked INTEGER,events INTEGER,latest INTEGER,candidates INTEGER,copied INTEGER,reason TEXT)")
  c.execute("INSERT OR IGNORE INTO copy_health VALUES(1,0,0,0,0,0,'Not polled yet')")
def run(path):
 now=int(time.time())
 try:events=activity(100)
 except Exception as e:
  with LOCK,sqlite3.connect(path,timeout=15) as c:c.execute("UPDATE copy_state SET error=? WHERE id=1",(str(e)[:250],))
  return {"error":str(e)[:250]}

 # Target's public open-position value excludes unknown cash balance.
 # Paper ledger is USD internally; 500 EUR starting capital converted to USD.

 with LOCK,sqlite3.connect(path,timeout=15) as c:
  state=c.execute("SELECT cash,last_seen,started FROM copy_state WHERE id=1").fetchone()
  cash,last_seen,started=state
  if not started:cash=500
  valid=[e for e in events if str(e.get("side","")).upper() in ("BUY","SELL") and e.get("timestamp") and e.get("asset")]
  latest=max([int(e["timestamp"]) for e in valid],default=0)
  if not started:
   c.execute("UPDATE copy_health SET checked=?,events=?,latest=?,candidates=0,copied=0,reason=? WHERE id=1",(now,len(valid),latest,"First poll: historical trades excluded"))
   c.execute("UPDATE copy_state SET cash=?,started=?,last_seen=?,error=NULL WHERE id=1",(cash,now,max(latest,now-30)))
   return {"status":"initialized","recent_public_trades":len(valid),"latest_public_trade":latest}
  # Overlap 5 minutes to handle delayed indexing; deduplicate by stable trade key.
  new=sorted((e for e in valid if int(e["timestamp"])>=max(started,last_seen-300) and now-int(e["timestamp"])<=600),key=lambda e:int(e["timestamp"]))
  copied=0
  if new:
   try:
    denominator=target_exposure_value()
    fx=usd_per_eur()
   except Exception as exc:
    c.execute("UPDATE copy_health SET checked=?,events=?,latest=?,candidates=?,copied=0,reason=? WHERE id=1",(now,len(valid),latest,len(new),"Sizing API error: "+str(exc)[:150]))
    c.execute("UPDATE copy_state SET error=? WHERE id=1",(("Sizing unavailable: "+str(exc))[:250],))
    return {"error":"Sizing unavailable: "+str(exc)[:180]}
  for e in new:
   side=str(e["side"]).upper();asset=str(e["asset"]);t=int(e["timestamp"]);market=str(e.get("title") or e.get("slug") or e.get("conditionId") or "")
   price=float(e.get("price") or 0);target_size=float(e.get("size") or 0)
   if not 0.02<=price<=0.98 or target_size<=0:continue
   key=":".join(str(e.get(k) or "") for k in ("transactionHash","asset","side","timestamp","price","size"))
   if c.execute("SELECT 1 FROM copy_trades WHERE trade_key=?",(key,)).fetchone():continue
   p=c.execute("SELECT shares,cost,last_price FROM copy_positions WHERE asset=?",(asset,)).fetchone()
   if side=="BUY":
    if c.execute("SELECT COUNT(*) FROM copy_positions").fetchone()[0]>=5 and not p:continue
    # Assumed 2 cent price disadvantage + 1 cent execution cost per share, not observed fill.
    simulated_price=min(.99,price+.02)
    target_notional=target_size*price
    target_weight=target_notional/denominator
    paper_equity=cash+sum(x[1] for x in c.execute("SELECT shares,cost,last_price FROM copy_positions"))
    amount=min(cash,max(0,paper_equity*target_weight))
    if amount<1:continue
    shares=amount*fx/(simulated_price+.01)
    cash-=amount
    old_shares,old_cost=(p[0],p[1]) if p else (0,0)
    c.execute("INSERT OR REPLACE INTO copy_positions VALUES(?,?,?,?,?)",(asset,market,old_shares+shares,old_cost+amount,simulated_price))
    reason="Paper copy: target BUY; 2c adverse price and 1c cost/share assumed"
   else:
    if not p:continue
    simulated_price=max(.001,price-.02)
    target_weight=(target_size*price)/denominator
    paper_equity=cash+sum(x[0]*x[2] for x in c.execute("SELECT shares,cost,last_price FROM copy_positions"))
    shares=min(p[0],max(0,paper_equity*target_weight)*fx/max(.001,p[2]))
    amount=max(0,shares*(simulated_price-.01)/fx)
    cash+=amount
    left=p[0]-shares
    if left<1e-7:c.execute("DELETE FROM copy_positions WHERE asset=?",(asset,))
    else:c.execute("UPDATE copy_positions SET shares=?,cost=?,last_price=? WHERE asset=?",(left,p[1]*left/p[0],simulated_price,asset))
    reason="Paper copy: target SELL; 2c adverse price and 1c cost/share assumed"
   c.execute("INSERT INTO copy_trades(trade_key,t,market,asset,side,target_price,paper_price,shares,spent,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
    (key,t,market,asset,side,price,simulated_price,shares,amount,reason))
   copied+=1
  c.execute("UPDATE copy_health SET checked=?,events=?,latest=?,candidates=?,copied=?,reason=? WHERE id=1",(now,len(valid),latest,len(new),copied,"Trades copied" if copied else ("Candidates filtered or duplicate" if new else "No new public trades in polling window")))
  c.execute("UPDATE copy_state SET cash=?,last_seen=?,error=NULL WHERE id=1",(cash,max(last_seen,latest)))
 return {"status":"running","copied":copied,"latest_public_trade":latest}
def report(path):
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  state=c.execute("SELECT cash,last_seen,started,error FROM copy_state WHERE id=1").fetchone()
  positions=c.execute("SELECT asset,market,shares,cost,last_price FROM copy_positions ORDER BY cost DESC").fetchall()
  trades=c.execute("SELECT t,market,side,target_price,paper_price,shares,spent,reason FROM copy_trades ORDER BY id DESC LIMIT 40").fetchall()
  health=c.execute("SELECT checked,events,latest,candidates,copied,reason FROM copy_health WHERE id=1").fetchone()
 cash,last_seen,started,error=state
 return {"target":TARGET,"cash_eur":round(cash,2),"cash_usd":None,"starting_capital_eur":500,"sizing_method":"target_trade_usd / target_open_positions_value_usd (proxy, excludes unknown cash)","positions":[{"asset":x[0],"market":x[1],"shares":x[2],"cost":x[3],"last_trade_price":x[4]} for x in positions],
 "trades":[{"timestamp":x[0],"market":x[1],"side":x[2],"target_price":x[3],"paper_price":x[4],"shares":x[5],"amount":x[6],"reason":x[7]} for x in trades],
 "health":{"checked_at":health[0],"public_events":health[1],"latest_public_trade":health[2],"candidates":health[3],"copied_last_poll":health[4],"reason":health[5]} if health else None,
 "last_target_trade":last_seen,"started":started,"error":error,"execution_enabled":False,
 "status":"not_started" if not started else ("api_error" if error else "monitoring"),
 "warning":"Initial capital and paper cash held in EUR; individual simulated contracts priced in USD using Kraken EUR/USD at copying time. Target weight is trade notional divided by public OPEN POSITION value, not actual full portfolio equity: target cash is unknown, so exact percentage matching is impossible. No historical trades copied. 2c adverse price and 1c/share assumed. Render storage ephemeral."}
