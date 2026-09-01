#!/usr/bin/env python3
"""TTBOX Release Management Server.

管理 API 与设备更新 API 分离：管理端使用 /api/admin，设备端使用 /api/update。
包的 SHA256 始终由服务端计算；发布清单由服务端从数据库生成，签名由发布机通过 CLI 完成后回传。
"""
from __future__ import annotations
import base64, hashlib, hmac, json, os, secrets, sqlite3, time, tarfile, zipfile, shutil
from datetime import datetime, timezone
from pathlib import Path
from functools import wraps
from flask import Flask, jsonify, request, send_file, make_response, render_template_string

ROOT = Path(os.environ.get('TTBOX_RELEASE_ROOT', Path(__file__).resolve().parent)).resolve()
DATA = Path(os.environ.get('TTBOX_RELEASE_DATA', '/var/lib/ttbox/release-manager'))
STORAGE = Path(os.environ.get('TTBOX_RELEASE_STORAGE', '/srv/ttbox'))
DB = DATA / 'releases.db'
SECRET_FILE = DATA / 'jwt.secret'
PUBLIC_KEY = Path(os.environ.get('TTBOX_RELEASE_PUBLIC_KEY', str(STORAGE / 'releases/release-public.pem')))
PRODUCT = 'TTBOX'
CHANNELS = {'stable', 'beta', 'developer'}
STATUSES = {'DRAFT', 'TESTING', 'PUBLISHED', 'REVOKED'}

app = Flask(__name__)


def now(): return datetime.now(timezone.utc).isoformat()
def ensure_dirs():
    DATA.mkdir(parents=True, exist_ok=True)
    for d in ('releases/stable', 'releases/beta', 'releases/developer', 'manifests', 'metadata'):
        (STORAGE / d).mkdir(parents=True, exist_ok=True)
    if not SECRET_FILE.exists(): SECRET_FILE.write_text(secrets.token_urlsafe(48), encoding='utf-8')

def db():
    ensure_dirs(); c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); return c

def init_db():
    c = db()
    c.executescript('''
    CREATE TABLE IF NOT EXISTS admins (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'ADMIN', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS releases (id INTEGER PRIMARY KEY, product TEXT NOT NULL, version TEXT NOT NULL, build TEXT NOT NULL, channel TEXT NOT NULL, status TEXT NOT NULL, hardware TEXT NOT NULL, release_notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, published_at TEXT, manifest_json TEXT, signature TEXT, signing_key_id TEXT, UNIQUE(product, version, channel));
    CREATE TABLE IF NOT EXISTS packages (id INTEGER PRIMARY KEY, release_id INTEGER NOT NULL REFERENCES releases(id) ON DELETE CASCADE, component TEXT NOT NULL, filename TEXT NOT NULL, product TEXT NOT NULL, version TEXT NOT NULL, hardware TEXT NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(release_id, component, filename));
    CREATE TABLE IF NOT EXISTS devices (id TEXT PRIMARY KEY, hardware TEXT NOT NULL, version TEXT NOT NULL, build TEXT NOT NULL, channel TEXT NOT NULL, online INTEGER NOT NULL DEFAULT 0, last_seen TEXT NOT NULL, update_status TEXT NOT NULL DEFAULT 'IDLE', update_request TEXT);
    CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, actor TEXT, action TEXT NOT NULL, resource TEXT, detail TEXT, created_at TEXT NOT NULL);
    ''')
    if not c.execute('SELECT 1 FROM admins LIMIT 1').fetchone():
        # 首次启动必须通过环境变量设置密码，不写入产品默认密码。
        password = os.environ.get('TTBOX_ADMIN_PASSWORD')
        if password:
            c.execute('INSERT INTO admins(username,password_hash,role,created_at) VALUES(?,?,?,?)', ('admin', hash_password(password), 'ADMIN', now()))
    c.commit(); c.close()

def hash_password(password):
    salt = secrets.token_bytes(16); value = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return 'scrypt$' + base64.urlsafe_b64encode(salt).decode() + '$' + base64.urlsafe_b64encode(value).decode()

def verify_password(password, encoded):
    try:
        _, s, v = encoded.split('$'); salt = base64.urlsafe_b64decode(s); expected = base64.urlsafe_b64decode(v)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
        return hmac.compare_digest(actual, expected)
    except Exception: return False

def token(user, role):
    body = base64.urlsafe_b64encode(json.dumps({'u': user, 'r': role, 'exp': int(time.time()) + 8*3600}, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(SECRET_FILE.read_bytes(), body.encode(), hashlib.sha256).digest()
    return body + '.' + base64.urlsafe_b64encode(sig).decode().rstrip('=')

def auth_user():
    raw = request.headers.get('Authorization', '').removeprefix('Bearer ').strip() or request.cookies.get('ttbox_session', '')
    try:
        body, sig = raw.split('.'); expected = hmac.new(SECRET_FILE.read_bytes(), body.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(base64.urlsafe_b64decode(sig + '=='), expected): return None
        data = json.loads(base64.urlsafe_b64decode(body + '=='))
        return data if data['exp'] >= time.time() else None
    except Exception: return None

def admin_required(fn):
    @wraps(fn)
    def wrapped(*a, **kw):
        user = auth_user()
        if not user: return jsonify(ok=False, error='管理员认证失败'), 401
        request.actor = user['u']; return fn(*a, **kw)
    return wrapped

def audit(action, resource='', detail=''):
    c = db(); c.execute('INSERT INTO audit_log(actor,action,resource,detail,created_at) VALUES(?,?,?,?,?)', (getattr(request, 'actor', 'device'), action, resource, detail, now())); c.commit(); c.close()

def row_release(row):
    if not row: return None
    d = dict(row); d['packages'] = [dict(x) for x in db().execute('SELECT component,filename,product,version,hardware,size,sha256 FROM packages WHERE release_id=?', (d['id'],)).fetchall()]
    if d.get('manifest_json'): d['manifest'] = json.loads(d['manifest_json'])
    d.pop('manifest_json', None); return d

def valid_release_payload(data):
    required = ['version', 'build', 'channel', 'hardware']
    if any(not str(data.get(x, '')).strip() for x in required): return 'version/build/channel/hardware 必填'
    if data.get('product', PRODUCT) != PRODUCT: return 'product 不匹配'
    if data['channel'] not in CHANNELS: return 'channel 无效'
    return None

def version_key(value):
    return tuple(int(x) if x.isdigit() else 0 for x in str(value).split('.')[:3])

def verify_package_signature(pkg_sha256, signature):
    """逐包验证 Ed25519 签名（与设备端引擎一致：signature 是对单个包 SHA256 的签名）。"""
    if not signature or not PUBLIC_KEY.exists(): return False
    try:
        from cryptography.hazmat.primitives import serialization
        key = serialization.load_pem_public_key(PUBLIC_KEY.read_bytes())
        key.verify(base64.b64decode(signature), pkg_sha256.encode())
        return True
    except Exception:
        return False


def verify_release_signature(packages, signatures):
    """signatures: list[str]，按 packages 顺序逐包验证。"""
    if not signatures or len(signatures) != len(packages): return False
    for pkg, sig in zip(packages, signatures):
        if not verify_package_signature(pkg['sha256'], sig): return False
    return True

@app.post('/api/admin/login')
def login():
    data = request.get_json(silent=True) or {}; c = db(); row = c.execute('SELECT * FROM admins WHERE username=?', (data.get('username',''),)).fetchone(); c.close()
    if not row or not verify_password(str(data.get('password','')), row['password_hash']): return jsonify(ok=False, error='账号或密码错误'), 401
    value = token(row['username'], row['role']); r = jsonify(ok=True, data={'role': row['role'], 'token': value}); r.set_cookie('ttbox_session', value, httponly=True, samesite='Strict', secure=False, max_age=28800); return r

@app.post('/api/admin/logout')
def logout():
    r = jsonify(ok=True); r.delete_cookie('ttbox_session'); return r

@app.get('/api/releases')
@admin_required
def releases():
    c=db(); rows=c.execute('SELECT * FROM releases ORDER BY id DESC').fetchall(); c.close(); return jsonify(ok=True, data=[row_release(x) for x in rows])

@app.post('/api/releases')
@admin_required
def create_release():
    data=request.get_json(silent=True) or {}; err=valid_release_payload(data)
    if err: return jsonify(ok=False,error=err),400
    c=db()
    try:
        c.execute('INSERT INTO releases(product,version,build,channel,status,hardware,release_notes,created_at) VALUES(?,?,?,?,?,?,?,?)', (PRODUCT,data['version'],data['build'],data['channel'],'DRAFT',data['hardware'],data.get('release_notes',''),now())); c.commit(); rid=c.execute('SELECT last_insert_rowid()').fetchone()[0]
    except sqlite3.IntegrityError as e: c.close(); return jsonify(ok=False,error=f'版本已存在: {e}'),409
    c.close(); audit('release.create',str(rid)); return jsonify(ok=True,data={'id':rid}),201

@app.get('/api/releases/<int:rid>')
@admin_required
def release_detail(rid):
    c=db(); row=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone(); c.close(); return (jsonify(ok=True,data=row_release(row)) if row else (jsonify(ok=False,error='Release 不存在'),404))

@app.put('/api/releases/<int:rid>')
@admin_required
def edit_release(rid):
    data=request.get_json(silent=True) or {}; c=db(); row=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone()
    if not row: c.close(); return jsonify(ok=False,error='Release 不存在'),404
    fields={k:data[k] for k in ('build','channel','hardware','release_notes') if k in data}
    if 'channel' in fields and fields['channel'] not in CHANNELS: c.close(); return jsonify(ok=False,error='channel 无效'),400
    if row['status'] in {'PUBLISHED','REVOKED'}: c.close(); return jsonify(ok=False,error='当前状态不允许编辑'),409
    if fields: c.execute('UPDATE releases SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',(*fields.values(),rid)); c.commit()
    c.close(); audit('release.edit',str(rid)); return release_detail(rid)

@app.post('/api/releases/<int:rid>/packages')
@admin_required
def upload_package(rid):
    c=db(); rel=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone(); c.close()
    if not rel: return jsonify(ok=False,error='Release 不存在'),404
    f=request.files.get('package'); meta=request.form
    if not f or not f.filename or not f.filename.endswith(('.tar.gz','.tgz','.zip')): return jsonify(ok=False,error='仅允许 tar.gz/tgz/zip'),400
    safe=Path(f.filename).name; tmp=DATA/'incoming'; tmp.mkdir(exist_ok=True); path=tmp/safe; f.save(path)
    raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest(); product=meta.get('product',PRODUCT); version=meta.get('version',rel['version']); hardware=meta.get('hardware',rel['hardware']); component=meta.get('component',Path(safe).stem)
    # 包必须自带 package.json；表单字段只用于辅助定位，服务器以包内元数据为准。
    try:
        with tarfile.open(path, 'r:*') as archive:
            candidates=[x for x in archive.getnames() if Path(x).name == 'package.json']
            if not candidates: raise ValueError('缺少 package.json')
            package=json.load(archive.extractfile(candidates[0]))
    except (tarfile.TarError, ValueError, json.JSONDecodeError, KeyError) as exc:
        path.unlink(missing_ok=True); return jsonify(ok=False,error=f'package.json 校验失败: {exc}'),400
    product=str(package.get('product','')); version=str(package.get('version','')); hardware=str(package.get('hardware','')); component=str(package.get('component',component))
    if product != PRODUCT or version != rel['version'] or hardware != rel['hardware']: path.unlink(missing_ok=True); return jsonify(ok=False,error='package 元数据与 Release 不匹配'),400
    dst=STORAGE/'releases'/rel['channel']/str(rid)/safe; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(path,dst); path.unlink(missing_ok=True)
    c=db(); c.execute('INSERT OR REPLACE INTO packages(release_id,component,filename,product,version,hardware,size,sha256,path,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(rid,component,safe,product,version,hardware,len(raw),sha,str(dst),now())); c.commit(); c.close(); audit('package.upload',str(rid),safe); return jsonify(ok=True,data={'filename':safe,'size':len(raw),'sha256':sha}),201

@app.get('/api/releases/<int:rid>/packages')
@admin_required
def packages(rid):
    c=db(); rows=c.execute('SELECT component,filename,product,version,hardware,size,sha256,created_at FROM packages WHERE release_id=?',(rid,)).fetchall(); c.close(); return jsonify(ok=True,data=[dict(x) for x in rows])

def build_manifest(rel, packs, signatures=None):
    signatures = signatures or [''] * len(packs)
    return {'product':rel['product'],'version':rel['version'],'build':rel['build'],'channel':rel['channel'],'target_hardware':[rel['hardware']], 'release_date':rel['created_at'],'release_notes':rel['release_notes'],'packages':[{'package_id':p['filename'],'component':p['component'],'version':p['version'],'target':'system','hardware':[p['hardware']],'size':p['size'],'sha256':p['sha256'],'signature':signatures[i] if i < len(signatures) else '', 'url':f"/api/update/package/{p['filename']}",'required':True} for i,p in enumerate(packs)]}

@app.post('/api/releases/<int:rid>/test')
@admin_required
def test_release(rid):
    c=db(); rel=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone(); packs=c.execute('SELECT * FROM packages WHERE release_id=?',(rid,)).fetchall()
    if not rel: c.close(); return jsonify(ok=False,error='Release 不存在'),404
    if not packs: c.close(); return jsonify(ok=False,error='至少上传一个 package'),400
    manifest=build_manifest(rel,packs); c.execute('UPDATE releases SET status=?,manifest_json=? WHERE id=?',('TESTING',json.dumps(manifest,ensure_ascii=False),rid)); c.commit(); c.close(); audit('release.test',str(rid)); return jsonify(ok=True,data=manifest)

@app.post('/api/releases/<int:rid>/publish')
@admin_required
def publish_release(rid):
    c=db(); rel=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone();
    if not rel: c.close(); return jsonify(ok=False,error='Release 不存在'),404
    if rel['status'] not in {'TESTING','PUBLISHED'}: c.close(); return jsonify(ok=False,error='Release 必须先进入 TESTING'),409
    data=request.get_json(silent=True) or {}; target_channel=data.get('channel',rel['channel'])
    if target_channel not in CHANNELS: c.close(); return jsonify(ok=False,error='channel 无效'),400
    signatures=data.get('signatures',[]); key_id=data.get('signing_key_id',rel['signing_key_id'])
    packs=c.execute('SELECT * FROM packages WHERE release_id=? ORDER BY id',(rid,)).fetchall()
    if not signatures: c.close(); return jsonify(ok=False,error='发布前必须提交逐包签名数组'),400
    if not verify_release_signature(packs, signatures): c.close(); return jsonify(ok=False,error='发布机签名校验失败'),400
    c.execute('UPDATE releases SET status=?,channel=?,signature=?,signing_key_id=?,published_at=? WHERE id=?',('PUBLISHED',target_channel,signatures[0],key_id,now(),rid)); c.commit(); row=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone(); c.close();
    manifest=build_manifest(row,packs,signatures); manifest['signature']=signatures[0]; manifest['signing_key_id']=key_id
    c=db(); c.execute('UPDATE releases SET manifest_json=? WHERE id=?',(json.dumps(manifest,ensure_ascii=False),rid)); c.commit(); c.close()
    out=STORAGE/'manifests'/f"{row['channel']}.json"; out.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8'); audit('release.publish',str(rid),row['channel']); return jsonify(ok=True,data=manifest)

@app.post('/api/releases/<int:rid>/revoke')
@admin_required
def revoke_release(rid):
    c=db(); row=c.execute('SELECT * FROM releases WHERE id=?',(rid,)).fetchone();
    if not row: c.close(); return jsonify(ok=False,error='Release 不存在'),404
    c.execute('UPDATE releases SET status=? WHERE id=?',('REVOKED',rid)); c.commit(); c.close();
    p=STORAGE/'manifests'/f'{row["channel"]}.json';
    if p.exists(): p.unlink()
    audit('release.revoke',str(rid),row['channel']); return jsonify(ok=True,data={'status':'REVOKED'})

@app.get('/api/devices')
@admin_required
def devices():
    c=db(); rows=c.execute('SELECT * FROM devices ORDER BY last_seen DESC').fetchall(); c.close(); return jsonify(ok=True,data=[dict(x) for x in rows])

@app.get('/api/devices/<device_id>')
@admin_required
def device(device_id):
    c=db(); row=c.execute('SELECT * FROM devices WHERE id=?',(device_id,)).fetchone(); c.close(); return (jsonify(ok=True,data=dict(row)) if row else (jsonify(ok=False,error='设备不存在'),404))

@app.post('/api/devices/<device_id>/update')
@admin_required
def request_update(device_id):
    data=request.get_json(silent=True) or {}; c=db(); row=c.execute('SELECT 1 FROM devices WHERE id=?',(device_id,)).fetchone()
    if not row: c.close(); return jsonify(ok=False,error='设备不存在'),404
    c.execute('UPDATE devices SET update_status=?,update_request=? WHERE id=?',('REQUESTED',json.dumps(data),device_id)); c.commit(); c.close(); audit('device.update',device_id); return jsonify(ok=True,data={'status':'REQUESTED'})

# 设备身份沿用心跳上报，不建立第二套身份。
@app.post('/api/device/heartbeat')
def heartbeat():
    data=request.get_json(silent=True) or {}; required=['device_id','hardware','version','build','channel']
    if any(not data.get(x) for x in required): return jsonify(ok=False,error='设备字段不完整'),400
    c=db(); c.execute('INSERT INTO devices(id,hardware,version,build,channel,online,last_seen,update_status) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET hardware=excluded.hardware,version=excluded.version,build=excluded.build,channel=excluded.channel,online=1,last_seen=excluded.last_seen,update_status=excluded.update_status',(data['device_id'],data['hardware'],data['version'],data['build'],data['channel'],1,now(),data.get('update_status','IDLE'))); c.commit(); c.close(); return jsonify(ok=True,data={'accepted':True})

# 设备端协议：只读取已发布的通道清单。
@app.post('/api/update/check')
@app.get('/api/update/check')
def device_check():
    data=request.get_json(silent=True) or request.args.to_dict(); channel=data.get('channel','stable'); c=db(); row=c.execute("SELECT * FROM releases WHERE channel=? AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(channel,)).fetchone(); c.close()
    if not row: return jsonify(ok=False,error='channel 无已发布版本'),404
    manifest=json.loads(row['manifest_json']); compatible=manifest['target_hardware'][0].lower() == str(data.get('hardware','rk3588')).lower(); available=version_key(manifest['version']) > version_key(data.get('current_version','0.0.0'))
    return jsonify(ok=True,data={'update_available':available and compatible,'latest_version':manifest['version'],'channel':channel,'release_date':manifest['release_date'],'release_notes':manifest['release_notes'],'hardware_compatible':compatible,'manifest_url':f"/api/update/manifest/{PRODUCT}/{manifest['version']}/{channel}/manifest.json"})

@app.get('/api/update/manifest/<product>/<version>/<channel>/manifest.json')
def device_manifest(product,version,channel):
    if product != PRODUCT: return jsonify(ok=False,error='product 不匹配'),404
    c=db(); row=c.execute("SELECT * FROM releases WHERE product=? AND version=? AND channel=? AND status='PUBLISHED'",(product,version,channel)).fetchone(); c.close()
    if not row: return jsonify(ok=False,error='manifest 不存在'),404
    return jsonify(json.loads(row['manifest_json']))

@app.get('/api/update/package/<filename>')
def device_package(filename):
    safe=Path(filename).name; c=db(); row=c.execute("SELECT path FROM packages p JOIN releases r ON p.release_id=r.id WHERE p.filename=? AND r.status='PUBLISHED'",(safe,)).fetchone(); c.close()
    if not row or not Path(row['path']).exists(): return jsonify(ok=False,error='package 不存在'),404
    return send_file(row['path'],as_attachment=True,download_name=safe)

@app.get('/api/update/live/<channel>')
def device_live(channel):
    c=db(); row=c.execute("SELECT manifest_json FROM releases WHERE channel=? AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(channel,)).fetchone(); c.close()
    if not row: return jsonify(ok=False,error='channel 无已发布版本'),404
    return jsonify(json.loads(row['manifest_json']))

@app.get('/api/update/release-notes/<version>')
def device_release_notes(version):
    c=db(); row=c.execute("SELECT release_notes FROM releases WHERE version=? AND status='PUBLISHED' ORDER BY id DESC LIMIT 1",(version,)).fetchone(); c.close()
    if not row: return jsonify(ok=False,error='Release 不存在'),404
    return make_response(row['release_notes'],200,{'Content-Type':'text/markdown; charset=utf-8'})

@app.get('/api/health')
def health(): return jsonify(ok=True,service='ttbox-release-manager',storage=str(STORAGE),database=str(DB))

ADMIN_HTML='''<!doctype html><meta charset="utf-8"><title>TTBOX Release Center</title><style>body{font:14px system-ui;background:#10141c;color:#e7edf7;margin:40px}button{padding:8px;margin:4px;background:#2d6cdf;color:white;border:0;border-radius:4px}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #394354;padding:9px;text-align:left}</style><h1>TTBOX Release Center</h1><p>发布管理、通道与设备</p><div id="out">请使用管理 API 登录后操作。</div><script>async function load(){let r=await fetch('/api/releases');let j=await r.json();document.querySelector('#out').innerHTML='<table><tr><th>ID</th><th>版本</th><th>Build</th><th>Channel</th><th>状态</th></tr>'+((j.data||[]).map(x=>`<tr><td>${x.id}</td><td>${x.version}</td><td>${x.build}</td><td>${x.channel}</td><td>${x.status}</td></tr>`).join(''))+'</table>'};load()</script>'''
@app.get('/admin')
def admin_page(): return render_template_string(ADMIN_HTML)

if __name__ == '__main__':
    init_db(); app.run(host=os.environ.get('TTBOX_RELEASE_HOST','127.0.0.1'), port=int(os.environ.get('TTBOX_RELEASE_PORT','8090')), debug=False)
else: init_db()
