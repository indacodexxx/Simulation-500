"""Banc d'essai reproductible, sans ordre reel ni fuite de donnees futures.
Usage: python research.py --csv candles.csv
CSV: timestamp,open,high,low,close,volume (volume facultatif).
Les donnees sont chargees par l'utilisateur; aucune performance n'est inventee.
"""
import argparse,csv,json,math,statistics
from datetime import datetime

STRATEGIES=("momentum","mean_reversion","breakout","ema_trend")
def ema(values,period):
 out=values[0];k=2/(period+1)
 for x in values[1:]:out=k*x+(1-k)*out
 return out
def signal(closes,strategy):
 if len(closes)<55:return 0
 p=closes[-1];prev=closes[-21];returns=[closes[i]/closes[i-1]-1 for i in range(-20,0)]
 avg=statistics.mean(returns);sd=statistics.pstdev(returns)
 if strategy=="momentum":return int(p/prev-1>.007 and p>ema(closes[-50:],20))
 if strategy=="mean_reversion":return int(sd>0 and (returns[-1]-avg)/sd < -1.5 and p>ema(closes[-50:],50)*.97)
 if strategy=="breakout":return int(p>max(closes[-21:-1])*1.001)
 if strategy=="ema_trend":return int(ema(closes[-50:],10)>ema(closes[-50:],30) and p>ema(closes[-50:],20))
 return 0

def simulate(bars,strategy,commission,spread,slippage,start_index=55):
 # Signal calcule a la cloture de t, execution seulement a l'ouverture de t+1.
 cash=500.;qty=0.;entry=0.;peak=500.;maxdd=0.;wins=0;losses=0;trades=0;fees=0.
 cost=commission+spread/2+slippage
 for i in range(max(55,start_index),len(bars)-1):
  closes=[b["close"] for b in bars[:i+1]]
  desire=signal(closes,strategy)
  next_open=bars[i+1]["open"]
  if qty and not desire:
   proceeds=qty*next_open*(1-cost);fees+=qty*next_open*cost
   wins+=int(proceeds>entry);losses+=int(proceeds<=entry)
   cash+=proceeds;qty=0.;entry=0.;trades+=1
  elif not qty and desire:
   entry=cash;fees+=cash*cost;qty=cash*(1-cost)/next_open;cash=0.
  equity=cash+qty*bars[i+1]["close"]*(1-cost if qty else 1)
  peak=max(peak,equity);maxdd=max(maxdd,1-equity/peak)
 equity=cash+qty*bars[-1]["close"]*(1-cost if qty else 1)
 if qty:fees+=qty*bars[-1]["close"]*cost
 return {"strategy":strategy,"end_eur":round(equity,2),"return_pct":round((equity/500-1)*100,3),
 "max_drawdown_pct":round(maxdd*100,3),"round_trips":trades,"wins":wins,"losses":losses,
 "estimated_costs_eur":round(fees,2)}

def run(bars,asset_type):
 if len(bars)<300:raise ValueError("Au moins 300 bougies sont necessaires")
 costs={"crypto":(.004,.001,.0005),"equity":(.001,.0005,.0005)}
 if asset_type not in costs:raise ValueError("Type d'actif invalide")
 n=len(bars);cut=int(n*.7)
 if cut<120 or n-cut<70:raise ValueError("Historique insuffisant pour 70/30")
 c,s,l=costs[asset_type]
 train=[simulate(bars[:cut],x,c,s,l) for x in STRATEGIES]
 # Choix uniquement sur la periode d'entrainement.
 chosen=max(train,key=lambda x:(x["return_pct"],-x["max_drawdown_pct"]))["strategy"]
 # Ajout du contexte pre-test pour initialiser les indicateurs sans le trader.
 # Use prior history only for indicator warmup; start trading at test boundary.
 test=[simulate(bars,x,c,s,l,start_index=cut-1) for x in STRATEGIES]
 buyhold=500*bars[-1]["close"]/bars[cut]["open"]*(1-(c+s/2+l))**2
 return {"status":"research_only","candles":n,"split":{"train_candles":cut,"test_candles":n-cut},
 "cost_model":{"commission_per_side":c,"spread_full":s,"slippage_per_side":l},
 "train_results":train,"chosen_from_train":chosen,"test_results":test,
 "buy_and_hold_test_end_eur":round(buyhold,2),
 "warning":"Test simplifie: OHLC, pas de carnet d'ordres, execution hypothetique a l'ouverture suivante, pas de garantie de performance future. La selection sur train ne doit pas etre modifiee selon le test."}

def load(path):
 with open(path,newline="",encoding="utf-8-sig") as f:
  reader=csv.DictReader(f);required={"timestamp","open","high","low","close"}
  if not required.issubset(reader.fieldnames or []):raise ValueError("Colonnes requises: timestamp,open,high,low,close")
  rows=[]
  for r in reader:
   b={k:float(r[k]) for k in ("open","high","low","close")}
   if not all(math.isfinite(v) and v>0 for v in b.values()):continue
   if b["high"]<max(b["open"],b["close"]) or b["low"]>min(b["open"],b["close"]):continue
   b["timestamp"]=r["timestamp"];rows.append(b)
 if len({b["timestamp"] for b in rows})!=len(rows):raise ValueError("Horodatages dupliques")
 if any(rows[i]["timestamp"]>=rows[i+1]["timestamp"] for i in range(len(rows)-1)):raise ValueError("Bougies non triees")
 return rows

if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--csv",required=True);p.add_argument("--asset-type",choices=["crypto","equity"],default="crypto")
 a=p.parse_args();print(json.dumps(run(load(a.csv),a.asset_type),indent=2,ensure_ascii=False))
