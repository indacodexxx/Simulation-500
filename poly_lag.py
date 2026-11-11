"""Exploratory Polymarket quote-lag model, independent of real orders."""
import sqlite3,threading
LOCK=threading.Lock()
def init(path):
 with sqlite3.connect(path,timeout=15) as c:
  c.execute("CREATE TABLE IF NOT EXISTS poly_lag_quotes (id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER,market_id TEXT,btc_spot REAL,outcome TEXT,ask REAL,bid REAL,ask_size REAL)")
  c.execute("CREATE INDEX IF NOT EXISTS poly_lag_idx ON poly_lag_quotes(market_id,outcome,t)")
def btc_spot(get):
 data=get("https://api.kraken.com/0/public/Ticker?pair=XBTUSD")
 if data.get("error"):raise ValueError(str(data["error"]))
 return float(next(iter(data["result"].values()))["c"][0])
def collect(path,market_result,get):
 if not market_result.get("markets"):return {"recorded":0,"reason":"no markets"}
 try:spot=btc_spot(get)
 except Exception as e:return {"recorded":0,"reason":"spot unavailable: "+str(e)[:120]}
 n=0
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  for m in market_result["markets"]:
   for q in m.get("quotes",[]):
    if q.get("ask") is None or q.get("bid") is None:continue
    c.execute("INSERT INTO poly_lag_quotes(t,market_id,btc_spot,outcome,ask,bid,ask_size) VALUES(?,?,?,?,?,?,?)",
     (market_result["timestamp"],m["id"],spot,q["outcome"],q["ask"],q["bid"],q.get("ask_size",0)))
    n+=1
  c.execute("DELETE FROM poly_lag_quotes WHERE t<?",(market_result["timestamp"]-30*86400,))
 return {"recorded":n,"btc_spot_usd":spot}
def analyze(rows,horizon=300):
 groups={}
 for row in rows:groups.setdefault((row[1],row[3]),[]).append(row)
 signals=[];evaluated=[]
 for (market,outcome),items in groups.items():
  direction=1 if outcome.lower() in ("up","yes") else (-1 if outcome.lower() in ("down","no") else 0)
  if not direction:continue
  for i in range(1,len(items)):
   t,_,spot,_,ask,bid,size=items[i]
   prev=items[i-1];delta=t-prev[0]
   if not 120<=delta<=360 or prev[2]<=0:continue
   move=spot/prev[2]-1
   if direction*move<.0015 or abs(ask-prev[4])>=.005 or not .02<ask<.98 or size<10:continue
   future=next((q for q in items[i+1:] if t+horizon<=q[0]<=t+horizon+120),None)
   signal={"market_id":market,"outcome":outcome,"timestamp":t,"btc_move_pct":round(move*100,4),
    "entry_ask":ask,"status":"pending"}
   if future:
    # illustrative 1-cent round trip cost per contract, not verified exchange fee
    ret=(future[5]-ask-.01)/ask*100
    signal["net_return_pct"]=round(ret,4);signal["status"]="evaluated";evaluated.append(ret)
   signals.append(signal)
 return {"signals":len(signals),"evaluated":len(evaluated),
 "mean_net_return_pct":round(sum(evaluated)/len(evaluated),4) if evaluated else None,
 "win_rate_pct":round(100*sum(x>0 for x in evaluated)/len(evaluated),2) if evaluated else None,
 "recent":signals[-20:],"horizon_seconds":horizon,
 "warning":"Exploratory lag hypothesis; no real orders, no verified fills, no settlement model. Correlated signals. 1 cent illustrative cost is not a verified fee."}
def report(path):
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  rows=c.execute("SELECT t,market_id,btc_spot,outcome,ask,bid,ask_size FROM poly_lag_quotes ORDER BY market_id,outcome,t").fetchall()
 return {"observations":len(rows),"tests":{"300":analyze(rows,300),"900":analyze(rows,900)},
 "execution_enabled":False,"status":"research_only"}
