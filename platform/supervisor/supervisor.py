"""统一 Supervisor：服务编排、Runtime 接管和 crash recovery。"""
from __future__ import annotations
from dataclasses import dataclass
import time
from .systemd_adapter import ServiceAdapter, ServiceStatus
from ..runtime.controller import RuntimeController, RuntimeSnapshot
from ..health.monitor import HealthMonitor, UnifiedHealth

@dataclass(frozen=True)
class SupervisorStatus:
    runtime: RuntimeSnapshot; services: dict[str,ServiceStatus]; health: UnifiedHealth; recovered: bool; timestamp: float

class Supervisor:
    def __init__(self, runtime: RuntimeController, services: ServiceAdapter, health: HealthMonitor, core_service='ttbox-core', auxiliary=()):
        self.runtime=runtime; self.services=services; self.health=health; self.core_service=core_service; self.auxiliary=tuple(auxiliary); self._recovered=False
    def start(self):
        for name in self.auxiliary: self.services.start(name)
        snap=self.runtime.start()
        return self.status()
    def stop(self):
        self.runtime.stop()
        for name in reversed(self.auxiliary): self.services.stop(name)
        return self.status()
    def restart(self):
        self.runtime.stop()
        for name in reversed(self.auxiliary): self.services.stop(name)
        self.runtime.start()
        for name in self.auxiliary: self.services.start(name)
        return self.status()
    def recover(self):
        self._recovered=False
        if self.runtime.state.value not in ('FAILED','RUNNING'): return self.status()
        self.runtime.stop(); self.runtime.start(); self._recovered=True
        return self.status()
    def status(self):
        service_states={n:self.services.status(n) for n in (self.core_service,*self.auxiliary)}
        return SupervisorStatus(self.runtime.status(),service_states,self.health.check(),self._recovered,time.time())
