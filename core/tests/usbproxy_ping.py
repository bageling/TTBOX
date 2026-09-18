import socket,struct,time,os,sys
# mouse cmd.sock 默认单点真源（A-PATH-5）：复用同仓 plugins/web/lib/paths.py。
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "plugins", "web"))
from lib.paths import MOUSE_CMD_SOCK_DEFAULT as _CMD
def rpc(typ,rid,payload=b""):
    s=socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET)
    s.connect(_CMD)
    s.settimeout(3)
    hdr=struct.pack("<HBBI",0x4F50,1,typ,rid)
    s.sendall(hdr+payload)
    r=s.recv(256)
    s.close()
    return r
# PING (1) -> PING_RESP (2)
try:
    r=rpc(1,1)
    print("PING resp",len(r),"bytes:",r.hex())
except Exception as ex:
    print("PING fail",ex)
# GET_STATE (6) -> resp(7) 9B payload
try:
    r=rpc(6,2)
    print("GET_STATE resp",len(r),"bytes:",r.hex())
    if len(r)>=15:
        magic,ver,typ,rid=struct.unpack("<HBBI",r[:8])
        mask=struct.unpack("<B",r[8:9])[0]
        ts=struct.unpack("<q",r[9:17])[0]
        print("type=",typ,"mask=",mask,"ts_ns=",ts)
except Exception as ex:
    print("GET_STATE fail",ex)
