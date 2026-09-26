# -*- coding: utf-8 -*-
"""M2.07 B21 到期锁定 —— 驱动脚本（主理人/QA 用，非产品代码）

B21 需要「先由云端侧把测试卡改成到期/禁用」这一外部前置，验收脚本自身做不到，
因此把该前置做成可重复、可精确还原的驱动脚本。

判定链（与 ttbox_m207_accept.py::b21 一致）：
  云端改卡（expire_hours=0 / status='used'）
    → web 心跳（interval 60s）收到 403 ⇒ 快路径 IPC ACTIVATE_CLOUD{deactivate}
    → core 唯一执法快照 flipping ⇒ pipeline_allowed() 变 false
    → 断言 /api/license: license.activated == false 且
                        license.capabilities.capture == false（= pipeline_allowed()）

用法（宿主侧跑，Windows）：
  python ttbox_m207_b21_expire.py snapshot   # 只读快照卡/设备行 → 存 to tmp json
  python ttbox_m207_b21_expire.py expire     # 置为到期
  python ttbox_m207_b21_expire.py watch      # 轮询板端直到锁定（≤240s）
  python ttbox_m207_b21_expire.py restore    # 按快照精确还原卡行
  python ttbox_m207_b21_expire.py run        # snapshot → expire → watch → restore 一条龙

退出码：0 = 目标达成（锁定观察到 / 还原成功），1 = 未达成。
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.parse

# ---- 云端（业主自有 SaaS）----
# ★ 2026-09-26：旧七牛 38.127.133.6（NAT 10015）已宕机，现役阿里云 47.104.18.178，SSH 直连 22。
CLOUD_HOST = '47.104.18.178'
CLOUD_PORT = 22
CLOUD_USER = 'root'
# ★ 口令不落库（本仓库可能被公开）。从环境变量取；未设置时在真正连云端那一步明确报错。
CLOUD_PASS = os.environ.get('TTBOX_CLOUD_PASS', '')
CLOUD_DB = '/opt/license-saas/license-saas.db'
CARD = 'LS-TTBOX-TEST-0001-M2X7'

# ---- 板端 ----
BOARD_HOST = 'root@192.168.0.104'
BOARD_PW = 'root'
BOARD_HOSTKEY = 'SHA256:JCSZToi0WY02mohoJm6g7ap5ii6fWP1Dzy1lVjozvq0'
WEB_BASE = 'http://127.0.0.1:8000'

SNAP_PATH = os.path.join(os.environ.get('TEMP', '/tmp'), 'ttbox_m207_b21_snapshot.json')
WATCH_TIMEOUT_S = 240          # 心跳 60s + core 60s 扫描兜底，留足余量
PIPELINE_DIR = ('/c/Users/Administrator/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin'
                ':/c/Windows/System32:/c/Windows')


def _proxy() -> tuple:
    p = os.environ.get('https_proxy') or os.environ.get('http_proxy') or ''
    u = urllib.parse.urlparse(p)
    return (u.hostname or '127.0.0.1', u.port or 49621)


def cloud_cmd(sql_py: str, timeout: int = 40) -> str:
    """把一段 python 源码丢到云端容器/主机上执行（内联 sqlite3）。"""
    import paramiko  # 延迟导入：宿主没装时给清晰报错

    ph, pp = _proxy()
    s = socket.create_connection((ph, pp), timeout=20)
    s.sendall(('CONNECT %s:%d HTTP/1.1\r\nHost: %s:%d\r\n\r\n'
               % (CLOUD_HOST, CLOUD_PORT, CLOUD_HOST, CLOUD_PORT)).encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        c = s.recv(4096)
        if not c:
            raise RuntimeError('代理隧道被关闭（检查 https_proxy 端口是否变了）')
        buf += c

    def _dec(b) -> str:
        # paramiko 的 makefile 在不同版本/平台上可能给 bytes 而非 str，统一解码
        return b.decode('utf-8', 'replace') if isinstance(b, (bytes, bytearray)) else str(b)

    t = paramiko.Transport(s)
    if not CLOUD_PASS:
        raise SystemExit(
            '缺少服务器口令：请先设置环境变量 TTBOX_CLOUD_PASS 再重跑本脚本。\n'
            '（口令不再硬编码在源码里，见 config/README.md「凭据纪律」。）'
        )
    t.connect(username=CLOUD_USER, password=CLOUD_PASS)
    chan = t.open_session(timeout=timeout)
    chan.exec_command("python3 - << 'PYEOF'\n%s\nPYEOF" % sql_py)
    out = _dec(chan.makefile('r', -1).read())
    err = _dec(chan.makefile_stderr('r', -1).read())
    chan.close()
    t.close()
    if err.strip():
        out += '\n[stderr] ' + err.strip()
    return out


# ---------------------------------------------------------------------------
# 卡行快照 / 改 / 还原
# ---------------------------------------------------------------------------
_CARD_COLS = "id,card_key,status,machine_code,used_at,expire_hours,created_at"


def _sql_select() -> str:
    return ("import sqlite3, json\n"
            "c = sqlite3.connect(%r)\n"
            "c.row_factory = sqlite3.Row\n"
            "rows = c.execute('select %s from cards where card_key=?', (%r,)).fetchall()\n"
            "print('CARDS_JSON=' + json.dumps([dict(r) for r in rows]))\n"
            "d = c.execute(\"select id,machine_code,status from devices where machine_code like '9ecf%%'\").fetchall()\n"
            "print('DEVICES_JSON=' + json.dumps([dict(r) for r in d]))\n"
            % (CLOUD_DB, _CARD_COLS, CARD))


def _parse(out: str, tag: str):
    for line in out.splitlines():
        if line.startswith(tag):
            return json.loads(line[len(tag):])
    return None


def snapshot(verbose: bool = True) -> dict:
    out = cloud_cmd(_sql_select())
    cards = _parse(out, 'CARDS_JSON=') or []
    if not cards:
        raise RuntimeError('云端查不到卡 %s；原始输出：%s' % (CARD, out[:400]))
    snap = {'card': cards[0], 'devices': _parse(out, 'DEVICES_JSON=') or [], 'at': int(time.time())}
    with open(SNAP_PATH, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    if verbose:
        print('[snapshot] %s' % json.dumps(snap, ensure_ascii=False))
        print('[snapshot] 已存 %s' % SNAP_PATH)
    return snap


def expire() -> str:
    """置为到期（**真实到期语义**）：把 used_at 前移到窗口之外。

    ★ 巨大坑（2026-09-17 实测踩到）：云端 `clientHeartbeat`（server.go:1125）与
      `clientCardLogin`（server.go:945）判到期的算式是
          start = used_at（缺省 now）
          if expire_hours <= 0 { expire_hours = expire_days * 24 }   ← 回落！
          if now > start + expire_hours ⇒ 403「卡密已到期」
      所以「把 expire_hours 置 0」**根本不构成到期**——它反而回落成 expire_days*24，
      卡还是有效的（这就是首轮 B21 盯着 240s 全是 activated=True 的原因）。
      唯一可靠的到期构造 = 让 `used_at + max(expire_hours, expire_days*24)` 落在过去，
      即**前移 used_at**（不动 expire_hours/expire_days，保证 snapshot 还原即复原）。
    """
    sql = (
        "import sqlite3\n"
        "c = sqlite3.connect(%r)\n"
        "c.execute(\"update cards set status='used', used_at = \"\n"
        "          \"datetime('now','+8 hours','-' || (max(expire_hours, expire_days*24) + 2) || ' hours') \"\n"
        "          \"where card_key=?\", (%r,))\n"
        "c.commit()\n"
        "print('EXPIRED_ROW=', c.execute('select status,expire_hours,expire_days,used_at "
        "from cards where card_key=?', (%r,)).fetchone())\n"
    ) % (CLOUD_DB, CARD, CARD)
    return cloud_cmd(sql)


def restore() -> str:
    """按快照精确还原卡行（不猜默认值）。"""
    with open(SNAP_PATH, 'r', encoding='utf-8') as f:
        snap = json.load(f)
    row = snap['card']
    return cloud_cmd(
        "import sqlite3\n"
        "c = sqlite3.connect(%r)\n"
        "c.execute('update cards set status=?, machine_code=?, used_at=?, expire_hours=? where card_key=?',\n"
        "          (%r, %r, %r, %r, %r))\n"
        "c.commit()\n"
        "print('RESTORED=', c.total_changes)\n"
        % (CLOUD_DB, row.get('status'), row.get('machine_code'),
           row.get('used_at'), row.get('expire_hours'), CARD))


# ---------------------------------------------------------------------------
# 板端投影轮询
# ---------------------------------------------------------------------------
def _plink(cmd: str, timeout: int = 30) -> str:
    env = dict(os.environ)
    env['PATH'] = PIPELINE_DIR + os.pathsep + env.get('PATH', '')
    r = subprocess.run(
        ['plink', '-ssh', '-pw', BOARD_PW, '-hostkey', BOARD_HOSTKEY,
         '-batch', BOARD_HOST, cmd],
        capture_output=True, text=True, timeout=timeout, env=env)
    return (r.stdout or '') + (('\n[err] ' + r.stderr) if r.stderr.strip() else '')


def board_license() -> dict:
    """取板端 /api/license 的 license 段（activated / capabilities / features）。"""
    raw = _plink(
        "curl -s %s/api/license | python3 -c \"import sys,json;"
        "d=json.load(sys.stdin).get('data',{});print(json.dumps(d.get('license') or {}))\"" % WEB_BASE)
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith('{'):
            try:
                return json.loads(line)
            except Exception:
                pass
    return {}


def _state() -> tuple:
    lic = board_license()
    cap = (lic.get('capabilities') or {})
    return bool(lic.get('activated')), bool(cap.get('capture')), lic


def watch() -> bool:
    """轮询直到锁定：activated==false 且 capabilities.capture==false（= pipeline_allowed()）。"""
    t0 = time.time()
    while time.time() - t0 < WATCH_TIMEOUT_S:
        act, pipe, lic = _state()
        el = int(time.time() - t0)
        print('  t+%3ds  activated=%-5s pipeline_allowed(capture)=%-5s features=%s'
              % (el, act, pipe, lic.get('features')), flush=True)
        if not act and not pipe:
            print('[watch] 锁定已确认：activated=false 且 pipeline_allowed()=false（t+%ds）' % el)
            return True
        time.sleep(10)
    print('[watch] 超时 %ds 未观察到锁定' % WATCH_TIMEOUT_S)
    return False


def run() -> int:
    print('=' * 74)
    print('B21 到期锁定驱动  卡=%s  板=%s' % (CARD, BOARD_HOST))
    print('=' * 74)

    print('\n[1/5] 前置：板端应为已激活态')
    act, pipe, _ = _state()
    print('  activated=%s pipeline_allowed=%s' % (act, pipe))
    if not (act and pipe):
        print('  !! 前置不满足：需先激活（云端正例 card-login）。中止。')
        return 1

    print('\n[2/5] 快照云端卡行（供精确还原）')
    snapshot()

    locked = False
    try:
        print('\n[3/5] 置卡到期（前移 used_at；不是置 expire_hours=0 —— 那会回落到 expire_days）')
        print('  ' + expire().strip().replace('\n', '\n  '))

        print('\n[4/5] 观察锁定（心跳 60s ⇒ 403 ⇒ deactivate；core 60s 扫描兜底）')
        locked = watch()
    finally:
        # ★ 本脚本改的是**生产云端**的卡行 —— 无论成败都必须还原（含异常中断）。
        print('\n[5/5] 还原卡行')
        print('  ' + restore().strip().replace('\n', '\n  '))

    print('\n--- 还原后重新 card-login 恢复激活态 ---')
    out = _plink(
        "curl -s -o /dev/null -w 'reactivate HTTP=%%{http_code}\\n' -X POST "
        "-H 'Content-Type: application/json' -d '{\"license_key\":\"%s\"}' %s/api/license/activate"
        % (CARD, WEB_BASE))
    print('  ' + out.strip())

    act, pipe, lic = _state()
    print('  恢复后 activated=%s pipeline_allowed=%s features=%s'
          % (act, pipe, lic.get('features')))
    ok = locked and act and pipe
    print('\nB21 结论：%s' % ('PASS' if ok else 'FAIL'))
    print('=' * 74)
    return 0 if ok else 1


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'run'
    if cmd == 'snapshot':
        snapshot()
        return 0
    if cmd == 'expire':
        print(expire())
        return 0
    if cmd == 'watch':
        return 0 if watch() else 1
    if cmd == 'restore':
        print(restore())
        return 0
    if cmd == 'run':
        return run()
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main())
