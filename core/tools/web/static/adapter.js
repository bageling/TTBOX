/* AIBox × YU 1:1 界面适配层
   驱动 YU 渲染页对接 AIBox 后端 API（/api/state /api/profile /api/inference /frame.bmp）。
   本地打开（无后端）时自动容错：界面保持 YU 静态样式，功能 toast 提示。 */
(function(){
const $=s=>document.querySelector(s);

async function api(path, opts){
  try{
    const r=await fetch(path, opts);
    let j=null; try{j=await r.json();}catch(e){}
    return {ok:r.ok, data:j};
  }catch(e){ return {ok:false, data:null}; }
}
function toast(msg){
  const t=$('#toast'); if(!t)return;
  t.textContent=msg; t.classList.remove('hidden');
  clearTimeout(window._tt); window._tt=setTimeout(()=>t.classList.add('hidden'),2400);
}
const hasBackend = location.protocol.indexOf('http')===0;

/* ===== AIBox 无卡密授权：解除 YU 未激活锁定 ===== */
document.body.classList.remove('license-loading');
document.body.classList.remove('license-gate');
const actPanel=$('#homeActivationPanel'); if(actPanel)actPanel.style.display='none';
const gate=$('#licenseGateOverlay'); if(gate)gate.classList.add('hidden');
const disclaimer=$('#disclaimerDialog'); if(disclaimer)disclaimer.classList.add('hidden');
const activationOverlay=$('#activationSetupOverlay'); if(activationOverlay)activationOverlay.classList.add('hidden');

/* ===== 模块导航 ===== */
document.querySelectorAll('.module-tab').forEach(t=>{
  t.addEventListener('click',()=>{
    document.querySelectorAll('.module-tab').forEach(x=>x.classList.remove('is-active'));
    document.querySelectorAll('.page-card').forEach(x=>x.classList.remove('is-active'));
    t.classList.add('is-active');
    const pg=document.getElementById(t.dataset.pageTarget);
    if(pg)pg.classList.add('is-active');
    if(t.dataset.pageTarget==='model-page'){ if(typeof loadModels==='function')loadModels(); }
  });
});

/* ===== 分区 tab（移动控制/辅助功能内部分区切换） ===== */
function bindSectionTabs(targetAttr, sectionAttr){
  document.querySelectorAll('['+targetAttr+']').forEach(btn=>{
    btn.addEventListener('click',()=>{
      document.querySelectorAll('['+targetAttr+']').forEach(x=>{x.classList.remove('is-active'); x.setAttribute('aria-selected','false');});
      document.querySelectorAll('['+sectionAttr+']').forEach(x=>x.classList.remove('is-active'));
      btn.classList.add('is-active'); btn.setAttribute('aria-selected','true');
      const sec=document.getElementById(btn.getAttribute(targetAttr));
      if(sec){ sec.classList.add('is-active'); sec.removeAttribute('hidden'); }
    });
  });
}
bindSectionTabs('data-control-section-target','data-control-section');
bindSectionTabs('data-assist-section-target','data-assist-section');

/* ===== 主题切换 ===== */
function applyTheme(mode){
  document.documentElement.dataset.theme=mode;
  document.documentElement.dataset.visualThemeColor=mode;
  const btn=$('#themeToggleButton');
  if(btn)btn.textContent=(mode==='dark'?'浅色':'深色');
  try{localStorage.setItem('yu-theme',mode);}catch(e){}
}
const themeBtn=$('#themeToggleButton');
if(themeBtn)themeBtn.addEventListener('click',()=>{
  const cur=document.documentElement.dataset.theme||'dark';
  applyTheme(cur==='dark'?'light':'dark');
});
applyTheme((function(){try{return localStorage.getItem('yu-theme')||'dark';}catch(e){return 'dark';}})());

/* ===== 已实现控件（对接后端） ===== */
const implemented = new Set([
  'capture_crop_size','range_factor','video_detection_confidence','video_detection_iou',
  'sens','controller_kp_x','controller_kp_y','controller_predict_x','controller_predict_y',
  'controller_ki_x','controller_ki_y','controller_kd_x','controller_kd_y',
  'controller_rate_x','controller_rate_y','controller_output_deadzone',
  'controller_selector_lost_grace_ms',
  'controller_aim_reference_offset_x','controller_aim_reference_offset_y',
  'capture_crop_offset_x','capture_crop_offset_y','pos','capture_device','capture_format_preference',
  'controller_pull_curve_enabled','controller_pull_curve_strength','controller_pull_curve_jitter_px','controller_pull_curve_min_distance',
  'controller_continuous_lead_enabled','controller_continuous_lead_enter_distance','controller_continuous_lead_scale',
  'controller_continuous_lead_fade_in_ms','controller_continuous_lead_fade_out_ms','controller_continuous_lead_near_disable_ratio',
  'controller_block_physical_mouse_x_while_aiming','controller_block_physical_mouse_y_while_aiming',
  'controller_aim_fire_lock_y','controller_y_axis_fire_hotkey','controller_y_axis_fire_release_delay_sec',
  'hotkey_guard_enabled','hotkey_guard_toggle_hotkey',
  'recoil_enabled','recoil_hotkey','recoil_hotkey_mode','recoil_strength','recoil_speed',
  'recoil_only_when_target_visible','recoil_target_lost_release_ms',
  'recoil_trigger_delay_enabled','recoil_trigger_delay_ms',
  'recoil_humanize_enabled','recoil_humanize_curve_strength','recoil_humanize_jitter_px','recoil_humanize_jitter_frequency',
  'rapid_fire_enabled','rapid_fire_hotkey','rapid_fire_press_base_ms','rapid_fire_interval_base_ms',
  'auto_back_flick_enabled','crosshair_detection_enabled','crosshair_hotkey','crosshair_hotkey2','crosshair_hotkey_mode',
  'crosshair_roi_w','crosshair_roi_h',
  'fan_control_enabled','fan_control_temperature_source'
]);

/* ===== 未实现控件拦截 → 待接入（动态卡片/弹窗内部放行） ===== */
document.addEventListener('click',e=>{
  const el=e.target.closest('[data-config]');
  if(!el)return;
  if(implemented.has(el.id))return;
  if(el.closest('.aim-profile-card,.auto-trigger-profile-card,.class-offset-popover,.physical-button-block-dialog,.preset-card,.preset-import-dialog'))return;
  const label=el.closest('.field')?.querySelector('.field-label,.field-hint')?.textContent?.trim();
  toast('「'+(label||el.id||'该功能')+'」待接入');
  e.preventDefault();
  e.stopPropagation();
},true);

/* ===== 参数收集（YU id → TTBox profile 字段，全量功能控件） ===== */
function val(id){const e=$(id);if(!e)return undefined;const v=parseFloat(e.value);return isNaN(v)?undefined:v;}
function chk(id){const e=$(id);return e?e.checked:false;}
function str(id){const e=$(id);return e?e.value:undefined;}

function collectAutoTrigger(){
  const ed=$('#autoTriggerProfilesEditor');
  if(!ed)return {enabled:false, profiles:[]};
  const profiles=Array.from(ed.querySelectorAll('.auto-trigger-profile-card')).map(card=>{
    const v=s=>{const e=card.querySelector(s);return e?e.value:'';};
    const c=s=>{const e=card.querySelector(s);return e?e.checked:false;};
    return {
      hotkey:v('.auto-trigger-hotkey')||'left',
      hotkey2:v('.auto-trigger-hotkey2')||'',
      hotkey_mode:v('.auto-trigger-hotkey-mode')||'any',
      class_filter_mask:0,
      enabled:c('.auto-trigger-enabled')
    };
  });
  return {enabled:profiles.some(p=>p.enabled), profiles};
}

function collectFeatures(){
  return {
    hotkey_guard:{
      enabled:chk('#hotkey_guard_enabled'),
      toggle_hotkey:str('#hotkey_guard_toggle_hotkey')||'scroll_lock'
    },
    mouse_output:{
      blocked_physical_buttons:Array.from(document.querySelectorAll('.physical-button-block-row[data-physical-button]')||[]).map(r=>r.dataset.physicalButton).filter(Boolean),
      kmboxnet:{enabled:chk('#kmbox_enabled'),ip:str('#kmbox_ip')||'',port:val('#kmbox_port')||0,
                uuid:str('#kmbox_uuid')||'',monitor_port:val('#kmbox_monitor_port')||0,
                timeout_ms:val('#kmbox_timeout_ms')||300,encrypted:chk('#kmbox_encrypted')}
    },
    ai_controller:{
      predict_x:val('#controller_predict_x')!=null?val('#controller_predict_x'):0.5,
      predict_y:val('#controller_predict_y')!=null?val('#controller_predict_y'):0.4,
      smooth_x:val('#controller_smooth_x')!=null?val('#controller_smooth_x'):9900,
      smooth_y:val('#controller_smooth_y')!=null?val('#controller_smooth_y'):9900,
      output_deadzone:val('#controller_output_deadzone')!=null?val('#controller_output_deadzone'):1,
      selector_search_radius:val('#controller_selector_search_radius')!=null?val('#controller_selector_search_radius'):170,
      pull_curve_enabled:chk('#controller_pull_curve_enabled'),
      pull_curve_strength:val('#controller_pull_curve_strength')||0,
      pull_curve_jitter_px:val('#controller_pull_curve_jitter_px')||0,
      pull_curve_min_distance:val('#controller_pull_curve_min_distance')||0,
      continuous_lead_enabled:chk('#controller_continuous_lead_enabled'),
      continuous_lead_enter_distance:val('#controller_continuous_lead_enter_distance')||0,
      continuous_lead_scale:val('#controller_continuous_lead_scale')||0,
      continuous_lead_fade_in_ms:val('#controller_continuous_lead_fade_in_ms')||0,
      continuous_lead_fade_out_ms:val('#controller_continuous_lead_fade_out_ms')||0,
      continuous_lead_near_disable_ratio:val('#controller_continuous_lead_near_disable_ratio')||0,
      aim_fire_lock_y:chk('#controller_aim_fire_lock_y'),
      y_axis_fire_hotkey:str('#controller_y_axis_fire_hotkey')||'left',
      y_axis_fire_release_delay_sec:val('#controller_y_axis_fire_release_delay_sec')||0,
      block_physical_mouse_x_while_aiming:chk('#controller_block_physical_mouse_x_while_aiming'),
      block_physical_mouse_y_while_aiming:chk('#controller_block_physical_mouse_y_while_aiming')
    },
    recoil:{
      enabled:chk('#recoil_enabled'),
      hotkey:str('#recoil_hotkey')||'left',
      hotkey_mode:str('#recoil_hotkey_mode')||'any',
      strength:val('#recoil_strength')||0,
      speed:val('#recoil_speed')!=null?val('#recoil_speed'):1,
      only_when_target_visible:chk('#recoil_only_when_target_visible'),
      target_lost_release_ms:val('#recoil_target_lost_release_ms')||0,
      trigger_delay_enabled:chk('#recoil_trigger_delay_enabled'),
      trigger_delay_ms:val('#recoil_trigger_delay_ms')||0,
      humanize_enabled:chk('#recoil_humanize_enabled'),
      humanize_curve_strength:val('#recoil_humanize_curve_strength')||0,
      humanize_jitter_px:val('#recoil_humanize_jitter_px')||0,
      humanize_jitter_frequency:val('#recoil_humanize_jitter_frequency')||0
    },
    rapid_fire:{
      enabled:chk('#rapid_fire_enabled'),
      hotkey:str('#rapid_fire_hotkey')||'left',
      press_base_ms:val('#rapid_fire_press_base_ms')||30,
      interval_base_ms:val('#rapid_fire_interval_base_ms')||60
    },
    auto_trigger: collectAutoTrigger(),
    auto_back_flick:{ enabled:chk('#auto_back_flick_enabled') },
    crosshair:{
      detection_enabled:chk('#crosshair_detection_enabled'),
      hotkey:str('#crosshair_hotkey')||'auto',
      hotkey2:str('#crosshair_hotkey2')||'',
      hotkey_mode:str('#crosshair_hotkey_mode')||'any',
      roi_w:val('#crosshair_roi_w')||80,
      roi_h:val('#crosshair_roi_h')||80,
      slots:[0,1,2].map(i=>{const r=document.querySelector('input[name="crosshair_slot_'+i+'_color"]:checked');
        return {enabled:chk('#crosshair_slot_'+i+'_enabled'), color:(r?r.value:'green')};})
    },
    fan_control:{
      enabled:chk('#fan_control_enabled'),
      temperature_source:str('#fan_control_temperature_source')||'auto'
    }
  };
}

function collectProfile(prev){
  const f=collectFeatures();
  const proxyMode=(document.querySelector('input[name="mouse_proxy_mode"]:checked')||{}).value||'full_passthrough';
  return {
    model_id:(prev&&prev.model_id)||'',
    capture:{
      // 截取尺寸 = 屏幕裁剪区域（ROI），0=不启用（全帧，保持默认视野）
      width: val('#capture_crop_size')||0,
      height: val('#capture_crop_size')||0,
      offset_x: val('#capture_crop_offset_x')||0,
      offset_y: val('#capture_crop_offset_y')||0
    },
    inference:{
      confidence: val('#video_detection_confidence')!=null?val('#video_detection_confidence'):((lastProfile.inference&&lastProfile.inference.confidence!=null)?lastProfile.inference.confidence:((prev&&prev.inference&&prev.inference.confidence)||0)),
      iou: val('#video_detection_iou')!=null?val('#video_detection_iou'):((lastProfile.inference&&lastProfile.inference.iou!=null)?lastProfile.inference.iou:((prev&&prev.inference&&prev.inference.iou)||0)),
      // 类别过滤/最大检测数由方案/后端管理，以轮询真实状态为准，防止旧缓存覆盖
      class_filter:(lastProfile.inference&&lastProfile.inference.class_filter)||(prev&&prev.inference&&prev.inference.class_filter)||[],
      max_detections:(lastProfile.inference&&lastProfile.inference.max_detections)||(prev&&prev.inference&&prev.inference.max_detections)||0
    },
    fov:(lastProfile.fov)||(prev&&prev.fov)||{enabled:false,shape:0,radius:0.5,center_x:0.5,center_y:0.5},
    mouse:{
      // 自瞄启用/热键/类别偏移由"应用方案"管理，一律以轮询真实状态(lastProfile)优先，
      // 防止前端旧缓存(_profile)在保存任意参数时把自瞄关掉或把热键改回默认
      enabled: (lastProfile.mouse&&lastProfile.mouse.enabled!=null)?lastProfile.mouse.enabled:((prev&&prev.mouse&&prev.mouse.enabled!=null)?prev.mouse.enabled:false),
      proxy_mode: proxyMode,
      aim_hotkey: (lastProfile.mouse&&lastProfile.mouse.aim_hotkey!=null)?lastProfile.mouse.aim_hotkey:((prev&&prev.mouse&&prev.mouse.aim_hotkey!=null)?prev.mouse.aim_hotkey:2),
      aim_hotkey2: (lastProfile.mouse&&lastProfile.mouse.aim_hotkey2!=null)?lastProfile.mouse.aim_hotkey2:((prev&&prev.mouse&&prev.mouse.aim_hotkey2!=null)?prev.mouse.aim_hotkey2:0),
      aim_hotkey_mode: (lastProfile.mouse&&lastProfile.mouse.aim_hotkey_mode!=null)?lastProfile.mouse.aim_hotkey_mode:((prev&&prev.mouse&&prev.mouse.aim_hotkey_mode!=null)?prev.mouse.aim_hotkey_mode:'any'),
      fov_range: val('#range_factor')!=null?val('#range_factor'):((prev&&prev.mouse&&prev.mouse.fov_range)||1),
      confidence:(prev&&prev.mouse&&prev.mouse.confidence)!=null?(prev.mouse.confidence):0.25,
      prediction_s: val('#controller_predict_x')!=null?val('#controller_predict_x'):((prev&&prev.mouse&&prev.mouse.prediction_s)||0),
      kp_x: val('#controller_kp_x')!=null?val('#controller_kp_x'):((prev&&prev.mouse&&prev.mouse.kp_x)||17),
      kp_y: val('#controller_kp_y')!=null?val('#controller_kp_y'):((prev&&prev.mouse&&prev.mouse.kp_y)||10),
      ki_x: val('#controller_ki_x')!=null?val('#controller_ki_x'):((prev&&prev.mouse&&prev.mouse.ki_x)||0),
      ki_y: val('#controller_ki_y')!=null?val('#controller_ki_y'):((prev&&prev.mouse&&prev.mouse.ki_y)||0),
      kd_x: val('#controller_kd_x')!=null?val('#controller_kd_x'):((prev&&prev.mouse&&prev.mouse.kd_x)||0),
      kd_y: val('#controller_kd_y')!=null?val('#controller_kd_y'):((prev&&prev.mouse&&prev.mouse.kd_y)||0),
      rate_x: val('#controller_rate_x')!=null?val('#controller_rate_x'):((prev&&prev.mouse&&prev.mouse.rate_x)||1),
      rate_y: val('#controller_rate_y')!=null?val('#controller_rate_y'):((prev&&prev.mouse&&prev.mouse.rate_y)||1),
      sensitivity: val('#sens')!=null?val('#sens'):((prev&&prev.mouse&&prev.mouse.sensitivity)||1),
      output_scale:(prev&&prev.mouse&&prev.mouse.output_scale)!=null?(prev.mouse.output_scale):1,
      deadzone_x: val('#controller_output_deadzone')!=null?val('#controller_output_deadzone'):((prev&&prev.mouse&&prev.mouse.deadzone_x)||1),
      deadzone_y: val('#controller_output_deadzone')!=null?val('#controller_output_deadzone'):((prev&&prev.mouse&&prev.mouse.deadzone_y)||1),
      smooth:(lastProfile.mouse&&lastProfile.mouse.smooth!=null)?lastProfile.mouse.smooth:((prev&&prev.mouse&&prev.mouse.smooth)||0),
      aim_offset_x: val('#controller_aim_reference_offset_x')!=null?val('#controller_aim_reference_offset_x'):((prev&&prev.mouse&&prev.mouse.aim_offset_x)||0),
      aim_offset_y: val('#controller_aim_reference_offset_y')!=null?val('#controller_aim_reference_offset_y'):((prev&&prev.mouse&&prev.mouse.aim_offset_y)||0),
      lost_grace_ms: val('#controller_selector_lost_grace_ms')!=null?val('#controller_selector_lost_grace_ms'):((prev&&prev.mouse&&prev.mouse.lost_grace_ms)||78),
      block_physical_x: f.ai_controller.block_physical_mouse_x_while_aiming,
      block_physical_y: f.ai_controller.block_physical_mouse_y_while_aiming,
      fov_mode:false, hfov:83.105, vfov:53,
      move_speed_x:500, move_speed_y:500, aim_part:0,
      // 类别偏移来自激活方案（apply_aim_profile 写入），以轮询真实状态优先
      class_offsets: (lastProfile.mouse&&lastProfile.mouse.class_offsets)||(prev&&prev.mouse&&prev.mouse.class_offsets)||[]
    },
    preview:{
      width: val('#capture_crop_size')||320,
      height: val('#capture_crop_size')||320,
      roi_w: val('#capture_crop_size')||320,
      roi_h: val('#capture_crop_size')||320,
      offset_x: val('#capture_crop_offset_x')||0,
      offset_y: val('#capture_crop_offset_y')||0,
      center_crop:true
    },
    features: f
  };
}
let _profile={};
async function saveProfile(){
  _profile=collectProfile(_profile);
  const r=await api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(_profile)});
  toast(hasBackend?(r.ok?'已保存 ✓':'保存失败'):'本地预览：参数将保存到板端（/api/profile）');
}
let pt=null;
function onParamChange(){ clearTimeout(pt); pt=setTimeout(saveProfile,500); }

/* 绑定已实现控件 change（必须带 # 前缀） */
implemented.forEach(id=>{const e=$('#'+id);if(e)e.addEventListener('change',onParamChange);});
/* 透传模式 radio（无 id）：UI 联动（只读切换/提示）+ 自动保存 */
document.querySelectorAll('input[name="mouse_proxy_mode"]').forEach(r=>r.addEventListener('change',()=>{
  applyMouseModeUI(r.value);
  onParamChange();
}));
/* 截取尺寸预设滑块（0-4 → 192/256/320/416/640）联动数字输入 + 自动保存 */
const CROP_PRESETS=[192,256,320,416,640];
const cropRange=$('#capture_crop_size_range');
if(cropRange)cropRange.addEventListener('input',()=>{
  const v=CROP_PRESETS[Number(cropRange.value)]||320;
  const cs=$('#capture_crop_size'); if(cs)cs.value=v;
  onParamChange();
});
/* 通用滑块联动：所有 `xxx_range` ↔ 数字输入 `xxx` 双向同步 + 自动保存。
   修复 YU 界面滑块拖动无效（FOV 半径/置信度/IOU/灵敏度/背闪参数等）。 */
document.querySelectorAll('input[type="range"]').forEach(r=>{
  if(!r.id||r.id==='capture_crop_size_range')return;  // 预设滑块已单独处理
  const base=r.id.replace(/_range$/,'');
  const num=base&&document.getElementById(base);
  if(!num)return;
  r.addEventListener('input',()=>{num.value=r.value;onParamChange();});
  num.addEventListener('input',()=>{
    const v=parseFloat(num.value);
    if(!isNaN(v)&&v>=parseFloat(r.min)&&v<=parseFloat(r.max))r.value=num.value;
  });
});
/* 瞄准点偏移：改动后立即移动瞄准参考点（不等 500ms 轮询），让调节立刻可见 */
['#controller_aim_reference_offset_x','#controller_aim_reference_offset_y'].forEach(id=>{
  const e=$(id); if(!e)return;
  e.addEventListener('change',()=>{
    if(!lastProfile.mouse)lastProfile.mouse={};
    lastProfile.mouse.aim_offset_x=val('#controller_aim_reference_offset_x')||0;
    lastProfile.mouse.aim_offset_y=val('#controller_aim_reference_offset_y')||0;
    updateAimRangeOverlay();
  });
});

/* ===== Platform V1 API Client + Dashboard ===== */
const apiClient={
 async request(path,opts={}){try{const r=await fetch(path,opts);let data=null;try{data=await r.json();}catch(e){}return {ok:r.ok,status:r.status,data};}catch(e){return {ok:false,status:0,data:null};}},
 getStatus(){return this.request('/api/v1/status')}, getHealth(){return this.request('/api/v1/health')}, getRuntime(){return this.request('/api/v1/runtime')}, getInference(){return this.request('/api/v1/inference')}, getModel(){return this.request('/api/v1/model')},
 startRuntime(){return this.request('/api/v1/runtime/start',{method:'POST'})}, stopRuntime(){return this.request('/api/v1/runtime/stop',{method:'POST'})}, restartRuntime(){return this.request('/api/v1/runtime/restart',{method:'POST'})}
};
window.ttboxApi=apiClient;
function fmt(v,s=''){return v==null?'UNAVAILABLE':String(v)+s;}
function setDash(id,v){const e=document.querySelector(id);if(e)e.textContent=v;}
function renderPlatform(data){
 const st=data&&data.status, h=data&&data.health; const inf=data&&data.inference&&data.inference.metrics||{}; const mdl=data&&data.model&&data.model.metrics||{}; const run=data&&data.runtime&&data.runtime.metrics||{};
 const offline=!data; setDash('#statusBadge',offline?'OFFLINE':(h&&h.status)||'UNAVAILABLE'); const sb=document.querySelector('#statusBadge'); if(sb)sb.className='status-badge '+(offline?'idle':((h&&h.status)==='HEALTHY'?'ok':'warn'));
 setDash('#mobileLatency',fmt(inf.e2e_latency_us!=null?(inf.e2e_latency_us/1000).toFixed(1):null,' ms')); setDash('#mobileCaptureFps',fmt(inf.capture_fps,' FPS')); setDash('#mobileFps',fmt(inf.inference_fps,' FPS'));
 const rs=document.querySelector('#runtimeSummary'); if(rs){rs.innerHTML=[['Runtime',fmt(run.state)],['PID',fmt(run.pid)],['Restart',fmt(run.restart_count)],['Inference',fmt(inf.inference_fps,' FPS')],['Capture',fmt(inf.capture_fps,' FPS')],['E2E',fmt(inf.e2e_latency_us!=null?(inf.e2e_latency_us/1000).toFixed(1):null,' ms')],['RKNN',fmt(inf.rknn_latency_us!=null?(inf.rknn_latency_us/1000).toFixed(1):null,' ms')],['RGA',fmt(inf.rga_latency_us!=null?(inf.rga_latency_us/1000).toFixed(1):null,' ms')],['Decode',fmt(inf.decode_latency_us!=null?(inf.decode_latency_us/1000).toFixed(1):null,' ms')],['NPU',fmt(inf.npu_core0)+' / '+fmt(inf.npu_core1)+' / '+fmt(inf.npu_core2)],['Model',fmt(mdl.path)],['Errors',fmt(inf.errors)]].map(x=>'<div class="runtime-stat"><span>'+x[0]+'</span><strong>'+x[1]+'</strong></div>').join('');}
}
async function refreshPlatform(){const [s,h]=await Promise.all([apiClient.getStatus(),apiClient.getHealth()]); if(!s.ok||!h.ok){renderPlatform(null);return;} const d=s.data||{}; d.health=h.data; renderPlatform(d);}
async function runtimeAction(action){const fn={start:apiClient.startRuntime,stop:apiClient.stopRuntime,restart:apiClient.restartRuntime}[action];if(!fn)return;const r=await fn.call(apiClient);toast(r.ok?'操作完成':'操作失败');setTimeout(refreshPlatform,500);}
const startBtn=document.querySelector('#startButton'); if(startBtn)startBtn.addEventListener('click',()=>runtimeAction('restart'));
refreshPlatform(); setInterval(refreshPlatform,1500);

/* ===== 瞄准范围框 + 参考点（对齐 YU：crop×FOV半径 方框 + 瞄准点绿点） ===== */
function getPreviewImageLayout(stage, cropSize){
  if(!stage)return null;
  const sr=stage.getBoundingClientRect();
  if(sr.width<=0||sr.height<=0)return null;
  const pv=$('#previewImage');
  const nw=pv&&pv.naturalWidth>0?pv.naturalWidth:cropSize;
  const nh=pv&&pv.naturalHeight>0?pv.naturalHeight:cropSize;
  const sc=Math.min(sr.width/nw, sr.height/nh);
  const iw=nw*sc, ih=nh*sc;
  return {imageLeft:(sr.width-iw)*0.5, imageTop:(sr.height-ih)*0.5, imageWidth:iw, imageHeight:ih,
          xScale:iw/cropSize, yScale:ih/cropSize, cropScale:Math.min(iw,ih)/cropSize};
}
function updateAimRangeOverlay(){
  const ov=$('#aimRangeOverlay'), dot=$('#aimReferenceDot');
  if(!ov&&!dot)return;
  const stage=document.querySelector('.preview-stage');
  if(!stage)return;
  const pv=lastProfile.preview||{}, mo=lastProfile.mouse||{};
  const cropSize=pv.roi_w||pv.width||320;
  const layout=getPreviewImageLayout(stage,cropSize);
  if(!layout)return;
  if(ov){
    const rf=mo.fov_range!=null?mo.fov_range:1;
    const side=Math.max(2, cropSize*rf*layout.cropScale);
    ov.style.left=(layout.imageLeft+layout.imageWidth*0.5)+'px';
    ov.style.top=(layout.imageTop+layout.imageHeight*0.5)+'px';
    ov.style.width=side+'px';
    ov.style.height=side+'px';
  }
  if(dot){
    const maxR=Math.max(0,cropSize-1);
    const rx=Math.min(Math.max(0, cropSize*0.5+(mo.aim_offset_x||0)), maxR);
    const ry=Math.min(Math.max(0, cropSize*0.5+(mo.aim_offset_y||0)), maxR);
    dot.style.left=(layout.imageLeft+rx*layout.xScale)+'px';
    dot.style.top=(layout.imageTop+ry*layout.yScale)+'px';
  }
}
window.addEventListener('resize', updateAimRangeOverlay);

/* ===== 参数回填：后端 profile → YU 控件（只填空值，不覆盖正在编辑的） ===== */
function backfillFeatures(f){
  if(!f)return;
  const setVal=(id,v)=>{
    const e=$(id);
    if(!e || !(e.value===''||e.value==null))return;
    e.value=(v!=null?v:'');
    // 同步联动滑块（如有）：保证刷新后滑块位置与数字框一致
    const r=$('#'+String(id).replace(/^#/,'')+'_range');
    if(r&&v!=null&&v!==''){
      const n=parseFloat(v);
      if(!isNaN(n)&&n>=parseFloat(r.min)&&n<=parseFloat(r.max))r.value=n;
    }
  };
  const setSel=(id,v)=>{const e=$(id); if(e&&v!=null) e.value=v;};  // select 强制回填
  const setChk=(id,v)=>{const e=$(id); if(e) e.checked=!!v;};
  const hg=f.hotkey_guard||{}, mo=f.mouse_output||{}, ac=f.ai_controller||{};
  const rc=f.recoil||{}, rf=f.rapid_fire||{}, ch=f.crosshair||{}, fc=f.fan_control||{};
  setChk('#hotkey_guard_enabled', hg.enabled);
  setSel('#hotkey_guard_toggle_hotkey', hg.toggle_hotkey);
  setChk('#controller_pull_curve_enabled', ac.pull_curve_enabled);
  setVal('#controller_pull_curve_strength', ac.pull_curve_strength);
  setVal('#controller_pull_curve_jitter_px', ac.pull_curve_jitter_px);
  setVal('#controller_pull_curve_min_distance', ac.pull_curve_min_distance);
  setVal('#controller_predict_x', ac.predict_x);
  setVal('#controller_predict_y', ac.predict_y);
  if(ac.output_deadzone!=null)setVal('#controller_output_deadzone', ac.output_deadzone);
  setChk('#controller_continuous_lead_enabled', ac.continuous_lead_enabled);
  setVal('#controller_continuous_lead_enter_distance', ac.continuous_lead_enter_distance);
  setVal('#controller_continuous_lead_scale', ac.continuous_lead_scale);
  setVal('#controller_continuous_lead_fade_in_ms', ac.continuous_lead_fade_in_ms);
  setVal('#controller_continuous_lead_fade_out_ms', ac.continuous_lead_fade_out_ms);
  setVal('#controller_continuous_lead_near_disable_ratio', ac.continuous_lead_near_disable_ratio);
  setChk('#controller_aim_fire_lock_y', ac.aim_fire_lock_y);
  setSel('#controller_y_axis_fire_hotkey', ac.y_axis_fire_hotkey);
  setVal('#controller_y_axis_fire_release_delay_sec', ac.y_axis_fire_release_delay_sec);
  setChk('#controller_block_physical_mouse_x_while_aiming', ac.block_physical_mouse_x_while_aiming);
  setChk('#controller_block_physical_mouse_y_while_aiming', ac.block_physical_mouse_y_while_aiming);
  setChk('#recoil_enabled', rc.enabled);
  setSel('#recoil_hotkey', rc.hotkey);
  setSel('#recoil_hotkey_mode', rc.hotkey_mode);
  setVal('#recoil_strength', rc.strength);
  setVal('#recoil_speed', rc.speed);
  setChk('#recoil_only_when_target_visible', rc.only_when_target_visible);
  setVal('#recoil_target_lost_release_ms', rc.target_lost_release_ms);
  setChk('#recoil_trigger_delay_enabled', rc.trigger_delay_enabled);
  setVal('#recoil_trigger_delay_ms', rc.trigger_delay_ms);
  setChk('#recoil_humanize_enabled', rc.humanize_enabled);
  setVal('#recoil_humanize_curve_strength', rc.humanize_curve_strength);
  setVal('#recoil_humanize_jitter_px', rc.humanize_jitter_px);
  setVal('#recoil_humanize_jitter_frequency', rc.humanize_jitter_frequency);
  setChk('#rapid_fire_enabled', rf.enabled);
  setSel('#rapid_fire_hotkey', rf.hotkey);
  setVal('#rapid_fire_press_base_ms', rf.press_base_ms);
  setVal('#rapid_fire_interval_base_ms', rf.interval_base_ms);
  setChk('#auto_back_flick_enabled', f.auto_back_flick&&f.auto_back_flick.enabled);
  setChk('#crosshair_detection_enabled', ch.detection_enabled);
  setSel('#crosshair_hotkey', ch.hotkey);
  setSel('#crosshair_hotkey2', ch.hotkey2);
  setSel('#crosshair_hotkey_mode', ch.hotkey_mode);
  setVal('#crosshair_roi_w', ch.roi_w);
  setVal('#crosshair_roi_h', ch.roi_h);
  setChk('#fan_control_enabled', fc.enabled);
  setSel('#fan_control_temperature_source', fc.temperature_source);
  renderBlockedPhysicalButtons(mo.blocked_physical_buttons||[]);
}

function backfillProfile(profile){
  if(!profile)return;
  const m=profile.mouse||{}, inf=profile.inference||{}, pv=profile.preview||{};
  // setValSync：数字框 + 联动滑块（_range）一起回填，修复刷新后滑块不恢复位置
  const setValSync=(id,v)=>{
    const e=$(id);
    if(!e || !(e.value===''||e.value==null))return;
    e.value=(v!=null?v:'');
    const r=$('#'+String(id).replace(/^#/,'')+'_range');
    if(r&&v!=null&&v!==''){
      const n=parseFloat(v);
      if(!isNaN(n)&&n>=parseFloat(r.min)&&n<=parseFloat(r.max))r.value=n;
    }
  };
  const setChk=(id,v)=>{const e=$(id); if(e) e.checked=!!v;};
  const mdef={kp_x:17,kp_y:10,ki_x:0,ki_y:0,kd_x:0,kd_y:0,rate_x:1,rate_y:1,prediction_s:0,
              predict_x:0.5,predict_y:0.4,sensitivity:1,deadzone_x:1,deadzone_y:1,
              lost_grace_ms:78,aim_offset_x:0,aim_offset_y:0};
  Object.keys(mdef).forEach(k=>{if(m[k]==null)m[k]=mdef[k];});
  setValSync('#sens', m.sensitivity);
  setValSync('#range_factor', m.fov_range);
  setValSync('#video_detection_confidence', inf.confidence);
  setValSync('#video_detection_iou', inf.iou);
  setValSync('#controller_kp_x', m.kp_x);
  setValSync('#controller_kp_y', m.kp_y);
  setValSync('#controller_ki_x', m.ki_x);
  setValSync('#controller_ki_y', m.ki_y);
  setValSync('#controller_kd_x', m.kd_x);
  setValSync('#controller_kd_y', m.kd_y);
  setValSync('#controller_predict_x', m.predict_x!=null?m.predict_x:m.prediction_s);
  setValSync('#controller_predict_y', m.predict_y!=null?m.predict_y:m.prediction_s);
  setValSync('#controller_rate_x', m.rate_x);
  setValSync('#controller_rate_y', m.rate_y);
  setValSync('#controller_output_deadzone', m.output_deadzone!=null?m.output_deadzone:m.deadzone_x);
  setValSync('#controller_selector_lost_grace_ms', m.lost_grace_ms);
  setValSync('#controller_aim_reference_offset_x', m.aim_offset_x);
  setValSync('#controller_aim_reference_offset_y', m.aim_offset_y);
  setChk('#controller_aim_fire_lock_y', m.aim_fire_lock_y);
  setChk('#controller_block_physical_mouse_x_while_aiming', m.block_physical_x);
  setChk('#controller_block_physical_mouse_y_while_aiming', m.block_physical_y);
  const cap=profile.capture||{};
  const cs=$('#capture_crop_size');
  if(cs && cs.value===''){
    const v=(cap.width||pv.roi_w||pv.width||0);
    cs.value=v;
    // 截取尺寸预设滑块（0-4 索引）同步回位
    const cr=$('#capture_crop_size_range');
    if(cr){const idx=CROP_PRESETS.indexOf(Number(v));if(idx>=0)cr.value=idx;}
  }
  setValSync('#capture_crop_offset_x', cap.offset_x!=null?cap.offset_x:pv.offset_x);
  setValSync('#capture_crop_offset_y', cap.offset_y!=null?cap.offset_y:pv.offset_y);
  backfillFeatures(profile.features);
}

/* ===== 实时画面：/frame.bmp 双缓冲（60fps 上限） ===== */
(function(){
  const img=$('#previewImage'); if(!img)return;
  const a=new Image(),b=new Image(); let front=a,back=b,loading=false,lastShow=0;
  function show(){const t=front;front=back;back=t;img.src=front.src;img.style.opacity=1;lastShow=performance.now();}
  function pump(){
    if(loading)return;
    loading=true;
    back.onload=function(){ loading=false; const w=Math.max(0,66-(performance.now()-lastShow)); if(w>0)setTimeout(()=>{show();pump();},w); else {show();pump();} };
    back.onerror=function(){ loading=false; setTimeout(pump,500); };
    back.src='/frame.bmp?t='+Date.now();
  }
  pump();
})();

refreshState();
setInterval(refreshState,500);

/* ===== A10.2：aim_profiles 多方案编辑器 + 自动标定（真实数据） ===== */
const POINTER_HK=['left','right','middle','back','forward'];
const HK_LABELS={left:'左键',right:'右键',middle:'中键',back:'侧键(后)',forward:'侧键(前)'};
let aimProfiles=[];

function aimDefaultProfile(){
  return {hotkey:'left',hotkey2:'',hotkey_mode:'any',class_filter_mask:0,
          offset_x:0.5,offset_y:0.5,pos:0.5,offset_switch_enabled:false,
          offset_switch_hotkey:'',alternate_offset_x:0.5,alternate_offset_y:0.5,
          class_offsets:[],sensitivity:1,fov_scale:1};
}
function hkOptions(sel,val){
  return ['',...POINTER_HK].map(h=>'<option value="'+h+'"'+(h===val?' selected':'')+'>'+(h?HK_LABELS[h]:'不使用')+'</option>').join('');
}
function aimProfileCard(p,i,active){
  const card=document.createElement('div');
  card.className='aim-profile-card'+(active?' is-active':'');
  const mask=p.class_filter_mask||0;
  const nClass=Math.max(1,Math.min(_modelClassNames.length||6,32));  // 位掩码 int 上限 32 类
  let chips='';
  for(let c=0;c<nClass;c++){
    const name=_modelClassNames[c]||('类别'+c);
    chips+='<label class="aim-mini-class class-chip-label"><input class="aim-profile-class" type="checkbox" data-class-id="'+c+'"'+(mask&(1<<c)?' checked':'')+'><span class="class-chip-name">'+name+'</span></label>';
  }
  card.innerHTML=
    '<div class="aim-profile-head"><strong>方案 '+(i+1)+(active?'（当前使用）':'')+'</strong>'+
      '<button class="mini-danger" type="button" data-remove-profile>删除</button></div>'+
    '<div class="form-subgrid">'+
      '<div class="aim-profile-hotkey-grid span-all"><div class="hotkey-pair-grid">'+
        '<label class="field">主按键<select class="aim-profile-hotkey">'+hkOptions(p.hotkey,p.hotkey)+'</select></label>'+
        '<label class="field">副按键<select class="aim-profile-hotkey2">'+hkOptions(p.hotkey2||'',p.hotkey2||'')+'</select></label>'+
      '</div>'+
      '<label class="field">触发方式<select class="aim-profile-hotkey-mode">'+
        '<option value="any"'+(p.hotkey_mode==='all'?'':' selected')+'>任一按键</option>'+
        '<option value="all"'+(p.hotkey_mode==='all'?' selected':'')+'>同时按下</option>'+
      '</select></label></div>'+
      '<div class="slider-field span-all"><div class="label-row"><label>热键移动倍率</label>'+
        '<input class="aim-profile-sensitivity value-input" type="number" min="0.1" max="3" step="0.01" value="'+(p.sensitivity!=null?p.sensitivity:1)+'"></div>'+
        '<input class="aim-profile-sensitivity-range" type="range" min="0.1" max="3" step="0.01" value="'+(p.sensitivity!=null?p.sensitivity:1)+'"></div>'+
      '<div class="slider-field span-all"><div class="label-row"><label>热键 FOV 缩放</label>'+
        '<input class="aim-profile-fov-scale value-input" type="number" min="0.1" max="1" step="0.01" value="'+(p.fov_scale!=null?p.fov_scale:1)+'"></div>'+
        '<input class="aim-profile-fov-scale-range" type="range" min="0.1" max="1" step="0.01" value="'+(p.fov_scale!=null?p.fov_scale:1)+'"></div>'+
      '<div class="aim-profile-axis-grid span-all">'+
        '<div class="slider-field aim-profile-axis-field"><div class="label-row"><label>X轴偏移</label>'+
          '<input class="aim-profile-offset-x value-input" type="number" min="0" max="1" step="0.01" value="'+(p.offset_x!=null?p.offset_x:0.5)+'"></div>'+
          '<input class="aim-profile-offset-x-range" type="range" min="0" max="1" step="0.01" value="'+(p.offset_x!=null?p.offset_x:0.5)+'"><span class="field-hint">0 左 · 0.5 中 · 1 右</span></div>'+
        '<div class="slider-field aim-profile-axis-field"><div class="label-row"><label>Y轴偏移</label>'+
          '<input class="aim-profile-offset-y value-input" type="number" min="0" max="1" step="0.01" value="'+(p.offset_y!=null?p.offset_y:0.5)+'"></div>'+
          '<input class="aim-profile-offset-y-range" type="range" min="0" max="1" step="0.01" value="'+(p.offset_y!=null?p.offset_y:0.5)+'"><span class="field-hint">0 上 · 0.5 中 · 1 下</span></div>'+
      '</div>'+
      '<div class="aim-profile-class-row span-all"><span class="field-hint">类别过滤（不勾选=瞄准全部类别；勾选=仅瞄准该类别）</span><div class="class-chip-row">'+chips+'</div></div>'+
      '<div class="aim-profile-actions span-all">'+
        '<button class="primary-button" type="button" data-apply-profile>应用此方案</button>'+
        '<button class="ghost-button" type="button" data-save-profiles>保存全部方案</button>'+
      '</div>'+
    '</div>';
  /* 数字输入 ↔ 滑条 双向同步 */
  card.querySelectorAll('.value-input').forEach(inp=>{
    const r=inp.nextElementSibling;
    if(r&&r.type==='range'){ inp.addEventListener('input',()=>{r.value=inp.value;}); r.addEventListener('input',()=>{inp.value=r.value;}); }
  });
  card.querySelector('[data-remove-profile]').addEventListener('click',()=>{
    if(aimProfiles.length<=1){toast('至少保留一个方案');return;}
    card.remove(); saveAimProfiles();
  });
  card.querySelector('[data-save-profiles]').addEventListener('click',()=>saveAimProfiles());
  card.querySelector('[data-apply-profile]').addEventListener('click',()=>{
    const idx=Array.prototype.indexOf.call($('#aimProfilesEditor').children,card);
    saveAimProfiles(idx);
  });
  return card;
}
function collectAimProfilesDom(){
  const ed=$('#aimProfilesEditor');
  if(!ed)return [aimDefaultProfile()];
  return Array.from(ed.querySelectorAll('.aim-profile-card')).map(card=>{
    const val=s=>{const e=card.querySelector(s);return e?parseFloat(e.value):1;};
    let mask=0;
    card.querySelectorAll('.aim-profile-class').forEach(cb=>{if(cb.checked)mask|=1<<Number(cb.dataset.classId);});
    return {
      hotkey: card.querySelector('.aim-profile-hotkey').value||'left',
      hotkey2: card.querySelector('.aim-profile-hotkey2').value||'',
      hotkey_mode: card.querySelector('.aim-profile-hotkey-mode').value||'any',
      class_filter_mask: mask,
      offset_x: val('.aim-profile-offset-x'), offset_y: val('.aim-profile-offset-y'),
      pos: val('.aim-profile-offset-y'),
      sensitivity: val('.aim-profile-sensitivity'), fov_scale: val('.aim-profile-fov-scale'),
      offset_switch_enabled:false, offset_switch_hotkey:'',
      alternate_offset_x:0.5, alternate_offset_y:0.5, class_offsets:[]
    };
  });
}
async function saveAimProfiles(activate){
  const body={profiles:collectAimProfilesDom()};
  if(activate!=null) body.activate=activate;
  const r=await api('/api/aim_profiles',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast(r.ok?(r.data&&r.data.detail?(activate!=null?(r.data.detail+'｜自瞄已启用（按住热键瞄准）'):r.data.detail):'已保存'):'保存失败');
  if(activate!=null){
    // 立即同步 lastProfile（避免 500ms 轮询窗口内任意参数保存用旧值关掉自瞄/热键）
    if(!lastProfile.mouse)lastProfile.mouse={};
    lastProfile.mouse.enabled=true;
    const ap0=aimProfiles[activate];
    if(ap0){
      const hkb={'left':1,'right':2,'middle':4,'back':8,'forward':16};
      if(ap0.hotkey!=null)lastProfile.mouse.aim_hotkey=hkb[ap0.hotkey]||2;
      if(ap0.hotkey2!=null)lastProfile.mouse.aim_hotkey2=hkb[ap0.hotkey2]||0;
      if(ap0.hotkey_mode!=null)lastProfile.mouse.aim_hotkey_mode=ap0.hotkey_mode;
      if(ap0.class_offsets!=null)lastProfile.mouse.class_offsets=ap0.class_offsets;
    }
    setTimeout(refreshState,600);
    setTimeout(loadAimProfiles,800);
    // 方案参数同步到主界面控件，避免后续任意参数保存时用旧 UI 值覆盖方案生效值
    const ap=aimProfiles[activate];
    if(ap){
      const setv=(id,v)=>{const e=$(id); if(e&&v!=null)e.value=v;};
      if(ap.sensitivity!=null)setv('#sens',ap.sensitivity);
      if(ap.fov_scale!=null)setv('#range_factor',ap.fov_scale);
      const crop=val('#capture_crop_size')||320;
      if(ap.offset_x!=null)setv('#controller_aim_reference_offset_x',Math.round((ap.offset_x-0.5)*crop));
      if(ap.offset_y!=null)setv('#controller_aim_reference_offset_y',Math.round((ap.offset_y-0.5)*crop));
      updateAimRangeOverlay();
    }
  }
}
function renderAimProfilesEditor(active){
  const ed=$('#aimProfilesEditor'); if(!ed)return;
  ed.innerHTML='';
  (aimProfiles.length?aimProfiles:[aimDefaultProfile()]).forEach((p,i)=>ed.appendChild(aimProfileCard(p,i,i===active)));
}
async function loadAimProfiles(){
  const r=await api('/api/aim_profiles');
  if(r.ok){ aimProfiles=r.data.profiles||[]; renderAimProfilesEditor(r.data.active); }
}
const addProfileBtn=$('#addAimProfileButton');
if(addProfileBtn)addProfileBtn.addEventListener('click',()=>{
  const last=aimProfiles[aimProfiles.length-1]||aimDefaultProfile();
  aimProfiles.push(Object.assign(aimDefaultProfile(),{hotkey:last.hotkey==='left'?'right':'left'}));
  renderAimProfilesEditor();
});

/* ===== 自动标定（真实闭环：稳定检测→往返注入→gain 计算） ===== */
function setValN(id,v){const e=$(id); if(e&&v!=null&&(e.value===''||e.value==null)) e.value=v;}
async function refreshCalibration(){
  const r=await api('/api/control/calibration');
  if(!r.ok)return;
  const st=r.data, cal=st.calibration;
  const set=(id,v)=>{const e=$(id);if(e)e.textContent=v;};
  const badge=$('#autoCalibrationStatusBadge');
  if(badge){
    const map={idle:['等待目标','idle'],running:['标定中','running'],success:['已标定','ok'],failed:['标定失败','error'],cancelled:['已取消','idle']};
    const m=map[st.status]||['未知','idle'];
    badge.textContent=m[0]; badge.className='status-badge '+m[1];
  }
  set('#autoCalibrationCandidate', st.candidate_count!=null?st.candidate_count+' 帧':'--');
  set('#autoCalibrationStability', st.stable_ms!=null?st.stable_ms+' ms':'--');
  // 无目标时明确提示（YU：没有识别目标无法开始标定）
  if(st.status==='idle' && !_calTargetFound && st.reason==='idle'){
    set('#autoCalibrationReason','未识别到目标：请将准星对准画面中的目标，等待检测框出现');
  } else {
    set('#autoCalibrationReason', st.reason||'--');
  }
  set('#autoCalibrationConfidence', cal&&cal.confidence!=null?(+cal.confidence).toFixed(3):'未标定');
  set('#autoCalibrationModel', cal?(cal.model_id||'--'):'--');
  set('#autoCalibrationTime', cal?(cal.calibrated_at||'--'):'--');
  set('#autoCalibrationContext', cal&&cal.capture?('crop '+cal.capture.crop_size):'--');
  const bar=$('#autoCalibrationProgressBar');
  if(bar) bar.style.width=Math.round((st.progress||0)*100)+'%';
  const startBtn=$('#startAutoCalibrationButton');
  if(startBtn) startBtn.disabled=(st.status==='running')||!_calTargetFound;
  const cancelBtn=$('#cancelAutoCalibrationButton');
  if(cancelBtn) cancelBtn.disabled=!(st.status==='running');
  if(cal){
    setValN('#autoCalibrationGainX', cal.mouse_gain_x_px_per_count);
    setValN('#autoCalibrationGainY', cal.mouse_gain_y_px_per_count);
    setValN('#autoCalibrationDelay', cal.mouse_response_delay_ms);
    const sv=$('#saveAutoCalibrationValuesButton'); if(sv)sv.disabled=false;
  }
}
const calConfirmBtn=$('#confirmAutoCalibrationButton');
if(calConfirmBtn)calConfirmBtn.addEventListener('click',async()=>{
  const dlg=$('#autoCalibrationConfirmDialog'); if(dlg)dlg.hidden=true;
  const r=await api('/api/control/calibration/start',{method:'POST',body:'{}'});
  toast(r.ok?'标定已启动':(r.data&&r.data.error?'启动失败：'+r.data.error:'启动失败'));
  setTimeout(refreshCalibration,300);
});
const calStartBtn=$('#startAutoCalibrationButton');
if(calStartBtn)calStartBtn.addEventListener('click',()=>{
  const dlg=$('#autoCalibrationConfirmDialog'); if(dlg)dlg.hidden=false;
});
['#closeAutoCalibrationConfirmButton','#cancelAutoCalibrationConfirmButton'].forEach(s=>{
  const b=$(s); if(b)b.addEventListener('click',()=>{const d=$('#autoCalibrationConfirmDialog'); if(d)d.hidden=true;});
});
const calCancelBtn=$('#cancelAutoCalibrationButton');
if(calCancelBtn)calCancelBtn.addEventListener('click',async()=>{
  await api('/api/control/calibration/cancel',{method:'POST',body:'{}'});
  toast('标定已取消'); setTimeout(refreshCalibration,300);
});
const calClearBtn=$('#clearAutoCalibrationButton');
if(calClearBtn)calClearBtn.addEventListener('click',async()=>{
  const r=await api('/api/control/calibration',{method:'DELETE'});
  toast(r.ok?'标定已清除':'清除失败');
  setTimeout(refreshCalibration,300);
});
const calSaveBtn=$('#saveAutoCalibrationValuesButton');
if(calSaveBtn)calSaveBtn.addEventListener('click',async()=>{
  const v=id=>{const e=$(id);return e?parseFloat(e.value):0;};
  const body={mouse_gain_x_px_per_count:v('#autoCalibrationGainX'),
              mouse_gain_y_px_per_count:v('#autoCalibrationGainY'),
              mouse_response_delay_ms:v('#autoCalibrationDelay')};
  const r=await api('/api/control/calibration',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast(r.ok?'手动参数已保存并生效':'保存失败');
  setTimeout(refreshState,600);
});

loadAimProfiles();
refreshCalibration();
setInterval(refreshCalibration,250);

/* ===== 热键选项填充（YU 用 JS 填充，adapter 补齐） ===== */
const HK_OPTIONS=['left','right','middle','back','forward'];
const HK_NAMES={left:'左键',right:'右键',middle:'中键',back:'侧键(后)',forward:'侧键(前)'};
function fillHotkeySelects(){
  const fill=(id,extra)=>{
    const sel=$(id); if(!sel||sel.options.length>0)return;
    (extra||[]).concat(HK_OPTIONS).forEach(h=>{
      const o=document.createElement('option'); o.value=h; o.textContent=HK_NAMES[h]||h; sel.appendChild(o);
    });
  };
  fill('#recoil_hotkey'); fill('#rapid_fire_hotkey'); fill('#crosshair_hotkey');
  fill('#crosshair_hotkey2',['']); fill('#hotkey_guard_toggle_hotkey',['scroll_lock']);
  fill('#controller_y_axis_fire_hotkey',['']);
}
fillHotkeySelects();

/* ===== 屏蔽物理按键（physicalButtonBlockDialog） ===== */
function renderBlockedPhysicalButtons(blocked){
  const list=$('#physicalButtonBlockList'); if(!list)return;
  const sel=$('#physicalButtonBlockSelect'); const empty=$('#physicalButtonBlockEmpty');
  const blockedN=blocked||[];
  list.innerHTML='';
  blockedN.forEach(name=>{
    const row=document.createElement('div');
    row.className='physical-button-block-row';
    row.dataset.physicalButton=name;
    const label=document.createElement('span'); label.textContent=HK_NAMES[name]||name;
    const btn=document.createElement('button'); btn.type='button'; btn.className='icon-button';
    btn.textContent='×'; btn.setAttribute('aria-label','移除'+label.textContent);
    btn.addEventListener('click',()=>{
      row.remove();
      const cur=collectFeatures().mouse_output.blocked_physical_buttons;
      renderBlockedPhysicalButtons(cur); saveProfile();
    });
    row.append(label,btn); list.appendChild(row);
  });
  if(empty) empty.hidden=blockedN.length>0;
  const remaining=HK_OPTIONS.filter(h=>!blockedN.includes(h));
  if(sel){
    sel.innerHTML='';
    remaining.forEach(h=>{const o=document.createElement('option');o.value=h;o.textContent=HK_NAMES[h];sel.appendChild(o);});
    sel.disabled=remaining.length===0;
  }
}
const addBlockedBtn=$('#addPhysicalButtonBlockButton');
if(addBlockedBtn)addBlockedBtn.addEventListener('click',()=>{
  const sel=$('#physicalButtonBlockSelect'); const h=sel&&sel.value; if(!h)return;
  const cur=collectFeatures().mouse_output.blocked_physical_buttons;
  if(cur.includes(h))return;
  renderBlockedPhysicalButtons(cur.concat([h]));
});
const openBlockedBtn=$('#physicalButtonBlockButton');
if(openBlockedBtn)openBlockedBtn.addEventListener('click',()=>{
  renderBlockedPhysicalButtons(collectFeatures().mouse_output.blocked_physical_buttons);
  const dlg=$('#physicalButtonBlockDialog'); if(dlg)dlg.hidden=false;
});
['#closePhysicalButtonBlockButton','#cancelPhysicalButtonBlockButton'].forEach(s=>{
  const b=$(s); if(b)b.addEventListener('click',()=>{const d=$('#physicalButtonBlockDialog'); if(d)d.hidden=true;});
});
const saveBlockedBtn=$('#savePhysicalButtonBlockButton');
if(saveBlockedBtn)saveBlockedBtn.addEventListener('click',()=>{
  const dlg=$('#physicalButtonBlockDialog'); if(dlg)dlg.hidden=true;
  saveProfile();
});

/* ===== 预设参数（preset-page） ===== */
let presets=[];
async function loadPresets(){
  const r=await api('/api/presets');
  if(!r.ok)return;
  presets=r.data.presets||[];
  renderPresets();
}
function renderPresets(){
  const list=$('#presetCardList'); if(!list)return;
  const sum=$('#presetLibrarySummary'); const empty=$('#presetEmptyState');
  if(sum)sum.textContent=presets.length?presets.length+' 个预设参数':'暂无预设参数';
  if(empty)empty.hidden=presets.length>0;
  list.innerHTML='';
  presets.forEach(p=>{
    const card=document.createElement('div'); card.className='preset-card';
    card.innerHTML='<div class="preset-card-info"><strong></strong><span></span></div>'+
      '<div class="preset-card-actions"><button class="ghost-button" type="button" data-preset-apply>应用</button>'+
      '<button class="ghost-button danger" type="button" data-preset-del>删除</button></div>';
    card.querySelector('strong').textContent=p.name;
    card.querySelector('span').textContent=(p.time||'')+(p.model_id?(' · '+p.model_id):'');
    card.querySelector('[data-preset-apply]').addEventListener('click',async()=>{
      const r2=await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'apply',name:p.name})});
      toast(r2.ok?'已应用预设「'+p.name+'」':(r2.data&&r2.data.error?'应用失败：'+r2.data.error:'应用失败'));
      setTimeout(refreshState,600);
    });
    card.querySelector('[data-preset-del]').addEventListener('click',async()=>{
      await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'delete',name:p.name})});
      loadPresets();
    });
    list.appendChild(card);
  });
}
const savePresetBtn=$('#savePresetButton');
if(savePresetBtn)savePresetBtn.addEventListener('click',async()=>{
  const name=$('#presetName')&&$('#presetName').value;
  if(!name){toast('请输入预设名称');return;}
  const r=await api('/api/presets',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'save',name:name})});
  toast(r.ok?'预设已保存':'保存失败');
  if(r.ok){loadPresets();}
});
const openPresetImportBtn=$('#openPresetImportButton');
if(openPresetImportBtn)openPresetImportBtn.addEventListener('click',()=>{
  const dlg=$('#presetImportDialog'); if(dlg)dlg.hidden=false;
});
['#closePresetImportButton','#cancelPresetImportButton'].forEach(s=>{
  const b=$(s); if(b)b.addEventListener('click',()=>{const d=$('#presetImportDialog'); if(d)d.hidden=true;});
});
const presetImportForm=$('#presetImportForm');
if(presetImportForm)presetImportForm.addEventListener('submit',async(e)=>{
  e.preventDefault();
  const file=$('#presetImportFile'); if(!file||!file.files.length){toast('请选择预设文件');return;}
  try{
    const prof=JSON.parse(await file.files[0].text());
    const r=await api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(prof)});
    toast(r.ok?'预设已导入并应用':'导入失败');
  }catch(err){toast('文件解析失败');}
  const dlg=$('#presetImportDialog'); if(dlg)dlg.hidden=true;
  loadPresets(); setTimeout(refreshState,600);
});

/* ===== 自动开火 profiles 编辑器 ===== */
function autoTriggerCard(p,i){
  const card=document.createElement('div'); card.className='auto-trigger-profile-card';
  const opts=['left','right','middle','back','forward'].map(h=>'<option value="'+h+'"'+(h===(p.hotkey||'left')?' selected':'')+'>'+(HK_NAMES[h])+'</option>').join('');
  card.innerHTML=
    '<div class="aim-profile-head"><strong>开火配置 '+(i+1)+'</strong>'+
      '<button class="mini-danger" type="button" data-remove>删除</button></div>'+
    '<div class="form-subgrid"><div class="field aim-profile-hotkey-grid">'+
      '<label class="field">触发按键<select class="auto-trigger-hotkey">'+opts+'</select></label>'+
      '<label class="check-line"><input class="auto-trigger-enabled" type="checkbox"'+(p.enabled!==false?' checked':'')+'> 启用</label>'+
    '</div></div>';
  card.querySelector('[data-remove]').addEventListener('click',()=>{card.remove();saveProfile();});
  card.querySelectorAll('select,input').forEach(el=>el.addEventListener('change',()=>{const f=lastProfile.features||{};f.auto_trigger=collectAutoTrigger();lastProfile.features=f;saveProfile();}));
  return card;
}
let lastAutoTriggerSig='';
function renderAutoTrigger(){
  const ed=$('#autoTriggerProfilesEditor'); if(!ed)return;
  const f=(lastProfile.features&&lastProfile.features.auto_trigger)||{profiles:[]};
  const sig=JSON.stringify(f);
  if(sig===lastAutoTriggerSig && ed.children.length>0)return;
  lastAutoTriggerSig=sig;
  ed.innerHTML='';
  (f.profiles&&f.profiles.length?f.profiles:[{hotkey:'left',enabled:false}]).forEach((p,i)=>ed.appendChild(autoTriggerCard(p,i)));
}
const addAutoTriggerBtn=$('#addAutoTriggerProfileButton');
if(addAutoTriggerBtn)addAutoTriggerBtn.addEventListener('click',()=>{
  const ed=$('#autoTriggerProfilesEditor'); if(!ed)return;
  const n=ed.querySelectorAll('.auto-trigger-profile-card').length;
  ed.appendChild(autoTriggerCard({hotkey:n%2?'right':'left',enabled:true},n));
});

loadPresets();
renderAutoTrigger();

/* ===== 系统状态回填（授权/存储/更新/风扇/Hailo/键鼠盒子/网络，全部真实数据） ===== */
function backfillSystem(s){
  const set=(id,v)=>{const e=$(id);if(e)e.textContent=v;};
  const sys=s.system||{}, auth=sys.authorization||{}, storage=sys.storage||{};
  const upd=sys.update||{}, fan=sys.fan||{};
  set('#licenseStatusPill','永久授权');
  set('#licensePlan','永久授权');
  if(storage.total){
    const gb=v=>v!=null?(v/1e9).toFixed(1):'--';
    set('#storageExpandSummary','总 '+gb(storage.total)+' GB · 已用 '+gb(storage.used)+' GB ('+(storage.used_pct||0)+'%) · 剩余 '+gb(storage.free)+' GB');
    set('#storageExpandPill','已读取');
  } else {
    set('#storageExpandPill','读取失败');
  }
  set('#updateStatusPill', upd.available?'有更新':'最新版本');
  set('#updateSummary', upd.available?(upd.version||''):'当前已是最新版本 '+((upd.version)||'V0.01'));
  if(upd.notes) set('#updateReleaseNotes', upd.notes);
  set('#fanControlPill', fan.available?'PWM 可用':'未检测到PWM接口');
  set('#fanRuntimeMode', fan.available?'运行中':'待机');
  set('#fanControlAvailableValue', fan.available?'是':'否');
  set('#fanTemperatureSourceValue', fan.temperature_source||'--');
  set('#fanTemperatureValue', fan.temp_c?fan.temp_c+'°C':'--');
  set('#fanPwmValue', fan.pwm||'--');
  set('#fanRpmValue', fan.rpm||'--');
  set('#fanErrorValue', fan.error||'--');
  // Hailo-8（真实检测状态；由 refreshHailo 刷新，避免轮询覆盖）
  if(typeof fillHailo==='function')fillHailo(_hailoCache||{});
  set('#hailoInstallStage','空闲');
  // 键鼠盒子（USB C 桥直连，无外接盒子 → 未启用真实状态）
  set('#kmboxStatus','未启用');
  // 网络
  const net=s.network||{};
  set('#wifiStatusPill', net.nmcli?(net.wifi_connected?('已连接 '+(net.wifi_ssid||'')):'未连接'):'未检测到 nmcli');
  const wsum=$('#wifiSummary');
  if(wsum)wsum.innerHTML='<div class="runtime-stat"><span>IP 地址</span><strong>'+(net.ip||'--')+'</strong></div>'+
    '<div class="runtime-stat"><span>主机名</span><strong>'+(net.hostname||'--')+'</strong></div>'+
    '<div class="runtime-stat"><span>Wi-Fi</span><strong>'+(net.wifi_connected?(net.wifi_ssid||'已连接'):'未连接')+'</strong></div>';
  // 总览硬件卡（systemSummary）
  const hw=s.hwmon||{};
  const ss=$('#systemSummary');
  if(ss){
    ss.innerHTML='<div class="runtime-stat"><span>SoC 温度</span><strong>'+(hw.soc_temp_c!=null?hw.soc_temp_c+'°C':'--')+'</strong></div>'+
      '<div class="runtime-stat"><span>CPU</span><strong>'+(hw.cpu4_freq_hz!=null?(hw.cpu4_freq_hz/1e6).toFixed(1)+' GHz':'--')+'</strong></div>'+
      '<div class="runtime-stat"><span>存储</span><strong>'+(storage.total?(storage.used_pct+'%'):'--')+'</strong></div>'+
      '<div class="runtime-stat"><span>版本</span><strong>V0.01</strong></div>';
  }
}

/* ===== 个性曲线训练（真实采集：phys 移动 + 目标位置） ===== */
let motionState={status:'idle',samples:0,quality:0};
async function refreshMotion(){
  const r=await api('/api/motion');
  if(r.ok)motionState=r.data;
  const badge=$('#motionTrainingLeaseBadge');
  if(badge){
    badge.textContent=motionState.status==='running'?('采集中 '+motionState.samples+' 样本'):(motionState.samples>0?(motionState.samples+' 样本'):'未采集');
    badge.className='status-badge '+(motionState.status==='running'?'running':(motionState.quality>=40?'ok':'idle'));
  }
  const startBtn=$('#motionTrainingStart'), stopBtn=$('#motionTrainingStop');
  if(startBtn)startBtn.disabled=motionState.status==='running';
  if(stopBtn)stopBtn.disabled=motionState.status!=='running';
  const q=$('#motionTrainingQualityBadge');
  if(q){q.textContent=motionState.quality>=40?'已训练':(motionState.samples>0?'采样中':'未训练');q.className='status-badge '+(motionState.quality>=40?'ok':'idle');}
  const qb=$('#motionTrainingQualityBar');
  if(qb)qb.style.width=motionState.quality+'%';
}
const mtStart=$('#motionTrainingStart');
if(mtStart)mtStart.addEventListener('click',async()=>{
  const r=await api('/api/motion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'start'})});
  toast(r.ok?'采集已开始':'启动失败'); refreshMotion();
});
const mtStop=$('#motionTrainingStop');
if(mtStop)mtStop.addEventListener('click',async()=>{
  const r=await api('/api/motion',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'stop'})});
  if(r.ok&&r.data&&r.data.detail)toast(r.data.detail); else toast('采集已结束');
  refreshMotion();
});
refreshMotion();
setInterval(refreshMotion,1000);

/* ===== 开机自启动开关（总览页 mobile/desktop 两个 data-auto-start-toggle） ===== */
async function refreshAutoStart(){
  const r=await api('/api/auto-start');
  if(!r.ok)return;
  document.querySelectorAll('[data-auto-start-toggle]').forEach(cb=>cb.checked=!!(r.data&&r.data.enabled));
}
document.querySelectorAll('[data-auto-start-toggle]').forEach(cb=>{
  cb.addEventListener('change',async()=>{
    const r=await api('/api/auto-start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:cb.checked})});
    if(!r.ok){cb.checked=!cb.checked;toast('设置失败');return;}
    document.querySelectorAll('[data-auto-start-toggle]').forEach(x=>{x.checked=cb.checked;});
    toast(cb.checked?'开机自启动已开启':'开机自启动已关闭');
  });
});
refreshAutoStart();

/* ===== 显示器 EDID 身份（显示与鼠标页：随机身份/保存并应用/刷新） ===== */
async function loadDisplay(){
  const r=await api('/api/display');
  if(!r.ok)return;
  const cfg=r.data.config||{};
  const setv=(id,v)=>{const e=$(id);if(e&&v!=null&&(e.value===''||e.value==null))e.value=v;};
  const nm=cfg.native_mode||'';
  const sel=$('#display_native_mode');
  if(sel){
    let matched='';
    Array.from(sel.options).forEach(o=>{if(o.value&&nm.indexOf(o.value)>=0)matched=o.value;});
    if(matched)sel.value=matched;
  }
  setv('#display_name',cfg.name);
  setv('#display_vendor',cfg.vendor);
  setv('#display_product_id',cfg.product_id);
  setv('#display_serial',cfg.serial);
  const no=$('#display_native_only'); if(no&&cfg.native_only!=null)no.checked=!!cfg.native_only;
  setv('#display_loopout_pixel_format',cfg.loopout_pixel_format);
  const pill=$('#displayHardwareStatus');
  if(pill)pill.textContent=(r.data.edid&&r.data.edid.applied)?'已应用':'应用失败';
  const log=$('#displayHardwareLog');
  if(log&&r.data.log&&r.data.log.length)log.textContent=r.data.log.join('\n');
}
function collectDisplayConfig(){
  return {
    native_mode:str('#display_native_mode')||'',
    native_only:chk('#display_native_only'),
    pixel_format:str('#display_loopout_pixel_format')||'rgb888',
    name:str('#display_name')||'',vendor:str('#display_vendor')||'',
    product_id:str('#display_product_id')||'',serial:str('#display_serial')||''
  };
}
const randDispBtn=$('#randomDisplayHardwareButton');
if(randDispBtn)randDispBtn.addEventListener('click',async()=>{
  const r=await api('/api/display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'randomize'})});
  if(r.ok&&r.data&&r.data.identity){
    const c=r.data.identity;
    const setv=(id,v)=>{const e=$(id);if(e)e.value=v||'';};
    setv('#display_name',c.name);setv('#display_vendor',c.vendor);
    setv('#display_product_id',c.product_id);setv('#display_serial',c.serial);
    toast('已生成随机身份，点击「保存并应用」后生效');
  } else {
    toast('生成失败');
  }
});
const saveDispBtn=$('#saveDisplayHardwareButton');
if(saveDispBtn)saveDispBtn.addEventListener('click',async()=>{
  const r=await api('/api/display',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'save',config:collectDisplayConfig()})});
  toast(r.ok?'显示器配置已保存并应用':'保存失败');
  loadDisplay();
});
const refreshHwBtn=$('#refreshHardwareButton');
if(refreshHwBtn)refreshHwBtn.addEventListener('click',loadDisplay);
loadDisplay();

/* ===== USB 鼠标硬件信息（显示与鼠标页：完整透传真实信息 / 合成模式自定义身份） ===== */
const MOUSE_EDITABLE=['#mouse_usb_vid','#mouse_usb_pid','#mouse_usb_manufacturer','#mouse_usb_product','#mouse_usb_serial','#mouse_usb_configuration'];
function applyMouseModeUI(mode){
  const editable=mode==='synthetic';
  MOUSE_EDITABLE.forEach(id=>{const e=$(id);if(e)e.toggleAttribute('readonly',!editable);});
  const hint=$('#mouseHardwareModeHint');
  if(hint)hint.textContent=editable
    ?'合成模式：自定义 USB 身份（VID/PID/制造商等），点击「保存并应用」后重建生效。'
    :'完整透传：使用真实鼠标硬件信息（C 桥 3 路双向透传）。';
}
function fillMouseFields(d){
  const setv=(id,v)=>{const e=$(id);if(e&&v!=null)e.value=v;};
  setv('#mouse_settle_delay_sec', d.settle_delay_sec);
  setv('#mouse_identity_change_settle_delay_sec', d.identity_change_settle_delay_sec);
  const mode=(document.querySelector('input[name="mouse_proxy_mode"]:checked')||{}).value||'full_passthrough';
  const idn=(mode==='synthetic'&&d.identity&&Object.keys(d.identity).length)?d.identity:(d.real||{});
  setv('#mouse_usb_vid', idn.vid); setv('#mouse_usb_pid', idn.pid);
  setv('#mouse_usb_manufacturer', idn.manufacturer); setv('#mouse_usb_product', idn.product);
  setv('#mouse_usb_serial', idn.serial); setv('#mouse_usb_configuration', idn.configuration);
  setv('#mouse_usb_bcd_usb', idn.bcd_usb); setv('#mouse_usb_bcd_device', idn.bcd_device);
  setv('#mouse_usb_device_class', idn.device_class); setv('#mouse_usb_device_subclass', idn.device_subclass);
  setv('#mouse_usb_device_protocol', idn.device_protocol); setv('#mouse_usb_max_power', idn.max_power);
  setv('#mouse_hid_protocol', idn.hid_protocol); setv('#mouse_hid_subclass', idn.hid_subclass);
  setv('#mouse_hid_report_length', idn.hid_report_length); setv('#mouse_hid_interval', idn.hid_interval);
  setv('#mouse_hid_report_desc_hex', idn.hid_report_desc_hex);
  applyMouseModeUI(mode);
  const pill=$('#mouseHardwareStatus');
  if(pill)pill.textContent=(d.real&&Object.keys(d.real).length?'透传中':'未读取')+(mode==='synthetic'?' · 合成':'');
}
async function loadMouse(){
  const r=await api('/api/mouse');
  if(!r.ok)return;
  const d=r.data||{};
  const mode=d.proxy_mode==='synthetic'?'synthetic':'full_passthrough';
  const pm=$('input[name="mouse_proxy_mode"][value="'+mode+'"]');
  if(pm)pm.checked=true;
  fillMouseFields(d);
}
const saveMouseTimingBtn=$('#saveMouseProxyTimingButton');
if(saveMouseTimingBtn)saveMouseTimingBtn.addEventListener('click',async()=>{
  const r=await api('/api/mouse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
    action:'save',
    settle_delay_sec:val('#mouse_settle_delay_sec'),
    identity_change_settle_delay_sec:val('#mouse_identity_change_settle_delay_sec')
  })});
  toast(r.ok?'等待时间已保存':'保存失败：'+((r.data&&r.data.detail)||'未知错误'));
});
const randMouseBtn=$('#randomMouseHardwareButton');
if(randMouseBtn)randMouseBtn.addEventListener('click',async()=>{
  const mode=(document.querySelector('input[name="mouse_proxy_mode"]:checked')||{}).value||'full_passthrough';
  if(mode!=='synthetic'){toast('请先切换到「合成模式」再生成随机身份');return;}
  const r=await api('/api/mouse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'randomize'})});
  if(r.ok&&r.data&&r.data.identity){
    const c=r.data.identity;
    const setv=(id,v)=>{const e=$(id);if(e)e.value=v||'';};
    setv('#mouse_usb_vid',c.vid);setv('#mouse_usb_pid',c.pid);
    setv('#mouse_usb_manufacturer',c.manufacturer);setv('#mouse_usb_product',c.product);
    setv('#mouse_usb_serial',c.serial);setv('#mouse_usb_configuration',c.configuration);
    toast('已生成随机身份，点击「保存并应用」后生效');
  }else{
    toast('生成失败');
  }
});
const saveMouseHwBtn=$('#saveMouseHardwareButton');
if(saveMouseHwBtn)saveMouseHwBtn.addEventListener('click',async()=>{
  const mode=(document.querySelector('input[name="mouse_proxy_mode"]:checked')||{}).value||'full_passthrough';
  const body={
    action:'save',
    proxy_mode:mode,
    settle_delay_sec:val('#mouse_settle_delay_sec'),
    identity_change_settle_delay_sec:val('#mouse_identity_change_settle_delay_sec')
  };
  if(mode==='synthetic'){
    body.identity={
      vid:str('#mouse_usb_vid')||'',pid:str('#mouse_usb_pid')||'',
      manufacturer:str('#mouse_usb_manufacturer')||'',product:str('#mouse_usb_product')||'',
      serial:str('#mouse_usb_serial')||'',configuration:str('#mouse_usb_configuration')||''
    };
  }
  const r=await api('/api/mouse',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast(r.ok?'鼠标身份已保存并应用（主机将重新枚举）':'保存失败：'+((r.data&&r.data.detail)||'未知错误'));
  setTimeout(loadMouse,1800);
});
loadMouse();

/* ===== 模型库（05 模块：列表/导入/远端连接/设备码，复杂功能待接入） ===== */
function escHtml(s){return String(s==null?'':s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function fmtSize(b){b=Number(b)||0;if(b>=1048576)return (b/1048576).toFixed(1)+' MB';if(b>=1024)return (b/1024).toFixed(0)+' KB';return b+' B';}
function setDialog(sel,open){const d=$(sel);if(d)d.hidden=!open;}
let modelFilter='all', modelGameFilter='';

function renderModelFilters(models){
  const box=$('#modelBackendFilters'); if(!box)return;
  const opts=[['all','全部'],['rknn','RKNN'],['hef','HEF'],['remote','远端']];
  box.innerHTML='';
  opts.forEach(([v,label])=>{
    const b=document.createElement('button');
    b.type='button';
    b.className='model-filter-chip model-format-chip'+(modelFilter===v?' is-active':'');
    b.textContent=label;
    b.addEventListener('click',()=>{modelFilter=v;renderModelFilters(models);renderModelList(models);});
    box.appendChild(b);
  });
  const gbox=$('#modelGameFilters'); if(!gbox)return;
  const games=['',...new Set((models||[]).map(m=>m.game_profile||'').filter(Boolean))];
  gbox.innerHTML='';
  games.forEach(g=>{
    const b=document.createElement('button');
    b.type='button';
    b.className='model-filter-chip'+(modelGameFilter===g?' is-active':'');
    b.textContent=g||'全部游戏';
    b.addEventListener('click',()=>{modelGameFilter=g;renderModelFilters(models);renderModelList(models);});
    gbox.appendChild(b);
  });
}
function filteredModels(models){
  return (models||[]).filter(m=>{
    if(modelFilter!=='all'){
      const b=m.backend==='remote'?'remote':(m.backend==='hef'||m.file_name.toLowerCase().endsWith('.hef')?'hef':'rknn');
      if(b!==modelFilter)return false;
    }
    if(modelGameFilter&&(m.game_profile||'')!==modelGameFilter)return false;
    return true;
  });
}
function modelCardHtml(m){
  const status=m.active?'当前使用':(m.status==='staging'?(m.backend==='onnx'?'待转换':'暂存中'):'可切换');
  const backendLabel=m.backend==='remote'?'远端':(m.backend==='hef'?'HEF':(m.backend==='onnx'?'ONNX':'RKNN'));
  const pending=m.backend!=='rknn'||m.status==='staging'&&m.backend!=='rknn';
  return '<span class="model-card-top">'+
    '<span class="model-card-title">'+escHtml(m.file_name)+'</span>'+
    '<span class="model-card-status">'+status+'</span></span>'+
    '<span class="model-card-meta">'+
      '<span>'+backendLabel+'</span><span>'+fmtSize(m.size)+'</span>'+
      '<span>'+(m.class_count?m.class_count+' 类':(m.class_names&&m.class_names.length?m.class_names.length+' 类':'类别未标注'))+'</span>'+
      '<span>'+escHtml(m.game_profile||'通用')+'</span>'+
    '</span>'+
    '<div class="model-card-actions">'+
      '<button class="ghost-button" type="button" data-model-select'+(pending||m.active?' disabled':'')+'>使用</button>'+
      '<button class="ghost-button" type="button" data-model-classes>类别</button>'+
      '<button class="ghost-button" type="button" data-model-delete'+(m.active?' disabled':'')+'>删除</button>'+
    '</div>';
}
function renderModelList(models){
  const list=$('#modelCardList'), empty=$('#modelEmptyState');
  if(!list)return;
  list.innerHTML='';
  const filtered=filteredModels(models);
  if(empty)empty.hidden=filtered.length!==0;
  filtered.forEach(m=>{
    const card=document.createElement('div');
    card.className='model-card'+(m.active?' is-active':'');
    card.innerHTML=modelCardHtml(m);
    card.querySelector('[data-model-select]').addEventListener('click',async()=>{
      if(m.backend!=='rknn'){toast('该模型后端待接入（'+(m.backend==='onnx'?'ONNX 需转换为 RKNN':'远端/Hailo 待接入')+'）');return;}
      const r=await api('/api/models/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:m.id})});
      toast(r.ok?'已切换模型':'切换失败：'+((r.data&&r.data.detail)||''));
      setTimeout(()=>loadModels(),1200);
    });
    card.querySelector('[data-model-classes]').addEventListener('click',()=>{toast('类别编辑待接入');});
    card.querySelector('[data-model-delete]').addEventListener('click',async()=>{
      const r=await api('/api/models/delete',{method:'DELETE',headers:{'Content-Type':'application/json'},body:JSON.stringify({model_id:m.id})});
      toast(r.ok?'模型已删除':'删除失败：'+((r.data&&r.data.detail)||''));
      setTimeout(()=>loadModels(),800);
    });
    list.appendChild(card);
  });
}
let _modelsRetry=0;
var _modelClassNames=[];  // 当前模型类别名缓存（aim 方案类别过滤 chip 用）
var _calTargetFound=false;  // 标定前置：是否识别到目标（refreshState 更新）
async function loadModels(){
  const r=await api('/api/models');
  if(!r.ok){
    if(_modelsRetry<5){_modelsRetry++;setTimeout(loadModels,2000);}
    return;
  }
  _modelsRetry=0;
  const models=r.data.models||[];
  const am=models.find(m=>m.active);
  const nameSrc=am&&am.class_names&&am.class_names.length?am.class_names:
                 ((models.find(m=>m.class_names&&m.class_names.length)||{}).class_names||[]);
  _modelClassNames=Array.isArray(nameSrc)?nameSrc:[];
  const pill=$('#modelLibrarySummary');
  if(pill)pill.textContent=models.length+' 个可用';
  const cur=$('#modelCurrentName'); if(cur)cur.textContent=am?(am.file_name||am.name):'尚未选择模型';
  const curMeta=$('#modelCurrentMeta');
  if(curMeta)curMeta.textContent=am?(am.backend==='remote'?'远端':am.backend==='hef'?'HEF':'RKNN')+' · '+fmtSize(am.size)+(am.class_count?' · '+am.class_count+' 类':''):'等待模型信息';
  renderModelFilters(models);
  renderModelList(models);
}

/* 模型导入对话框 */
const openImportBtn=$('#openModelImportButton');
if(openImportBtn)openImportBtn.addEventListener('click',()=>setDialog('#modelImportDialog',true));
[['#closeModelImportButton','#modelImportDialog'],['#cancelModelImportButton','#modelImportDialog']].forEach(([btn,d])=>{
  const b=$(btn); if(b)b.addEventListener('click',()=>setDialog(d,false));
});
document.querySelectorAll('#modelImportForm input[name="model_type"]').forEach(r=>r.addEventListener('change',()=>{
  const t=(document.querySelector('#modelImportForm input[name="model_type"]:checked')||{}).value||'rknn';
  const hint=$('#modelImportHint');
  if(hint){
    if(t==='onnx'){hint.hidden=false;hint.textContent='上传 ONNX 后会自动转换为 RKNN（板端转换待接入，先存暂存区）。';}
    else if(t==='remote_onnx'){hint.hidden=false;hint.textContent='远端 ONNX 需 Windows 端推理服务（远端推理接入待后续实现）。';}
    else if(t==='hef'){hint.hidden=false;hint.textContent='HEF 需 Hailo-8 硬件（当前未检测到，待接入）。';}
    else {hint.hidden=true;hint.textContent='';}
  }
}));
const importForm=$('#modelImportForm');
if(importForm)importForm.addEventListener('submit',async(e)=>{
  e.preventDefault();
  const btn=$('#submitModelImportButton');
  if(btn){btn.disabled=true;btn.textContent='上传中...';}
  const r=await api('/api/models/import',{method:'POST',body:new FormData(importForm)});
  if(btn){btn.disabled=false;btn.textContent='导入';}
  toast(r.ok?(r.data&&r.data.detail||'导入成功'):'导入失败：'+((r.data&&r.data.detail)||'未知错误'));
  if(r.ok){setDialog('#modelImportDialog',false);importForm.reset();setTimeout(()=>loadModels(),800);}
});

/* 连接远端推理对话框（设备端先行，推理接入待后续） */
const openRemoteBtn=$('#openRemoteConnectButton');
if(openRemoteBtn)openRemoteBtn.addEventListener('click',()=>setDialog('#remoteConnectDialog',true));
[['#closeRemoteConnectButton','#remoteConnectDialog'],['#cancelRemoteConnectButton','#remoteConnectDialog']].forEach(([btn,d])=>{
  const b=$(btn); if(b)b.addEventListener('click',()=>setDialog(d,false));
});
const remoteForm=$('#remoteConnectForm');
if(remoteForm)remoteForm.addEventListener('submit',async(e)=>{
  e.preventDefault();
  const host=($('#remoteHostInput')||{}).value?$('#remoteHostInput').value.trim():'';
  const btn=$('#submitRemoteConnectButton');
  if(btn){btn.disabled=true;btn.textContent='连接中...';}
  const r=await api('/api/remote/connect',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({host})});
  if(btn){btn.disabled=false;btn.textContent='连接';}
  const err=$('#remoteConnectError');
  if(r.ok){
    if(err)err.hidden=true;
    toast((r.data&&r.data.detail)||'已连接远端主机');
    setDialog('#remoteConnectDialog',false);
  }else{
    const msg=(r.data&&r.data.detail)||'连接失败';
    if(err){err.hidden=false;err.textContent=msg;}
  }
});
/* 复制设备码 */
const codeBtn=$('#copyModelDeviceCodeButton');
if(codeBtn)codeBtn.addEventListener('click',async()=>{
  const r=await api('/api/remote/device-code');
  if(!r.ok||!r.data||!r.data.device_code){toast('获取设备码失败');return;}
  const code=r.data.device_code;
  try{await navigator.clipboard.writeText(code);toast('设备码已复制：'+code);}
  catch(err){toast('设备码：'+code);}
});
loadModels();

/* ===== 全功能按钮绑定（总览/系统操作/Hailo/键鼠盒子/网络/主题/系统状态） ===== */
/* ---- 总览：授权刷新/激活、重启/关机 ---- */
const refreshLicBtn=$('#refreshLicenseButton');
if(refreshLicBtn)refreshLicBtn.addEventListener('click',()=>{toast('设备已激活（永久授权）');setTimeout(refreshState,300);});
const activateLicBtn=$('#activateLicenseButton');
if(activateLicBtn)activateLicBtn.addEventListener('click',()=>toast('设备已激活（永久授权），无需卡密'));
const rebootBtn=$('#rebootSystemButton');
if(rebootBtn)rebootBtn.addEventListener('click',async()=>{
  if(!hasBackend){toast('本地预览：重启将调用板端 /api/system');return;}
  if(!window.confirm('确定重启设备？连接将断开，稍后可重新访问。'))return;
  const r=await api('/api/system',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'reboot'})});
  toast(r.ok?(r.data&&r.data.detail||'正在重启'):'重启失败');
});
const poweroffBtn=$('#poweroffSystemButton');
if(poweroffBtn)poweroffBtn.addEventListener('click',async()=>{
  if(!hasBackend){toast('本地预览：关机将调用板端 /api/system');return;}
  if(!window.confirm('确定关机？设备将完全断电，需手动开机。'))return;
  const r=await api('/api/system',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'poweroff'})});
  toast(r.ok?(r.data&&r.data.detail||'正在关机'):'关机失败');
});
const resetOverviewBtn=$('#resetOverviewDefaultsButton');
if(resetOverviewBtn)resetOverviewBtn.addEventListener('click',()=>toast('重置默认参数待接入'));

/* ---- Hailo-8（07）：真实检测 ---- */
var _hailoCache={};
function fillHailo(h){
  _hailoCache=h||{};
  const set=(id,v)=>{const e=$(id);if(e)e.textContent=v;};
  set('#hailoReadyPill',h.detected?'已检测':'未检测');
  set('#hailoPcieValue',h.pcie||'未检测到 Hailo-8 PCIe 设备');
  set('#hailoDriverValue',h.driver||'未安装');
  set('#hailoRuntimeValue',h.runtime||'未安装');
  set('#hailoScanValue','--'); set('#hailoKernelValue','--'); set('#hailoTemperatureValue','--');
}
async function refreshHailo(){
  const r=await api('/api/hailo/status');
  if(r.ok)fillHailo(r.data||{});
}
const refreshHailoBtn=$('#refreshHailoButton');
if(refreshHailoBtn)refreshHailoBtn.addEventListener('click',refreshHailo);
refreshHailo();

/* ---- 键鼠盒子（08）---- */
const saveKmboxBtn=$('#saveKmboxButton');
if(saveKmboxBtn)saveKmboxBtn.addEventListener('click',async()=>{
  const body={
    enabled:chk('#kmbox_enabled'), ip:str('#kmbox_ip')||'',
    port:val('#kmbox_port')||0, uuid:str('#kmbox_uuid')||'',
    monitor_port:val('#kmbox_monitor_port')||0,
    timeout_ms:val('#kmbox_timeout_ms')||300, encrypted:chk('#kmbox_encrypted')
  };
  const r=await api('/api/kmbox/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  toast(r.ok?'键鼠盒子配置已保存':'保存失败：'+((r.data&&r.data.detail)||''));
});
/* ---- 测试画圈（真实 HID 注入）---- */
const testMouseCircleBtn=$('#testMouseCircleButton');
if(testMouseCircleBtn)testMouseCircleBtn.addEventListener('click',async()=>{
  const r=await api('/api/aim/draw_circle',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({radius:40,rounds:2})});
  toast((r.data&&r.data.detail)||'画圈测试失败（hidg1 未就绪）');
});

/* ---- 串口盒子：刷新 = 真实检测串口设备 ---- */
async function refreshSerialDevices(){
  const r=await api('/api/serial/devices');
  const d=r.data||{};
  const devs=d.devices||[];
  ['#makcu_port','#ferrum_port','#kmboxb_port'].forEach(selId=>{
    const sel=$(selId); if(!sel)return;
    sel.innerHTML='<option value="">自动（未检测到设备）</option>'+(devs.map(x=>'<option value="'+escHtml(x.dev)+'">'+escHtml(x.dev+' ('+x.chip+')')+'</option>')).join('');
  });
  const msg=devs.length?('检测到 '+devs.length+' 个串口设备'):'未检测到串口设备';
  ['#makcuStatus','#ferrumStatus','#kmboxbStatus'].forEach(id=>{const p=$(id); if(p)p.textContent=devs.length?'已检测':'未检测';});
  toast(msg);
}
['refreshMakcuDevices','refreshFerrumDevices','refreshKmboxbDevices'].forEach(prefix=>{
  const b=$('#'+prefix+'Button');
  if(b)b.addEventListener('click',refreshSerialDevices);
});

/* ---- 串口盒子：保存配置（真实持久化；串口协议未实现则如实反馈） ---- */
async function saveSerialBox(kind){
  const port=$('#'+kind+'_port'); const enabled=$('#'+kind+'_enabled');
  const r=await api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({features:{mouse_output:{[kind]:{enabled:!!(enabled&&enabled.checked),port:(port&&port.value)||''}}}})});
  if(!r.ok){toast('保存失败：'+((r.data&&r.data.error)||''));return;}
  toast((port&&port.value)?(kind+' 配置已保存（连接 '+port.value+' 的串口协议待接入）'):(kind+' 配置已保存；未检测到串口设备'));
}
['saveMakcu','saveFerrum','saveKmboxb'].forEach(prefix=>{
  const b=$('#'+prefix+'Button');
  if(b)b.addEventListener('click',()=>saveSerialBox({saveMakcu:'makcu',saveFerrum:'ferrum',saveKmboxb:'kmboxb'}[prefix]));
});
const saveCatnetBtn=$('#saveCatnetButton');
if(saveCatnetBtn)saveCatnetBtn.addEventListener('click',async()=>{
  const body={enabled:chk('#catnet_enabled'),ip:str('#catnet_ip')||'',port:val('#catnet_port')||0,uuid:str('#catnet_uuid')||'',monitor_port:val('#catnet_monitor_port')||0,timeout_ms:val('#catnet_timeout_ms')||300};
  const r=await api('/api/profile',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({features:{mouse_output:{catnetnet:body}}})});
  toast(r.ok?'CatNet 配置已保存（UDP 连接协议待接入）':'保存失败：'+((r.data&&r.data.error)||''));
});

/* ---- 网络配置（09）---- */
const wifiModePairs=[['#wifiClientModeButton','#wifiClientPanel'],['#wifiApModeButton','#wifiApPanel']];
wifiModePairs.forEach(([btn,panel])=>{
  const b=$(btn); if(!b)return;
  b.addEventListener('click',()=>{
    wifiModePairs.forEach(([b2,p2])=>{
      const e=$(b2); if(e)e.classList.toggle('is-active',e===b);
      const p=$(p2); if(p)p.hidden=(p2!==panel);
    });
  });
});
function wifiAction(action){
  return api('/api/network/wifi',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
}
[['#wifiScanButton','scan'],['#wifiConnectButton','connect'],['#wifiFallbackButton','fallback'],
 ['#wifiApApplyButton','ap_apply'],['#wifiClientActivateButton','client_activate']].forEach(([id,act])=>{
  const b=$(id); if(!b)return;
  b.addEventListener('click',async()=>{const r=await wifiAction(act);toast((r.data&&r.data.detail)||'操作不可用');});
});
const applyNetBtn=$('#applyNetworkAccessButton');
if(applyNetBtn)applyNetBtn.addEventListener('click',async()=>{
  const hostname=($('#lanHostnameInput')||{}).value?$('#lanHostnameInput').value.trim():'';
  if(!hostname){toast('请输入局域网名称');return;}
  const r=await api('/api/system',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'hostname',hostname})});
  toast(r.ok?'主机名已更新':'更新失败：'+((r.data&&r.data.detail)||''));
  setTimeout(refreshState,1000);
});
/* ---- 局域网扫描 / 拉黑 / 清除 ---- */
async function lanScan(){
  const r=await api('/api/network/lan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'scan'})});
  const d=r.data||{};
  toast(d.detail||'扫描失败');
  const sel=$('#lanBlockDeviceSelect');
  if(sel)sel.innerHTML='<option value="">— 选择设备 —</option>'+(d.devices||[]).map(x=>'<option value="'+escHtml(x.ip)+'">'+escHtml(x.ip+' ('+x.mac+')')+'</option>').join('');
  const sum=$('#lanBlockSummary'); if(sum)sum.textContent=(d.blocked&&d.blocked.length)?('已拉黑：'+escHtml(d.blocked.join(', '))):'当前无拉黑 IP';
  const pill=$('#lanBlockStatusPill'); if(pill)pill.textContent='已读取';
  const btn=$('#applyLanBlockButton'); if(btn)btn.disabled=false;
  const clr=$('#clearLanBlockButton'); if(clr)clr.disabled=!(d.blocked&&d.blocked.length);
}
const scanLanBtn=$('#scanLanDevicesButton');
if(scanLanBtn)scanLanBtn.addEventListener('click',lanScan);
const applyLanBlockBtn=$('#applyLanBlockButton');
if(applyLanBlockBtn)applyLanBlockBtn.addEventListener('click',async()=>{
  const sel=$('#lanBlockDeviceSelect'); let ip=(sel&&sel.value)?sel.value:'';
  const inp=$('#lanBlockIpInput'); if(inp&&inp.value.trim())ip=inp.value.trim();
  if(!ip){toast('请选择或输入要拉黑的 IP');return;}
  const r=await api('/api/network/lan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'block',ip})});
  toast((r.data&&r.data.detail)||'拉黑失败'); lanScan();
});
const clearLanBlockBtn=$('#clearLanBlockButton');
if(clearLanBlockBtn)clearLanBlockBtn.addEventListener('click',async()=>{
  const r=await api('/api/network/lan',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'clear'})});
  toast((r.data&&r.data.detail)||'清除失败'); lanScan();
});

/* ---- USB 诊断日志导出 ---- */
const usbDiagBtn=$('#downloadUsbDiagnosticsButton');
if(usbDiagBtn)usbDiagBtn.addEventListener('click',async()=>{
  const r=await api('/api/diagnostics/usb');
  const d=r.data||{};
  if(!d.text){toast('USB 诊断收集失败');return;}
  const blob=new Blob([d.text],{type:'text/plain'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='ttbox-usb-diagnostics-'+Date.now()+'.txt'; a.click();
  setTimeout(()=>URL.revokeObjectURL(a.href),3000);
  toast('USB 诊断日志已导出');
});

/* ---- 主题商店（11）：本地主题扫描 + 应用 ---- */
function applyTheme(id,file){
  let link=$('#theme-style-link');
  if(!link){link=document.createElement('link');link.id='theme-style-link';link.rel='stylesheet';document.head.appendChild(link);}
  link.href='/themes/'+encodeURIComponent(file);
  document.documentElement.dataset.theme=id;
  try{localStorage.setItem('ttbox_theme',id);}catch(e){}
  toast('已应用主题：'+id);
  const st=$('#themeStoreStatus'); if(st)st.textContent='当前主题：'+id;
}
async function refreshThemes(){
  const st=$('#themeStoreStatus'); if(st)st.textContent='正在读取主题目录';
  const r=await api('/api/themes');
  const d=r.data||{themes:[]};
  const list=$('#themeCardList'); if(!list)return;
  list.innerHTML='';
  if(d.error){if(st)st.textContent='主题目录读取失败：'+d.error;return;}
  const themes=d.themes||[];
  if(st)st.textContent=themes.length?('发现 '+themes.length+' 个本地主题'):('暂无本地主题（可将主题 CSS 放入 '+(d.dir||'themes')+'）');
  themes.forEach(t=>{
    const card=document.createElement('div'); card.className='theme-card';
    card.innerHTML='<div class="theme-card-body"><h4>'+escHtml(t.title)+'</h4><small>'+escHtml(t.file)+'</small></div><div class="theme-card-actions"><button class="ghost-button" type="button" data-id="'+escHtml(t.id)+'" data-file="'+escHtml(t.file)+'">应用</button></div>';
    list.appendChild(card);
  });
  list.querySelectorAll('button[data-file]').forEach(btn=>{btn.addEventListener('click',()=>applyTheme(btn.dataset.id,btn.dataset.file));});
}
const refreshThemesBtn=$('#refreshThemesButton');
if(refreshThemesBtn)refreshThemesBtn.addEventListener('click',refreshThemes);
try{const saved=localStorage.getItem('ttbox_theme'); if(saved)document.documentElement.dataset.theme=saved;}catch(e){}
const closeThemePreviewBtn=$('#closeThemePreviewButton');
if(closeThemePreviewBtn)closeThemePreviewBtn.addEventListener('click',()=>{const d=$('#themePreviewDialog');if(d)d.hidden=true;});

/* ---- 系统状态（12）---- */
const refreshStorageBtn=$('#refreshStorageButton');
if(refreshStorageBtn)refreshStorageBtn.addEventListener('click',()=>{refreshState();toast('已刷新系统状态');});
const reactivateBtn=$('#reactivateDeviceButton');
if(reactivateBtn)reactivateBtn.addEventListener('click',()=>toast('设备为永久授权，无需修复'));
/* ---- 更新检查 / 安装（真实检测本地更新包）---- */
const checkUpdateBtn=$('#checkUpdateButton');
if(checkUpdateBtn)checkUpdateBtn.addEventListener('click',async()=>{
  const r=await api('/api/update/check');
  const d=r.data||{};
  const pill=$('#updateStatusPill'); if(pill)pill.textContent=d.available?'有更新':'最新版本';
  const sum=$('#updateSummary'); if(sum)sum.textContent=d.notes||'未配置更新源';
  const notes=$('#updateReleaseNotes'); if(notes)notes.textContent=(d.packages||[]).join('\n')||'';
  const inst=$('#installUpdateButton'); if(inst)inst.disabled=!(d.available);
  toast(d.available?('发现 '+(d.packages||[]).length+' 个更新包'):'当前已是最新版本');
});
const installUpdateBtn=$('#installUpdateButton');
if(installUpdateBtn)installUpdateBtn.addEventListener('click',async()=>{
  const r=await api('/api/update/check');
  const d=r.data||{};
  if(!d.available){toast('无可用更新包');return;}
  toast('更新包位于 '+(d.source||'')+'；当前仅支持检测，完整 OTA 安装流程待接入');
});
['switchUpdateVersion','cleanupUpdateStatus'].forEach(prefix=>{
  const b=$('#'+prefix+'Button');
  if(b)b.addEventListener('click',()=>toast('当前无历史版本/更新状态可管理'));
});
const cancelUpdateVerBtn=$('#cancelUpdateVersionButton');
if(cancelUpdateVerBtn)cancelUpdateVerBtn.addEventListener('click',()=>{const d=$('#updateVersionDialog');if(d)d.hidden=true;});

/* ---- 预设参数（10）：清理未使用预设 ---- */
const cleanupPresetsBtn=$('#cleanupUnusedPresetsButton');
if(cleanupPresetsBtn)cleanupPresetsBtn.addEventListener('click',async()=>{
  const r=await api('/api/presets/cleanup',{method:'POST'});
  const d=r.data||{};
  toast(d.detail||'清理失败');
  if(typeof loadPresets==='function')loadPresets();
});
})();
