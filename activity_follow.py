"""Public Polymarket V2 activity follower; no trading credentials or real orders."""
import time,sqlite3,urllib.parse
import polygon_follow
TARGET="0xcE25E214D5cfE4f459cf67F08DF581885AAE7Fdc"
def scan(c):
 from copy_wallet import fetch
 c.execute("CREATE TABLE IF NOT EXISTS activity_cursor(id INTEGER PRIMARY KEY CHECK(id=1),started_at INTEGER NOT NULL)")
 now=int(time.time())
 row=c.execute("SELECT started_at FROM activity_cursor WHERE id=1").fetchone()
 # Only follow new trades, never silently replay stale fills as current trades.
 start=row[0] if row else now
 if not row:
  c.execute("INSERT INTO activity_cursor VALUES(1,?)",(start,))
 url="https://data-api.polymarket.com/v2/activity?"+urllib.parse.urlencode({"user":TARGET,"limit":200,"type":"TRADE","sort_by":"TIMESTAMP","sort_direction":"DESC","start":max(1,start-5)})
 payload=fetch(url)
 rows=payload.get("data") if isinstance(payload,dict) else payload
 if not isinstance(rows,list):
  raise ValueError("Unexpected Polymarket V2 activity response")
 events=[]
 for a in rows:
  try:
   timestamp=int(a.get("timestamp") or 0)
   if timestamp<start:continue
   side=str(a.get("side") or "").upper()
   token=str(a.get("token_id") or a.get("tokenId") or a.get("asset") or "")
   price=float(a.get("price") or 0)
   shares=float(a.get("size") or 0)
   notional=float(a.get("usdc_size") or a.get("usdcSize") or shares*price)
   tx=str(a.get("transaction_hash") or a.get("transactionHash") or "")
   if side not in ("BUY","SELL") or not token or not tx or not 0<price<1 or shares<=0 or notional<=0:continue
   # Stable per-fill fingerprint; identical fills inside same tx are aggregated by design.
   key="v2:"+tx+":"+token+":"+side+":"+str(timestamp)+":"+str(shares)+":"+str(notional)
   events.append({"key":key,"block":timestamp,"asset":token,"side":side,"price":price,"notional":notional,"shares":shares})
  except (ValueError,TypeError):continue
 events.sort(key=lambda e:(e["block"],e["key"]))
 return events,{"latest":now,"cursor":start,"events":len(events),"source":"Polymarket V2 activity"}
def execute(path):
 with sqlite3.connect(path,timeout=20) as c:
  events,meta=scan(c)
 if events:
  from copy_wallet import usd_per_eur
  fx=usd_per_eur()
 else:fx=None
 return polygon_follow.execute(path,fx=fx,preloaded=(events,meta))
