/* console.js — TTBOX 控制台主逻辑（M2.07）
   基于竞品骨架：状态轮询 + collectConfig(data-config) 映射 + 各区块 API 调用。
   端点接线按 m2.07-impl-spec.md §2.4 映射表（YU → TTBOX）；裁剪页不出现。 */

(() => {
  'use strict';

  // ------------------------------------------------------------------
  // API 帮手（统一 {ok, data|error} 契约）
  // ------------------------------------------------------------------
  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: 'same-origin',
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      const err = new Error(payload.error || `HTTP ${response.status}`);
      err.status = response.status;
      throw err;
    }
    return payload.data !== undefined ? payload.data : payload;
  }

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const setText = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
  const fmtPct = (v) => `${Number(v || 0).toFixed(0)}%`;
  const fmtTemp = (t) => (t ? `${Number(t).toFixed(1)}°C` : '--');

  // ------------------------------------------------------------------
  // toast / 状态徽章
  // ------------------------------------------------------------------
  let toastTimer = 0;
  function toast(message, isError = false) {
    const el = $('#toast');
    if (!el) return;
    el.textContent = message || '';
    el.className = `toast${isError ? ' error' : ''}`;
    el.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => { el.hidden = true; }, 2800);
  }

  function setStatusBadge(text, cls) {
    const el = $('#statusBadge');
    if (el) { el.textContent = text; el.className = `status-badge ${cls}`; }
  }

  function setSyncBadge(text, cls) {
    const el = $('#applyIndicator');
    if (el) { el.textContent = text; el.className = `sync-badge ${cls}`; }
  }

  // ------------------------------------------------------------------
  // 页面切换（侧边栏 module-tab ↔ page-card）
  // ------------------------------------------------------------------
  function switchPage(target) {
    $$('.module-tab').forEach((btn) => {
      btn.classList.toggle('is-active', btn.dataset.pageTarget === target);
    });
    $$('.page-card').forEach((card) => {
      card.classList.toggle('is-active', card.id === target);
    });
  }
  $$('.module-tab').forEach((btn) => {
    btn.addEventListener('click', () => switchPage(btn.dataset.pageTarget));
  });

  // ------------------------------------------------------------------
  // data-config：collectConfig / populate（扁平 key 支持 a.b.c 路径）
  // ------------------------------------------------------------------
  function setPath(obj, path, value) {
    const parts = path.split('.');
    let cur = obj;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const key = parts[i].replace(/\[(\d+)\]/, '.$1');
      if (!(key in cur) || typeof cur[key] !== 'object' || cur[key] === null) cur[key] = {};
      cur = cur[key];
    }
    cur[parts[parts.length - 1]] = value;
  }

  function getPath(obj, path) {
    return path.split('.').reduce((acc, key) => (acc === undefined || acc === null ? undefined : acc[key]), obj);
  }

  function coerce(input) {
    if (input.type === 'checkbox') return input.checked;
    if (input.type === 'number' || input.type === 'range') {
      return input.value === '' ? null : Number(input.value);
    }
    return input.value;
  }

  function collectConfig() {
    const body = {};
    $$('[data-config]').forEach((input) => {
      const value = coerce(input);
      if (value === null || value === '') return;
      setPath(body, input.dataset.config, value);
    });
    // aim_profiles[0]：热键/瞄准点/profile 灵敏度（后端从 body.aim_profiles[0] 读取）
    const hotkey = document.getElementById('aimHotkey');
    if (hotkey) {
      const numOrNull = (id) => {
        const el = document.getElementById(id);
        return el && el.value !== '' ? Number(el.value) : null;
      };
      body.aim_profiles = [{
        hotkey: hotkey.value,
        hotkey2: (document.getElementById('aimHotkey2') || {}).value || '',
        hotkey_mode: (document.getElementById('aimHotkeyMode') || {}).value || 'any',
        sensitivity: numOrNull('profile_sensitivity'),
        offset_x: numOrNull('profile_offset_x'),
        offset_y: numOrNull('profile_offset_y'),
      }];
    }
    return body;
  }

  function populateConfig(cfg) {
    $$('[data-config]').forEach((input) => {
      const value = getPath(cfg, input.dataset.config);
      if (value === undefined || value === null) return;
      if (input.type === 'checkbox') input.checked = !!value;
      else input.value = String(value);
    });
    // aim_profiles[0] 回读（与 collectConfig 对称）
    const profiles = (cfg && cfg.aim_profiles) || [];
    const p0 = profiles[0];
    if (p0) {
      const setVal = (id, v) => {
        const el = document.getElementById(id);
        if (el && v !== undefined && v !== null) el.value = String(v);
      };
      setVal('aimHotkey', p0.hotkey || 'right');
      setVal('aimHotkey2', p0.hotkey2 || '');
      setVal('aimHotkeyMode', p0.hotkey_mode || 'any');
      setVal('profile_sensitivity', p0.sensitivity);
      setVal('profile_offset_x', p0.offset_x);
      setVal('profile_offset_y', p0.offset_y);
    }
  }

  let saveTimer = 0;
  function scheduleSave() {
    setSyncBadge('待保存', 'pending');
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(saveConfig, 600);
  }

  async function saveConfig() {
    setSyncBadge('保存中', 'saving');
    try {
      const data = await api('/api/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectConfig()),
      });
      populateConfig(data || {});
      setSyncBadge('已同步', 'ready');
    } catch (err) {
      setSyncBadge('保存失败', 'error');
      toast(err.message, true);
    }
  }

  $$('[data-config]').forEach((input) => {
    input.addEventListener('change', scheduleSave);
    if (input.type === 'range' || input.type === 'number' || input.type === 'text') {
      input.addEventListener('input', () => {
        // range → 数值框联动（slider-field 内同组）
        const wrap = input.closest('.slider-field');
        if (!wrap) return;
        const num = wrap.querySelector('.value-input');
        const range = wrap.querySelector('input[type="range"]');
        if (num && range && input === range) num.value = range.value;
      });
    }
  });

  async function loadConfig() {
    try {
      populateConfig(await api('/api/config'));
      setSyncBadge('已同步', 'ready');
    } catch (err) {
      setSyncBadge('离线', 'error');
    }
  }

  // ------------------------------------------------------------------
  // 品牌应用（M2.04 ui_brand 语义，token 来自 _page_context）
  // ------------------------------------------------------------------
  function applyBrand(ui) {
    if (!ui) return;
    setText('brandMark', ui.brand_mark || 'TT');
    setText('brandEyebrow', ui.brand_eyebrow || 'TTBOX SYSTEM');
    setText('brandTitle', ui.brand_title || 'TTBOX 控制台');
    if (ui.theme && ui.theme.accent) {
      document.documentElement.style.setProperty('--accent', ui.theme.accent);
    }
  }

  // ------------------------------------------------------------------
  // 状态轮询 /api/state（指标 / 激活锁 / 风扇 / 板载资源）
  // ------------------------------------------------------------------
  function applyState(data) {
    if (!data) return;
    const st = data.state || {};
    const lic = st.license || {};
    const detection = st.detection || {};
    const capture = st.capture || {};
    const latency = st.latency || {};
    document.body.classList.toggle('license-locked', !lic.activated);
    setStatusBadge(lic.activated ? '已激活' : '未激活', lic.activated ? 'live' : 'error');

    setText('mobileLatency', latency.e2e_ms !== undefined ? `${latency.e2e_ms} ms` : '-- ms');
    setText('mobileCaptureFps', `${capture.capture_fps || 0} 帧/秒`);
    setText('mobileFps', `${detection.inference_fps || 0} 帧/秒`);
    setText('mobileVideoStatus', data.state && st.status === 'running' ? '运行中'
      : (st.status === 'degraded' ? '降级' : '待机'));
    setText('activeModelName', data.selected_model_id || st.selected_model_id || '未选择');

    // 激活卡（总览）：云端字段（卡密类型/到期北京时间/解绑余量/心跳在线）
    const cloud = st.cloud || {};
    setText('licenseCardType', lic.activated ? `卡密（${lic.plan || 'subscription'}）` : '未激活');
    setText('licenseExpireAt', cloud.expire_at || '—');
    const maxD = Number(cloud.max_devices || 0);
    setText('licenseDevices', maxD > 0 ? `剩余解绑 ${maxD} 台` : '—');
    const hb = cloud.heartbeat || {};
    setText('licenseHeartbeat', hb.online ? '在线' : (hb.error || '离线'));

    // 风扇（读路径真实 PWM；写路径 P1）
    const fan = st.fan_control || {};
    setText('fanPercent', fmtPct(fan.pwm_percent));
    setText('fanTemperature', fmtTemp(fan.temperature_celsius));

    // 检测统计（实时锁定区块）
    setText('detectCount', String(detection.detections || 0));
    setText('trackCount', String(detection.tracks || 0));
  }

  async function pollState() {
    try {
      applyState(await api('/api/state'));
    } catch (err) {
      if (err.status === 403) { window.location.replace('/activate'); return; }
      setStatusBadge('未连接', 'error');
    }
  }

  async function pollSystemResources() {
    // 板载资源（真实 procfs/sysfs 读数来自 /api/system）
    try {
      const sys = await api('/api/system');
      setText('resCpu', fmtPct(sys.cpu_percent));
      setText('resMem', fmtPct(sys.memory && sys.memory.percent));
      setText('resStorage', fmtPct(sys.storage && sys.storage.percent));
      setText('socTemperature', fmtTemp(sys.temperature && sys.temperature.celsius));
    } catch (err) { /* ignore */ }
  }

  // ------------------------------------------------------------------
  // 启动/停止 + 重启/关机（免密直通）
  // ------------------------------------------------------------------
  async function toggleControl() {
    const btn = $('#startButton');
    if (!btn) return;
    const isRunning = btn.classList.contains('start');
    try {
      await api(isRunning ? '/api/control/stop' : '/api/control/start', { method: 'POST' });
      btn.classList.toggle('start', !isRunning);
      btn.classList.toggle('stop', isRunning);
      btn.querySelector('strong').textContent = isRunning ? '启动' : '停止';
      toast(isRunning ? '已停止 AI 链路' : 'AI 链路已启动');
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function systemAction(path, label) {
    try {
      await api(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      toast(`${label}指令已下发`);
    } catch (err) {
      toast(err.message, true);
    }
  }

  // ------------------------------------------------------------------
  // 模型库（导入语义 = ONNX→RKNN 转换链）
  // ------------------------------------------------------------------
  async function refreshModels() {
    const tbody = $('#modelTableBody');
    if (!tbody) return;
    try {
      const data = await api('/api/models');
      const rows = (data && (data.models || data.installed)) || [];
      tbody.innerHTML = '';
      rows.forEach((m) => {
        const tr = document.createElement('tr');
        if (m.active || m.selected) tr.className = 'is-active';
        const name = m.label || m.model_id || m.id || '';
        const fmt = m.format || m.source_format || '';
        const status = m.active || m.selected ? '使用中' : (m.status || '就绪');
        tr.innerHTML = `<td>${name}</td><td>${fmt}</td><td>${status}</td>
          <td><button class="ghost-button" data-model-select="${m.model_id || m.id}">选择</button>
              <button class="ghost-button" data-model-delete="${m.model_id || m.id}">删除</button></td>`;
        tbody.appendChild(tr);
      });
    } catch (err) {
      toast(`模型列表加载失败：${err.message}`, true);
    }
  }

  async function refreshPresets() {
    const tbody = $('#presetTableBody');
    if (!tbody) return;
    try {
      const data = await api('/api/presets');
      const rows = (data && (data.presets || data.items)) || [];
      tbody.innerHTML = '';
      rows.forEach((p) => {
        const name = typeof p === 'string' ? p : (p.name || '');
        const tr = document.createElement('tr');
        tr.innerHTML = `<td>${name}</td>
          <td><button class="ghost-button" data-preset-load="${name}">加载</button>
              <button class="ghost-button" data-preset-export="${name}">导出</button>
              <button class="ghost-button" data-preset-delete="${name}">删除</button></td>`;
        tbody.appendChild(tr);
      });
    } catch (err) {
      toast(`预设列表加载失败：${err.message}`, true);
    }
  }

  async function refreshWifi() {
    const list = $('#wifiList');
    if (!list) return;
    try {
      const data = await api('/api/network/wifi');
      const status = data && (data.status || data);
      setText('wifiStatus', status && status.ssid ? `已连接：${status.ssid}` : '未连接');
    } catch (err) { /* 激活前白名单可达但可能失败：静默 */ }
  }

  async function refreshBlocklist() {
    if (!$('#blocklistSummary')) return;
    try {
      const data = await api('/api/system/lan-blocklist');
      const ips = (data && (data.blocked_ips || data.ips)) || [];
      setText('blocklistSummary', ips.length ? `${ips.length} 个 IP` : '无');
    } catch (err) { /* ignore */ }
  }

  async function refreshHardwarePage() {
    // 显示器（EDID 身份配置走 GET/PUT /api/hardware/display）
    try {
      const disp = await api('/api/hardware/display');
      const modes = (disp && (disp.modes || disp.available_modes)) || [];
      const current = (disp && (disp.current || disp.current_mode)) || '';
      setText('displayCurrentMode', typeof current === 'string' ? current : (current.label || '—'));
      const sel = $('#displayModeSelect');
      if (sel && modes.length) {
        sel.innerHTML = '';
        modes.forEach((m) => {
          const opt = document.createElement('option');
          const token = m.token || m.id || m;
          opt.value = typeof token === 'string' ? token : String(token);
          opt.textContent = m.label || opt.value;
          if (current && (current.token === opt.value || current === opt.value)) opt.selected = true;
          sel.appendChild(opt);
        });
      }
    } catch (err) { /* ignore */ }
    // 鼠标（USB 直出硬件信息 + 输出模式）
    try {
      const mouse = await api('/api/hardware/mouse');
      const info = (mouse && (mouse.device || mouse)) || {};
      setText('mouseDeviceName', info.product || info.name || (mouse && mouse.mode) || '—');
      const modes = (mouse && (mouse.modes || mouse.available_modes)) || [];
      const sel = $('#mouseModeSelect');
      if (sel && modes.length) {
        sel.innerHTML = '';
        modes.forEach((m) => {
          const opt = document.createElement('option');
          const token = m.token || m.id || m;
          opt.value = typeof token === 'string' ? token : String(token);
          opt.textContent = m.label || opt.value;
          if (mouse.mode && mouse.mode === opt.value) opt.selected = true;
          sel.appendChild(opt);
        });
      }
    } catch (err) { /* ignore */ }
  }

  async function refreshSystemPage() {
    try {
      const sys = await api('/api/system');
      setText('sysHostname', sys.hostname || '');
      setText('sysLanIp', sys.lan_ipv4 || '');
      setText('sysVersion', sys.app_version || sys.version || '');
      if (sys.uptime_seconds !== undefined) {
        const d = Math.floor(Number(sys.uptime_seconds) / 86400);
        const h = Math.floor((Number(sys.uptime_seconds) % 86400) / 3600);
        setText('sysUptime', `${d} 天 ${h} 小时`);
      }
    } catch (err) { /* ignore */ }
    try {
      const lic = await api('/api/license');
      const l = (lic && lic.license) || {};
      const cloud = (lic && lic.cloud) || {};
      setText('licensePageState', l.activated ? (l.state || 'valid') : 'unactivated');
      setText('licensePageExpire', cloud.expire_at || '—');
      setText('licensePageCard', cloud.card_mask || l.short_code || '—');
      const hb = cloud.heartbeat || {};
      setText('licensePageHeartbeat', hb.online ? '在线' : (hb.error || '离线'));
      setText('licensePageMachine', cloud.machine_code || '—');
      setText('licensePageDevices', cloud.max_devices ? `剩余解绑 ${cloud.max_devices} 台` : '—');
    } catch (err) { /* ignore */ }
  }

  // ------------------------------------------------------------------
  // 事件绑定
  // ------------------------------------------------------------------
  function bindEvents() {
    const start = $('#startButton');
    if (start) start.addEventListener('click', toggleControl);

    const reboot = $('#rebootButton');
    if (reboot) reboot.addEventListener('click', () => systemAction('/api/system/reboot', '重启'));
    const poweroff = $('#poweroffButton');
    if (poweroff) poweroff.addEventListener('click', () => {
      if (window.confirm('确认关机？关机后需手动上电。')) systemAction('/api/system/poweroff', '关机');
    });

    const refreshLic = $('#refreshLicenseButton');
    if (refreshLic) refreshLic.addEventListener('click', () => pollState().then(() => toast('授权状态已刷新')));
    const homeLic = $('#homeLicenseKey');
    const homeLicBtn = $('#homeLicenseActivate');
    if (homeLicBtn && homeLic) homeLicBtn.addEventListener('click', async () => {
      const key = homeLic.value.trim();
      if (!key) { toast('请输入卡密', true); return; }
      homeLicBtn.disabled = true;
      try {
        await api('/api/license/activate', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ license_key: key }),
        });
        toast('激活成功，正在刷新');
        setTimeout(() => window.location.reload(), 600);
      } catch (err) {
        toast(err.message, true);
      } finally {
        homeLicBtn.disabled = false;
      }
    });

    const modelImport = $('#modelImportInput');
    const modelImportBtn = $('#modelImportButton');
    if (modelImportBtn && modelImport) modelImportBtn.addEventListener('click', async () => {
      if (!modelImport.files || !modelImport.files[0]) { toast('请选择 .onnx 文件', true); return; }
      const fd = new FormData();
      fd.append('file', modelImport.files[0]);
      modelImportBtn.disabled = true;
      try {
        await api('/api/models/import-onnx', { method: 'POST', body: fd });
        toast('已提交 ONNX→RKNN 转换，稍后刷新查看');
        setTimeout(refreshModels, 1500);
      } catch (err) {
        toast(err.message, true);
      } finally {
        modelImportBtn.disabled = false;
      }
    });

    document.addEventListener('click', async (ev) => {
      const sel = ev.target.closest('[data-model-select]');
      const del = ev.target.closest('[data-model-delete]');
      const pload = ev.target.closest('[data-preset-load]');
      const pdel = ev.target.closest('[data-preset-delete]');
      const wconn = ev.target.closest('[data-wifi-connect]');
      try {
        if (sel) {
          await api('/api/models/select', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_id: sel.dataset.modelSelect }) });
          toast('模型已选择'); refreshModels();
        } else if (del) {
          if (!window.confirm('确认删除该模型？')) return;
          await api('/api/models/delete', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ model_id: del.dataset.modelDelete }) });
          toast('模型已删除'); refreshModels();
        } else if (pload) {
          await api('/api/presets/load', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: pload.dataset.presetLoad }) });
          toast('预设已加载'); await loadConfig();
        } else if (pdel) {
          if (!window.confirm('确认删除该预设？')) return;
          await api('/api/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: pdel.dataset.presetDelete, action: 'delete' }) });
          toast('预设已删除'); refreshPresets();
        } else if (wconn) {
          const ssid = wconn.dataset.wifiConnect;
          const pass = window.prompt(`输入 "${ssid}" 的密码：`) || '';
          await api('/api/network/wifi/connect', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ssid, password: pass }) });
          toast('Wi-Fi 连接指令已下发'); refreshWifi();
        }
      } catch (err) {
        toast(err.message, true);
      }
    });

    const presetSave = $('#presetSaveButton');
    const presetName = $('#presetNameInput');
    if (presetSave && presetName) presetSave.addEventListener('click', async () => {
      const name = presetName.value.trim();
      if (!name) { toast('请输入预设名称', true); return; }
      try {
        await api('/api/presets', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, config: collectConfig() }) });
        toast('预设已保存'); refreshPresets();
      } catch (err) { toast(err.message, true); }
    });

    const wifiScan = $('#wifiScanButton');
    if (wifiScan) wifiScan.addEventListener('click', async () => {
      const list = $('#wifiScanList');
      try {
        const data = await api('/api/network/wifi/scan', { method: 'POST' });
        const nets = (data && (data.networks || data.ap_list)) || [];
        if (list) {
          list.innerHTML = '';
          nets.forEach((n) => {
            const ssid = typeof n === 'string' ? n : (n.ssid || '');
            if (!ssid) return;
            const li = document.createElement('li');
            li.innerHTML = `<span>${ssid}</span> <button class="ghost-button" data-wifi-connect="${ssid}">连接</button>`;
            list.appendChild(li);
          });
        }
      } catch (err) { toast(err.message, true); }
    });

    const otaInput = $('#otaUrlInput');
    const otaBtn = $('#otaInstallButton');
    if (otaBtn && otaInput) otaBtn.addEventListener('click', async () => {
      const url = otaInput.value.trim();
      if (!url) { toast('请输入更新包 https 地址', true); return; }
      try {
        await api('/api/update/install', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url }) });
        toast('OTA 安装已调度，设备将在后台更新');
      } catch (err) { toast(err.message, true); }
    });

    const blocklistBtn = $('#blocklistRefreshButton');
    if (blocklistBtn) blocklistBtn.addEventListener('click', refreshBlocklist);

    const dispApply = $('#displayApplyButton');
    if (dispApply) dispApply.addEventListener('click', async () => {
      const sel = $('#displayModeSelect');
      if (!sel || !sel.value) { toast('请选择显示模式', true); return; }
      try {
        await api('/api/hardware/display', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: sel.value }),
        });
        toast('显示模式已应用');
        refreshHardwarePage();
      } catch (err) { toast(err.message, true); }
    });

    const mouseApply = $('#mouseModeApplyButton');
    if (mouseApply) mouseApply.addEventListener('click', async () => {
      const sel = $('#mouseModeSelect');
      if (!sel || !sel.value) { toast('请选择鼠标模式', true); return; }
      try {
        await api('/api/hardware/mouse/mode', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ mode: sel.value }),
        });
        toast('鼠标模式已应用');
      } catch (err) { toast(err.message, true); }
    });

    const circleBtn = $('#mouseTestCircleButton');
    if (circleBtn) circleBtn.addEventListener('click', async () => {
      try {
        await api('/api/mouse-output/test-circle', { method: 'POST' });
        toast('画圆测试已下发');
      } catch (err) { toast(err.message, true); }
    });
  }

  // ------------------------------------------------------------------
  // 启动
  // ------------------------------------------------------------------
  function init() {
    bindEvents();
    pollState();
    loadConfig();
    refreshModels();
    refreshPresets();
    refreshWifi();
    refreshBlocklist();
    refreshHardwarePage();
    refreshSystemPage();
    pollSystemResources();
    // 轮询：状态 2s；系统/资源 10s
    window.setInterval(pollState, 2000);
    window.setInterval(refreshSystemPage, 10000);
    window.setInterval(pollSystemResources, 10000);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
