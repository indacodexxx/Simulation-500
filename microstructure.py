"""Microstructure research: public Kraken order books, no execution or profitability claims."""
import math,time,urllib.request,urllib.parse,json,concurrent.futures
PAIRS={"BTC-EUR":"XXBTZEUR","ETH-EUR":"XETHZEUR","SOL-EUR":"SOLEUR"}
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
 bid_eur=sum(p*q for p,q in bids[:10])
 ask_eur=sum(p*q for p,q in asks[:10])
 imbalance=(bid_eur-ask_eur)/(bid_eur+ask_eur) if bid_eur+ask_eur else 0
 return {"bid_eur":round(bid,6),"ask_eur":round(ask,6),"mid_eur":round(mid,6),
 "spread_bps":round((ask-bid)/mid*10000,2),"bid_depth_10_eur":round(bid_eur,2),
 "ask_depth_10_eur":round(ask_eur,2),"depth_imbalance":round(imbalance,4),
 "round_trip_spread_cost_bps":round(2*(ask-bid)/mid*10000,2),
 "status":"observation_only"}
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
 "warning":"A snapshot cannot establish profit. Fees, slippage, queue position, latency, adverse selection and inventory risk are not modeled. Spread is a cost for taker round trips, not guaranteed maker profit."}
