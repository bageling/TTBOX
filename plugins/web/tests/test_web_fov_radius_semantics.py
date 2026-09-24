# test_web_fov_radius_semantics.py — 回归锁：面板「瞄准范围」倍率 ↔ core fov.radius 的语义
#
# 业主口径（2026-09-24 钉死）：
#   瞄准范围 = **在截取尺寸（画幅裁剪大小）内划最大的圆形**。
#   面板倍率 1.00 就等于这个内接圆；缩倍率就是把圆按比例收小。
#
# 修复前的三处问题（本文件逐条钉死，防回退）：
#   ① `enabled = range_factor < 1.0` ⇒ 1.00 落 enabled=False（core 强制 fov_range=1.0）、
#      而 0.99 落 enabled=True（core 取 fov_range = radius×2 = 1.98）——
#      **滑块往小拖，圆几乎翻倍**，非单调。这是本次最硬的回归点。
#   ② 从不读 p0['fov_scale']（热键卡「热键 FOV 缩放」），
#      回填时又恒写 1.0 ⇒ 那个旋钮是死的，提示文案却在承诺"乘"。
#   ③ 倍率 0 未夹取 ⇒ core 的 fov.radius<=0 撞 RuntimeProfile.cpp:141
#      「FOV 半径必须在 (0,1]」，整份 SET_CONFIG 被拒（连带丢掉别的字段）。
#
# 两侧换算关系（core/src/aim/AimThread.cpp:117）：
#   fov_range = fov.enabled ? fov.radius × 2 : 1.0
#   ⇒ 面板倍率 k = fov.radius × 2（enabled 时）；enabled=false ⇒ 倍率视作 1.0
#   ⇒ 写回必须 radius = k / 2 且 enabled = True（否则 k 被 core 吞掉）
#
# 运行：python -m pytest plugins/web/tests/test_web_fov_radius_semantics.py -v
import importlib.util
import json
import os
import pathlib
import re
import sys
import tempfile

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB_SRC = REPO_ROOT / 'plugins' / 'web' / 'bin' / 'ttbox-web.py'

_load_seq = 0


def _load():
    global _load_seq
    _load_seq += 1
    name = 'ttbox_web_fovsem_%d_%d' % (os.getpid(), _load_seq)
    spec = importlib.util.spec_from_file_location(name, WEB_SRC)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def web_mod(monkeypatch):
    """加载 ttbox-web.py，并把全部运行期路径指到临时目录（不碰开发机真实 FHS）。"""
    tmp = tempfile.mkdtemp(prefix='ttbox_fovsem_%d_' % os.getpid())
    monkeypatch.setenv('TTBOX_PREFIX', tmp)
    monkeypatch.setenv('TTBOX_PRESETS_DIR', os.path.join(tmp, 'presets'))
    monkeypatch.setenv('TTBOX_CONFIG_DIR', os.path.join(tmp, 'config'))
    monkeypatch.setenv('TTBOX_MODELS_ROOT', os.path.join(tmp, 'models'))
    monkeypatch.setenv('TTBOX_MOTION_PROFILES_DIR', os.path.join(tmp, 'config', 'motion-profiles'))
    return _load()


def _base_profile(fov=None):
    """一份「Core 当前运行配置」的替身；capture 按板端实况 640×640。

    ★ capture 就是口径里的「截取尺寸」：TargetSelector 的搜索半径 =
      min(capture.width, capture.height) / 2 × fov_range（板端 = 320px × fov_range）。
    """
    p = {
        'capture': {'width': 640, 'height': 640, 'offset_x': 0, 'offset_y': 0},
        'inference': {'confidence': 0.5, 'iou': 0.45},
        'mouse': {'kp_x': 0.5, 'kp_y': 0.5},
        'fov': {'shape': 0, 'center_x': 0.5, 'center_y': 0.5, 'enabled': False, 'radius': 0.5},
        'model_id': 'model-A',
    }
    if fov is not None:
        p['fov'].update(fov)
    return p


def _client(web_mod, monkeypatch, profile=None, ipc_result=None, recorder=None):
    """装配一个把 IPC 与「当前配置」都替换掉的 test client；recorder 收下 SET_CONFIG 实参。"""
    def fake_ipc(req_type, params=None, timeout=5):
        if recorder is not None:
            recorder['req_type'] = req_type
            recorder['params'] = params
        return dict(ipc_result or {'status': 0, 'data': {}})

    monkeypatch.setattr(web_mod, 'ipc_request', fake_ipc)
    monkeypatch.setattr(web_mod, '_get_runtime_profile',
                        lambda: json.loads(json.dumps(profile if profile is not None else _base_profile())))
    monkeypatch.setattr(web_mod, '_activation_ok', lambda: True)
    return web_mod.app.test_client()


def _fov_of(web_mod, monkeypatch, body, profile=None):
    monkeypatch.setattr(web_mod, '_get_runtime_profile',
                        lambda: json.loads(json.dumps(profile if profile is not None else _base_profile())))
    return web_mod.web_body_to_profile(body)['fov']


# ===========================================================================
# 1. 纯函数：倍率夹取
# ===========================================================================

@pytest.mark.parametrize('raw,expected', [
    # 合法域内原样
    (1.0, 1.0), (0.99, 0.99), (0.5, 0.5), (0.1, 0.1), ('0.25', 0.25),
    # 上越界一律压到 1.0（>1 会让圆超出截取区，口头"瞄准范围"失去意义）
    (1.0001, 1.0), (1.5, 1.0), (2.0, 1.0), (100, 1.0),
    # 下越界一律抬到 0.1（0 会让 radius=0 撞 core 校验、且选靶全灭）
    (0.0, 0.1), (0.05, 0.1), (-1.0, 0.1), (-999, 0.1),
    # 非数值 / NaN 回退 default（默认 1.0 = 内接圆，最保守）
    (None, 1.0), ('', 1.0), ('abc', 1.0), ([], 1.0), ({}, 1.0),
    (float('nan'), 1.0), (float('inf'), 1.0), (float('-inf'), 0.1),
])
def test_fov_factor_clamp_stays_inside_legal_band(web_mod, raw, expected):
    got = web_mod._fov_factor_clamp(raw)
    assert got == pytest.approx(expected), f'raw={raw!r}'
    # 输出必须恒在 [0.1, 1.0]：这是 core「fov.radius ∈ (0,1]」能成立的前提
    assert web_mod.FOV_FACTOR_MIN <= got <= 1.0, f'raw={raw!r} → {got}'


def test_fov_factor_clamp_custom_default_is_respected(web_mod):
    assert web_mod._fov_factor_clamp(None, default=0.5) == pytest.approx(0.5)
    assert web_mod._fov_factor_clamp(float('nan'), default=0.75) == pytest.approx(0.75)


def test_fov_factor_min_is_below_one(web_mod):
    """下限必须是真下限：等于 1.0 会让"收小瞄准范围"这个功能整个消失。"""
    assert 0.0 < web_mod.FOV_FACTOR_MIN < 1.0


# ===========================================================================
# 2. 纯函数：core radius → 面板倍率（回填方向）
# ===========================================================================

@pytest.mark.parametrize('radius,enabled,expected', [
    # enabled ⇒ 倍率 = radius × 2（core 的 fov_range 就是 radius×2）
    (0.5, True, 1.0),
    (0.4, True, 0.8),
    (0.25, True, 0.5),
    (0.05, True, 0.1),
    # enabled=False ⇒ core 强制 fov_range=1.0，倍率就是 1.0（内接圆）
    (0.5, False, 1.0),
    (0.05, False, 1.0),
    (0.9, False, 1.0),
    # 越界 / 脏数据一律夹回合法带
    (1.0, True, 1.0),      # 2.0 → 夹到 1.0
    (0.0, True, 0.1),      # 0.0 → 夹到 0.1
    (None, True, 1.0),
    ('abc', True, 1.0),
    (float('nan'), True, 1.0),
])
def test_radius_to_factor_matches_core_fov_range(web_mod, radius, enabled, expected):
    assert web_mod._fov_radius_to_factor(radius, enabled) == pytest.approx(expected)


def test_radius_to_factor_defaults_to_enabled(web_mod):
    """只传 radius 时按 enabled 处理（面板回填一律拿到 core 的 enabled 标志）。"""
    assert web_mod._fov_radius_to_factor(0.4) == pytest.approx(0.8)


# ===========================================================================
# 3. 翻译层：面板倍率 → core fov（写方向）
# ===========================================================================

def test_range_factor_1_00_writes_radius_half_and_enabled_true(web_mod, monkeypatch):
    """★ 最硬的回归点：1.00 必须落成 enabled=True / radius=0.5。

    旧实现 `enabled = range_factor < 1.0` 让 1.00 走 enabled=False，
    core 于是把 fov_range 强制成 1.0 —— 看似数值相同，但那是"关"，
    接下来 0.99 就会变成 1.98×，滑块彻底非单调。
    """
    fov = _fov_of(web_mod, monkeypatch, {'range_factor': 1.0})
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.5)


def test_range_factor_0_99_does_not_inflate_the_circle(web_mod, monkeypatch):
    """★ 非单调回归点：0.99 只能得到 0.99×，绝不能是 1.98×。"""
    fov = _fov_of(web_mod, monkeypatch, {'range_factor': 0.99})
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.495)
    # core 侧最终生效倍率 = radius × 2
    assert fov['radius'] * 2.0 == pytest.approx(0.99)


def test_range_factor_is_monotonic_across_the_slider(web_mod, monkeypatch):
    """滑块从头拖到尾，生效倍率必须单调不增（旧实现在 1.0 处断崖）。"""
    ks = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1]
    effective = []
    for k in ks:
        fov = _fov_of(web_mod, monkeypatch, {'range_factor': k})
        assert fov['enabled'] is True, f'k={k} 竟落成"关"'
        effective.append(fov['radius'] * 2.0)
    for a, b in zip(effective, effective[1:]):
        assert a >= b - 1e-9, f'倍率序列非单调：{effective}'
    assert effective[0] == pytest.approx(1.0)
    assert effective[-1] == pytest.approx(0.1)


def test_range_factor_zero_is_clamped_and_never_emits_illegal_radius(web_mod, monkeypatch):
    """倍率 0（前端旧 min=0 能拖出来）必须夹到 0.1 ⇒ radius=0.05，仍在 (0,1]。"""
    fov = _fov_of(web_mod, monkeypatch, {'range_factor': 0})
    assert fov['radius'] == pytest.approx(0.05)
    assert 0.0 < fov['radius'] <= 1.0


def test_range_factor_above_one_is_clamped(web_mod, monkeypatch):
    fov = _fov_of(web_mod, monkeypatch, {'range_factor': 1.8})
    assert fov['radius'] == pytest.approx(0.5)


def test_hotkey_fov_scale_multiplies_overview_factor(web_mod, monkeypatch):
    """★ 热键卡旋钮必须真的参与相乘（旧实现从不读它）。1.0 × 0.5 ⇒ 0.5×。"""
    fov = _fov_of(web_mod, monkeypatch,
                  {'range_factor': 1.0, 'aim_profiles': [{'fov_scale': 0.5}]})
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.25)
    assert fov['radius'] * 2.0 == pytest.approx(0.5)


def test_hotkey_fov_scale_only_uses_current_core_radius_as_base(web_mod, monkeypatch):
    """只拖热键卡旋钮（未动总览）⇒ 基准取 core 当前生效半径，不当作 1.0 重置。

    core 现值 radius=0.4 ⇒ 当前倍率 0.8；再乘 0.5 ⇒ 0.4 ⇒ radius=0.2。
    若把缺省基准错当成 1.0，就会得到 0.5× —— 用户看着圆没缩小，但一拖就跳。
    """
    prof = _base_profile(fov={'enabled': True, 'radius': 0.4})
    fov = _fov_of(web_mod, monkeypatch,
                  {'aim_profiles': [{'fov_scale': 0.5}]}, profile=prof)
    assert fov['radius'] == pytest.approx(0.2)


def test_both_factors_absent_leaves_core_value_untouched(web_mod, monkeypatch):
    """两项都没提交（只改别的字段）⇒ 本项不参与保存，原样沿用 core 现值。"""
    prof = _base_profile(fov={'enabled': True, 'radius': 0.3})
    fov = _fov_of(web_mod, monkeypatch, {'capture': {'crop_size': 0}}, profile=prof)
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.3)


def test_fov_center_and_shape_are_preserved(web_mod, monkeypatch):
    """只改倍率不得把圆心 / 形状顶回默认。"""
    prof = _base_profile(fov={'shape': 1, 'center_x': 0.42, 'center_y': 0.58})
    fov = _fov_of(web_mod, monkeypatch, {'range_factor': 0.5}, profile=prof)
    assert fov['shape'] == 1
    assert fov['center_x'] == pytest.approx(0.42)
    assert fov['center_y'] == pytest.approx(0.58)
    assert fov['radius'] == pytest.approx(0.25)


def test_product_of_two_factors_is_clamped_once(web_mod, monkeypatch):
    """两个 0.1 相乘 = 0.01，不能写成 radius=0.005（虽仍合法，但会把圆缩到近乎全灭）。

    约定：先各自夹到 [0.1,1] 再相乘，乘积不再回夹 —— 这是"两层缩放叠乘"的真实语义；
    但结果必须仍满足 core 的 radius ∈ (0,1]。
    """
    fov = _fov_of(web_mod, monkeypatch,
                  {'range_factor': 0.1, 'aim_profiles': [{'fov_scale': 0.1}]})
    assert fov['radius'] == pytest.approx(0.005)
    assert 0.0 < fov['radius'] <= 1.0


# ===========================================================================
# 4. PUT /api/config：真正写给 core 的那一份 profile
# ===========================================================================

def test_put_config_writes_radius_half_of_factor(web_mod, monkeypatch):
    rec = {}
    client = _client(web_mod, monkeypatch, recorder=rec)
    rv = client.put('/api/config', json={'range_factor': 0.5})
    assert rv.status_code == 200
    assert rv.get_json()['ok'] is True
    assert rec['req_type'] == 'SET_CONFIG'
    fov = rec['params']['profile']['fov']
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.25)


def test_put_config_full_range_is_not_turned_off(web_mod, monkeypatch):
    """1.00 走的是「全开」而不是「关闭」—— 这是旧实现最容易看反的一处。"""
    rec = {}
    client = _client(web_mod, monkeypatch, recorder=rec)
    client.put('/api/config', json={'range_factor': 1.0})
    fov = rec['params']['profile']['fov']
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(0.5)


def test_put_config_untouched_factor_keeps_core_radius(web_mod, monkeypatch):
    """只改别的字段时不得把 fov 一并重写（否则用户没碰的圆会自己变）。"""
    rec = {}
    prof = _base_profile(fov={'enabled': True, 'radius': 0.3})
    client = _client(web_mod, monkeypatch, profile=prof, recorder=rec)
    client.put('/api/config', json={'capture': {'crop_size': 512}})
    fov = rec['params']['profile']['fov']
    assert fov['radius'] == pytest.approx(0.3)
    assert rec['params']['profile']['capture']['width'] == 512


@pytest.mark.parametrize('bad', [0, -1, 0.0, 2.0, 1e9])
def test_put_config_never_sends_illegal_fov_radius(web_mod, monkeypatch, bad):
    """任何前端脏值都不许把非法 radius 送进 core（否则整份配置被拒）。"""
    rec = {}
    client = _client(web_mod, monkeypatch, recorder=rec)
    rv = client.put('/api/config', json={'range_factor': bad})
    assert rv.status_code == 200
    r = rec['params']['profile']['fov']['radius']
    assert 0.0 < r <= 1.0, f'range_factor={bad!r} → radius={r}'


# ===========================================================================
# 5. 回填：core profile → 面板（写方向的逆）
# ===========================================================================

def test_profile_to_web_reports_radius_times_two(web_mod):
    prof = _base_profile(fov={'enabled': True, 'radius': 0.4})
    body = web_mod.profile_to_web(prof)
    assert body['range_factor'] == pytest.approx(0.8)


def test_profile_to_web_disabled_fov_reports_one(web_mod):
    """core 里 fov 是关的 ⇒ 生效倍率就是 1.0（内接圆），不是 radius×2。"""
    prof = _base_profile(fov={'enabled': False, 'radius': 0.5})
    body = web_mod.profile_to_web(prof)
    assert body['range_factor'] == pytest.approx(1.0)


def test_profile_to_web_hotkey_fov_scale_is_pinned_to_one(web_mod):
    """热键卡倍率恒回 1.0：core 只存一份最终半径，拆不开；回 1.0 防下次保存重复相乘。"""
    prof = _base_profile(fov={'enabled': True, 'radius': 0.4})
    body = web_mod.profile_to_web(prof)
    assert body['aim_profiles'][0]['fov_scale'] == pytest.approx(1.0)


@pytest.mark.parametrize('radius', [0.5, 0.4, 0.3, 0.2, 0.1, 0.05])
def test_profile_to_web_roundtrip_is_stable_inside_the_band(web_mod, monkeypatch, radius):
    """合法带内（radius ≤ 0.5 = 内接圆）回填 → 原样存回必须逐值稳定。"""
    prof = _base_profile(fov={'enabled': True, 'radius': radius})
    body = web_mod.profile_to_web(prof)
    fov = _fov_of(web_mod, monkeypatch, body, profile=prof)
    assert fov['enabled'] is True
    assert fov['radius'] == pytest.approx(radius, abs=1e-6)


@pytest.mark.parametrize('legacy_radius', [0.501, 0.6, 0.75, 0.9, 1.0])
def test_profile_to_web_clamps_oversized_radius_back_to_inscribed_circle(
        web_mod, monkeypatch, legacy_radius):
    """★ 唯一一处**故意**破坏往返的地方：radius > 0.5（= 圆已超出截取区）与
    「截取尺寸内划最大的圆形」口径直接冲突，回填夹到 1.0（内接圆）并如实存回 0.5。

    历史配置里 radius=1 & enabled=true 意即 2× 内接圆，正是这种越界值。
    这里是单向收敛（越界 ⇒ 内接圆 ⇒ 从此稳定），不是数据丢失：
    面板从此不可能再写出越界半径，用户看到的就是真实生效的圆。
    """
    prof = _base_profile(fov={'enabled': True, 'radius': legacy_radius})
    body = web_mod.profile_to_web(prof)
    assert body['range_factor'] == pytest.approx(1.0)
    fov = _fov_of(web_mod, monkeypatch, body, profile=prof)
    assert fov['radius'] == pytest.approx(0.5)
    assert fov['radius'] * 2.0 == pytest.approx(1.0)
    # 收敛后是稳定的：再走一轮不会继续缩
    body2 = web_mod.profile_to_web(_base_profile(fov=fov))
    fov2 = _fov_of(web_mod, monkeypatch, body2, profile=_base_profile(fov=fov))
    assert fov2['radius'] == pytest.approx(fov['radius'])


def test_profile_to_web_hotkey_card_entries_share_the_pinned_scale(web_mod):
    """面板可有多张热键卡；core 侧只有一份半径，每张卡的 fov_scale 都必须回 1.0。"""
    prof = _base_profile(fov={'enabled': True, 'radius': 0.4})
    body = web_mod.profile_to_web(prof)
    assert len(body['aim_profiles']) >= 1
    for card in body['aim_profiles']:
        assert card['fov_scale'] == pytest.approx(1.0)


# ===========================================================================
# 6. 跨层同值：面板（index.html）↔ 后端（ttbox-web.py）不许漂移
# ===========================================================================

TEMPLATE = REPO_ROOT / 'plugins' / 'web' / 'templates' / 'index.html'


def _html() -> str:
    return TEMPLATE.read_text(encoding='utf-8')


def _js_const(src, name):
    m = re.search(r'const\s+%s\s*=\s*([0-9.]+)\s*;' % re.escape(name), src)
    assert m, f'index.html 里找不到常量 {name}'
    return float(m.group(1))


def _input_attrs(src, elem_id):
    m = re.search(r'<input\s+id="%s"[^>]*>' % re.escape(elem_id), src)
    assert m, f'index.html 里找不到 input#{elem_id}'
    tag = m.group(0)
    out = {}
    for k in ('min', 'max', 'step'):
        mm = re.search(r'\b%s="([^"]+)"' % k, tag)
        if mm:
            out[k] = mm.group(1)
    return out


def test_html_overview_factor_min_matches_backend_constant(web_mod):
    """★ 跨层同值：面板下限与后端 FOV_FACTOR_MIN 必须逐位相同。

    这两处不同值 = 用户在面板上能拖出一个后端立刻抬回的值 ⇒ 滑块"回弹"、
    显示值与生效值不一致，是最难查的一类问题。
    """
    assert _js_const(_html(), 'OVERVIEW_FOV_FACTOR_MIN') == pytest.approx(web_mod.FOV_FACTOR_MIN)


def test_html_hotkey_fov_scale_min_matches_backend_constant(web_mod):
    """热键卡倍率与总览倍率共用同一个合法带（后端对两者都跑 _fov_factor_clamp）。"""
    assert _js_const(_html(), 'AIM_PROFILE_FOV_SCALE_MIN') == pytest.approx(web_mod.FOV_FACTOR_MIN)


def test_html_range_factor_clamp_table_uses_shared_min(web_mod):
    src = _html()
    m = re.search(r'range_factor:\s*\[([^\]]+)\]', src)
    assert m, 'index.html 的 NUMERIC_RANGE_LIMITS 里找不到 range_factor'
    assert m.group(1).strip() == 'OVERVIEW_FOV_FACTOR_MIN, 1', (
        'range_factor 的夹取区间必须写成 [OVERVIEW_FOV_FACTOR_MIN, 1]，'
        f'当前是 [{m.group(1).strip()}]'
    )


@pytest.mark.parametrize('elem_id', ['range_factor', 'range_factor_range'])
def test_html_range_factor_inputs_share_the_legal_band(web_mod, elem_id):
    attrs = _input_attrs(_html(), elem_id)
    assert float(attrs['min']) == pytest.approx(web_mod.FOV_FACTOR_MIN), f'#{elem_id} min'
    assert float(attrs['max']) == pytest.approx(1.0), f'#{elem_id} max'


def test_html_overlay_clamp_uses_shared_min_not_zero(web_mod):
    """★ 覆盖层曾是唯一漏改的落点：`clamp(…, 0, 1)` 会让倍率 0 时圆缩成一个点，
    而后端此时写的是 0.05 半径 —— 画出来的圆与实际生效范围不一致。
    """
    src = _html()
    m = re.search(r'function\s+updateAimRangeOverlay\s*\(\)\s*\{.*?\n\}', src, re.S)
    assert m, 'index.html 里找不到 updateAimRangeOverlay'
    body = m.group(0)
    assert 'OVERVIEW_FOV_FACTOR_MIN' in body, '覆盖层的倍率夹取没有用共享下限'
    assert not re.search(r'getNumber\("range_factor"[^)]*\)\s*,\s*0\s*,\s*1\s*\)', body), \
        '覆盖层仍在用 clamp(…, 0, 1)（旧的非共享下限）'


def test_html_overlay_side_is_the_circle_diameter(web_mod):
    """覆盖层边长 = 截取尺寸 × 倍率 = 瞄准范围圆的直径（倍率 1.00 即截取区内接圆）。

    盯的是"边长别被改成半径"：一旦写成 ×0.5，画布上的圆就只有实际范围的一半。
    """
    src = _html()
    m = re.search(r'function\s+updateAimRangeOverlay\s*\(\)\s*\{.*?\n\}', src, re.S)
    body = m.group(0)
    sm = re.search(r'const\s+side\s*=\s*([^;]+);', body)
    assert sm, '覆盖层里找不到 side 的算法'
    expr = sm.group(1)
    assert 'cropSize' in expr and 'rangeFactor' in expr and 'cropScale' in expr, expr
    assert '0.5' not in expr, f'边长不该再乘 0.5（那是半径）：{expr}'


def test_html_range_factor_hint_states_the_inscribed_circle_rule():
    """提示文案必须把口径说清：截取尺寸内**划最大的圆形**。

    文案是用户唯一能看到的"1.00 到底代表什么"的说明；它若含糊，
    用户会把 1.00 理解成"半个屏"或"整屏"，然后按错误预期调参。
    """
    src = _html()
    i = src.index('id="range_factor_range"')
    hint = src[i:i + 900]
    assert '截取' in hint and '圆' in hint, hint[:400]


def test_html_hotkey_fov_scale_hint_says_multiply_and_unified(web_mod):
    """热键卡提示必须说清是"在总览半径上再乘"，且对所有热键统一生效。

    后端只存一份半径，拆不开；文案若还写"每个热键独立"，用户会以为设了没用。
    """
    src = _html()
    i = src.index('aim-profile-fov-scale-range')
    hint = src[i:i + 700]
    assert '乘' in hint, hint[:400]
    assert '统一' in hint or '所有热键' in hint, hint[:400]
