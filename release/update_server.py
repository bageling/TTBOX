import os, json, hashlib, base64, logging, socket
from flask import Flask, jsonify, request, send_file, abort

app = Flask(__name__)

RELEASE_DIR = '/srv/ttbox/releases'
MANIFEST_DIR = '/srv/ttbox/manifests/live'
PUBLIC_KEY_PATH = '/srv/ttbox/releases/release-public.pem'

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# 加载公钥用于验证
with open(PUBLIC_KEY_PATH) as f:
    PUBLIC_KEY_PEM = f.read()

# 从 cryptography 导入 Ed25519
def verify_signature(sha256_hash, signature_b64):
    """验证 Ed25519 签名"""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.hazmat.primitives import serialization
        public_key = ed25519.Ed25519PublicKey.from_public_bytes(
            b''.join(PUBLIC_KEY_PEM.encode().splitlines()[1:-1])
        )
        # 需要正确解析 PEM
        public_key = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode())
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, sha256_hash.encode())
        return True
    except Exception as e:
        logging.error(f'Signature verification failed: {e}')
        return False


def load_manifest(channel):
    """加载指定通道的 manifest"""
    manifest_path = os.path.join(MANIFEST_DIR, f'{channel}.json')
    if not os.path.exists(manifest_path):
        return None
    with open(manifest_path) as f:
        return json.load(f)


@app.route('/api/update/check', methods=['GET', 'POST'])
def check_update():
    """检查更新"""
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
    else:
        data = request.args.to_dict()

    product = data.get('product', 'TTBOX')
    current_version = data.get('current_version', '0.0.0')
    hardware = data.get('hardware', 'rk3588')
    channel = data.get('channel', 'stable')
    components = data.get('components', [])

    manifest = load_manifest(channel)
    if not manifest:
        return jsonify({
            'ok': False,
            'error': f'Channel {channel} not found'
        }), 404

    latest_version = manifest['version']

    # 比较版本号（简单字符串比较，后续可改为 semver）
    def parse_version(v):
        parts = v.split('.')
        return tuple(int(p) if p.isdigit() else 0 for p in parts[:3])

    current = parse_version(current_version)
    latest = parse_version(latest_version)

    update_available = latest > current

    # 检查硬件兼容性
    hardware_ok = hardware in manifest.get('target_hardware', [])

    # 检查最低版本
    min_version = manifest.get('min_version', '0.0.0')
    min_ok = parse_version(current_version) >= parse_version(min_version)

    return jsonify({
        'ok': True,
        'data': {
            'update_available': update_available and hardware_ok and min_ok,
            'latest_version': latest_version,
            'channel': channel,
            'release_date': manifest.get('release_date', ''),
            'release_notes_url': f'/api/update/release-notes/{latest_version}',
            'manifest_url': f'/api/update/manifest/{product}/{latest_version}/{channel}/manifest.json',
            'critical': False,
            'hardware_compatible': hardware_ok,
            'version_compatible': min_ok
        }
    })


@app.route('/api/update/manifest/<product>/<version>/<channel>/manifest.json')
def get_manifest(product, version, channel):
    """获取 manifest"""
    manifest = load_manifest(channel)
    if not manifest:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    if manifest['version'] != version:
        return jsonify({'ok': False, 'error': f'Version {version} not found in channel {channel}'}), 404
    return jsonify(manifest)


@app.route('/api/update/package/<package_id>')
def download_package(package_id):
    """下载更新包"""
    # 在所有通道中搜索
    for channel in ['stable', 'beta', 'developer']:
        pkg_path = os.path.join(RELEASE_DIR, channel, package_id.replace('.tar.gz', ''), package_id)
        if os.path.exists(pkg_path):
            return send_file(pkg_path, mimetype='application/octet-stream')
        # 也检查直接路径
        pkg_path = os.path.join(RELEASE_DIR, channel, package_id)
        if os.path.exists(pkg_path):
            return send_file(pkg_path, mimetype='application/octet-stream')

    return jsonify({'ok': False, 'error': 'Package not found'}), 404


@app.route('/api/update/release-notes/<version>')
def release_notes(version):
    """获取发布说明"""
    for channel in ['stable', 'beta', 'developer']:
        manifest = load_manifest(channel)
        if manifest and manifest['version'] == version:
            notes = manifest.get('release_notes', '')
            return notes, 200, {'Content-Type': 'text/markdown'}
    return jsonify({'ok': False, 'error': 'Not found'}), 404


@app.route('/api/update/live/<channel>')
def get_live_manifest(channel):
    """获取指定通道的 live manifest"""
    manifest = load_manifest(channel)
    if not manifest:
        return jsonify({'ok': False, 'error': 'Channel not found'}), 404
    return jsonify(manifest)


@app.route('/api/health')
def health():
    return jsonify({'ok': True, 'service': 'ttbox-update-server', 'version': '0.1.0'})


@app.route('/')
def index():
    return jsonify({
        'service': 'TTBOX Update Server',
        'version': '0.1.0',
        'endpoints': [
            'GET /api/health',
            'GET/POST /api/update/check',
            'GET /api/update/manifest/<product>/<version>/<channel>/manifest.json',
            'GET /api/update/package/<package_id>',
            'GET /api/update/release-notes/<version>',
            'GET /api/update/live/<channel>',
        ]
    })


if __name__ == '__main__':
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8081
    app.run(host='127.0.0.1', port=port, debug=False)