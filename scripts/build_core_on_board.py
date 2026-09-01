"""板端 Core 编译：增量构建 → 新二进制 → 备份旧二进制 → 替换 → 重启 ttbox-core。
编译失败则不动二进制不重启（保持当前运行环境）。"""
import paramiko
import sys

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.0.53', username='root', password='orangepi', timeout=10, allow_agent=False, look_for_keys=False)

def run(cmd, timeout=560):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors='ignore')
    err = e.read().decode(errors='ignore')
    rc = o.channel.recv_exit_status()
    return rc, out, err

# 1) 增量编译（板端已有 build 目录）
rc, out, err = run('cd /opt/ttbox/core/build && cmake --build . -j 6 2>&1 | tail -12; echo "RC=$?"', 560)
print(out[-1200:])
if 'Error' in out or 'error:' in out.lower():
    print('BUILD FAILED, aborting (binary untouched)')
    c.close()
    sys.exit(1)

# 2) 确认新二进制
rc, out, _ = run('ls -la /opt/ttbox/core/build/ttbox_core_main')
print(out.strip())

# 3) 备份旧二进制 + 替换 + 重启 core
rc, out, err = run(
    'cp -a /opt/ttbox/core/build/ttbox_core_main /opt/ttbox/backup/20260901_phase83/ttbox_core_main.bak && '
    'systemctl restart ttbox-core && sleep 5 && systemctl is-active ttbox-core', 60)
print('restart:', out.strip(), err.strip()[:200])

# 4) 服务健康
rc, out, _ = run('systemctl is-active ttbox-core ttbox-web; journalctl -u ttbox-core --no-pager -n 5 2>/dev/null | tail -4')
print(out)
c.close()
