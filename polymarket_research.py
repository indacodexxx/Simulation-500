"""Independent Polymarket BTC Up/Down price-discovery experiment.
Public read-only endpoints. No wallet, keys, orders or claimed profitability.
"""
import json,urllib.request,urllib.parse,time,sqlite3,threading,math
LOCK=threading.Lock()
def get(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Simulation500-Research/1.0","Accept":"application/json"})
 with urllib.request.urlopen(req,timeout=9) as r:return json.load(r)
def unpack(x):
 if isinstance(x,str):
  try:return json.loads(x)
  except (ValueError,TypeError):return []
 return x or []
def discover():
 # Gamma market listing is not a guarantee of exhaustive discovery.
 data=get("https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=500&order=id&ascending=false")
 found=[]
 for m in data:
  title=(m.get("question") or "").lower()
  slug=(m.get("slug") or "").lower()
  if "bitcoin" not in title and "btc" not in title and "btc" not in slug:continue
  if not any(x in title+" "+slug for x in ("up or down","up-down","up/down")):continue
  outcomes=unpack(m.get("outcomes"));tokens=unpack(m.get("clobTokenIds"))
  if len(outcomes)!=2 or len(tokens)!=2:continue
  found.append({"id":str(m.get("id")),"question":m.get("question"),"slug":m.get("slug"),
   "end_date":m.get("endDate"),"outcomes":outcomes,"tokens":tokens})
 return found[:10]
def best_ask(token):
 book=get("https://clob.polymarket.com/book?"+urllib.parse.urlencode({"token_id":token}))
 asks=[(float(x["price"]),float(x["size"])) for x in book.get("asks",[]) if float(x["size"])>0]
 bids=[(float(x["price"]),float(x["size"])) for x in book.get("bids",[]) if float(x["size"])>0]
 return {"ask":min(asks)[0] if asks else None,"bid":max(bids)[0] if bids else None,"ask_size":min(asks)[1] if asks else 0}
def observe():
 now=int(time.time());markets=[];errors=[]
 try:found=discover()
 except Exception as e:return {"timestamp":now,"markets":[],"errors":["Gamma: "+str(e)[:160]],"status":"discovery_failed"}
 for m in found[:5]:
  try:
   quotes=[best_ask(token) for token in m["tokens"]]
   m["quotes"]=[{"outcome":name,**q} for name,q in zip(m["outcomes"],quotes)]
   if all(q["ask"] is not None for q in quotes):
    m["combined_ask"]=round(sum(q["ask"] for q in quotes),6)
    m["combined_ask_minus_one"]=round(sum(q["ask"] for q in quotes)-1,6)
   markets.append(m)
  except Exception as e:errors.append(m["id"]+": "+str(e)[:160])
 return {"timestamp":now,"markets":markets,"errors":errors,"status":"observed",
 "execution_enabled":False,"warning":"Combined ask < 1 is not executable profit: token fees, size, execution simultaneity, settlement, and market rules are not verified. This is not the alleged bot strategy."}
def init(path):
 with sqlite3.connect(path,timeout=15) as c:
  c.execute("""CREATE TABLE IF NOT EXISTS polymarket_observations
  (id INTEGER PRIMARY KEY AUTOINCREMENT,t INTEGER NOT NULL,market_id TEXT NOT NULL,
   question TEXT,yes_ask REAL,no_ask REAL,combined_ask REAL)""")
  c.execute("CREATE INDEX IF NOT EXISTS idx_poly_time ON polymarket_observations(t)")
def collect(path):
 result=observe()
 if result["markets"]:
  with LOCK,sqlite3.connect(path,timeout=15) as c:
   for m in result["markets"]:
    q=m.get("quotes",[])
    if len(q)!=2:continue
    a,b=q[0].get("ask"),q[1].get("ask")
    if a is None or b is None:continue
    c.execute("INSERT INTO polymarket_observations(t,market_id,question,yes_ask,no_ask,combined_ask) VALUES(?,?,?,?,?,?)",
     (result["timestamp"],m["id"],m["question"],a,b,a+b))
   c.execute("DELETE FROM polymarket_observations WHERE t<?",(int(time.time())-30*86400,))
 return result
def report(path):
 with LOCK,sqlite3.connect(path,timeout=15) as c:
  rows=c.execute("SELECT market_id,COUNT(*),MIN(combined_ask),AVG(combined_ask),MAX(t) FROM polymarket_observations GROUP BY market_id ORDER BY MAX(t) DESC LIMIT 15").fetchall()
 return {"markets":[{"id":r[0],"observations":r[1],"lowest_combined_ask":round(r[2],5),
  "mean_combined_ask":round(r[3],5),"last_observed":r[4]} for r in rows],
 "execution_enabled":False,"status":"research_only",
 "warning":"No trades, no realized returns, no fair-value or stale-quote model yet. Historical observations can disappear on Render ephemeral storage."}
