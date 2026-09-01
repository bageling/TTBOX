#!/usr/bin/env python3
"""
TTBOX Update Engine - 设备端更新引擎
独立运行，与 TTBOX Core 解耦。

运行方式:
  python3 update_engine.py [--port PORT]

systemd: ttbox-update.service
"""

import os, sys, json, hashlib, base64, time, shutil, tarfile, socket, logging, argparse
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

# ── 常量 ──
VERSION = '0.1.0'

# 文件系统路径
ETC_DIR = '/etc/ttbox'
VAR_DIR = '/var/lib/ttbox'
UPDATE_DIR = '/var/lib/ttbox/update'
STAGING_DIR = '/var/lib/ttbox/update/staging'
BACKUP_DIR = '/var/lib/ttbox/update/backup'
DOWNLOADS_DIR = '/var/lib/ttbox/update/downloads'
KEYS_DIR = '/var/lib/ttbox/update/trusted_keys'
RUN_DIR = '/run/ttbox'
STATE_FILE = '/var/lib/ttbox/update/update_state.json'
LOCK_FILE = '/run/ttbox/update.lock'
LOG_DIR = '/var/log/ttbox'
LOG_FILE = '/var/log/ttbox/update.log'

# 默认更新服务器
DEFAULT_SERVER = 'http://127.0.0.1:8081'

# ── 状态机 ──
STATES = [
    'IDLE', 'CHECKING', 'DOWNLOADING', 'VERIFYING', 'STAGING',
    'READY', 'APPLYING', 'HEALTH_CHECK', 'COMMITTED',
    'FAILED', 'ROLLING_BACK', 'ROLLED_BACK'
]

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE) if os.path.exists(LOG_DIR) else logging.StreamHandler(),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('update-engine')


class UpdateEngine:
    """TTBOX Update Engine 核心"""

    def __init__(self, server_url=DEFAULT_SERVER):
        self.server_url = server_url
        self.state = self._load_state()
        self._ensure_dirs()

    def _ensure_dirs(self):
        """确保目录存在"""
        for d in [ETC_DIR, VAR_DIR, UPDATE_DIR, STAGING_DIR, BACKUP_DIR,
                  DOWNLOADS_DIR, KEYS_DIR, RUN_DIR, LOG_DIR]:
            os.makedirs(d, exist_ok=True)

    def _load_state(self):
        """加载持久化状态"""
        if os.path.exists(STATE_FILE):
            try:
                with open(STATE_FILE) as f:
                    return json.load(f)
            except Exception as e:
                log.error(f'Failed to load state: {e}')
        return {
            'state': 'IDLE',
            'current_version': '0.0.0',
            'previous_version': '',
            'last_update_time': '',
            'attempted_version': '',
            'attempted_channel': '',
            'error_count': 0,
            'last_error': '',
            'rollback_available': False,
            'backup_path': ''
        }

    def _save_state(self):
        """持久化状态"""
        with open(STATE_FILE, 'w') as f:
            json.dump(self.state, f, indent=2)

    def _set_state(self, new_state):
        """设置状态并持久化"""
        log.info(f'State: {self.state["state"]} → {new_state}')
        self.state['state'] = new_state
        self._save_state()

    def _acquire_lock(self):
        """获取更新锁"""
        try:
            self.lock_fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(self.lock_fd, str(os.getpid()).encode())
            return True
        except FileExistsError:
            log.error('Update lock already held')
            return False

    def _release_lock(self):
        """释放更新锁"""
        if hasattr(self, 'lock_fd'):
            os.close(self.lock_fd)
            if os.path.exists(LOCK_FILE):
                os.remove(LOCK_FILE)

    def _http_get(self, url):
        """HTTP GET 请求（内置 socket 实现，无外部依赖）"""
        import urllib.request
        req = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read(), resp.status

    def _http_post_json(self, url, data):
        """HTTP POST JSON 请求"""
        import urllib.request
        body = json.dumps(data).encode()
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode()), resp.status

    def _load_public_key(self):
        """加载信任的公钥"""
        for f in os.listdir(KEYS_DIR):
            if f.endswith('.pub'):
                with open(os.path.join(KEYS_DIR, f)) as kf:
                    return kf.read()
        # 也检查 /etc/ttbox/
        key_path = os.path.join(ETC_DIR, 'release-public.pem')
        if os.path.exists(key_path):
            with open(key_path) as f:
                return f.read()
        log.error('No trusted public key found')
        return None

    def _verify_signature(self, sha256_hash, signature_b64):
        """验证 Ed25519 签名"""
        pem = self._load_public_key()
        if not pem:
            return False
        try:
            public_key = serialization.load_pem_public_key(pem.encode())
            signature = base64.b64decode(signature_b64)
            public_key.verify(signature, sha256_hash.encode())
            return True
        except Exception as e:
            log.error(f'Signature verification failed: {e}')
            return False

    def _check_disk_space(self, required_bytes):
        """检查磁盘空间"""
        import shutil
        usage = shutil.disk_usage(UPDATE_DIR)
        free_mb = usage.free / (1024 * 1024)
        required_mb = required_bytes * 2 / (1024 * 1024)  # 下载+备份
        return free_mb >= required_mb

    # ─── 公开 API ───

    def get_status(self):
        """获取更新状态"""
        return {
            'ok': True,
            'data': {
                'state': self.state['state'],
                'current_version': self.state['current_version'],
                'previous_version': self.state['previous_version'],
                'last_update_time': self.state['last_update_time'],
                'attempted_version': self.state['attempted_version'],
                'error_count': self.state['error_count'],
                'last_error': self.state['last_error'],
                'rollback_available': self.state['rollback_available']
            }
        }

    def check_update(self):
        """检查 OTA 更新"""
        if self.state['state'] not in ['IDLE', 'FAILED', 'ROLLED_BACK']:
            return {'ok': False, 'error': 'Update in progress'}

        self._set_state('CHECKING')
        try:
            url = f'{self.server_url}/api/update/check'
            body = json.dumps({
                'product': 'TTBOX',
                'current_version': self.state['current_version'],
                'hardware': 'rk3588',
                'channel': 'stable',
                'components': ['core', 'web', 'gateway']
            }).encode()
            import urllib.request
            req = urllib.request.Request(url, data=body, method='POST')
            req.add_header('Content-Type', 'application/json')
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode())

            if not result.get('ok'):
                self._set_state('IDLE')
                return result

            data = result.get('data', {})
            if data.get('update_available'):
                self.state['attempted_version'] = data['latest_version']
                self.state['attempted_channel'] = data.get('channel', 'stable')
                self._save_state()
                self._set_state('IDLE')
                return {
                    'ok': True,
                    'data': {
                        'update_available': True,
                        'latest_version': data['latest_version'],
                        'release_date': data.get('release_date', ''),
                        'release_notes_url': data.get('release_notes_url', ''),
                        'manifest_url': data.get('manifest_url', '')
                    }
                }
            else:
                self._set_state('IDLE')
                return {
                    'ok': True,
                    'data': {'update_available': False, 'message': 'Already up to date'}
                }

        except Exception as e:
            log.error(f'Check update failed: {e}')
            self.state['last_error'] = str(e)
            self.state['error_count'] += 1
            self._save_state()
            self._set_state('IDLE')
            return {'ok': False, 'error': str(e)}

    def download_update(self, version=None):
        """下载更新包"""
        version = version or self.state['attempted_version']
        if not version:
            return {'ok': False, 'error': 'No version specified'}

        self._set_state('DOWNLOADING')

        try:
            # 获取 manifest
            manifest_url = f'{self.server_url}/api/update/manifest/TTBOX/{version}/stable/manifest.json'
            manifest_data, status = self._http_get(manifest_url)
            manifest = json.loads(manifest_data.decode())

            # 验证 manifest 签名
            manifest_sig = manifest.get('signature', '')
            manifest_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
            if not self._verify_signature(manifest_hash, manifest_sig):
                self._set_state('FAILED')
                self.state['last_error'] = 'Manifest signature verification failed'
                self._save_state()
                return {'ok': False, 'error': 'Manifest signature verification failed'}

            # 检查磁盘空间
            total_size = sum(p.get('size', 0) for p in manifest.get('packages', []))
            if not self._check_disk_space(total_size):
                self._set_state('FAILED')
                self.state['last_error'] = 'Insufficient disk space'
                self._save_state()
                return {'ok': False, 'error': 'Insufficient disk space'}

            # 下载每个包
            downloaded = []
            for pkg in manifest.get('packages', []):
                pkg_url = f'{self.server_url}{pkg["url"]}'
                pkg_path = os.path.join(DOWNLOADS_DIR, pkg['package_id'])

                log.info(f'Downloading: {pkg["package_id"]}')
                data, status = self._http_get(pkg_url)

                with open(pkg_path, 'wb') as f:
                    f.write(data)

                # 验证 SHA256
                actual_hash = hashlib.sha256(data).hexdigest()
                if actual_hash != pkg['sha256']:
                    os.remove(pkg_path)
                    self._set_state('FAILED')
                    self.state['last_error'] = f'SHA256 mismatch for {pkg["package_id"]}'
                    self._save_state()
                    return {'ok': False, 'error': 'SHA256 mismatch'}

                downloaded.append(pkg_path)

            self.state['attempted_version'] = version
            self._save_state()
            self._set_state('VERIFYING')

            return {
                'ok': True,
                'data': {
                    'version': version,
                    'packages': len(downloaded),
                    'total_size': total_size
                }
            }

        except Exception as e:
            log.error(f'Download failed: {e}')
            self.state['last_error'] = str(e)
            self.state['error_count'] += 1
            self._save_state()
            self._set_state('FAILED')
            return {'ok': False, 'error': str(e)}

    def stage_update(self):
        """准备更新（staging）"""
        self._set_state('STAGING')

        try:
            version = self.state['attempted_version']
            staging_dir = os.path.join(STAGING_DIR, f'v{version}')

            # 清理旧 staging
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)

            # 解压包
            for f in os.listdir(DOWNLOADS_DIR):
                if f.endswith('.tar.gz'):
                    pkg_path = os.path.join(DOWNLOADS_DIR, f)
                    with tarfile.open(pkg_path, 'r:gz') as tar:
                        tar.extractall(path=staging_dir)

            log.info(f'Staged to: {staging_dir}')
            self._set_state('READY')
            return {'ok': True, 'data': {'staging_dir': staging_dir}}

        except Exception as e:
            log.error(f'Stage failed: {e}')
            self.state['last_error'] = str(e)
            self._save_state()
            self._set_state('FAILED')
            return {'ok': False, 'error': str(e)}

    def apply_update(self):
        """应用更新"""
        if not self._acquire_lock():
            return {'ok': False, 'error': 'Cannot acquire lock'}

        try:
            self._set_state('APPLYING')
            version = self.state['attempted_version']
            staging_dir = os.path.join(STAGING_DIR, f'v{version}')

            if not os.path.exists(staging_dir):
                self._release_lock()
                self._set_state('FAILED')
                return {'ok': False, 'error': 'Staging dir not found'}

            # 创建备份
            backup_dir = os.path.join(BACKUP_DIR, f'v{self.state["current_version"]}')
            os.makedirs(backup_dir, exist_ok=True)

            # 备份当前版本
            backup_paths = [
                '/usr/local/bin/ttbox-core',
                '/usr/local/bin/ttbox-web',
                '/etc/ttbox/ttbox.conf',
            ]
            for src in backup_paths:
                if os.path.exists(src):
                    dst = os.path.join(backup_dir, os.path.relpath(src, '/'))
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

            self.state['backup_path'] = backup_dir
            self.state['previous_version'] = self.state['current_version']
            self.state['rollback_available'] = True
            self._save_state()

            # 应用新文件
            files_dir = os.path.join(staging_dir, f'TTBOX-core-{version}-rk3588', 'files')
            if os.path.exists(files_dir):
                for root, dirs, files in os.walk(files_dir):
                    for f in files:
                        src = os.path.join(root, f)
                        dst = os.path.join('/', os.path.relpath(root, files_dir), f)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        log.info(f'Installed: {dst}')

            # 更新版本
            self.state['current_version'] = version
            self.state['last_update_time'] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            self._save_state()

            self._set_state('HEALTH_CHECK')

            # 健康检查
            health_ok = self._health_check()
            if health_ok:
                self._set_state('COMMITTED')
                self._release_lock()
                return {'ok': True, 'data': {'version': version, 'status': 'committed'}}
            else:
                log.warning('Health check failed, rolling back')
                self.rollback()
                self._release_lock()
                return {'ok': False, 'error': 'Health check failed, rolled back'}

        except Exception as e:
            log.error(f'Apply failed: {e}')
            self.state['last_error'] = str(e)
            self._save_state()
            self._release_lock()
            self.rollback()
            return {'ok': False, 'error': str(e)}

    def _health_check(self):
        """健康检查"""
        import subprocess
        checks = [
            ('ttbox-core', '/usr/local/bin/ttbox-core'),
            ('ttbox-web', '/usr/local/bin/ttbox-web'),
        ]

        for name, path in checks:
            if not os.path.exists(path):
                log.warning(f'Health check failed: {name} not found at {path}')
                return False
            # 检查文件是否可执行
            if not os.access(path, os.X_OK):
                log.warning(f'Health check failed: {name} not executable')
                return False

        log.info('Health check passed')
        return True

    def rollback(self):
        """回滚到上一版本"""
        if not self.state['rollback_available']:
            return {'ok': False, 'error': 'No rollback available'}

        self._set_state('ROLLING_BACK')
        backup_dir = self.state['backup_path']

        if not os.path.exists(backup_dir):
            self._set_state('FAILED')
            return {'ok': False, 'error': 'Backup not found'}

        try:
            # 恢复备份
            for root, dirs, files in os.walk(backup_dir):
                for f in files:
                    src = os.path.join(root, f)
                    dst = os.path.join('/', os.path.relpath(root, backup_dir), f)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)

            # 恢复版本
            self.state['current_version'] = self.state['previous_version']
            self.state['previous_version'] = ''
            self.state['rollback_available'] = False
            self.state['backup_path'] = ''
            self._save_state()

            self._set_state('ROLLED_BACK')
            log.info('Rollback completed')
            return {'ok': True, 'data': {'version': self.state['current_version']}}

        except Exception as e:
            log.error(f'Rollback failed: {e}')
            self.state['last_error'] = str(e)
            self._save_state()
            self._set_state('FAILED')
            return {'ok': False, 'error': str(e)}

    def start_update(self, version=None):
        """完整更新流程（检查 + 下载 + 验证 + 应用）"""
        # 1. 检查
        check_result = self.check_update()
        if not check_result.get('ok'):
            return check_result
        if not check_result.get('data', {}).get('update_available'):
            return {'ok': True, 'data': {'message': 'Already up to date'}}

        # 2. 下载
        dl_result = self.download_update(version)
        if not dl_result.get('ok'):
            return dl_result

        # 3. Stage
        stage_result = self.stage_update()
        if not stage_result.get('ok'):
            return stage_result

        # 4. Apply
        apply_result = self.apply_update()
        return apply_result


# ─── IPC Server（Unix Socket） ───

class UpdateIpcServer:
    """Update Engine IPC 服务器"""

    def __init__(self, engine, socket_path='/var/run/ttbox/update.sock'):
        self.engine = engine
        self.socket_path = socket_path

    def start(self):
        """启动 IPC 服务器"""
        import socketserver

        os.makedirs(os.path.dirname(self.socket_path), exist_ok=True)
        if os.path.exists(self.socket_path):
            os.remove(self.socket_path)

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    data = self.rfile.readline()
                    if not data:
                        return
                    request = json.loads(data.decode())
                    cmd = request.get('type', '')
                    params = request.get('params', {})

                    response = self._dispatch(cmd, params)
                    self.wfile.write((json.dumps(response) + '\n').encode())
                except Exception as e:
                    error_resp = {'type': 'ERROR', 'status': -1, 'error': str(e)}
                    self.wfile.write((json.dumps(error_resp) + '\n').encode())

            def _dispatch(self, cmd, params):
                handlers = {
                    'GET_STATUS': lambda: engine.get_status()['data'],
                    'CHECK_UPDATE': lambda: engine.check_update(),
                    'DOWNLOAD_UPDATE': lambda: engine.download_update(params.get('version')),
                    'STAGE_UPDATE': lambda: engine.stage_update(),
                    'APPLY_UPDATE': lambda: engine.apply_update(),
                    'START_UPDATE': lambda: engine.start_update(params.get('version')),
                    'ROLLBACK': lambda: engine.rollback(),
                }

                handler = handlers.get(cmd)
                if not handler:
                    return {'type': 'ERROR', 'status': -1, 'error': f'Unknown command: {cmd}'}

                result = handler()
                return {'type': cmd, 'status': 0 if result.get('ok') else 1, 'data': result.get('data', {}), 'error': result.get('error', '')}

        server = socketserver.UnixStreamServer(self.socket_path, Handler)
        log.info(f'IPC server listening on {self.socket_path}')
        server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='TTBOX Update Engine')
    parser.add_argument('--server', default=DEFAULT_SERVER, help='Update server URL')
    parser.add_argument('--mode', choices=['standalone', 'ipc'], default='standalone',
                       help='Run mode: standalone (one-shot) or ipc (socket server)')
    parser.add_argument('--action', choices=['check', 'download', 'stage', 'apply', 'start', 'rollback', 'status'],
                       default='status', help='Action for standalone mode')
    parser.add_argument('--version', help='Target version for download/apply')
    args = parser.parse_args()

    engine = UpdateEngine(server_url=args.server)

    if args.mode == 'ipc':
        server = UpdateIpcServer(engine)
        server.start()
    else:
        actions = {
            'status': lambda: print(json.dumps(engine.get_status(), indent=2, ensure_ascii=False)),
            'check': lambda: print(json.dumps(engine.check_update(), indent=2, ensure_ascii=False)),
            'download': lambda: print(json.dumps(engine.download_update(args.version), indent=2, ensure_ascii=False)),
            'stage': lambda: print(json.dumps(engine.stage_update(), indent=2, ensure_ascii=False)),
            'apply': lambda: print(json.dumps(engine.apply_update(), indent=2, ensure_ascii=False)),
            'start': lambda: print(json.dumps(engine.start_update(args.version), indent=2, ensure_ascii=False)),
            'rollback': lambda: print(json.dumps(engine.rollback(), indent=2, ensure_ascii=False)),
        }
        actions.get(args.action, lambda: print('Unknown action'))()
