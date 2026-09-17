#!/usr/bin/env python3
"""修正版：A29 真测（登录成功 + 封禁生效后打）+ XFF 源码判据改 AST/真读取口径。

上一版两处假象已修正：
  (a) A29：cli2 复用同一 CRED_PATH，first-setup 已存在 ⇒ 登录失败 ⇒ 全程 401，
      "均被拒"是未鉴权的巧合，不是封禁生效。
  (b) XFF 源码判据：`grep -n` 输出带行号前缀，strip 后不以 '#' 开头 ⇒ 注释过滤失效；
      且 docstring 里的文字也被计入。改判据 = **AST 真读取**（`request.headers...['X-Forwarded-For']`）。
"""
import ast
import importlib.util
import os
import shutil
import sys
import tempfile

REPO = '/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main'
os.chdir(REPO)
sys.path = [p for p in sys.path if p not in ('', '.', REPO)]
SRC = os.path.join(REPO, 'plugins/web/bin/ttbox-web.py')

print('=== XFF：AST 真读取判据（注释/docstring 天然不入 AST） ===')
tree = ast.parse(open(SRC, encoding='utf-8').read())
hits = []
for node in ast.walk(tree):
    if isinstance(node, ast.Subscript):
        seg = ast.unparse(node.slice) if hasattr(ast, 'unparse') else ''
        if 'X-Forwarded-For' in seg or 'HTTP_X_FORWARDED' in seg:
            hits.append((node.lineno, ast.unparse(node)))
    if isinstance(node, ast.Call):
        src = ast.unparse(node)
        if 'X-Forwarded-For' in src or 'HTTP_X_FORWARDED' in src:
            hits.append((node.lineno, src))
print('  AST 真读取命中 = %d（期望 0）' % len(hits))
for h in hits:
    print('    :%d %s' % h)

spec = importlib.util.spec_from_file_location('w2', SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules['w2'] = mod
spec.loader.exec_module(mod)

cred_dir = tempfile.mkdtemp(prefix='yuprobe2_')
mod.CRED_PATH = os.path.join(cred_dir, 'c.json')
mod._SESSIONS.clear()
mod._RL.clear()
mod.ipc_request = lambda c, *a, **k: {'status': 1}

cli = mod.app.test_client()
fs = cli.post('/api/auth/first-setup', json={'password': 'Yu-Pass-2026'})
lg = cli.post('/api/auth/login', json={'password': 'Yu-Pass-2026'})
print()
print('=== 登录确认（必须都是 200，否则后面全是假象） ===')
print('  first-setup=%s login=%s' % (fs.status_code, lg.status_code))
assert fs.status_code == 200 and lg.status_code == 200, '登录未成功 ⇒ 后续判据无效'

print()
print('=== 基线：未封禁 IP 可访问 ===')
base_ok = cli.get('/api/state', environ_base={'REMOTE_ADDR': '203.0.113.9'})
print('  203.0.113.9 未封禁 -> %s（期望 200）' % base_ok.status_code)

print()
print('=== A29：注入封禁集 → 被封 IP + 伪造 XFF 仍须被拒 ===')
mod._BLOCKLIST_CACHE['ts'] = 0.0
mod._BLOCKLIST_CACHE['ips'] = frozenset({'203.0.113.9'})
mod._invalidate_blocklist_cache()
# 直接注入缓存（封禁数据面），再确认 before_request 仍读取它
mod._BLOCKLIST_CACHE['ts'] = __import__('time').time()
mod._BLOCKLIST_CACHE['ips'] = frozenset({'203.0.113.9'})

cases = {
    '被封IP + 无XFF': {'REMOTE_ADDR': '203.0.113.9'},
    '被封IP + XFF=1.2.3.4': {'REMOTE_ADDR': '203.0.113.9', 'HTTP_X_FORWARDED_FOR': '1.2.3.4'},
    '被封IP + XFF=127.0.0.1': {'REMOTE_ADDR': '203.0.113.9', 'HTTP_X_FORWARDED_FOR': '127.0.0.1'},
    '未封IP + XFF=被封IP': {'REMOTE_ADDR': '198.51.100.7', 'HTTP_X_FORWARDED_FOR': '203.0.113.9'},
}
results = {}
for name, env in cases.items():
    r = cli.get('/api/state', environ_base=env)
    results[name] = r.status_code
    print('  %-22s -> %s' % (name, r.status_code))

bypass = results['被封IP + XFF=1.2.3.4'] == 200 or results['被封IP + XFF=127.0.0.1'] == 200
misblock = results['未封IP + XFF=被封IP'] != 200
print()
print('  A29 %s' % ('FAIL（XFF 绕过成功）' if bypass else 'PASS（伪造 XFF 不能解封）'))
print('  反向：XFF 被封、真 IP 未封 -> %s（期望 200，若被拒说明误读了 XFF）' % results['未封IP + XFF=被封IP'])
if misblock:
    print('  ⚠ 误封：说明判定读了 XFF（更严重）')

shutil.rmtree(cred_dir, ignore_errors=True)
ok = (len(hits) == 0) and (not bypass) and (not misblock) and results['被封IP + 无XFF'] != 200
print()
print('总结: XFF真读取=%d, 绕过=%s, 误封=%s, 封禁生效=%s' %
      (len(hits), bypass, misblock, results['被封IP + 无XFF'] != 200))
sys.exit(0 if ok else 1)
