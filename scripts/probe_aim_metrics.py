import socket, json

s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(5)
s.connect('/tmp/ttbox_core.sock')
s.sendall(b'{"type":"GET_STATUS"}\n')
buf = b''
while b'\n' not in buf:
    chunk = s.recv(65536)
    if not chunk:
        break
    buf += chunk
d = json.loads(buf.decode())
m = d.get('data', {}).get('metrics', {})
keys = sorted(k for k in m if 'aim' in k or 'track' in k or 'target' in k)
print(json.dumps({k: m[k] for k in keys}, ensure_ascii=False))
