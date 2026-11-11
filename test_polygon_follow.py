import unittest,sqlite3,tempfile,os
from unittest.mock import patch
import polygon_follow as p
class PolygonFollowerTests(unittest.TestCase):
 def test_decodes_real_v2_abi_and_maker_topic(self):
  wallet="0x"+("0"*24)+p.WALLET[2:]
  data=[0,12345,2000000,4000000,0,0,0]
  log={"topics":[p.ORDER_FILLED,"0x"+"1"*64,wallet,"0x"+"2"*64],"data":"0x"+"".join(format(x,"064x") for x in data),"transactionHash":"0xabc","logIndex":"0x1","blockNumber":"0x10"}
  def fake_rpc(method,args):
   if method=="eth_blockNumber":return "0x2000"
   if method=="eth_getLogs":return [log] if args[0]["address"]==p.EXCHANGES[0] else []
   raise AssertionError(method)
  with tempfile.TemporaryDirectory() as d:
   with sqlite3.connect(os.path.join(d,"test.sqlite")) as c:
    with patch.object(p,"rpc",side_effect=fake_rpc):
     events,meta=p.scan(c)
     self.assertEqual(len(events),1)
     self.assertEqual(events[0]["side"],"BUY")
     self.assertEqual(events[0]["price"],.5)
     self.assertEqual(events[0]["notional"],2)
     self.assertEqual(events[0]["asset"],"12345")
     self.assertEqual(meta["events"],1)
 def test_rpc_fallback(self):
  class Resp:
   def __enter__(self):return self
   def __exit__(self,*a):return False
   def read(self,*a):return b'{"jsonrpc":"2.0","result":"0x2","id":1}'
  calls=[]
  def urlopen(req,timeout):
   calls.append(req.full_url)
   if len(calls)==1:raise PermissionError("403")
   return Resp()
  with patch.object(p.urllib.request,"urlopen",side_effect=urlopen):
   self.assertEqual(p.rpc("eth_blockNumber",[]),"0x2")
   self.assertGreaterEqual(len(calls),2)
if __name__=="__main__":unittest.main()
