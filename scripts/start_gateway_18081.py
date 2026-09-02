import paramiko
c = paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.0.53', username='root', password='orangepi', timeout=10, allow_agent=False, look_for_keys=False)

# 用 setsid + 完全重定向，避免 SSH 通道等待后台进程输出
cmd = (
    'ss -ltn 2>/dev/null | grep -q ":18081 " && echo ALREADY || ( '
    'rm -rf /tmp/ttbox-phase84-motion && mkdir -p /tmp/ttbox-phase84-motion && '
    'setsid env TTBOX_ROOT=/opt/ttbox/web TTBOX_WEB_HOST=0.0.0.0 TTBOX_WEB_PORT=18081 '
    'TTBOX_MOTION_PROFILES_DIR=/tmp/ttbox-phase84-motion '
    'python3 /opt/ttbox/web/ttbox_web.py >/tmp/ttbox-phase84-gateway.log 2>&1 < /dev/null & '
    'echo STARTED ) ; sleep 2 ; ss -ltn 2>/dev/null | grep ":18081 " || echo NO_LISTENER'
)
_, o, e = c.exec_command(cmd, timeout=30)
print('OUT:', o.read().decode(errors='ignore'))
err = e.read().decode(errors='ignore')
if err:
    print('ERR:', err[:400])
c.close()