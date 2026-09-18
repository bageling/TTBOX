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

# 参照物 12 页签的 section id（顺序即侧栏 01..12）
EXPECTED_PAGES = [
    'home', 'profiles', 'control', 'assist', 'model', 'wifi',
    'hardware', 'hailo', 'kmbox', 'preset', 'license', 'fan',
]


def _src() -> str:
    return TEMPLATE.read_text(encoding='utf-8')


def test_template_exists():
    assert TEMPLATE.is_file(), f'面板模板缺失: {TEMPLATE}'


def test_twelve_pages_present():
    """12 个 section id=*-page：多一个/少一个都说明整包落地被裁剪。"""
    ids = re.findall(r'<section id="([a-z0-9\-]+)-page"', _src())
    assert len(ids) == 12, f'期望 12 个 section.*-page，实际 {len(ids)}: {ids}'
    assert set(ids) == set(EXPECTED_PAGES)


def test_twelve_page_targets_present():
    """12 个 data-page-target（侧栏导航），与 12 个 section 一一对应。"""
    targets = re.findall(r'data-page-target="([a-z0-9\-]+)"', _src())
    assert len(targets) == 12, f'期望 12 个 data-page-target，实际 {len(targets)}: {targets}'
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
