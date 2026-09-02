import paramiko
c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.0.53',username='root',password='orangepi',timeout=10,allow_agent=False,look_for_keys=False)
cmd=(
 'ss -ltn 2>/dev/null | grep -q ":18082 " && echo ALREADY || '
 '(rm -rf /tmp/ttbox-phase84b-motion && mkdir -p /tmp/ttbox-phase84b-motion && '
 'setsid env TTBOX_ROOT=/opt/ttbox/web TTBOX_WEB_HOST=0.0.0.0 TTBOX_WEB_PORT=18082 '
 'TTBOX_MOTION_PROFILES_DIR=/tmp/ttbox-phase84b-motion '
 'python3 /opt/ttbox/web/ttbox_web.py >/tmp/ttbox-phase84b-gateway.log 2>&1 < /dev/null & echo STARTED); '
 'sleep 2; ss -ltn 2>/dev/null | grep ":18082 " || echo NO_LISTENER'
)
_,o,e=c.exec_command(cmd,timeout=25)
print(o.read().decode(errors='ignore')); print(e.read().decode(errors='ignore')[:300]); c.close()
