"""AIBOX 能力对齐：模型 staging/validate/install/activate/rollback 控制面。"""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json, shutil, tempfile

@dataclass(frozen=True)
class ModelInfo:
    model_id: str; state: str; path: str

class ModelManager:
    def __init__(self, root: str|Path):
        self.root=Path(root).resolve(); self.staging=self.root/'staging'; self.versions=self.root/'versions'; self.current=self.root/'current'
        for p in (self.staging,self.versions): p.mkdir(parents=True,exist_ok=True)
    def _id(self, model_id):
        if not model_id or Path(model_id).name != model_id or model_id in ('.','..') or any(c in model_id for c in '/\\'):
            raise ValueError('invalid model id')
        return model_id
    def upload(self, model_id: str, source: str|Path) -> ModelInfo:
        mid=self._id(model_id); src=Path(source).resolve()
        if not src.is_file(): raise FileNotFoundError(src)
        dst=self.staging/mid; dst.mkdir(parents=True,exist_ok=True); shutil.copy2(src,dst/'model.rknn')
        return ModelInfo(mid,'staging',str(dst))
    def validate(self, model_id: str) -> ModelInfo:
        mid=self._id(model_id); d=self.staging/mid; f=d/'model.rknn'
        if not f.is_file() or f.stat().st_size==0: raise ValueError('staged model is missing or empty')
        (d/'validation.json').write_text(json.dumps({'ok':True,'model_id':mid},ensure_ascii=False),encoding='utf-8')
        return ModelInfo(mid,'validated',str(d))
    def install(self, model_id: str) -> ModelInfo:
        mid=self._id(model_id); src=self.staging/mid; marker=src/'validation.json'
        if not marker.is_file(): raise ValueError('model must be validated before install')
        dst=self.versions/mid
        if dst.exists(): shutil.rmtree(dst)
        shutil.copytree(src,dst); return ModelInfo(mid,'installed',str(dst))
    def activate(self, model_id: str) -> ModelInfo:
        mid=self._id(model_id); src=self.versions/mid
        if not (src/'model.rknn').is_file(): raise ValueError('model is not installed')
        previous=self.current.resolve() if self.current.is_symlink() else None
        if self.current.exists() or self.current.is_symlink(): self.current.unlink() if self.current.is_symlink() else shutil.rmtree(self.current)
        self.current.symlink_to(src,target_is_directory=True)
        if previous: (self.root/'previous').write_text(str(previous),encoding='utf-8')
        return ModelInfo(mid,'active',str(src))
    def rollback(self) -> ModelInfo:
        if not self.current.is_symlink(): raise ValueError('no active model')
        previous_file=self.root/'previous'
        if not previous_file.is_file(): raise ValueError('no previous model')
        prev=Path(previous_file.read_text(encoding='utf-8')).resolve()
        if not prev.is_dir(): raise ValueError('previous model is missing')
        self.current.unlink(); self.current.symlink_to(prev,target_is_directory=True)
        return ModelInfo(prev.name,'active',str(prev))
    def list(self) -> list[ModelInfo]:
        return [ModelInfo(p.name,'active' if self.current.is_symlink() and self.current.resolve()==p.resolve() else 'installed',str(p)) for p in sorted(self.versions.iterdir()) if p.is_dir()]
