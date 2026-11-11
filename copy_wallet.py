"""Public Polymarket wallet follow in PAPER mode only.
Trades are copied from the public Data API with a 500 EUR independent ledger.
No keys, wallets, on-chain transactions or real orders.
"""
import json,time,sqlite3,threading,urllib.request,urllib.parse,urllib.error
import polygon_follow
TARGET="0xcE25E214D5cfE4f459cf67F08DF581885AAE7Fdc"
LOCK=threading.Lock()
POLYGON_RPC="https://polygon-bor-rpc.publicnode.com"
def polygon_rpc(method,params):
 body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
 req=urllib.request.Request(POLYGON_RPC,data=body,headers={"Content-Type":"application/json","User-Agent":"Simulation500/1.0"})
 with urllib.request.urlopen(req,timeout=12) as response:result=json.load(response)
 if "error" in result:raise RuntimeError("Polygon RPC: "+str(result["error"])[:180])
 return result["result"]
def polygon_probe():
 """Read-only Polygon ERC-1155 transfer probe. Not equivalent to filled trades."""
 latest=int(polygon_rpc("eth_blockNumber",[]),16)
 return {"block":latest,"rpc":POLYGON_RPC,"wallet":TARGET,"note":"RPC connectivity only; transfers require verified event topics and token mapping"}

def fetch(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Simulation500-PaperCopy/1.0","Accept":"application/json"})
 try:
  with urllib.request.urlopen(req,timeout=12) as r:return json.load(r)
 except urllib.error.HTTPError as e:
  try: detail=e.read(350).decode("utf-8","replace")
  except Exception:detail=""
  raise RuntimeError("HTTP "+str(e.code)+" from "+urllib.parse.urlsplit(url).netloc+urllib.parse.urlsplit(url).path+"; "+detail[:180]) from e
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
 """Use independent EUR/USD feeds; never guess an exchange rate."""
 failures=[]
 for name,url,parser in [
  ("Frankfurter","https://api.frankfurter.dev/v1/latest?base=EUR&symbols=USD",lambda d:float(d["rates"]["USD"])),
  ("ExchangeRate API","https://open.er-api.com/v6/latest/EUR",lambda d:float(d["rates"]["USD"])),
  ("Kraken","https://api.kraken.com/0/public/Ticker?pair=EURUSD",lambda d:float(next(iter(d["result"].values()))["c"][0]))]:
  try:
   rate=parser(fetch(url))
   if 0.5<rate<2.0:return rate
   raise ValueError("Out of range")
  except Exception as exc:failures.append(name+": "+str(exc)[:80])
 raise RuntimeError("No EUR/USD provider available: "+" | ".join(failures))
def open_positions():
 """Fetch public open positions; reject unknown response shapes rather than inventing positions."""
 url="https://data-api.polymarket.com/v2/positions?"+urllib.parse.urlencode({"user":TARGET,"limit":500,"status":"OPEN"})
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
  if asset and shares>0 and 0<price<1 and value>0:
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
 try:
  return polygon_follow.execute(path)
 except Exception as exc:
  with LOCK,sqlite3.connect(path,timeout=15) as c:
   c.execute("UPDATE copy_state SET error=? WHERE id=1",(str(exc)[:250],))
   c.execute("UPDATE copy_health SET checked=?,reason=? WHERE id=1",(now,"Polygon follower error: "+str(exc)[:180]))
  return {"error":str(exc)[:180]}
def selftest():
 """Execute one deterministic paper BUY in a disposable database, never the live portfolio."""
 from unittest.mock import patch
 event={"key":"selftest:synthetic:1","block":1,"asset":"123456789","side":"BUY","price":0.5,"notional":200.0,"shares":400.0}
 with tempfile.TemporaryDirectory(prefix="simulation500-test-") as directory:
  path=os.path.join(directory,"isolated.sqlite")
  init(path)
  with patch.object(polygon_follow,"scan",return_value=([event],{"latest":1,"cursor":1,"events":1})):
   first=polygon_follow.execute(path,fx=1.1)
   second=polygon_follow.execute(path,fx=1.1)
  with sqlite3.connect(path) as c:
   cash=c.execute("SELECT cash FROM copy_state WHERE id=1").fetchone()[0]
   count=c.execute("SELECT COUNT(*) FROM copy_trades").fetchone()[0]
   positions=c.execute("SELECT COUNT(*) FROM copy_positions").fetchone()[0]
  passed=(first["copied"]==1 and second["copied"]==0 and count==1 and positions==1 and abs(cash-(500-200*.05/1.1))<.001)
  return {"passed":passed,"mode":"isolated_synthetic_test","source":"synthetic, NOT a Polymarket wallet trade","first":first,"second":second,"trade_count":count,"positions":positions,"remaining_cash_eur":round(cash,2),"live_portfolio_modified":False}
def diagnostics():
 """Independent upstream checks, safe for read-only API health endpoint."""
 out={"target":TARGET,"checked_at":int(time.time()),"paper_only":True}
 try:
  out["eur_usd"]={"ok":True,"rate":usd_per_eur()}
 except Exception as exc:
  out["eur_usd"]={"ok":False,"error":str(exc)[:450]}
 try:
  block=int(polygon_follow.rpc("eth_blockNumber",[]),16)
  out["polygon"]={"ok":True,"latest_block":block,"rpc_fallbacks":len(polygon_follow.RPC_URLS)}
 except Exception as exc:
  out["polygon"]={"ok":False,"error":str(exc)[:450]}
 return out
def report(path):
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  state=c.execute("SELECT cash,last_seen,started,error FROM copy_state WHERE id=1").fetchone()
  positions=c.execute("SELECT asset,market,shares,cost,last_price FROM copy_positions ORDER BY cost DESC").fetchall()
  trades=c.execute("SELECT t,market,side,target_price,paper_price,shares,spent,reason FROM copy_trades ORDER BY id DESC LIMIT 40").fetchall()
  health=c.execute("SELECT checked,events,latest,candidates,copied,reason FROM copy_health WHERE id=1").fetchone()
 cash,last_seen,started,error=state
 return {"target":TARGET,"cash_eur":round(cash,2),"cash_usd":None,"starting_capital_eur":500,"sizing_method":"Polygon V2 maker OrderFilled: 5% of observed fill notional, capped at 50 EUR per order; simulated spread and fee","positions":[{"asset":x[0],"market":x[1],"shares":x[2],"cost":x[3],"last_trade_price":x[4]} for x in positions],
 "trades":[{"timestamp":x[0],"market":x[1],"side":x[2],"target_price":x[3],"paper_price":x[4],"shares":x[5],"amount":x[6],"reason":x[7]} for x in trades],
 "health":{"checked_at":health[0],"public_events":health[1],"latest_public_trade":health[2],"candidates":health[3],"copied_last_poll":health[4],"reason":health[5]} if health else None,
 "last_target_trade":last_seen or None,"started":started,"error":error,"execution_enabled":False,
 "status":"not_started" if not started else ("api_error" if error else "monitoring"),
 "warning":"Polygon V2 on-chain maker fills only. Wallet must be the exchange maker; proxy or routed taker fills may be missed. 5% of source trade notional, not exact portfolio mirroring. No historical backfill beyond initial 2000 blocks. EUR/USD from Kraken; paper slippage and fees approximate. Render SQLite storage is ephemeral."}
