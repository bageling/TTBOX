# test_web_recoil_translation.py — ttbox-web.py 压枪翻译层单测（本地，不启动 Web）
# 验证 Web 前端 recoil 提交格式 → mouse.recoil（位掩码 int）转换正确。
import sys

# 从 ttbox-web.py 提取翻译函数（避免启动 Flask）
src = open('plugins/web/bin/ttbox-web.py', encoding='utf-8').read()
# 提取 HOTKEY_BITS / BIT_HOTKEYS / _hotkey_to_bits / _bits_to_hotkey
ns = {}
start = src.index('HOTKEY_BITS =')
end = src.index('# controller 内的数值/布尔直通字段')
exec(src[start:end], ns)

failures = 0

def check(cond, msg):
    global failures
    if cond:
        print('  PASS:', msg)
    else:
        print('  FAIL:', msg)
        failures += 1

hotkey_to_bits = ns['_hotkey_to_bits']
bits_to_hotkey = ns['_bits_to_hotkey']

print('[翻译层] hotkey 字符串 → 位掩码')
check(hotkey_to_bits('left', 1) == 1, "'left' → 1")
check(hotkey_to_bits('right', 2) == 2, "'right' → 2")
check(hotkey_to_bits('middle', 0) == 4, "'middle' → 4")
check(hotkey_to_bits('', 0) == 0, "'' → 0")
check(hotkey_to_bits('back', 0) == 8, "'back' → 8")
check(hotkey_to_bits('forward', 0) == 16, "'forward' → 16")

print('[翻译层] 位掩码 → hotkey 字符串')
check(bits_to_hotkey(1) == 'left', "1 → 'left'")
check(bits_to_hotkey(2) == 'right', "2 → 'right'")
check(bits_to_hotkey(0) == '', "0 → ''")

# 模拟 Web 前端提交的 recoil 块（RECOIL_DEFAULTS 语义）
#
# ★ 2026-09-24 面板收敛（业主裁定「新版替老版，界面只留一套」）后，面板提交的 recoil
#   块只剩「总开关 + 触发键」四项：压枪算法换成 BB 三段查表，其余参数归 recoil_bb_* /
#   vc_* 两组（走 body.ai.controller，见 test_web_bb_modules.py）。
#   老速率模型的 strength / speed / humanize_* / trigger_delay_* 后端**仍然认**（已装机
#   设备的旧值不丢），但面板不再产出它们，所以这里也不再当作提交面来断言。
print('[映射] Web recoil 块 → mouse.recoil 字段翻译')
web_recoil = {
    'enabled': True,
    'hotkey': 'left',
    'hotkey2': '',
    'hotkey_mode': 'any',
}
mouse_recoil = {}
if web_recoil.get('enabled') is not None:
    mouse_recoil['enabled'] = bool(web_recoil['enabled'])
if web_recoil.get('hotkey') is not None:
    mouse_recoil['hotkey'] = hotkey_to_bits(web_recoil['hotkey'], 1) or 1
if web_recoil.get('hotkey2') is not None:
    mouse_recoil['hotkey2'] = hotkey_to_bits(web_recoil['hotkey2'], 0)
if web_recoil.get('hotkey_mode') is not None:
    mouse_recoil['hotkey_mode'] = 2 if str(web_recoil['hotkey_mode']) == 'all' else 1

check(mouse_recoil.get('enabled') is True, "enabled → true")
check(mouse_recoil.get('hotkey') == 1, "hotkey 'left' → 1")
check(mouse_recoil.get('hotkey2') == 0, "hotkey2 '' → 0")
check(mouse_recoil.get('hotkey_mode') == 1, "hotkey_mode 'any' → 1")

print('[映射] 压枪总开关 → BB 三段查表引擎（面板只留一套）')
# 后端 ttbox-web.py 在写完 recoil 之后做这一步镜像；这里照抄同一语义，锁死契约。
mouse = {'recoil': mouse_recoil}
if 'enabled' in mouse_recoil:
    mouse.setdefault('recoil_bb', {})['enabled'] = mouse_recoil['enabled']
check(mouse['recoil_bb']['enabled'] is True, "开关开 → recoil_bb.enabled 跟着开")

mouse_off = {'recoil': {'enabled': False}}
if 'enabled' in mouse_off['recoil']:
    mouse_off.setdefault('recoil_bb', {})['enabled'] = mouse_off['recoil']['enabled']
check(mouse_off['recoil_bb']['enabled'] is False, "开关关 → recoil_bb.enabled 跟着关")

# 面板没提交压枪块时不得凭空造出 recoil_bb（Core 会保留原值）
mouse_none = {}
if 'enabled' in {}:
    mouse_none.setdefault('recoil_bb', {})['enabled'] = True
check('recoil_bb' not in mouse_none, "没提交压枪块 → 不造 recoil_bb")

print('[映射] all 模式')
web_all = dict(web_recoil, hotkey_mode='all')
mode_all = 2 if str(web_all['hotkey_mode']) == 'all' else 1
check(mode_all == 2, "hotkey_mode 'all' → 2")

print()
print('结果: %d failures' % failures)
sys.exit(1 if failures else 0)
