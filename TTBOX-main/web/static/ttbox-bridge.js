// ttbox-bridge.js —— 在 yu 的 app.js 之前加载。
// 把 yu 前端的 /api/* 请求桥接到 TTBOX Core 的 /api/v1/*，返回 yu 形状 {ok, data}。
// v2：完整双向翻译（PUT /api/config: yu body ↔ RuntimeProfile；热键字符串↔位图；state 真数据）。
(function () {
  'use strict';

  // ---- 防蹦迪节流：10 秒内最多一次页面重载（授权 gate 循环保险丝）----
  (function () {
    const origReload = window.location.reload.bind(window.location);
    let lastReload = 0;
    window.location.reload = function () {
      const now = Date.now();
      if (now - lastReload < 10000) {
        console.warn('[ttbox-bridge] 已节流一次页面重载');
        return;
      }
      lastReload = now;
      origReload();
    };
  })();

  const origFetch = window.fetch.bind(window);

  // ---- 热键字符串 ↔ TTBOX 位图 ----
  const HOTKEY_BITS = { left: 1, right: 2, middle: 4, back: 8, forward: 16 };
  const BIT_HOTKEYS = { 1: 'left', 2: 'right', 4: 'middle', 8: 'back', 16: 'forward' };
  function hotkeyToBits(s) { return HOTKEY_BITS[s] || 0; }
  function bitsToHotkey(n) { return BIT_HOTKEYS[n] || ''; }

  // ---- yu controller → TTBOX mouse 字段映射 ----
  const CONTROLLER_MAP = {
    kp_x: 'kp_x', kp_y: 'kp_y', kd_x: 'kd_x', kd_y: 'kd_y',
    ki_x: 'ki_x', ki_y: 'ki_y', predict_x: 'predict_x', predict_y: 'predict_y',
    rate_x: 'rate_x', rate_y: 'rate_y',
    smooth_x: 'smooth_x', smooth_y: 'smooth_y',
    output_deadzone: 'output_deadzone',
    selector_lost_grace_ms: 'lost_grace_ms',
    aim_reference_offset_x: 'aim_offset_x',
    aim_reference_offset_y: 'aim_offset_y',
    y_axis_fire_hotkey: 'y_axis_fire_hotkey',
    y_axis_fire_release_delay_sec: 'y_axis_fire_release_delay_sec',
    aim_fire_lock_y: 'aim_fire_lock_y',
  };

  // ---- yu body → RuntimeProfile（SET_CONFIG）----
  function yuBodyToProfile(body) {
    const ctrl = (body.ai && body.ai.controller) || {};
    const mouse = {};
    for (const [yuKey, ttKey] of Object.entries(CONTROLLER_MAP)) {
      if (ctrl[yuKey] !== undefined) {
        if (yuKey === 'y_axis_fire_hotkey') {
          mouse[ttKey] = hotkeyToBits(ctrl[yuKey]);
        } else {
          mouse[ttKey] = ctrl[yuKey];
        }
      }
    }
    // 热键主/副/触发方式：yu 用 aim_profiles[0]（第一套热键档案）
    const profile0 = (Array.isArray(body.aim_profiles) && body.aim_profiles[0]) || {};
    if (profile0.hotkey !== undefined) mouse.aim_hotkey = hotkeyToBits(profile0.hotkey);
    if (profile0.hotkey2 !== undefined) mouse.aim_hotkey2 = hotkeyToBits(profile0.hotkey2);
    if (profile0.hotkey_mode !== undefined) mouse.aim_hotkey_mode = profile0.hotkey_mode;
    if (profile0.sensitivity !== undefined) mouse.sensitivity = profile0.sensitivity;
    if (body.sens !== undefined) mouse.sensitivity = body.sens;

    // inference
    const inference = {};
    if (body.video_detection_confidence !== undefined) inference.confidence = body.video_detection_confidence;
    if (body.video_detection_iou !== undefined) inference.iou = body.video_detection_iou;

    // capture
    const cap = body.capture || {};
    const capture = {};
    if (cap.crop_size !== undefined) {
      capture.width = cap.crop_size;
      capture.height = cap.crop_size;
    }
    if (cap.crop_offset_x !== undefined) capture.offset_x = cap.crop_offset_x;
    if (cap.crop_offset_y !== undefined) capture.offset_y = cap.crop_offset_y;

    // fov：yu range_factor 语义 = FOV 范围因子（0~1，1=全屏）。
        // TTBOX 侧 fov.enabled=true + radius 才真正启用 FOV 过滤（AimThread/Decode 双消费）。
        // 映射：range_factor < 1.0 → 用户收紧视场 → 启用 FOV；=1.0 → 全屏关闭 FOV。
        const fov = {};
        if (body.range_factor !== undefined) {
          fov.radius = body.range_factor;
          fov.enabled = body.range_factor < 1.0;
          if (!fov.enabled) {
            fov.radius = 0.5; // 关闭时给默认半径（AimThread fov_range=radius*2=1.0 未生效分支）
          }
        }

        // latency → preview 帧率（yu preview_interval_ms = 每帧间隔 ms；TTBOX PreviewProfile.fps）
        const preview = {};
        const lat = body.latency || {};
        if (lat.preview_interval_ms !== undefined) {
          const iv = Number(lat.preview_interval_ms);
          if (Number.isFinite(iv) && iv > 0) {
            preview.fps = Math.max(1, Math.min(15, Math.round(1000 / iv)));
          }
        }

        const profile = { mouse, inference, capture, fov };
        if (Object.keys(preview).length > 0) profile.preview = preview;
        if (body.model_id !== undefined) profile.model_id = body.model_id;
        return profile;
  }

  // ---- RuntimeProfile → yu body（GET /api/config 回读）----
  function profileToYuBody(prof) {
    const mouse = prof.mouse || {};
    const ctrl = {};
    for (const [yuKey, ttKey] of Object.entries(CONTROLLER_MAP)) {
      if (mouse[ttKey] !== undefined) {
        ctrl[yuKey] = ttKey === 'y_axis_fire_hotkey' ? bitsToHotkey(mouse[ttKey]) : mouse[ttKey];
      }
    }
    const infer = prof.inference || {};
        const cap = prof.capture || {};
        const fovP = prof.fov || {};
        const prevP = prof.preview || {};
        const lat = {};
        if (prevP.fps !== undefined && prevP.fps > 0) {
          lat.preview_interval_ms = Math.round(1000 / prevP.fps);
        }
        return {
          model_id: prof.model_id || '',
          video_detection_confidence: infer.confidence,
          video_detection_iou: infer.iou,
          capture: {
            device: '/dev/video0',
            crop_size: cap.width,
            crop_offset_x: cap.offset_x,
            crop_offset_y: cap.offset_y,
          },
          // fov.enabled=true → 前端 range_factor 显示 radius（<1 收紧生效）；
          // enabled=false → 全屏 1.0（前端滑杆回正）
          range_factor: fovP.enabled ? fovP.radius : 1.0,
          sens: mouse.sensitivity,
          ai: { controller: ctrl },
          aim_profiles: [{
            hotkey: bitsToHotkey(mouse.aim_hotkey),
            hotkey2: bitsToHotkey(mouse.aim_hotkey2),
            hotkey_mode: mouse.aim_hotkey_mode || 'any',
            sensitivity: mouse.sensitivity,
            offset_x: 0.5, offset_y: 0.5,
            class_filter_mask: 0, fov_scale: 1.0,
          }],
          recoil: {}, rapid_fire: {}, auto_back_flick: {}, crosshair: {},
          hotkey_guard: { enabled: false, toggle_hotkey: 'middle' },
          mouse_output: {},
          latency: lat, fan_control: {}, loopout_overlay: {},
          pos: 0.5,
        };
  }

  // ---- API 映射 ----
  const READY_GET = {
    '/api/state': '/api/v1/state',
    '/api/models': '/api/v1/models',
    '/api/hardware/display': '/api/v1/hdmi',
    '/api/config': '/api/v1/config',
  };
  const PLANNED = [
    '/api/license', '/api/update', '/api/hailo', '/api/network/wifi', '/api/presets',
    '/api/themes', '/api/makcu', '/api/ferrum', '/api/kmboxb', '/api/mouse-output',
    '/api/diagnostics', '/api/remote', '/api/control/calibration', '/api/settings/auto-start',
    '/api/system',
  ];

  function isPlanned(path) {
    return PLANNED.some((p) => path === p || path.startsWith(p + '/'));
  }

  function plannedResponse(path, method) {
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({ ok: true, data: { planned: true, message: '开发中：TTBOX 后端尚未接入此功能（' + path + '）' } }),
    });
  }

  async function configGet() {
    const r = await origFetch('/api/v1/config');
    const j = await r.json();
    const body = profileToYuBody(j.profile || {});
    return { ok: true, json: () => Promise.resolve({ ok: true, data: body }) };
  }

  async function configPut(init) {
    const body = JSON.parse(init.body || '{}');
    // 先拿 Core 当前 canonical profile（保留 yu 没有的段：preview/geometry_filter 等，避免 validate 拒绝）
    let base = {};
    try {
      const r0 = await origFetch('/api/v1/config');
      const j0 = await r0.json();
      base = (j0 && j0.profile) || {};
    } catch (e) { /* 拿不到就裸提交 */ }
    const translated = yuBodyToProfile(body);
    const profile = Object.assign({}, base, translated);
    const r = await origFetch('/api/v1/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ profile }),
    });
    const j = await r.json();
    if (!r.ok) {
      return { ok: false, status: r.status, json: () => Promise.resolve({ ok: false, error: j.error || '保存失败' }) };
    }
    // 回读 canonical 转 yu 形状（前端 populateForm 自动同步）
    const back = profileToYuBody(j.profile || {});
    return { ok: true, json: () => Promise.resolve({ ok: true, data: { config: back, state: null } }) };
  }

  window.fetch = function (input, init) {
    let url = typeof input === 'string' ? input : input.url;
    const method = (init && init.method) || (input && input.method) || 'GET';
    const path = url.split('?')[0];

    if (!path.startsWith('/api/')) return origFetch(input, init);

    if (path === '/api/config') {
      if (method === 'PUT') return configPut(init);
      return configGet();
    }

    if (path === '/api/state') return origFetch('/api/v1/state', init);
    if (path === '/api/models') return origFetch('/api/v1/models', init);
    if (path === '/api/hardware/display') return origFetch('/api/v1/hdmi', init);

    const map = {
      '/api/control/start': ['/api/v1/runtime/start', 'POST'],
      '/api/control/stop': ['/api/v1/runtime/stop', 'POST'],
      '/api/models/select': ['/api/v1/models/activate', 'POST'],
      '/api/models/import': ['/api/v1/models/upload', 'POST'],
      '/api/models/delete': ['/api/v1/models/remove', 'POST'],
    };
    const m = map[path];
    if (m) {
      return origFetch(m[0], { ...(init || {}), method: m[1] });
    }

    if (isPlanned(path)) return plannedResponse(path, method);
    return plannedResponse(path, method);
  };

  console.log('[ttbox-bridge] v2 双向翻译已加载');
})();