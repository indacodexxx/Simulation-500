import unittest
import microstructure
class MicrostructureTests(unittest.TestCase):
 def test_no_future_quote_means_no_result(self):
  rows=[(100,99.,101.,.8),(200,100.,102.,.5)]
  result=microstructure._evaluate(rows,300)
  self.assertEqual(result["samples"],0)
 def test_costs_are_charged(self):
  rows=[(100,99.,101.,.8),(400,99.,101.,0)]
  result=microstructure._evaluate(rows,300)
  self.assertEqual(result["samples"],1)
  self.assertLess(result["mean_net_return_pct"],0)
 def test_positive_imbalance_required(self):
  rows=[(100,99.,101.,-.9),(400,110.,111.,0)]
  self.assertEqual(microstructure._evaluate(rows,300)["samples"],0)
if __name__=="__main__":unittest.main()
