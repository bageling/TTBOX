#!/usr/bin/env python3
import json, socket

def ipc_cmd(req_type, params=None):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        s.connect('/tmp/ttbox_core.sock')
        payload = {'type': req_type}
        if params: payload['params'] = params
        s.sendall(json.dumps(payload).encode() + b'\n')
        buf = b''
        while b'\n' not in buf:
            chunk = s.recv(65536)
            if not chunk: break
            buf += chunk
        if buf:
            return json.loads(buf.decode())
    except Exception as e:
        return {'status': -1, 'error': str(e)}
    finally:
        s.close()
    return {'status': -1, 'error': 'no response'}

for cmd in ['PING', 'GET_STATUS', 'GET_CONFIG', 'GET_PREVIEW']:
    r = ipc_cmd(cmd)
    status = r.get('status', '?')
    if cmd == 'GET_PREVIEW':
        data = r.get('data', {}) or {}
        has_jpeg = 'jpeg_base64' in data
        jpeg_len = len(data.get('jpeg_base64', ''))
        print(f'{cmd}: status={status} has_jpeg={has_jpeg} jpeg_len={jpeg_len}')
    else:
        print(f'{cmd}: status={status}')

r = ipc_cmd('RUNTIME_CONTROL', {'action': 'status'})
print(f'RUNTIME_CONTROL(status): st={r.get("status")} err={r.get("error","")}')

r = ipc_cmd('GET_STATUS')
data = r.get('data', {}) or {}
print(f'\nrunning={data.get("running")} runtime_running={data.get("runtime_running")}')
metrics = data.get('metrics', {}) or {}
print(f'capture_fps={metrics.get("capture_fps")} fps={metrics.get("fps")}')
print(f'aim_active={metrics.get("aim_active")}')
print(f'preview_fps={metrics.get("preview_fps")}')
print(f'preview_frames={metrics.get("preview_frames")}')
print(f'preview_bytes={metrics.get("preview_bytes")}')
print(f'preview_encode_ms={metrics.get("preview_encode_ms")}')
print(f'preview_width={metrics.get("preview_width")} preview_height={metrics.get("preview_height")}')
print(f'frames_total={metrics.get("frames_total")}')