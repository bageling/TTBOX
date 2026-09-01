"""重启 ttbox-web（Gateway 更新生效）并确认健康。"""
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect('192.168.0.53', username='root', password='orangepi', timeout=10, allow_agent=False, look_for_keys=False)


def run(cmd, timeout=60):
    _, o, e = c.exec_command(cmd, timeout=timeout)
    out = o.read().decode(errors='ignore')
    rc = o.channel.recv_exit_status()
    return rc, out


run('systemctl restart ttbox-web')
import time
time.sleep(3)
rc, out = run('systemctl is-active ttbox-web ttbox-core')
print('services:', out.strip())
rc, out = run('curl -s http://127.0.0.1:8081/api/control/calibration | head -c 400')
print('calibration GET:', out)
rc, out = run('curl -s http://127.0.0.1:8081/ | grep -c recordAimTraceButton')
print('index has aim-trace button:', out.strip())
rc, out = run('curl -s http://127.0.0.1:8081/api/state | python3 -c "import json,sys; d=json.load(sys.stdin); print(json.dumps(d[\'data\'][\'state\'][\'mouse_output\']))"')
print('state.mouse_output:', out.strip())
c.close()
