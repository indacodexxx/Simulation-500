import unittest
import research

def candles(n=400,step=.002):
 rows=[]
 for i in range(n):
  price=100*(1+step)**i
  rows.append({"timestamp":i,"open":price,"high":price*1.001,"low":price*.999,"close":price})
 return rows

class ResearchTests(unittest.TestCase):
 def test_flat_market_does_not_invent_returns(self):
  bars=candles(step=0)
  result=research.run(bars,"crypto")
  self.assertEqual(result["split"]["train_candles"],280)
  self.assertEqual(result["split"]["test_candles"],120)
  for row in result["test_results"]:
   self.assertLessEqual(row["end_eur"],500.01)

 def test_fee_drag_is_nonnegative(self):
  bars=candles(step=.002)
  gross=research.simulate(bars,"ema_trend",0,0,0)
  costly=research.simulate(bars,"ema_trend",.004,.001,.0005)
  self.assertLessEqual(costly["end_eur"],gross["end_eur"]+0.01)

 def test_test_period_is_untouched_by_training_selection(self):
  bars=candles(step=.001)
  result=research.run(bars,"equity")
  self.assertIn(result["chosen_from_train"],research.STRATEGIES)
  self.assertEqual(len(result["test_results"]),len(research.STRATEGIES))

 def test_short_history_rejected(self):
  with self.assertRaises(ValueError):
   research.run(candles(250),"crypto")

if __name__=="__main__":unittest.main()
