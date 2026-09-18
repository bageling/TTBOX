# test_web_console_parity.py — 面板 1:1 采用「结构 + 品牌插槽 + 零外联」回归
#
# 背景：面板 = 上游 YU 控制台**整包落地**为 plugins/web/templates/index.html，
# 叠加 4 处品牌插槽（data-ui-brand / data-theme / <title> / body.ui-brand-*）。
# 本文件锁死三件事，防止「换模板/改品牌」时把整包结构或零外联约束悄悄破坏：
#   1 12 个 section.*-page 与 12 个 data-page-target 一一对应（= 参照物的 12 页签契约）；
#   2 4 处品牌插槽齐全（模板 Jinja 变量名与 _page_context() 契约一致）；
#   3 面板**零外联**：无 <link>、无 <script src>、无 http(s):// 资源（本仓无构建链、离线可用）。
#
# 运行：python -m pytest plugins/web/tests/test_web_console_parity.py -v（从仓库根）
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / 'plugins' / 'web' / 'templates' / 'index.html'

# S1 减法（2026-09-18）后的 9 页签 section id（DOM 内 section 出现顺序）
# （07 Hailo / 08 键鼠盒子 / 09 无线 三个页签整段移除）
EXPECTED_PAGES = [
    'home', 'profiles', 'control', 'assist', 'model',
    'hardware', 'preset', 'license', 'fan',
]

# [布局冻结] 侧栏页签顺序 = 模板中 data-page-target 的出现顺序 = 01..09 展示顺序
EXPECTED_TAB_TARGETS = [
    'home-page', 'profiles-page', 'control-page', 'assist-page', 'model-page',
    'hardware-page', 'preset-page', 'license-page', 'fan-page',
]

# [布局冻结] 侧栏页签文案（编号 + 名称），逐字锁死
EXPECTED_TAB_LABELS = [
    '01总览', '02热键控制', '03移动控制', '04辅助功能', '05模型库', '06显示与鼠标',
    '07预设参数', '08系统状态', '09风扇控制',
]

# [布局冻结] .app-shell 固定宽度基线（px）；停用的上游响应式断点共 4 个
FROZEN_APP_SHELL_WIDTH = 1660
FROZEN_DISABLED_BREAKPOINTS = (1180, 920, 480, 560)
LAYOUT_FREEZE_DOC = (
    REPO_ROOT / 'docs' / 'handover' / '2026-09-17' / '控制台布局冻结基线-2026-09-18.md'
)


def _src() -> str:
    return TEMPLATE.read_text(encoding='utf-8')


def test_template_exists():
    assert TEMPLATE.is_file(), f'面板模板缺失: {TEMPLATE}'


def test_nine_pages_present():
    """9 个 section id=*-page：多一个/少一个都说明整包落地被裁剪或减法未同步。"""
    ids = re.findall(r'<section id="([a-z0-9\-]+)-page"', _src())
    assert len(ids) == 9, f'期望 9 个 section.*-page，实际 {len(ids)}: {ids}'
    assert set(ids) == set(EXPECTED_PAGES)


def test_nine_page_targets_present():
    """9 个 data-page-target（侧栏导航），与 9 个 section 一一对应。"""
    targets = re.findall(r'data-page-target="([a-z0-9\-]+)"', _src())
    assert len(targets) == 9, f'期望 9 个 data-page-target，实际 {len(targets)}: {targets}'
    assert set(targets) == {p + '-page' for p in EXPECTED_PAGES}


def test_license_gate_overlay_present():
    """面板保留上游 #licenseGateOverlay（运行期掉线/被撤销的就地兜底·第二重）。"""
    src = _src()
    assert 'id="licenseGateOverlay"' in src
    assert 'setLicenseNavigationLock' in src


def test_brand_slots_present():
    """4 处品牌插槽（模板变量名必须与 _page_context() 契约一致）。"""
    src = _src()
    assert 'data-ui-brand="{{ ui_skin }}"' in src
    assert 'data-theme="{{ default_theme }}"' in src
    assert '<title>{{ app_title }}</title>' in src
    assert 'class="license-loading ui-brand-{{ ui_skin }}"' in src


def test_zero_external_resources():
    """零外联：无 <link>、无 <script ... src=...>、无 http(s):// 资源引用。"""
    src = _src()
    assert '<link' not in src, '面板不得引入外链 <link>'
    assert re.search(r'<script[^>]*\ssrc=', src) is None, '面板不得引入 <script src=...>'
    assert re.search(r'(?:src|href)\s*=\s*["\']https?://', src) is None, '面板不得引用 http(s) 资源'
    # 内联脚本/样式是唯一形态（本仓无构建链）
    assert '<style>' in src and '</style>' in src
    assert '<script>' in src and '</script>' in src


def test_no_jinja_delimiter_leak_beyond_slots():
    """除 4 处品牌插槽外，模板不得残留其它 Jinja 变量（防止上游升级引入 {{ 冲突）。"""
    src = _src()
    stripped = (src
                .replace('{{ ui_skin }}', '')
                .replace('{{ default_theme }}', '')
                .replace('{{ app_title }}', ''))
    assert '{{' not in stripped
    assert '{%' not in stripped
    assert '{#' not in stripped


# ===================================================================
# [布局冻结] 现阶段定死该控制台布局（2026-09-18）
#   · 视觉：.app-shell 固定宽度 1660px，不再随窗口自适应；
#   · 基线：12 页签「顺序 + 文案」冻结，4 个上游响应式断点全部停用。
#   任何一处漂移 ⇒ 本段断言失败（= 改布局即测试失败）。
#   依据文档：docs/handover/2026-09-17/控制台布局冻结基线-2026-09-18.md
# ===================================================================


def _nav_block() -> str:
    """侧栏导航 <nav class="module-tabs">…</nav> 片段（页签顺序/文案的唯一来源）。"""
    m = re.search(r'<nav class="module-tabs".*?</nav>', _src(), re.S)
    assert m, '侧栏导航 <nav class="module-tabs"> 缺失'
    return m.group(0)


def test_nav_tab_order_is_frozen():
    """[布局冻结] 侧栏页签顺序锁死：data-page-target 出现顺序必须与基线完全一致。"""
    targets = re.findall(r'data-page-target="([a-z0-9\-]+)"', _nav_block())
    assert targets == EXPECTED_TAB_TARGETS, f'页签顺序漂移: {targets}'


def test_nav_tab_labels_are_frozen():
    """[布局冻结] 侧栏页签文案锁死：编号 + 名称必须逐字与基线一致。"""
    pairs = re.findall(r'<span>(\d{2})</span>([^<\n]+)', _nav_block())
    labels = [num + name.strip() for num, name in pairs]
    assert labels == EXPECTED_TAB_LABELS, f'页签文案漂移: {labels}'


def test_section_ids_match_tab_targets():
    """[布局冻结] 9 个 section id=*-page 的集合必须与页签契约一一对应（不多不少）。"""
    ids = re.findall(r'<section id="([a-z0-9\-]+)-page"', _src())
    expected_ids = {t[: -len('-page')] for t in EXPECTED_TAB_TARGETS}
    assert len(ids) == 9, f'期望 9 个 section.*-page，实际 {len(ids)}: {ids}'
    assert set(ids) == expected_ids, f'section id 集合与页签契约不一致: {sorted(set(ids))}'


def test_layout_width_is_frozen():
    """[布局冻结] .app-shell 固定宽度 1660px（width 与 min-width 各自锁死）。

    ★ 逐属性**行首锚定**匹配，规避子串"掩蔽"盲区：`'width: 1660px;' in block`
      会被 `min-width: 1660px;` 命中，导致仅改 `width`（如 1660→1661）时护栏**漏报**。
      锚定 `^\\s*width:` 后，`  min-width:` 行不再误配（其行首为 `m` 而非 `w`）。
    """
    m = re.search(r'\.app-shell\s*\{([^}]*)\}', _src())
    assert m, '.app-shell 基础规则缺失'
    block = m.group(1)
    width_m = re.search(r'(?m)^\s*width:\s*([0-9]+px)\s*;\s*$', block)
    minw_m = re.search(r'(?m)^\s*min-width:\s*([0-9]+px)\s*;\s*$', block)
    assert width_m, f'.app-shell 缺少 width 声明: {block!r}'
    assert minw_m, f'.app-shell 缺少 min-width 声明: {block!r}'
    assert width_m.group(1) == f'{FROZEN_APP_SHELL_WIDTH}px', \
        f'.app-shell width 漂移: {width_m.group(1)} (期望 {FROZEN_APP_SHELL_WIDTH}px)'
    assert minw_m.group(1) == f'{FROZEN_APP_SHELL_WIDTH}px', \
        f'.app-shell min-width 漂移: {minw_m.group(1)} (期望 {FROZEN_APP_SHELL_WIDTH}px)'
    # 旧的自适应写法必须绝迹（否则窄窗口会重新计算宽度）
    assert 'min(1660px' not in _src(), '不得残留 min(1660px, ...) 自适应宽度'


def test_responsive_breakpoints_disabled():
    """[布局冻结] 所有 @media 断点条件必须为 max-width: 0px（永不匹配）。

    上游 1180/920/480/560 四个响应式断点已停用；若有人复活任一数值宽度断点，
    小屏折叠规则会重新裁掉固定宽度布局（尤其 920px 段的 html,body{overflow-x:hidden}）
    —— 本断言即失败（= 改布局即测试失败）。
    """
    src = _src()
    conditions = re.findall(r'@media\s*\(([^)]*)\)', src)
    assert len(conditions) == len(FROZEN_DISABLED_BREAKPOINTS), (
        f'期望 {len(FROZEN_DISABLED_BREAKPOINTS)} 个 @media，实际 {len(conditions)}: {conditions}'
    )
    for cond in conditions:
        assert cond.replace(' ', '') == 'max-width:0px', f'存在未停用的断点: {cond!r}'
    # 数值宽度的 max-width 断点 / 任意 min-width 断点均不许存在
    assert re.search(r'@media\s*\(max-width:\s*(?!0px)\d', src) is None, '存在数值宽度 @media 断点'
    assert re.search(r'@media\s*\(min-width', src) is None, '不得使用 min-width 断点'


def test_layout_freeze_doc_exists():
    """[布局冻结] 基线文档固化（改布局须与文档同步）。"""
    assert LAYOUT_FREEZE_DOC.is_file(), f'布局冻结基线文档缺失: {LAYOUT_FREEZE_DOC}'
    text = LAYOUT_FREEZE_DOC.read_text(encoding='utf-8')
    assert '1660' in text
    assert 'max-width: 0px' in text, '文档须记录断点停用写法 max-width: 0px'
