"""Platform Runtime Controller：编排现有 C++ Core 的生命周期与状态。"""
from __future__ import annotations
from dataclasses import dataclass
import time
from typing import Any
from .lifecycle import RuntimeState, transition
from .process_adapter import ProcessAdapter

@dataclass(frozen=True)
class RuntimeSnapshot:
    state: str; pid: int|None; uptime: float; last_error: str|None; health: str; timestamp: float
    def as_dict(self) -> dict[str, Any]: return self.__dict__.copy()

class RuntimeController:
    def __init__(self, process: ProcessAdapter, clock=time.time):
        self.process=process; self._clock=clock; self._state=RuntimeState.STOPPED
        self._pid=None; self._started_at=None; self._last_error=None
    @property
    def state(self): return self._state
    def _move(self,target): self._state=transition(self._state,target)
    def _fail(self,error):
        self._last_error=str(error); self._state=RuntimeState.FAILED
    def start(self) -> RuntimeSnapshot:
        if self._state in (RuntimeState.READY,RuntimeState.RUNNING): return self.status()
        if self._state == RuntimeState.FAILED: self._move(RuntimeState.RECOVERING); self._move(RuntimeState.STARTING)
        else: self._move(RuntimeState.STARTING)
        try:
            info=self.process.start(); self._pid=info.pid; self._started_at=info.started_at or self._clock()
            self._move(RuntimeState.READY)
            if not self.process.health(): raise RuntimeError("core process health check failed")
            self._move(RuntimeState.RUNNING); self._last_error=None
        except Exception as exc: self._fail(exc)
        return self.status()
    def stop(self) -> RuntimeSnapshot:
        if self._state == RuntimeState.STOPPED: return self.status()
        if self._state == RuntimeState.FAILED: self.process.stop(); self._state=RuntimeState.STOPPED
        else:
            if self._state != RuntimeState.STOPPING: self._move(RuntimeState.STOPPING)
            try: self.process.stop(); self._state=RuntimeState.STOPPED; self._pid=None; self._started_at=None
            except Exception as exc: self._fail(exc)
        return self.status()
    def restart(self) -> RuntimeSnapshot:
        self.stop(); return self.start()
    def reload(self) -> RuntimeSnapshot:
        return self.restart() if self._state in (RuntimeState.READY,RuntimeState.RUNNING) else self.start()
    def health(self) -> dict[str,Any]:
        ok=self._state==RuntimeState.RUNNING and self.process.health()
        return {"ok":ok,"state":self._state.value,"last_error":self._last_error,"timestamp":self._clock()}
    def status(self) -> RuntimeSnapshot:
        now=self._clock(); uptime=max(0.0,now-self._started_at) if self._started_at and self._state==RuntimeState.RUNNING else 0.0
        health="healthy" if self._state==RuntimeState.RUNNING and self.process.health() else ("failed" if self._state==RuntimeState.FAILED else "stopped")
        return RuntimeSnapshot(self._state.value,self._pid,uptime,self._last_error,health,now)
