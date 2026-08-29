# AIBOX 板端全面审计报告（192.168.0.53）

审计日期: 2026-08-27 | 方式: SSH 只读 + HTTP/WS 只读探测 | 全部为 FACT/已直接观察

## 1. 系统拓扑（修正旧认知）

```
192.168.0.53 (bredos, Orange Pi RK3588, AIBOX OS 1.2.2 / Bookworm 6.1.99)
|
+- aibox-bl  (pid 101993, root, SCREEN 手动拉起, /usr/lib/aibox/aibox-bl, stripped, 3.2MB)
|     监听 0.0.0.0:8080  <- WebSocket 服务（YOLO AI 后端 API，GET 一律 501）
|
+- CloudFileManagerBackend  (pid 78942, /opt/autobl/, NOT STRIPPED, 2.0MB)
|     监听 0.0.0.0:5200   <- "AIBox控制台"（系统管理面板）
|     监听 127.0.0.1:45543/42853（内部端口）
|     静态资源: /opt/autobl/webui/ + /opt/autobl/config/ + logs/ + scripts/
|
+- nginx (web-aibox.service, /etc/web-aibox/nginx.conf, user web-aibox)
|     8001 HTTP  <- "blui" Flutter Web 应用（root /opt/web-aibox/web）
|     8443 HTTPS (tls cert /etc/web-aibox/tls/)
|
+- aibox.service (systemd)  ★ FAILED: SEGV 崩溃 (6.2s, signal=SEGV), disabled
|     ExecStart=/usr/bin/aibox -> export LD_LIBRARY_PATH=/usr/lib/aibox -> exec aibox-bl
|
+- aiboxkm.service  ★ inactive (dead), disabled  (aiboxkm 1.0.94 键鼠 USB gadget 代理)
|
+- 4101 = brltty（盲文终端服务）★ 不是 AIBOX 服务！旧报告认知错误
```

关键包版本（dpkg 实测）：
- aibox-rk3588 **1.0.5-8**（旧离线包是 1.0.2-13，板子已更新）
- aiboxkm **1.0.94**

## 2. 文件与配置 Ground Truth

| 路径 | 内容 |
|---|---|
| /usr/bin/aibox | bash wrapper: LD_LIBRARY_PATH=/usr/lib/aibox, AIBOX_CONFIG_PATH=/etc/aibox, DISPLAY=:0, cd /var/lib/aibox, exec aibox-bl |
| /etc/aibox/config.json | `{}`（空） |
| /etc/aibox/models.conf | 仅注释 |
| /etc/aibox/qiniu.conf | 七牛 CDN 配置（416 字节） |
| /usr/lib/aibox/model/ | csgo2/, sjz/, 老王relu-sjz_384-s-7.rknn (12.7MB) |
| /usr/lib/aibox/user-data/001100.json | ★★★ 用户数据库 98 条记录（blui 读写） |
| /var/lib/aibox/imgs/ | 推理截图目录 |
| /opt/autobl/CloudFileManagerBackend | 5200 后端二进制（not stripped） |
| /opt/web-aibox/web/ | blui Flutter Web（main.dart.js 4.5MB） |

## 3. 001100.json 数据模型（Web 面板全部字段，FACT）

8 个游戏: apex, cf, cfhd, csgo2, pubg, sjz, ssjj2, wwqy
每游戏: 4 个功能预设（配置1~4）+ 每类配置 1 条

### 记录类型与字段
- admin_user: username, password(sha256), token(blweb_token_001100), is_pro
- home_configs: card_key, game_name
- function_configs (x4/游戏): ai_mode(PID/FOV/FOVPID), dynamic_body_part, enabled, fire_mode, flash_shield, hotkey(左键/右键/中键), lock_position(头部/胸部/颈部), preset_name, recoil_control, relative_mode(准星), selected_faction(匪方/警方), trigger_mode(长按), trigger_switch, trigger_weapon(步枪/狙击/冲锋), weapon_switch
- function_global_configs: active_config_index
- pid_configs: aim_dead_ms=2.0, far_factor=1.0, integral_decay_rate=0.7, kd=0.03, kp=1.0, near_max_adjustment=5.0, pid_random_factor=0.0, predict=0.0, rate=0.3, smooth=0.0, y_axis_factor=1.0
- aim_configs: aim_range=384, track_range=0.0, head_height=0/range_x=3/range_y=5, neck_height=8/range_x=5/range_y=8, chest_height=15/range_x=15/range_y=20
- fire_configs: minion_interval=200/sleep=100/trigger_delay=0, rifle_interval=200/sleep=100, sniper_interval=500/sleep=300, weapon_switch_sleep=50
- fov_configs: fov=0.54, fov_time=100
- data_collections: collection_name, is_enabled, map_hotkey, target_hotkey, team_side
- crosshair_configs: 3 组 x 5 点 RGB + 每组 HSV 容差(h_tol/s_tol/v_tol/rgb_tol), crosshair_min_pixels, crosshair_search_radius, decay_end_height, move_factor_x/y, outer_ring_filter1~3, threshold, control_enabled_compat, color_preset_group1~3
- user_special_configs: items_json 功能卡片（见下）

### 功能卡片 (user_special_configs.items_json)
- 通用功能: displayMode=passthrough, versionMode=pc, emergencyStop(急停), enableDynamicAimRange(动态瞄准范围), enableDynamicCrosshairSearch(动态准星搜索), fireReset, testBallistic
- 三角洲功能: autoDodge/autoFlash/autoPeek/autoPick/autoSlideJump/switchScope + 各自热键(左键/下滚轮)
- 无畏契约功能: attackTarget, autoBackFlash
- 绘制相关: drawInferCrosshair(画推理准星), drawInferTarget(画推理目标框)

## 4. blui (8001) 连接模型

- Flutter Web, 路由: /, /login, /register, /function, /special_config
- 登录默认: server=192.168.5.65:8080, admin/admin（可改，存 localStorage: serverIp/serverPort/username/password）
- 主连接: **ws://host:8080/ws**（备用 ws://host:9278/ws）
- 用户数据 API: /api/v1/user-data/storage/config-list / config-get / config-put / config-delete
- 认证: username + token (blweb_token_001100)

## 5. 5200 控制台功能模块（JS 清单 FACT）
版本更新(版本信息/上传更新/直链/云端版本)、文件上传、系统工具(高刷目标设备 /dev/video0、高刷文件补丁、环出、安装包、删除包、恢复默认、重启/关机/清缓存)、服务控制(键鼠服务/核心服务/WebAIBox 启停+自启)、RK3588 性能档位(NPU/CPU/GPU/DDR 定频)、数据管理(采集数据/用户数据/模型数据)、排障日志/操作日志、云端模型(按游戏 ID 下载到 /home/bred/model/游戏名/)

## 6. 与 TTBOX 差异要点（下一步对照基础）
- AIBOX 核心 API 是 WebSocket（8080），TTBOX 是 REST/WS 混合 -> 需要核对
- AIBOX 数据模型以 游戏 x 预设 维度组织（每游戏 4 预设 x 8 游戏），TTBOX 是单 profile -> 需扩展
- AIBOX 有 fire/trigger/recoil/flash_shield 等高级功能卡，TTBOX 无 -> 在安全边界内做数据模型对齐
- AIBOX PID 参数默认值已取得（kp=1.0 kd=0.03 rate=0.3 aim_dead_ms=2 ...）-> 算法对照基准

## 7. 边界声明
本报告为只读审计。鼠标移动/坐标注入/HID 输出链不在复刻范围。
