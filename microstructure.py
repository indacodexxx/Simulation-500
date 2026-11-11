"""Forward-only Kraken order-book observation and cost-aware signal evaluation.
No real or paper orders. Uses the next recorded quote >= horizon as exit.
"""
import concurrent.futures,json,sqlite3,threading,time,urllib.parse,urllib.request
PAIRS={"BTC-EUR":"XXBTZEUR","ETH-EUR":"XETHZEUR","SOL-EUR":"SOLEUR"}
TAKER_FEE_PER_SIDE=.004
SLIPPAGE_BPS_PER_SIDE=5
HORIZONS=(300,900)
_lock=threading.Lock()
def _fetch(pair):
 url="https://api.kraken.com/0/public/Depth?"+urllib.parse.urlencode({"pair":pair,"count":25})
 req=urllib.request.Request(url,headers={"User-Agent":"Simulation500-Research/1.0"})
 with urllib.request.urlopen(req,timeout=12) as response:data=json.load(response)
 if data.get("error"):raise ValueError(str(data["error"]))
 book=next(iter(data["result"].values()))
 bids=[(float(x[0]),float(x[1])) for x in book["bids"]]
 asks=[(float(x[0]),float(x[1])) for x in book["asks"]]
 if not bids or not asks:raise ValueError("Empty order book")
 bid,ask=bids[0][0],asks[0][0]
 if bid<=0 or ask<=bid:raise ValueError("Invalid or crossed book")
 mid=(bid+ask)/2
 bd=sum(p*q for p,q in bids[:10]);ad=sum(p*q for p,q in asks[:10])
 return {"bid_eur":bid,"ask_eur":ask,"mid_eur":mid,
 "spread_bps":(ask-bid)/mid*10000,"bid_depth_10_eur":bd,
 "ask_depth_10_eur":ad,"depth_imbalance":(bd-ad)/(bd+ad) if bd+ad else 0}
def init(db_path):
 with sqlite3.connect(db_path,timeout=15) as c:
  c.execute("""CREATE TABLE IF NOT EXISTS book_observations
  (id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER NOT NULL,asset TEXT NOT NULL,
  bid REAL NOT NULL,ask REAL NOT NULL,imbalance REAL NOT NULL,
  bid_depth REAL NOT NULL,ask_depth REAL NOT NULL)""")
  c.execute("CREATE INDEX IF NOT EXISTS idx_book_asset_time ON book_observations(asset,t)")
def collect(db_path):
 now=int(time.time());results={};errors={}
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  futures={pool.submit(_fetch,pair):name for name,pair in PAIRS.items()}
  for future in concurrent.futures.as_completed(futures):
   name=futures[future]
   try:results[name]=future.result()
   except Exception as exc:errors[name]=str(exc)[:180]
 if results:
  with _lock,sqlite3.connect(db_path,timeout=15) as c:
   for name,x in results.items():
    c.execute("INSERT INTO book_observations(t,asset,bid,ask,imbalance,bid_depth,ask_depth) VALUES(?,?,?,?,?,?,?)",
     (now,name,x["bid_eur"],x["ask_eur"],x["depth_imbalance"],x["bid_depth_10_eur"],x["ask_depth_10_eur"]))
   c.execute("DELETE FROM book_observations WHERE t<?",(now-30*86400,))
 return {"timestamp":now,"markets":results,"errors":errors,"execution_enabled":False}
def _evaluate(rows,horizon,threshold=.2):
 """Signal at t; next observation at/after t+horizon used for exit.
 Ignore exits with excessive gaps (service was asleep).
 """
 outcomes=[]
 for i,row in enumerate(rows):
  t,bid,ask,imb=row
  if imb<threshold:continue
  target=t+horizon
  exit_row=next((r for r in rows[i+1:] if target<=r[0]<=target+120),None)
  if exit_row is None:continue
  exit_bid=exit_row[1]
  # Taker entry at ask, taker exit at bid, two fees, slippage per side.
  net=(exit_bid*(1-TAKER_FEE_PER_SIDE-SLIPPAGE_BPS_PER_SIDE/10000)/
       (ask*(1+TAKER_FEE_PER_SIDE+SLIPPAGE_BPS_PER_SIDE/10000))-1)
  outcomes.append(net)
 return {"samples":len(outcomes),"win_rate_pct":round(100*sum(x>0 for x in outcomes)/len(outcomes),2) if outcomes else None,
 "mean_net_return_pct":round(100*sum(outcomes)/len(outcomes),4) if outcomes else None,
 "total_non_compounded_pct":round(100*sum(outcomes),4) if outcomes else None,
 "status":"observed_not_validated" if len(outcomes)>=100 else "insufficient_samples"}
def report(db_path):
 with _lock,sqlite3.connect(db_path,timeout=15) as c:
  c.row_factory=sqlite3.Row
  rows=c.execute("SELECT t,asset,bid,ask,imbalance,bid_depth,ask_depth FROM book_observations ORDER BY asset,t").fetchall()
 grouped={a:[] for a in PAIRS}
 for r in rows:grouped[r["asset"]].append((r["t"],r["bid"],r["ask"],r["imbalance"]))
 results={}
 for asset,items in grouped.items():
  latest=items[-1] if items else None
  results[asset]={"observations":len(items),"last_observation":latest[0] if latest else None,
   "latest_imbalance":round(latest[3],4) if latest else None,
   "forward_tests":{str(h):_evaluate(items,h) for h in HORIZONS}}
 return {"source":"Kraken public order book, collected prospectively","markets":results,
 "fees":{"taker_per_side":TAKER_FEE_PER_SIDE,"slippage_bps_per_side":SLIPPAGE_BPS_PER_SIDE},
 "execution_enabled":False,"storage":"SQLite; persistence depends on Render DB_PATH configuration",
 "warning":"Not an executable strategy or profit guarantee. Overlapping observations are correlated; results are not independent trades. No queue or latency model. A positive result requires further out-of-sample tests."}
def snapshot():
 results={};errors={}
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
  futures={pool.submit(_fetch,pair):name for name,pair in PAIRS.items()}
  for future in concurrent.futures.as_completed(futures):
   name=futures[future]
   try:results[name]=future.result()
   except Exception as exc:errors[name]=str(exc)[:180]
 return {"timestamp":int(time.time()),"source":"Kraken public level-2 snapshots",
 "markets":results,"errors":errors,"execution_enabled":False,
 "warning":"Single snapshots are not profit signals. Spread and fees are costs, not guaranteed market-maker profit."}
