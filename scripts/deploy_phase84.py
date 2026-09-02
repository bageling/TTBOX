"""Phase 8.4 部署：本地验证产物 → 板端。
只同步本地已验证文件；先备份；Core 板端编译成功后才替换并重启 ttbox-core。
安全：不 reboot，不停止 release-manager，不碰 YU 服务。
"""
import hashlib
import paramiko
import sys

HOST, USER, PWD = '192.168.0.53', 'root', 'orangepi'
REPO = r'C:/Users/Administrator/Desktop/TTbox0831'
STAMP = '20260901_phase84'

WEB_FILES = [
    ('scripts/ttbox_web.py', '/opt/ttbox/web/ttbox_web.py'),
    ('web/templates/motion_training.html', '/opt/ttbox/web/templates/motion_training.html'),
    ('web/static/motion_training.js', '/opt/ttbox/web/static/motion_training.js'),
    ('web/static/motion_training_mobile.js', '/opt/ttbox/web/static/motion_training_mobile.js'),
    ('ttbox_motion/__init__.py', '/opt/ttbox/ttbox_motion/__init__.py'),
    ('ttbox_motion/training.py', '/opt/ttbox/ttbox_motion/training.py'),
]
CORE_FILES = [
    'core/CMakeLists.txt',
    'core/src/mouse/MouseTypes.hpp',
    'core/src/mouse/PersonalMotion.hpp',
    'core/src/mouse/PersonalMotion.cpp',
    'core/src/model/RuntimeProfile.cpp',
    'core/src/aim/AimThread.cpp',
]


def md5f(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()


c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=10, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()

# 1) 备份
c.exec_command(f'mkdir -p /opt/ttbox/backup/{STAMP}/web /opt/ttbox/backup/{STAMP}/core')[1].channel.recv_exit_status()
for _, remote in WEB_FILES:
    c.exec_command(f'cp -a {remote} /opt/ttbox/backup/{STAMP}/web/$(basename {remote}).bak 2>/dev/null')[1].channel.recv_exit_status()
for f in CORE_FILES:
    c.exec_command(f'cp -a /opt/ttbox/{f} /opt/ttbox/backup/{STAMP}/core/$(basename {f}).bak 2>/dev/null')[1].channel.recv_exit_status()
print('backup ok')

# 2) 创建目标目录并同步 Web + Domain
for directory in ['/opt/ttbox/ttbox_motion', '/opt/ttbox/web/templates', '/opt/ttbox/web/static']:
    c.exec_command(f'mkdir -p {directory}')[1].channel.recv_exit_status()
for local_rel, remote in WEB_FILES:
    sftp.put(f'{REPO}/{local_rel}', remote)
print('web/domain uploaded')

# 3) 同步 Core 源码
for f in CORE_FILES:
    sftp.put(f'{REPO}/{f}', '/opt/ttbox/' + f)
print('core sources uploaded')

# 4) 哈希核对
all_ok = True
for local_rel, remote in WEB_FILES:
    _, o, _ = c.exec_command(f'md5sum {remote}')
    rmd5 = o.read().decode().split()[0]
    lmd5 = md5f(f'{REPO}/{local_rel}')
    ok = rmd5 == lmd5
    all_ok &= ok
    print(('OK ' if ok else 'MISMATCH ') + remote)
for f in CORE_FILES:
    _, o, _ = c.exec_command(f'md5sum /opt/ttbox/{f}')
    rmd5 = o.read().decode().split()[0]
    lmd5 = md5f(f'{REPO}/{f}')
    ok = rmd5 == lmd5
    all_ok &= ok
    print(('OK ' if ok else 'MISMATCH ') + '/opt/ttbox/' + f)
sftp.close()
c.close()
print('ALL OK' if all_ok else 'HAS MISMATCH')
sys.exit(0 if all_ok else 1)