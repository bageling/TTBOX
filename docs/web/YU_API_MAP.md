# YU_API_MAP.md — yu 真机 API 实测清单（192.168.0.53:8080）

> 来源：板端 `/opt/aiassistance/web/app.py`（7059 行）全部路由 + 实测请求返回。
> 调研时间：2026-08-30。只读，未修改 yu。
> 说明：**用户给的 0.80 在网络上不存在**（ping 不通、ARP 无记录、全段扫描无此机），
> 经确认以 0.53 为基准（yu 已激活，license.activated=true）。

## API 总览

| 端点 | 用途 | 说明 | 页面 | 控件 | 改配置 | 实时生效 |
|---|---|---|---|---|---|---|
| GET / | 页面 | 桌面主页 index.html | 全部 | — | 否 | — |
| GET /desktop /mobile | 页面 | 桌面/手机入口 | 全部 | — | 否 | — |
| GET /api/state | **聚合状态** | 全部配置+运行状态+模型+预设（实测 9193B） | 所有页面 | 全部控件 | 否 | — |
| PUT /api/config | **配置修改** | 接收扁平键值，转 config.ai 等结构并落盘 | 所有页面 | 全部控件 | **是** | 是 |
| POST /api/control/start\|stop | 启停 | 启动/停止 AI 流水线 | 总览 | 运行开关 | 否 | 是 |
| GET /api/models | 模型列表 | 模型详情（display_name/backend/input/encrypted） | 模型库 | 列表 | 否 | — |
| POST /api/models/select | 模型切换 | 激活指定模型（实测当前 sjzv11） | 模型库 | 切换 | 是 | 是 |
| POST /api/models/import | 模型导入 | 上传 .rknn（支持 .enc 加密包） | 模型库 | 导入 | 是 | 否 |
| POST /api/models/delete | 模型删除 | 删除模型 | 模型库 | 删除 | 是 | 否 |
| POST /api/models/bind-preset | 模型绑预设 | 模型↔预设绑定 | 模型库 | 绑定 | 是 | 否 |
| POST /api/models/game-profile | 游戏档 | 设置游戏配置文件 | 模型库 | 游戏档 | 是 | 否 |
| POST /api/models/class-names | 类别名 | 模型类别中文名 | 模型库 | 类别 | 是 | 否 |
| GET /api/presets | 预设列表 | 预设名数组 | 预设 | 列表 | 否 | — |
| POST /api/presets | 保存预设 | 当前配置存为预设 | 预设 | 保存 | 是 | 否 |
| POST /api/presets/load | 加载预设 | 应用预设 | 预设 | 加载 | 是 | 是 |
| POST /api/presets/import | 导入预设 | 导入 json | 预设 | 导入 | 是 | 否 |
| GET /api/preview.jpg \| .mjpg | **实时画面** | JPEG/MJPEG 预览帧（实测 7.8KB/帧） | 总览 | 预览图 | 否 | — |
| GET /api/hardware/display | 显示器 | EDID/模式（实测 1440p144 PNG） | 硬件 | 配置 | 否 | — |
| PUT /api/hardware/display | 显示器配置 | 改分辨率/像素格式 | 硬件 | 分辨率 | 是 | 是 |
| GET /api/hardware/mouse | 鼠标描述 | HID 描述符（实测 Logitech C53F） | 硬件 | 鼠标 | 否 | — |
| PUT /api/hardware/mouse \| mode \| timing | 鼠标配置 | 改 HID 描述符 | 硬件 | 鼠标 | 是 | 是 |
| GET /api/system | 系统状态 | CPU/内存/存储/温度（实测全量） | 系统 | 资源卡 | 否 | — |
| GET /api/settings/auto-start | 自启动 | 开机自启状态 | 系统 | 自启 | 否 | — |
| PUT /api/settings/auto-start | 自启动配置 | 开机自启 | 系统 | 自启 | 是 | 否 |
| GET /api/events | 事件流 | SSE 实时推送 | 全站 | 刷新 | 否 | — |
| POST /api/control/calibration/start | 自动校准 | 校准鼠标增益 | 移动 | 校准 | — | — |
| GET /api/control/calibration | 校准状态 | 校准结果 | 移动 | 校准卡 | 否 | — |
| POST /api/diagnostics/aim-trace | 瞄准轨迹 | 导出瞄准轨迹诊断 | 系统 | 诊断 | — | — |
| GET /api/announcement | 公告 | 运营公告 | 首页 | 公告条 | 否 | — |
| GET /api/hailo/status | Hailo 状态 | Hailo-8 加速卡状态 | Hailo | 状态卡 | 否 | — |
| POST /api/hailo/install | Hailo 安装 | 安装 Hailo 依赖 | Hailo | 安装 | — | 否 |
| GET /api/network/wifi | WiFi 状态 | 当前 WiFi | 网络 | WiFi 卡 | 否 | — |
| POST /api/network/wifi/scan | WiFi 扫描 | 附近 WiFi | 网络 | 扫描 | 否 | — |
| POST /api/network/wifi/connect | WiFi 连接 | 连接 WiFi | 网络 | 连接 | — | 否 |
| GET /api/makcu/devices\|ferrum\|kmboxb | 盒子设备 | 外接鼠标盒子设备 | 盒子 | 列表 | 否 | — |
| POST /api/mouse-output/test-circle | 圆圈测试 | 鼠标画圈测试 | 盒子 | 测试 | — | — |
| GET /api/themes \| redeem \| install | 主题 | 主题商店/兑换/安装 | 主题 | 主题卡 | — | 否 |
| GET /api/license | 授权状态 | 激活状态/设备指纹（实测 activated=true） | 授权 | 状态卡 | 否 | — |
| POST /api/license/activate | 激活 | 卡密激活 | 授权 | 激活 | — | 否 |
| POST /api/update/check\|versions\|install | 系统更新 | 检查/安装更新 | 授权 | 更新 | — | 否 |
| GET /api/motion-profiles | 移动档案 | 个人移动曲线档案 | 移动训练 | 档案 | 否 | — |
| POST /api/motion-profiles\|train\|activate | 移动训练 | 训练个人移动曲线 | 移动训练 | 训练 | — | — |

## 关键结论

1. **核心契约只有 3 条**：`GET /api/state`（读一切）、`PUT /api/config`（写一切，扁平键）、`POST /api/control/start|stop`（启停）。
2. 前端把控件 id 直接当配置键（`controller_kp_x`、`video_detection_confidence`…）拼成扁平对象 PUT。
3. 实时画面 = `/api/preview.jpg`（静态帧轮询）或 `.mjpg`（流），SSE `/api/events` 做状态推送。
4. 配置修改**立即生效**（PUT 后 daemon 热更新），无需重启；模型切换需重启推理（提示语标注）。
5. yu 的后端是 Flask app.py + C daemon（aiassistance_daemon）双进程，Web 通过本地协议与 daemon 通信。
