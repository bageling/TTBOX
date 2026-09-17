#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ttbox_m2_license_accept.py — M2 B 系列板端验收驱动（在板端 root 下运行）。

覆盖（M2.01/M2.02 既有 + M2.03/M2.04/M2.05 本批新增）：
  · B0 无卡基线 fail-closed
  · B1 无签/坏签拒 · B2 他板卡拒 · B3 过期卡拒 · B8 ui_brand 篡改拒
  · B4 激活翻绿 · B9 一卡一设备 · B5 重启保持
  · B6 受限卡投影（features=[capture]）→ ★ M2.03 起含 capabilities 能力位投影；
    子断言（主理人裁定：预览降级与 ota 端点门控不发新号，均属 B6 的能力门控半边）：
      B6·ota  ota 端点门控（受限卡 POST /api/ota/install ⇒ 403 且不调度）〔需鉴权〕
      B6·预览 受限预览降级（watermark + fps 封顶；直连 IPC 读真 · 指标）
  · B7 重开后投影恢复（含 M2.03 能力位四页全开）
  · B10 M2.04 换皮投影（sample 卡 → ui_brand/brand_accent/brand_logo/theme）
  · B11 M2.05 卡号短码投影（card-valid → "TTB-006Z-0C33"，跨语言向量）
  · B12 F2 拒绝原因非空（验签级 + 解析级两路，message 不得为空/"card not set"）
  · B13 F3 激活端点独立限速（31 次失败 ⇒ 429）〔需鉴权〕
  · B14 F4 防降级拒绝（更旧 issued_at ⇒ 400 + message 含 downgrade）

★ B 号唯一权威 = ttbox-vs-yu-program/m2-acceptance-checklist.md（主理人发号；本脚本
  标签已按其 §1b 勘误锚对齐 —— ota 门控/预览降级为 B6 子断言，F2=B12、F3=B13）。

用法（板端）：
    install -d -m 0755 /root/m2-cards
    # 把发卡工具产出的 card-*.json 传进 /root/m2-cards/
    python3 ttbox_m2_license_accept.py [--restore-valid] [--password <PW>]

依赖板端：python3、curl 语义的 web :8000、可用的 AF_UNIX /run/ttbox/core.sock。
退出码：0 = 全过（无 FAIL）；1 = 有 FAIL。

默认行为会**先把 store 里的卡挪走**建立干净基线（生成 license.json.m2accept-retired），
结束时按 --restore-valid 决定是否重新激活有效卡（便于留一个可演示的绿灯态）。

★ --password 与鉴权面（B6·ota / B13 前置）：
  · 未给 --password ⇒ B6·ota / B13 标记 SKIP（403/429 属鉴权面，未登录读到的只会是 401；
    这两条已被 plugins/web/tests/ 的 pytest 全覆盖 —— pytest 用注入桶，无需板端鉴权）。
  · 给了 --password ⇒ 若板端尚未设置管理员密码（bootstrap），脚本会调 /api/auth/first-setup
    用该值**首次设置**（≥8 字符），随后登录拿到会话 cookie；已设置则直接用该值登录。
    ⚠ 这会永久改变板端 web 密码设置 ⇒ 演示机选一个记得住的值（或先手动登录过一次）。

★ 三个板端陷阱（本脚本已内建规避，改脚本时勿删）：
  1) core 重启后就绪期间，web 读不到 IPC 会回落到"诚实未激活"默认块
     （ttbox-web.py::_license_block 的 !lic 分支）⇒ 固定 sleep 判 B5 必假 FAIL。
     故用直连 core.sock 的 IPC PING 作精确就绪探针。
  2) ttbox-core 的 systemd 单元是 Restart=always + StartLimitBurst=5/5min：
     连跑几轮验收（每轮重启 2 次）会把启动配额打满 ⇒ 之后 systemd 直接拒启
     （start-limit-hit）⇒ 探针超时。故每次重启前先 `systemctl reset-failed ttbox-core`。
  3) F3 限速桶是 **ttbox-web 进程内内存**（_RL_ACTIVATE）：跑完 B13 该 IP 的激活额度
     会被打满（30 次/10 分钟）⇒ 后续（含收尾恢复有效卡）会被 429 挡住。故 B13 之后
     **重启 ttbox-web 清桶**（进程内状态随重启归零）再继续（见 restart_web()）。
"""
import http.cookiejar
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "http://127.0.0.1:8000"
CARDS = "/root/m2-cards"
SOCK = "/run/ttbox/core.sock"
RESTORE_VALID = ("--restore-valid" in sys.argv)

# 短码跨语言向量：license_id → 短码（与 C++/Python 两侧黄金向量一致，见
# core/tests/test_license_shortcode.cpp 与 tools/license/test_license_gen.py）。
SHORT_CODE_VECTOR = {"ttbox-lic-20260917-3842ff": "TTB-006Z-0C33"}


def _arg_value(flag):
    """取 `--flag value` 的 value（缺省 None）。"""
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


# ★ B6·ota / B13 前置：进入鉴权面所需的 web 管理员口令（opt-in）。缺省 None ⇒ 那两条 SKIP。
WEB_PASSWORD = _arg_value("--password") or _arg_value("--auth")

results = []

# 会话 cookie 罐（登录后自动带上；http() 统一走带 CookieProcessor 的 opener）。
_COOKIE_JAR = http.cookiejar.CookieJar()
_OPENER = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_COOKIE_JAR))


def core_ping(timeout=2.0):
    """直连 core.sock 发 IPC PING —— 精确的就绪探针（不依赖 web 的回落语义）。"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK)
        s.sendall(b'{"type":"PING"}\n')
        data = s.recv(4096)
        s.close()
        return b'"pong"' in data and b'true' in data
    except Exception:
        return False


def wait_core_ready(max_s=45):
    """轮询直到 core IPC 真的应答 PING；返回耗时秒（超时返回 -1）。"""
    for i in range(max_s):
        if core_ping():
            return i + 1
        time.sleep(1)
    return -1


def restart_core():
    """重启 core 并等就绪。

    ★ 必须先 reset-failed：ttbox-core 配置 StartLimitBurst=5 / StartLimitIntervalUSec=5min
      + Restart=always。验收脚本每轮要重启 2 次，连跑几轮就把 5/5min 配额打满 ⇒ 之后
      systemd 直接拒启（start-limit-hit），探针超时被误判成"core 起不来"。reset-failed
      清掉启动计数（对健康运行中的单元无副作用）。
    """
    subprocess.run(["systemctl", "reset-failed", "ttbox-core"], check=False)
    subprocess.run(["systemctl", "restart", "ttbox-core"], check=False)
    return wait_core_ready()


def reset_baseline():
    """复位到干净基线：清掉 store 里的卡（保留 .retired 备份），重启 core 并等就绪。"""
    subprocess.run(["bash", "-c",
                    "test -f /var/lib/ttbox/license/license.json && "
                    "mv -f /var/lib/ttbox/license/license.json "
                    "/var/lib/ttbox/license/license.json.m2accept-retired || true"],
                   check=False)
    return restart_core()


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=20) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            obj = json.loads(raw)
        except Exception:
            obj = {"raw": raw}
        return e.code, obj
    except Exception as e:  # 连接被拒/超时（web 重启窗口）⇒ 视作不可达
        return 0, {"error": str(e)}


def lic():
    _, d = http("GET", "/api/license")
    return d["data"]["license"]


def lic_data():
    """GET /api/license 的整个 data 块（含 license 子块 + ui 品牌投影）。

    ★ /api/license 属**永久鉴权白名单**（登录页需显示授权态）⇒ 无需会话即可读，
      是观测"投影"（短码/能力位/换皮）与"拒绝原因 message"的稳定入口。
    """
    _, d = http("GET", "/api/license")
    return d.get("data") or {}


def activate(card):
    body = open(f"{CARDS}/{card}").read().strip()
    return http("POST", "/api/license/activate", {"license_key": body})


def activate_raw(body):
    """直接投递信封文本（用于构造解析级拒绝 / 限速爆破），不读文件。"""
    return http("POST", "/api/license/activate", {"license_key": body})


def core_ipc(type_name, extra=None, timeout=3.0):
    """直连 core.sock 发一条 IPC 请求并解析 JSON 响应（用于读真 · 指标，绕过 web）。

    ★ 用于观测 M2.03 预览降级（metrics.preview_watermark / preview_fps）——
      该指标只在 IPC metrics 里，经 /api/license 看不到；直连 socket 也免鉴权。
    """
    payload = {"type": type_name}
    if isinstance(extra, dict):
        payload.update(extra)
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect(SOCK)
        s.sendall((json.dumps(payload) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        s.close()
        return json.loads(buf.decode("utf-8", "replace").strip().splitlines()[0])
    except Exception as e:
        return {"status": -1, "error": str(e)}


def ensure_session(password):
    """建立管理员会话（opt-in）。返回 True 表示已登录可访问鉴权面。

    板端 bootstrap（未设密码）⇒ first-setup(→409/200 皆忽略) 后 login；
    已设密码 ⇒ 直接用该值 login（口令不符则 401 ⇒ 返回 False ⇒ 相关条 SKIP）。
    """
    if not password or len(password) < 8:
        return False
    http("POST", "/api/auth/first-setup", {"password": password})   # 已配置 ⇒ 409，忽略
    code, resp = http("POST", "/api/auth/login", {"password": password})
    return code == 200 and isinstance(resp, dict) and resp.get("ok") is True


def wait_web_ready(max_s=30):
    for i in range(max_s):
        code, _ = http("GET", "/api/license")
        if code == 200:
            return i + 1
        time.sleep(1)
    return -1


def restart_web():
    """重启 ttbox-web 并等就绪（★ F3 限速桶是进程内内存 ⇒ 重启即清空）。

    仅 B13（F3 限速爆破）后调用：清掉被打满的 _RL_ACTIVATE，使收尾恢复有效卡不被 429 挡。
    """
    subprocess.run(["systemctl", "reset-failed", "ttbox-web"], check=False)
    subprocess.run(["systemctl", "restart", "ttbox-web"], check=False)
    return wait_web_ready()


def check(item, desc, ok, detail):
    results.append({"item": item, "desc": desc,
                    "verdict": "PASS" if ok else "FAIL", "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {item:5s} {desc}\n         -> {detail}", flush=True)


def skip(item, desc, reason):
    """三态：SKIP（不可判定，如缺 --password 无法进鉴权面）；不计入 FAIL。"""
    results.append({"item": item, "desc": desc, "verdict": "SKIP", "detail": reason})
    print(f"[SKIP] {item:5s} {desc}\n         -> {reason}", flush=True)


def err_of(resp):
    if isinstance(resp, dict):
        return resp.get("error") or json.dumps(resp, ensure_ascii=False)[:200]
    return str(resp)[:200]


print("=" * 78)
print("M2 B 系列板端验收（192.168.0.104 / current -> releases/1.3.0）")
print("=" * 78)

# ---------- 复位到干净基线 ----------
t_ready = reset_baseline()
print(f"[基线] core 就绪探针（IPC PING）：{'PASS t+%ds' % t_ready if t_ready > 0 else 'TIMEOUT'}")
print(f"[基线] /etc/ttbox/license.key 存在？ "
      f"{'YES（会遮蔽 store！）' if __import__('os').path.exists('/etc/ttbox/license.key') else 'NO（干净）'}")

# ---------- B0 基线：无卡 = 未激活（fail-closed）----------
L = lic()
check("B0", "无卡基线 fail-closed（AI 门应关）",
      L.get("activated") is False and L.get("state") in ("unactivated", "unknown"),
      f"activated={L.get('activated')} state={L.get('state')} plan={L.get('plan')} "
      f"features={L.get('features')}")

# ---------- B1 无签/坏签卡拒 ----------
for item, card, what in (
    ("B1a", "card-nosign.json", "无 signature 字段"),
    ("B1b", "card-badsig.json", "签名被翻转 1 bit"),
    ("B1c", "card-unknownkey.json", "未知 key_id（换族后旧卡）"),
):
    code, resp = activate(card)
    check(item, f"无签/坏签卡拒（{what}）",
          code == 400 and isinstance(resp, dict) and resp.get("ok") is False,
          f"HTTP {code} :: {err_of(resp)}")

# ---------- B2 他板卡拒（device_id 不符）----------
code, resp = activate("card-otherdev.json")
check("B2a", "他板卡拒（合法签名但 device=deadbeef…）",
      code == 400, f"HTTP {code} :: {err_of(resp)}")
code, resp = activate("card-tampered-device.json")
check("B2b", "改 device 不重签 ⇒ 拒（签名不符）",
      code == 400, f"HTTP {code} :: {err_of(resp)}")

# ---------- B3 过期卡拒 ----------
code, resp = activate("card-expired.json")
check("B3", "过期卡拒（9 天前到期）",
      code == 400, f"HTTP {code} :: {err_of(resp)}")

# ---------- B8 改 ui_brand 不重签 ⇒ 拒 ----------
code, resp = activate("card-tampered-brand.json")
check("B8", "改 ui_brand 不重签 ⇒ 拒",
      code == 400, f"HTTP {code} :: {err_of(resp)}")

# ---------- 负控走完后必须仍是未激活（拒绝不落盘）----------
L = lic()
check("N1", "全部负控后仍为未激活（拒绝不落盘）",
      L.get("activated") is False, f"activated={L.get('activated')} state={L.get('state')}")

# ---------- B4 激活有效卡 ⇒ /api/license 翻绿 ----------
code, resp = activate("card-valid.json")
d = resp.get("data", {}) if isinstance(resp, dict) else {}
L = lic()
ok4 = (code == 200 and d.get("activated") is True
       and L.get("activated") is True
       and L.get("plan") == "subscription"
       and set(L.get("features") or []) == {"capture", "inference", "aim", "ota"})
check("B4", "有效卡激活 ⇒ /api/license 翻绿（plan/features 一致）", ok4,
      f"HTTP {code} 激活响应 activated={d.get('activated')} plan={d.get('plan')} ; "
      f"投影 activated={L.get('activated')} state={L.get('state')} plan={L.get('plan')} "
      f"features={L.get('features')} expires_at={L.get('expires_at')}")

# ---------- B9 一卡一设备不可复用 ----------
# (a) 同卡重复激活同一设备 ⇒ 幂等（不产生第二份授权）
code2, resp2 = activate("card-valid.json")
L2 = lic()
check("B9a", "同卡重复激活同设备 ⇒ 幂等（仍单一激活态）",
      code2 == 200 and L2.get("activated") is True,
      f"HTTP {code2} activated={L2.get('activated')}")
# (b) 同一张卡换到设备绑定不符的机器 ⇒ 拒（由 B2a 同机制覆盖；此处复核卡内 device 即本板绑定串）
card = json.load(open(f"{CARDS}/card-valid.json"))
serial = ""
for line in open("/proc/cpuinfo"):
    if line.startswith("Serial"):
        serial = line.split(":", 1)[1].strip()
        break
check("B9b", "卡内 device == 本板绑定串（一卡一设备）",
      card["license"]["device"] == serial,
      f"card.device={card['license']['device']} board.serial={serial}")

# ---------- B5 重启保持 ----------
# ★ 判定必须等 core 真正就绪：core 重启后要重开 V4L2/NPU，期间 web 读不到 IPC 会
#   回落成"诚实未激活"默认块（_license_block 的 !lic 分支）——固定 sleep 会误判 FAIL。
#   用 IPC PING 精确探针等就绪（含 reset-failed 防 systemd 启动限速）。
elapsed = restart_core()
L3 = lic()
ok5 = (elapsed > 0 and L3.get("activated") is True and L3.get("plan") == "subscription"
       and set(L3.get("features") or []) == {"capture", "inference", "aim", "ota"})
check("B5", f"重启 core 后保持激活（LicenseStore 持久化 + 重启恢复链；就绪 t+{elapsed}s）", ok5,
      f"activated={L3.get('activated')} state={L3.get('state')} plan={L3.get('plan')} "
      f"features={L3.get('features')}")

# ---------- B7：重开后授权**投影**随卡恢复（含 M2.03 能力位）----------
# ★ M2.03 已实现：能力位（capabilities）与 features 同源投影随卡恢复；全功能卡四项应全 True。
_caps3 = L3.get("capabilities") or {}
ok7 = (L3.get("activated") is True and bool(L3.get("features"))
       and _caps3.get("capture") is True and _caps3.get("inference") is True
       and _caps3.get("aim") is True and _caps3.get("ota") is True)
check("B7", "重开后授权投影恢复（features + M2.03 能力位四页全开）", ok7,
      f"features={L3.get('features')} capabilities={_caps3}")

# ---------- B6：受限卡投影（M2.03 feature 级能力位）----------
# ★ M2.03 已实现：features=[capture] ⇒ capabilities 仅 capture=True，其余（inference/aim/ota）全 False；
#   core 据此在会话边界按位启停模块（执法由 core/Application::run 依 LicenseGate 快照下传）。
code, resp = activate("card-limited.json")
L4 = lic()
_caps4 = L4.get("capabilities") or {}
ok6 = (code == 200 and L4.get("features") == ["capture"]
       and _caps4.get("capture") is True
       and _caps4.get("inference") is False and _caps4.get("aim") is False
       and _caps4.get("ota") is False)
check("B6", "受限卡（features=[capture]）⇒ 能力位仅 capture（M2.03 feature 级门控）", ok6,
      f"HTTP {code} features={L4.get('features')} capabilities={_caps4} plan={L4.get('plan')} "
      f"short_code={L4.get('short_code')}")

# ---------- B6·预览：受限预览降级（B6 子断言 · 直连 IPC 读真 · 指标）----------
# ★ M2.03：非全功能 ⇒ 预览 fps=min(配置值,5) + 水印。判据 = metrics.preview_watermark。
#   ★ 特性 gate 只在**会话边界**（runtime 启动/重启）按卡态收窄 ⇒ 先重启 core 让受限卡重新
#     加载并生效，再读指标，判据才确定。runtime 未运行（无预览样本）时不可观测 ⇒ SKIP（不冤判）。
restart_core()
st_ipc = core_ipc("GET_STATUS")
_m = ((st_ipc or {}).get("data") or {}).get("metrics") if isinstance(st_ipc, dict) else None
if isinstance(_m, dict) and (int(_m.get("preview_frames", 0) or 0) > 0 or int(_m.get("preview_fps", 0) or 0) > 0):
    check("B6·预览", "受限卡下预览降级（watermark=True，fps 封顶）",
          bool(_m.get("preview_watermark")),
          f"preview_watermark={_m.get('preview_watermark')} preview_fps={_m.get('preview_fps')}")
else:
    skip("B6·预览", "受限卡下预览降级（watermark）",
         f"runtime/预览未运行 ⇒ metrics 无样本，不可观测（preview_frames="
         f"{(_m or {}).get('preview_frames')}）；执法逻辑由 core 单测覆盖")

# ---------- B10：M2.04 商业换皮投影（sample 渠道卡）----------
# 卡内 ui_brand=sample ⇒ web 品牌表 v2 投影：ui_brand/brand_accent/brand_logo/theme。
code, resp = activate("card-brand-sample.json")
data = lic_data()
ui = data.get("ui") or {}
theme = ui.get("theme") or {}
ok10 = (code == 200
        and data.get("ui_brand") == "sample"
        and ui.get("ui_brand") == "sample"
        and ui.get("brand_accent") == "#E4572E"
        and theme.get("mode") == "light"
        and ui.get("brand_logo") == "logos/sample.png")
check("B10", "M2.04 换皮投影（sample：accent/logo/theme 全量随卡）", ok10,
      f"HTTP {code} ui_brand={data.get('ui_brand')} brand_accent={ui.get('brand_accent')} "
      f"theme={theme} brand_logo={ui.get('brand_logo')} "
      f"（须知：板端 config/ui_brands.json 须为 v2，否则未知品牌回落 ttbox）")

# ---------- B11：M2.05 卡号可读短码投影（跨语言向量）----------
code, resp = activate("card-valid.json")
data = lic_data()
_sc = (data.get("license") or {}).get("short_code")
_exp_sc = SHORT_CODE_VECTOR["ttbox-lic-20260917-3842ff"]
ok11 = (code == 200 and _sc == _exp_sc)
check("B11", "M2.05 卡号短码投影（card-valid → 跨语言向量）", ok11,
      f"HTTP {code} short_code={_sc!r} 期望={_exp_sc!r}")

# ---------- B12：F2 拒绝原因非空（验签级 + 解析级两路）----------
# 缺陷 F2：卡在但被拒时 /api/license.message 为空。修复后两路拒绝都须写 last_error。
# ★ 顺序：先验签级（撤回内存卡）再解析级 —— 使解析拒时内存卡已空，原因不会被空卡分支覆盖。
# (a) 验签级：合法格式但 device 不符（他板卡）⇒ 验签拒绝（撤回内存卡）⇒ message 非空
code_v, resp_v = activate("card-otherdev.json")
msg_v = (lic_data().get("license") or {}).get("message") or ""
# (b) 解析级：投递非 JSON 信封（此刻内存卡已空）⇒ 解析拒绝 ⇒ message 非空
code_p, resp_p = activate_raw("{ this is not a license envelope")
msg_p = (lic_data().get("license") or {}).get("message") or ""
ok12 = (code_v == 400 and msg_v.strip() not in ("", "card not set")
        and code_p == 400 and msg_p.strip() not in ("", "card not set"))
check("B12", "F2 拒绝原因非空（验签级 + 解析级均写 last_error→message）", ok12,
      f"验签级 HTTP {code_v} message={msg_v!r} ; 解析级 HTTP {code_p} message={msg_p!r}")

# ---------- B14：F4 防降级拒绝（更旧 issued_at）----------
# 基线 = 已落盘的 card-valid.issued_at（1789626223）。card-downgrade-older.issued_at=1789626000 < 基线 ⇒ 拒。
# ★ F4 修复点：LicenseStore 从**嵌套 license.issued_at** 取基线（此前只读顶层 ⇒ 恒 0 ⇒ 形同虚设）。
code, resp = activate("card-downgrade-older.json")
msg = (lic_data().get("license") or {}).get("message") or ""
ok14 = (code == 400 and "downgrade" in msg.lower())
check("B14", "F4 防降级拒绝（更旧 issued_at ⇒ 400 + downgrade）", ok14,
      f"HTTP {code} message={msg!r}")

# ---------- B6·ota / B13：鉴权面（ota 门控 + 激活限速）----------
session_ok = ensure_session(WEB_PASSWORD)

# ---------- B6·ota：M2.03 ota 门控（B6 子断言 · 受限卡 ota=false ⇒ 403 且不调度）----------
if session_ok:
    activate("card-limited.json")            # ota=false 的卡
    caps = (lic().get("capabilities") or {})
    code, resp = http("POST", "/api/ota/install", {"url": "https://example.com/ttbox-fw.bin"})
    ok6ota = (caps.get("ota") is False and code == 403)
    check("B6·ota", "M2.03 ota 门控（未授权卡 POST /api/ota/install ⇒ 403，不调度）", ok6ota,
          f"capabilities.ota={caps.get('ota')} HTTP {code} :: {err_of(resp)}")
else:
    skip("B6·ota", "M2.03 ota 门控（403）",
         "缺 --password ⇒ 未登录，/api/ota/install 只会 401（鉴权先于 feature 门控）；"
         "pytest test_web_license_caps.py 已覆盖 403/200 两态")

# ---------- B13：F3 激活端点独立限速（31 次失败 ⇒ 429）----------
if session_ok:
    # 逐条投递非 JSON 信封（解析级拒绝，均计入失败桶）。第 31 次应被挡在限速（429）。
    last = 0
    for i in range(31):
        last, _r = activate_raw('{"license":{"license_id":"probe-%d"' % i)
    code = last
    ok13 = (code == 429)
    check("B13", "F3 激活端点独立限速（第 31 次失败 ⇒ 429）", ok13,
          f"第 31 次 HTTP {code}（期望 429；前 30 次应 400）")
    # ★ 清桶：F3 桶是 ttbox-web 进程内内存 ⇒ 重启即归零，恢复后续可激活。
    t_web = restart_web()
    if t_web > 0:
        # 重启后会话失效 ⇒ 重新登录（密码已设置，login 可用）
        session_ok = ensure_session(WEB_PASSWORD)
        print(f"[B13 收尾] ttbox-web 重启清限速桶：PASS t+{t_web}s；重新登录="
              f"{'OK' if session_ok else 'FAIL'}")
    else:
        print("[B13 收尾] ttbox-web 重启探针 TIMEOUT（后续恢复可能受 429 影响）", flush=True)
else:
    skip("B13", "F3 激活端点独立限速（31 次失败 ⇒ 429）",
         "缺 --password ⇒ 未登录，无法进入激活端点；"
         "pytest test_web_activate_ratelimit.py 已覆盖分桶/清零/XFF 等 6 项")

# ---------- 收尾：恢复状态 ----------
if RESTORE_VALID:
    code, resp = activate("card-valid.json")
    Lf = lic()
    print(f"\n[收尾] 已恢复有效卡激活态（HTTP {code}）：activated={Lf.get('activated')} "
          f"plan={Lf.get('plan')} features={Lf.get('features')} "
          f"short_code={Lf.get('short_code')}")
else:
    print(f"\n[收尾] 保留最后一次状态：activated={lic().get('activated')}")

n_pass = sum(1 for r in results if r["verdict"] == "PASS")
n_fail = sum(1 for r in results if r["verdict"] == "FAIL")
n_skip = sum(1 for r in results if r["verdict"] == "SKIP")
print("\n" + "=" * 78)
print(f"B 系列汇总：PASS={n_pass} FAIL={n_fail} SKIP={n_skip} 共 {len(results)} 项")
print("JSON_RESULT=" + json.dumps(
    {"pass": n_pass, "fail": n_fail, "skip": n_skip, "results": results},
    ensure_ascii=False))
print("=" * 78)
sys.exit(0 if n_fail == 0 else 1)
