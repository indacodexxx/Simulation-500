"""Public Polymarket V2 activity follower; no trading credentials or real orders."""
import time,sqlite3,urllib.parse
import polygon_follow
TARGET="0xcE25E214D5cfE4f459cf67F08DF581885AAE7Fdc"
def scan(c):
 from copy_wallet import fetch
 c.execute("CREATE TABLE IF NOT EXISTS activity_cursor(id INTEGER PRIMARY KEY CHECK(id=1),started_at INTEGER NOT NULL)")
 now=int(time.time())
 row=c.execute("SELECT started_at FROM activity_cursor WHERE id=1").fetchone()
 # One-time, explicitly labeled 24-hour historical paper backfill; deduplicated by trade_key.
 c.execute("CREATE TABLE IF NOT EXISTS activity_backfill(id INTEGER PRIMARY KEY CHECK(id=1),completed INTEGER NOT NULL)")
 backfilled=c.execute("SELECT completed FROM activity_backfill WHERE id=1").fetchone()
 historical=not backfilled
 start=max(1,now-86400) if historical else (row[0] if row else now)
 if not row:
  c.execute("INSERT INTO activity_cursor VALUES(1,?)",(now,))
 rows=[]
 # Fetch all recent fills, not just the first 200. Never silently truncate busy wallets.
 for offset in range(0,2000,200):
  url="https://data-api.polymarket.com/v2/activity?"+urllib.parse.urlencode({"user":TARGET,"limit":200,"offset":offset,"type":"TRADE","sort_by":"TIMESTAMP","sort_direction":"DESC"})
  payload=fetch(url)
  page=payload.get("data") if isinstance(payload,dict) else payload
  if not isinstance(page,list):raise ValueError("Unexpected Polymarket V2 activity response")
  rows.extend(page)
  if len(page)<200:break
  if min((int(a.get("timestamp") or 0) for a in page if isinstance(a,dict)),default=0)<start:break
 else:
  raise RuntimeError("Wallet activity exceeded 2000 fills; refusing to silently miss trades")
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
 return events,{"latest":max((e["block"] for e in events),default=0),"cursor":start,"events":len(events),"source":"Polymarket V2 historical backfill (24h)" if historical else "Polymarket V2 live activity","historical":historical}
def execute(path):
 with sqlite3.connect(path,timeout=20) as c:
  events,meta=scan(c)
 if events:
  from copy_wallet import usd_per_eur
  fx=usd_per_eur()
 else:fx=None
 result=polygon_follow.execute(path,fx=fx,preloaded=(events,meta))
 if meta["historical"]:
  with sqlite3.connect(path,timeout=20) as c:
   c.execute("INSERT OR REPLACE INTO activity_backfill VALUES(1,1)")
 return result
