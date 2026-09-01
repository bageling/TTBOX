"""Phase 8.3 部署：本地已验证文件 → 板端。
规则：板端先备份旧文件；SFTP 只传本地已验证产物；不 reboot；只 restart ttbox-web。
Core 源码同步后板端交叉编译（单独步骤），编译成功才替换二进制并 restart ttbox-core。
"""
import paramiko
import hashlib
import posixpath

HOST, USER, PWD = '192.168.0.53', 'root', 'orangepi'
LOCAL = r'C:/Users/Administrator/Desktop/TTbox0831'
STAMP = '20260901_phase83'

FILES_WEB = [
    ('scripts/ttbox_web.py', '/opt/ttbox/web/ttbox_web.py'),
    ('web/templates/index.html', '/opt/ttbox/web/templates/index.html'),
    ('web/static/app.js', '/opt/ttbox/web/static/app.js'),
    ('web/static/apiClient.js', '/opt/ttbox/web/static/apiClient.js'),
]
FILES_CORE = [
    'core/src/aim/AimThread.cpp',
    'core/src/aim/AimThread.hpp',
    'core/src/app/Application.cpp',
    'core/src/capture/V4L2Capture.cpp',
    'core/src/capture/V4L2Capture.hpp',
    'core/src/common/Metrics.hpp',
    'core/src/common/Types.hpp',
    'core/src/ipc/IpcServer.cpp',
    'core/src/ipc/IpcServer.hpp',
    'core/src/model/ModelRegistry.cpp',
    'core/src/preview/PreviewModule.cpp',
    'core/src/rga/RgaProcessor.cpp',
    'core/src/rknn/WorkerPool.cpp',
    'core/src/rknn/WorkerPool.hpp',
    'core/src/runtime/CoreRuntime.cpp',
    'core/src/output/OutputBackend.cpp',
]


def md5f(path):
    return hashlib.md5(open(path, 'rb').read()).hexdigest()


c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username=USER, password=PWD, timeout=10, allow_agent=False, look_for_keys=False)
sftp = c.open_sftp()

# 1) 备份旧文件
c.exec_command(f'mkdir -p /opt/ttbox/backup/{STAMP}/web /opt/ttbox/backup/{STAMP}/core')[1].channel.recv_exit_status()
for _, remote in FILES_WEB:
    _, o, _ = c.exec_command(f'cp -a {remote} /opt/ttbox/backup/{STAMP}/web/$(basename {remote}).bak 2>&1')
    o.channel.recv_exit_status()
for f in FILES_CORE:
    remote = '/opt/ttbox/' + f
    _, o, _ = c.exec_command(f'cp -a {remote} /opt/ttbox/backup/{STAMP}/core/$(basename {f}).bak 2>&1')
    o.channel.recv_exit_status()
print('backup done')

# 2) 传 Web（Gateway + 前端）
for local_rel, remote in FILES_WEB:
    sftp.put(posixpath.join(LOCAL, local_rel.replace('/', '\\')), remote) if False else sftp.put(f'{LOCAL}/{local_rel}', remote)
print('web files uploaded')

# 3) 传 Core 源码
for f in FILES_CORE:
    sftp.put(f'{LOCAL}/{f}', '/opt/ttbox/' + f)
print('core sources uploaded')

# 4) 哈希核对
ok = True
for local_rel, remote in FILES_WEB:
    _, o, _ = c.exec_command(f'md5sum {remote}')
    rmd5 = o.read().decode().split()[0]
    lmd5 = md5f(f'{LOCAL}/{local_rel}')
    status = 'OK' if rmd5 == lmd5 else 'MISMATCH'
    if rmd5 != lmd5:
        ok = False
    print(f'{status} {remote}')
for f in FILES_CORE:
    remote = '/opt/ttbox/' + f
    _, o, _ = c.exec_command(f'md5sum {remote}')
    rmd5 = o.read().decode().split()[0]
    lmd5 = md5f(f'{LOCAL}/{f}')
    status = 'OK' if rmd5 == lmd5 else 'MISMATCH'
    if rmd5 != lmd5:
        ok = False
    print(f'{status} {remote}')
sftp.close()
c.close()
print('ALL OK' if ok else 'HAS MISMATCH')
