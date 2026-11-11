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
 url="https://data-api.polymarket.com/v2/activity?"+urllib.parse.urlencode({"user":TARGET,"limit":limit,"type":"TRADE","sort_by":"TIMESTAMP","sort_direction":"DESC"})
 payload=fetch(url)
 if not isinstance(payload,dict) or not isinstance(payload.get("data"),list):raise ValueError("Unexpected Data API v2 activity envelope")
 return payload["data"]
def target_exposure_value():
 payload=fetch("https://data-api.polymarket.com/v2/value?"+urllib.parse.urlencode({"user":TARGET}))
 data=payload.get("data") if isinstance(payload,dict) else None
 if not isinstance(data,dict):raise ValueError("Target position value unavailable")
 value=float(data.get("value") or 0)
 if value<=0:raise ValueError("Target open-position value is zero; cannot size proportional trades")
 return value
def usd_per_eur():
 d=fetch("https://api.kraken.com/0/public/Ticker?pair=EURUSD")
 if d.get("error"):raise ValueError(str(d["error"]))
 return float(next(iter(d["result"].values()))["c"][0])
def open_positions():
 """Fetch public open positions; reject unknown response shapes rather than inventing positions."""
 url="https://data-api.polymarket.com/positions?"+urllib.parse.urlencode({"user":TARGET,"limit":500,"sizeThreshold":0})
 payload=fetch(url)
 rows=payload.get("data") if isinstance(payload,dict) else payload
 if isinstance(rows,dict):rows=rows.get("positions")
 if not isinstance(rows,list):raise ValueError("Unexpected v2 positions payload; no bootstrap performed")
 positions=[]
 for p in rows:
  if not isinstance(p,dict):continue
  asset=p.get("token_id") or p.get("asset") or p.get("tokenId")
  shares=float(p.get("current_size") or p.get("size") or 0)
  price=float(p.get("current_price") or p.get("curPrice") or p.get("price") or 0)
  value=float(p.get("current_value") or p.get("currentValue") or shares*price)
  if asset and shares>0 and 0.02<=price<=0.98 and value>0:
   positions.append((str(asset),str(p.get("title") or p.get("slug") or p.get("conditionId") or ""),shares,price,value))
 return positions
def bootstrap(path,now):
 """Initial paper allocation mirrors observable open-position weights, not unknown source cash."""
 positions=open_positions()
 if not positions:raise ValueError("No eligible public open positions returned")
 total=sum(p[4] for p in positions)
 if total<=0:raise ValueError("Source position value is zero")
 fx=usd_per_eur()
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  state=c.execute("SELECT cash,started FROM copy_state WHERE id=1").fetchone()
  if state[1]:return {"status":"already_started"}
  cash=500.0
  copied=0
  for asset,market,source_shares,price,value in sorted(positions,key=lambda p:p[4],reverse=True)[:5]:
   allocation=min(cash,500.0*value/total)
   simulated_price=min(.99,price+.02)
   if allocation<1:continue
   shares=allocation*fx/(simulated_price+.01)
   cash-=allocation
   c.execute("INSERT OR REPLACE INTO copy_positions VALUES(?,?,?,?,?)",(asset,market,shares,allocation,simulated_price))
   c.execute("INSERT OR IGNORE INTO copy_trades(trade_key,t,market,asset,side,target_price,paper_price,shares,spent,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
    ("bootstrap:"+asset,now,market,asset,"BUY",price,simulated_price,shares,allocation,"Initial public open-position snapshot; hypothetical 2c price disadvantage and 1c/share cost"))
   copied+=1
  if not copied:raise ValueError("All public position allocations were below 1 EUR")
  c.execute("UPDATE copy_state SET cash=?,started=?,last_seen=?,error=NULL WHERE id=1",(cash,now,now))
  c.execute("UPDATE copy_health SET checked=?,events=0,latest=0,candidates=?,copied=?,reason=? WHERE id=1",(now,len(positions),copied,"Initial public open positions synchronized"))
 return {"status":"bootstrapped","positions":copied,"cash_eur":round(cash,2)}
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
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  cash,last_seen,started=c.execute("SELECT cash,last_seen,started FROM copy_state WHERE id=1").fetchone()
 # Public positions are the authoritative desired portfolio. Reconcile the snapshot
 # instead of relying on the first 100 activity records (which can omit fast trades).
 try:
  positions=open_positions()
  fx=usd_per_eur()
 except Exception as exc:
  with LOCK,sqlite3.connect(path,timeout=15) as c:
   c.execute("UPDATE copy_state SET error=? WHERE id=1",(str(exc)[:250],))
   c.execute("UPDATE copy_health SET checked=?,reason=? WHERE id=1",(now,"Snapshot error: "+str(exc)[:150]))
  return {"error":str(exc)[:180]}
 # Limit exposure to the top five current public positions. No source cash visibility.
 selected=sorted(positions,key=lambda x:x[4],reverse=True)[:5]
 total=sum(x[4] for x in positions)
 desired={p[0]:p for p in selected}
 copied=0
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  if not started:
   cash=500.
   c.execute("UPDATE copy_state SET started=?,cash=?,last_seen=? WHERE id=1",(now,cash,now))
  existing={x[0]:x for x in c.execute("SELECT asset,market,shares,cost,last_price FROM copy_positions")}
  # Sell disappeared positions first, releasing cash for new entries.
  for asset,p in existing.items():
   if asset in desired:continue
   shares=p[2];price=max(.001,p[4]-.02)
   proceeds=max(0.,shares*(price-.01)/fx)
   cash+=proceeds
   c.execute("DELETE FROM copy_positions WHERE asset=?",(asset,))
   c.execute("INSERT OR IGNORE INTO copy_trades(trade_key,t,market,asset,side,target_price,paper_price,shares,spent,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
    ("snapshot:sell:"+asset+":"+str(now),now,p[1],asset,"SELL",p[4],price,shares,proceeds,"Public source position absent from latest snapshot; simulated exit"))
   copied+=1
  # Existing positions are held; buy only new positions. Avoid repeated buys
  # solely because market prices changed.
  for asset,market,source_shares,price,value in selected:
   if asset in existing:continue
   allocation=min(cash,500.*value/total) if total>0 else 0.
   if allocation<1:continue
   entry=min(.99,price+.02)
   shares=allocation*fx/(entry+.01)
   cash-=allocation
   c.execute("INSERT INTO copy_positions VALUES(?,?,?,?,?)",(asset,market,shares,allocation,entry))
   c.execute("INSERT OR IGNORE INTO copy_trades(trade_key,t,market,asset,side,target_price,paper_price,shares,spent,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",
    ("snapshot:buy:"+asset+":"+str(now),now,market,asset,"BUY",price,entry,shares,allocation,"Public source position detected; proportional paper allocation"))
   copied+=1
  c.execute("UPDATE copy_state SET cash=?,error=NULL WHERE id=1",(cash,))
  c.execute("UPDATE copy_health SET checked=?,events=?,latest=?,candidates=?,copied=?,reason=? WHERE id=1",
   (now,len(positions),0,len(selected),copied,"Snapshot reconciled: "+str(copied)+" paper orders" if copied else "Snapshot checked; no eligible positions" if not positions else "Snapshot checked; no change in top five positions"))
 return {"status":"monitoring","copied":copied,"source_open_positions":len(positions),"cash_eur":round(cash,2)}
def report(path):
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  state=c.execute("SELECT cash,last_seen,started,error FROM copy_state WHERE id=1").fetchone()
  positions=c.execute("SELECT asset,market,shares,cost,last_price FROM copy_positions ORDER BY cost DESC").fetchall()
  trades=c.execute("SELECT t,market,side,target_price,paper_price,shares,spent,reason FROM copy_trades ORDER BY id DESC LIMIT 40").fetchall()
  health=c.execute("SELECT checked,events,latest,candidates,copied,reason FROM copy_health WHERE id=1").fetchone()
 cash,last_seen,started,error=state
 return {"target":TARGET,"cash_eur":round(cash,2),"cash_usd":None,"starting_capital_eur":500,"sizing_method":"public open-position snapshot: each new top-five position allocated 500 EUR * position_value / all_open_positions_value","positions":[{"asset":x[0],"market":x[1],"shares":x[2],"cost":x[3],"last_trade_price":x[4]} for x in positions],
 "trades":[{"timestamp":x[0],"market":x[1],"side":x[2],"target_price":x[3],"paper_price":x[4],"shares":x[5],"amount":x[6],"reason":x[7]} for x in trades],
 "health":{"checked_at":health[0],"public_events":health[1],"latest_public_trade":health[2],"candidates":health[3],"copied_last_poll":health[4],"reason":health[5]} if health else None,
 "last_target_trade":None,"started":started,"error":error,"execution_enabled":False,
 "status":"not_started" if not started else ("api_error" if error else "monitoring"),
 "warning":"Reconciles public top-five open positions, not individual source fills. Position changes within already-held tokens are not copied. Source cash unknown. Initial snapshot allocation mirrors top five public positions only, excludes unknown source cash and is not exact portfolio copy. Initial capital and paper cash held in EUR; individual simulated contracts priced in USD using Kraken EUR/USD at copying time. Target weight is trade notional divided by public OPEN POSITION value, not actual full portfolio equity: target cash is unknown, so exact percentage matching is impossible. No historical trades copied. 2c adverse price and 1c/share assumed. Render storage ephemeral."}
