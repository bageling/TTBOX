#!/usr/bin/env python3
"""ttbox-release：发布机 CLI。签名只在本机执行，服务器仅接收签名后的清单。"""
import argparse, base64, hashlib, json, os, urllib.request, urllib.error, uuid
from pathlib import Path

def req(url, method='GET', data=None, token=None, files=None):
    headers={'Content-Type':'application/json'}
    if token: headers['Authorization']='Bearer '+token
    body=json.dumps(data).encode() if data is not None else None
    r=urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(r, timeout=30) as x: return json.load(x)

def sign_hashes(paths, private_key):
    from cryptography.hazmat.primitives import serialization
    key=serialization.load_pem_private_key(Path(private_key).read_bytes(), password=None)
    out=[]
    for x in paths:
        digest=hashlib.sha256(Path(x).read_bytes()).hexdigest()
        out.append(base64.b64encode(key.sign(digest.encode())).decode())
    return out

def upload(url, token, package, component, version, hardware):
    b='----TTBOX'+uuid.uuid4().hex; chunks=[]
    for k,v in [('component',component),('product','TTBOX'),('version',version),('hardware',hardware)]:
        chunks.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    p=Path(package); chunks.append(f'--{b}\r\nContent-Disposition: form-data; name="package"; filename="{p.name}"\r\nContent-Type: application/gzip\r\n\r\n'.encode()+p.read_bytes()+b'\r\n'); chunks.append(f'--{b}--\r\n'.encode())
    r=urllib.request.Request(url,data=b''.join(chunks),headers={'Authorization':'Bearer '+token,'Content-Type':f'multipart/form-data; boundary={b}'},method='POST')
    with urllib.request.urlopen(r,timeout=60) as x:return json.load(x)

def main():
    p=argparse.ArgumentParser(prog='ttbox-release'); p.add_argument('--server',default=os.environ.get('TTBOX_RELEASE_SERVER','http://127.0.0.1:8090')); p.add_argument('--token',default=os.environ.get('TTBOX_RELEASE_TOKEN',''))
    s=p.add_subparsers(dest='cmd',required=True)
    for n in ('list','create','test','publish','revoke','devices','sign'): s.add_parser(n)
    c=s.choices['create']; c.add_argument('--version',required=True); c.add_argument('--build',required=True); c.add_argument('--channel',default='beta'); c.add_argument('--hardware',default='rk3588'); c.add_argument('--notes',default='')
    for n in ('test','publish','revoke'): s.choices[n].add_argument('id',type=int)
    s.choices['sign'].add_argument('--private-key',required=True); s.choices['sign'].add_argument('package',nargs='+')
    s.choices['create'].add_argument('--id',type=int)
    s.add_parser('upload').add_argument('id',type=int)
    s.choices['upload'].add_argument('--package',required=True); s.choices['upload'].add_argument('--component',default='core'); s.choices['upload'].add_argument('--version',required=True); s.choices['upload'].add_argument('--hardware',default='rk3588')
    s.choices['publish'].add_argument('--signature',required=True); s.choices['publish'].add_argument('--key-id',default='release-machine')
    a=p.parse_args(); base=a.server.rstrip('/'); h={'Authorization':'Bearer '+a.token,'Content-Type':'application/json'}
    if a.cmd=='sign': print(json.dumps(sign_hashes(a.package,a.private_key),ensure_ascii=False)); return
    if a.cmd=='upload': print(json.dumps(upload(f'{base}/api/releases/{a.id}/packages',a.token,a.package,a.component,a.version,a.hardware),ensure_ascii=False,indent=2)); return
    if a.cmd=='list': out=req(base+'/api/releases',token=a.token)
    elif a.cmd=='devices': out=req(base+'/api/devices',token=a.token)
    elif a.cmd=='create': out=req(base+'/api/releases','POST',{'version':a.version,'build':a.build,'channel':a.channel,'hardware':a.hardware,'release_notes':a.notes},a.token)
    elif a.cmd=='test': out=req(f'{base}/api/releases/{a.id}/test','POST',{},a.token)
    elif a.cmd=='revoke': out=req(f'{base}/api/releases/{a.id}/revoke','POST',{},a.token)
    else:
        signatures=json.loads(a.signature) if isinstance(a.signature,str) else a.signature
        out=req(f'{base}/api/releases/{a.id}/publish','POST',{'signatures':signatures,'signing_key_id':a.key_id},a.token)
    print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
