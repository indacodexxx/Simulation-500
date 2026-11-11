"""Read-only Polygon V2 exchange fill follower. No real trading."""
import json,urllib.request,os,time,sqlite3
RPC=os.getenv("POLYGON_RPC","https://polygon-bor-rpc.publicnode.com")
RPC_URLS=list(dict.fromkeys([u.strip() for u in (os.getenv("POLYGON_RPC_URLS","").split(",")+[RPC,"https://polygon-rpc.com","https://polygon.drpc.org","https://polygon.llamarpc.com"]) if u.strip()]))
WALLET="0xcE25E214D5cfE4f459cf67F08DF581885AAE7Fdc".lower()
ORDER_FILLED="0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee"
EXCHANGES=["0xE111180000d2663C0091e4f400237545B87B996B","0xe2222d279d744050d28e00520010520000310F59"]
def rpc(method,params):
 data=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
 failures=[]
 for url in RPC_URLS:
  try:
   req=urllib.request.Request(url,data=data,headers={"Content-Type":"application/json","Accept":"application/json","User-Agent":"Simulation500-Paper/1.0"})
   with urllib.request.urlopen(req,timeout=8) as r:out=json.load(r)
   if out.get("error"):raise RuntimeError(str(out["error"])[:120])
   if "result" not in out:raise ValueError("Missing JSON-RPC result")
   return out["result"]
  except Exception as exc:
   failures.append(url.split("/")[2]+": "+str(exc)[:90])
 raise RuntimeError("All Polygon RPC providers failed: "+" | ".join(failures))
def scan(c,limit=250):
 c.execute("CREATE TABLE IF NOT EXISTS polygon_cursor(id INTEGER PRIMARY KEY,block INTEGER)")
 latest=int(rpc("eth_blockNumber",[]),16)
 row=c.execute("SELECT block FROM polygon_cursor WHERE id=1").fetchone()
 start=(row[0]+1) if row else max(0,latest-5000)
 end=min(latest-8,start+limit-1)
 if end<start:return [],{"latest":latest,"cursor":start-1,"events":0}
 topic="0x"+("0"*24)+WALLET[2:]
 events=[]
 for contract in EXCHANGES:
  logs=rpc("eth_getLogs",[{"address":contract,"fromBlock":hex(start),"toBlock":hex(end),"topics":[ORDER_FILLED,None,topic]}])
  for log in logs:
   ts=log.get("topics") or []
   if len(ts)!=4 or ts[0].lower()!=ORDER_FILLED or ts[2].lower()!=topic:continue
   if len(log.get("data","")[2:])!=7*64:continue
   words=[int(log["data"][2+i*64:2+(i+1)*64],16) for i in range(7)]
   side,token,maker_amt,taker_amt,fee=words[:5]
   if side not in (0,1) or token==0 or maker_amt<=0 or taker_amt<=0:continue
   collateral=(maker_amt if side==0 else taker_amt)/1e6
   shares=(taker_amt if side==0 else maker_amt)/1e6
   if not (collateral>0 and shares>0):continue
   price=collateral/shares
   if not 0<price<1:continue
   events.append({"key":log["transactionHash"]+":"+hex(int(log["logIndex"],16)),"block":int(log["blockNumber"],16),"asset":str(token),"side":"BUY" if side==0 else "SELL","price":price,"notional":collateral,"shares":shares})
 events.sort(key=lambda x:(x["block"],x["key"]))
 c.execute("INSERT INTO polygon_cursor(id,block) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET block=excluded.block",(end,))
 return events,{"latest":latest,"cursor":end,"events":len(events)}
def execute(path,fx=None,preloaded=None):
 now=int(time.time())
 with sqlite3.connect(path,timeout=20) as c:
  events,meta=preloaded if preloaded is not None else scan(c)
  if events and fx is None:
   from copy_wallet import usd_per_eur
   fx=usd_per_eur()
  cash,_,started=c.execute("SELECT cash,last_seen,started FROM copy_state WHERE id=1").fetchone()
  c.execute("CREATE TABLE IF NOT EXISTS copy_skipped (trade_key TEXT PRIMARY KEY, t INTEGER, asset TEXT, side TEXT, reason TEXT)")
  count=0
  skipped=0
  for ev in events:
   if c.execute("SELECT 1 FROM copy_trades WHERE trade_key=?",(ev["key"],)).fetchone():continue
   asset=ev["asset"];market="Polymarket token "+asset[:20]
   pos=c.execute("SELECT shares,cost,last_price FROM copy_positions WHERE asset=?",(asset,)).fetchone()
   px=ev["price"]
   # Fixed 5% of observed source fill, capped at 10% initial paper bankroll.
   amount=min(cash,50.0,ev["notional"]*.05/fx)
   if ev["side"]=="BUY":
    if amount<0.10:
     c.execute("INSERT OR IGNORE INTO copy_skipped VALUES(?,?,?,?,?)",(ev["key"],now,asset,"BUY","Source trade below EUR 0.10 paper minimum"))
     skipped+=1
     continue
    fill=min(.99,px+.02);qty=amount*fx/(fill+.01)
    if pos:
     c.execute("UPDATE copy_positions SET shares=?,cost=?,last_price=? WHERE asset=?",(pos[0]+qty,pos[1]+amount,fill,asset))
    else:c.execute("INSERT INTO copy_positions VALUES(?,?,?,?,?)",(asset,market,qty,amount,fill))
    cash-=amount
   else:
    if not pos:
     c.execute("INSERT OR IGNORE INTO copy_skipped VALUES(?,?,?,?,?)",(ev["key"],now,asset,"SELL","No matching paper position; cannot sell shares never bought"))
     skipped+=1
     continue
    # Proportional sale; never sell more shares than owned.
    qty=min(pos[0],ev["shares"]*.05)
    if qty<=0:
     c.execute("INSERT OR IGNORE INTO copy_skipped VALUES(?,?,?,?,?)",(ev["key"],now,asset,"SELL","No sellable paper shares"))
     skipped+=1
     continue
    fill=max(.001,px-.02)
    amount=max(0,qty*max(.001,fill-.01)/fx)
    cash+=amount
    if pos[0]-qty<1e-8:c.execute("DELETE FROM copy_positions WHERE asset=?",(asset,))
    else:c.execute("UPDATE copy_positions SET shares=?,cost=?,last_price=? WHERE asset=?",(pos[0]-qty,pos[1]*(1-qty/pos[0]),fill,asset))
   c.execute("INSERT INTO copy_trades(trade_key,t,market,asset,side,target_price,paper_price,shares,spent,reason) VALUES(?,?,?,?,?,?,?,?,?,?)",(ev["key"],now,market,asset,ev["side"],px,fill,qty,amount,("Historical source trade (24h backfill); simulated later" if meta.get("historical") else "Live observed source trade; paper execution")))
   count+=1
  c.execute("UPDATE copy_state SET cash=?,started=?,last_seen=?,error=NULL WHERE id=1",(cash,started or now,now))
  c.execute("UPDATE copy_health SET checked=?,events=?,latest=?,candidates=?,copied=?,reason=? WHERE id=1",(now,meta["events"],meta["latest"],len(events),count,str(meta.get("source","Polygon V2"))+" checked; "+str(count)+" new paper orders; "+str(skipped)+" skipped"))
 return {"status":"monitoring","copied":count,"polygon":meta,"cash_eur":round(cash,2)}
