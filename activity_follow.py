"""Public Polymarket V2 activity follower; no trading credentials or real orders."""
import time,sqlite3,urllib.parse,os
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
 historical=not backfilled and os.getenv('COPY_HISTORICAL_BACKFILL','0')=='1'
 # Default to live-only on fresh/ephemeral databases: do not replay yesterday's trades on deploy.
 start=max(1,now-86400) if historical else (row[0] if row else now)
 if not row:
  c.execute("INSERT INTO activity_cursor VALUES(1,?)",(now,))
 rows=[]
 # Polymarket V2 pagination uses next_cursor, never offset.
 cursor=None
 for page_number in range(50):
  params={"user":TARGET,"limit":200,"type":"TRADE","sort_by":"TIMESTAMP","sort_direction":"DESC"}
  if cursor:params["cursor"]=cursor
  payload=fetch("https://data-api.polymarket.com/v2/activity?"+urllib.parse.urlencode(params))
  page=payload.get("data") if isinstance(payload,dict) else payload
  if not isinstance(page,list):raise ValueError("Unexpected Polymarket V2 activity response")
  rows.extend(page)
  if not page:break
  if min((int(a.get("timestamp") or 0) for a in page if isinstance(a,dict)),default=0)<start:break
  next_cursor=payload.get("next_cursor") if isinstance(payload,dict) else None
  if not next_cursor:break
  if next_cursor==cursor:raise RuntimeError("Polymarket activity cursor did not advance")
  cursor=next_cursor
 else:
  raise RuntimeError("Wallet activity exceeded 50 pages; refusing to silently miss trades")
 events=[]
 filtered={'before_cursor':0,'invalid_fields':0,'invalid_timestamp':0}
 for a in rows:
  try:
   timestamp=int(a.get("timestamp") or 0)
   if timestamp<start:
    filtered['before_cursor']+=1
    continue
   side=str(a.get("side") or "").upper()
   token=str(a.get("token_id") or a.get("tokenId") or a.get("asset") or "")
   price=float(a.get("price") or 0)
   shares=float(a.get("size") or 0)
   notional=float(a.get("usdc_size") or a.get("usdcSize") or shares*price)
   tx=str(a.get("transaction_hash") or a.get("transactionHash") or "")
   if side not in ("BUY","SELL") or not token or not tx or not 0<price<1 or shares<=0 or notional<=0:
    filtered["invalid_fields"]+=1
    continue
   # Stable per-fill fingerprint; identical fills inside same tx are aggregated by design.
   key="v2:"+tx+":"+token+":"+side+":"+str(timestamp)+":"+str(shares)+":"+str(notional)
   events.append({"key":key,"block":timestamp,"asset":token,"side":side,"price":price,"notional":notional,"shares":shares})
  except (ValueError,TypeError):filtered['invalid_timestamp']+=1
 events.sort(key=lambda e:(e["block"],e["key"]))
 return events,{"latest":max((e["block"] for e in events),default=0),"cursor":start,"events":len(events),"raw_events":len(rows),"filtered":filtered,"source":"Polymarket V2 historical backfill (24h)" if historical else "Polymarket V2 live activity","historical":historical}
def execute(path):
 with sqlite3.connect(path,timeout=20) as c:
  events,meta=scan(c)
 if events:
  from copy_wallet import usd_per_eur
  fx=usd_per_eur()
 else:fx=None
 result=polygon_follow.execute(path,fx=fx,preloaded=(events,meta))
 with sqlite3.connect(path,timeout=20) as c:
  if meta["historical"]:
   c.execute("INSERT OR REPLACE INTO activity_backfill VALUES(1,1)")
  # Advance only after successful processing; overlap five seconds for late fills.
  c.execute("UPDATE activity_cursor SET started_at=? WHERE id=1",(max(meta["cursor"],int(time.time())-5),))
 return result
