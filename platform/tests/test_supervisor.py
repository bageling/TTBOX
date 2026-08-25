import unittest
from platform.runtime.controller import RuntimeController
from platform.runtime.process_adapter import MockProcessAdapter
from platform.supervisor.systemd_adapter import MockServiceAdapter
from platform.supervisor.supervisor import Supervisor
from platform.health.monitor import HealthMonitor
class SupervisorTests(unittest.TestCase):
 def setUp(self):
  self.r=RuntimeController(MockProcessAdapter()); self.svc=MockServiceAdapter(('ttbox-core','ttbox-hid')); self.h=HealthMonitor(self.r,self.svc,('ttbox-core','ttbox-hid')); self.s=Supervisor(self.r,self.svc,self.h,'ttbox-core',('ttbox-hid',))
 def test_start_status_health(self):
  x=self.s.start(); self.assertEqual(x.runtime.state,'RUNNING'); self.assertTrue(x.health.ok); self.assertTrue(x.services['ttbox-core'].active)
 def test_stop(self): self.s.start(); x=self.s.stop(); self.assertEqual(x.runtime.state,'STOPPED'); self.assertFalse(x.services['ttbox-core'].active)
 def test_restart(self): self.s.start(); x=self.s.restart(); self.assertEqual(x.runtime.state,'RUNNING'); self.assertIn(('restart','ttbox-core'),self.svc.calls)
 def test_recovery(self): self.s.start(); self.r._state=__import__('platform.runtime.lifecycle',fromlist=['RuntimeState']).RuntimeState.FAILED; x=self.s.recover(); self.assertTrue(x.recovered); self.assertEqual(x.runtime.state,'RUNNING')
 def test_health_failure(self): x=self.s.status(); self.assertFalse(x.health.ok)
if __name__=='__main__': unittest.main()
