# test_web_update_ui_stability.py — 系统更新卡片 UI 稳定化的回归锁
#
# 背景（业主 2026-09-20 反馈）：
#   ① 「更新完刷新会跳到预设参数那里」—— initPageNavigation 会恢复
#      localStorage[aiassistance_active_page]，而「系统状态」页是免记页
#      （LICENSE_GATE_PAGE_ID），于是更新后刷新只会落回更早停留的页签（常见是 07 预设参数）。
#   ② 「进度条一会有一会没有，乱显示」—— 面板空闲态用 display:none 收起进度头/进度条，
#      而安装提交后到更新器写出 RUNNING 之间，状态端点会先返回 idle ⇒ 进度条闪没再冒出。
#
# 本文件锁死修复后的口径：
#   ① 空闲态不再 display:none 收起进度头/进度条（结构恒定，只淡化数值）；
#   ② renderUpdateStatus 有「更新会话闩」：收到 idle 且在会期窗口内一律忽略；
#   ③ installUpdatePlan 提交后上闩；④ 更新成功刷新前把记住的页签归位到首页。
#
# 运行：python -m pytest plugins/web/tests/test_web_update_ui_stability.py -v
from __future__ import annotations

import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
TEMPLATE = REPO_ROOT / 'plugins' / 'web' / 'templates' / 'index.html'

HOME_PAGE_ID = 'home-page'


@pytest.fixture(scope='module')
def template_src() -> str:
    return TEMPLATE.read_text(encoding='utf-8')


def _js_function(src: str, signature: str) -> str:
    """抽出一个 JS 函数体（按大括号配对）。找不到签名就断言失败。"""
    start = src.find(signature)
    assert start >= 0, '未找到函数：%s' % signature
    brace = src.find('{', start)
    assert brace >= 0, '函数体缺失：%s' % signature
    depth = 0
    for i in range(brace, len(src)):
        ch = src[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError('大括号不配对：%s' % signature)


def test_idle_panel_does_not_hide_progress_bar(template_src):
    # 空闲态只剩淡化，不能再 display:none —— 显隐切换就是「进度条一会有一会没有」。
    idx = template_src.find('.update-progress-panel.is-idle')
    assert idx >= 0, '找不到 .update-progress-panel.is-idle 样式块'
    block = template_src[idx:template_src.index('}', idx) + 1]
    assert 'display: none' not in block, '空闲态仍在 display:none 收起进度头/进度条'
    assert 'update-progress-track' not in block, '空闲态仍单独隐藏进度条'


def test_render_update_status_has_session_latch(template_src):
    body = _js_function(template_src, 'function renderUpdateStatus(')
    # 收到 idle 时若在会期窗口内必须直接 return，不落到 DOM 写入。
    assert 'updateSessionDeadline' in body
    assert 'Date.now() < state.updateSessionDeadline' in body


def test_install_arms_session_latch(template_src):
    body = _js_function(template_src, 'async function installUpdatePlan(')
    assert 'state.updateSessionDeadline = Date.now() + UPDATE_SESSION_TIMEOUT_MS' in body


def test_update_refresh_lands_on_home_page(template_src):
    body = _js_function(template_src, 'function scheduleUpdatePageRefresh(')
    assert 'ACTIVE_PAGE_STORAGE_KEY' in body
    assert '"%s"' % HOME_PAGE_ID in body
    # 归位必须发生在 reload 之前。
    reset_at = body.find('ACTIVE_PAGE_STORAGE_KEY')
    reload_at = body.find('window.location.reload()')
    assert reset_at >= 0 and reload_at >= 0
    assert reset_at < reload_at, '页签归位必须在 reload 之前执行'


def test_state_initialises_session_deadline(template_src):
    assert 'updateSessionDeadline: 0,' in template_src
