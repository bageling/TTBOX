import unittest
from types import SimpleNamespace
from platform.supervisor.systemd_adapter import SystemdServiceAdapter
class SystemdAdapterTests(unittest.TestCase):
 def test_status_parsing_and_argv(self):
  calls=[]
  def run(argv,**kw):
   calls.append((argv,kw)); return SimpleNamespace(returncode=0,stdout="LoadState=loaded\nActiveState=active\nSubState=running\nMainPID=42\nResult=success\n",stderr="")
  s=SystemdServiceAdapter(runner=run).status("ttbox-core")
  self.assertTrue(s.active); self.assertEqual(s.main_pid,42); self.assertEqual(calls[0][0],['systemctl','show','ttbox-core','--no-page','--property=LoadState,ActiveState,SubState,MainPID,Result']); self.assertNotIn('shell',calls[0][1])
if __name__=='__main__': unittest.main()
