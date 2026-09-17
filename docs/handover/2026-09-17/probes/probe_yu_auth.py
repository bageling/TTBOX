#!/usr/bin/env python3
"""实测 §4A #22（A22 Web 敏感端点 401）+ #23（A29 XFF 不可绕过）。

不读账本结论，直接打真实端点。
"""
import importlib.util
import os
import shutil
import sys
import tempfile

REPO = '/mnt/c/Users/Administrator/Desktop/TTBOX-Module-Edition-main'
os.chdir(REPO)
sys.path = [p for p in sys.path if p not in ('', '.', REPO)]

spec = importlib.util.spec_from_file_location('w', os.path.join(REPO, 'plugins/web/bin/ttbox-web.py'))
mod = importlib.util.module_from_spec(spec)
sys.modules['w'] = mod
spec.loader.exec_module(mod)

cred_dir = tempfile.mkdtemp(prefix='yuprobe_')
mod.CRED_PATH = os.path.join(cred_dir, 'c.json')
mod._SESSIONS.clear()
mod._RL.clear()
mod._BLOCKLIST_CACHE['ts'] = 0.0
mod._BLOCKLIST_CACHE['ips'] = frozenset()
mod.ipc_request = lambda c, *a, **k: {'status': 1}

cli = mod.app.test_client()
FAILS = []

print('=== A22：未鉴权访问敏感端点必须 401 ===')
for p in ('reboot', 'poweroff', 'config', 'preview', 'state'):
    r = cli.post('/api/' + p, json={}) if p in ('reboot', 'poweroff') else cli.get('/api/' + p)
    ok = r.status_code == 401
    print('  %-10s -> %s  %s' % ('/api/' + p, r.status_code, 'PASS' if ok else 'FAIL'))
    if not ok:
        FAILS.append('A22:' + p)

print()
print('=== 负控：A22 判据是否有牙（若不鉴权则端点裸奔 ⇒ 判据必须能打红）===')
# 直接反向验证：登录后再打同一端点，不应再是 401 ⇒ 证明 401 来自鉴权层而非端点不存在
cli.post('/api/auth/first-setup', json={'password': 'Yu-Pass-2026'})
cli.post('/api/auth/login', json={'password': 'Yu-Pass-2026'})
codes = {}
for p in ('reboot', 'poweroff', 'config', 'preview', 'state'):
    r = cli.post('/api/' + p, json={'dry_run': True}) if p in ('reboot', 'poweroff') else cli.get('/api/' + p)
    codes[p] = r.status_code
print('  已登录状态码: ' + str(codes))
if all(v == 401 for v in codes.values()):
    print('  ⚠ 登录后仍全 401 ⇒ 端点可能被其它层拦截，A22 的 401 不专属鉴权层')
else:
    print('  登录后不再 401 ⇒ 401 确由鉴权层产生（判据有牙）')

print()
print('=== A29：XFF 不可绕过封禁 ===')
cli2 = mod.app.test_client()
cli2.post('/api/auth/first-setup', json={'password': 'Yu2-Pass-2026'})
cli2.post('/api/auth/login', json={'password': 'Yu2-Pass-2026'})
# 封禁一个"他人"IP（用环境变量注入 remote_addr）
base = {'REMOTE_ADDR': '203.0.113.9'}
try:
    r = cli2.post('/api/system/lan-blocklist', json={'ip': '203.0.113.9'},
                  environ_base=base)
    print('  封禁请求: %s %s' % (r.status_code, r.get_json()))
except Exception as e:
    print('  封禁端点调用异常: %r' % (e,))
mod._invalidate_blocklist_cache()
# 用被封 IP + 伪造 XFF 访问
xff = dict(base)
xff['HTTP_X_FORWARDED_FOR'] = '198.51.100.7'
r1 = cli2.get('/api/state', environ_base=xff)
xff2 = dict(base)
xff2['HTTP_X_FORWARDED_FOR'] = '127.0.0.1'
r2 = cli2.get('/api/state', environ_base=xff2)
print('  被封IP + XFF=198.51.100.7 -> %s' % r1.status_code)
print('  被封IP + XFF=127.0.0.1    -> %s' % r2.status_code)
bypass = (r1.status_code == 200 or r2.status_code == 200)
print('  A29 %s' % ('FAIL（XFF 绕过成功）' if bypass else 'PASS（均被拒）'))
if bypass:
    FAILS.append('A29')

print()
print('=== 源码判据：XFF 读取点 == 0 ===')
import subprocess
g = subprocess.run(['grep', '-rn', 'X-Forwarded-For', 'plugins/web/bin/ttbox-web.py'],
                   capture_output=True, text=True)
n = len([l for l in g.stdout.splitlines() if l.strip() and not l.strip().startswith('#')])
print('  非注释命中 = %d（期望 0）' % n)
if n != 0:
    FAILS.append('XFF-src')

shutil.rmtree(cred_dir, ignore_errors=True)
print()
print('结果: %d failures %s' % (len(FAILS), FAILS if FAILS else ''))
sys.exit(1 if FAILS else 0)
