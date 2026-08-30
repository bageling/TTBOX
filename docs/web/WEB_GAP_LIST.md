# WEB_GAP_LIST.md — TTBOX Web 缺口清单

## A 级：必须补（用户核心体验）

| # | 缺口 | 影响 | 方案 | 工作量 |
|---|---|---|---|---|
| G1 | GET_STATUS metrics 全 0（frames/fps/infer/aim 未接线） | 总览性能卡显示"暂无数据" | Core：status_provider 接 capture/worker 统计 | 0.5h |
| G2 | 实时画面（preview.jpg/mjpg） | yu 有、TTBOX 无 → 双开对比时核心差异 | Core 加 JPG 编码（V4L2 帧→软编码 JPEG→HTTP 轮询）或板端旁路抓帧 | 1-2d |
| G3 | 板端系统资源（温度/CPU/内存） | 系统页占位"暂未提供" | 板端独立 Python 小服务（读 /sys/class/thermal 等），不碰 Core | 2h |
| G4 | Web 网关部署到板端（systemd） | 现在 Web 只能本机 dev 跑 | dev_gateway → 生产化 + systemd 服务，端口 8081 | 2h |

## B 级：值得做（体验对齐）

| # | 缺口 | 方案 | 工作量 |
|---|---|---|---|
| G5 | 预设保存/加载 | Web 端 localStorage 存 JSON（无需 Core IPC） | 2h |
| G6 | 模型切换后提示重启 | Web 提示文案（Core 无热加载，如实标注） | 0.5h |
| G7 | 错误提示统一 | SET_CONFIG 失败原因透传（已有，补全页面） | 1h |

## C 级：放弃（yu 生态专属，TTBOX 无 Core 能力，不做）

压枪 / 连点 / 自动背闪 / 准星找色 / WiFi / 风扇 / Hailo-8 / 键鼠盒子(MAKCU/Ferrum/kmboxB) / 主题商店 / 授权激活 / 系统更新 / 个人移动曲线训练 / 自动校准

## 端口规划（并存）

- yu：`0.0.0.0:8080`（不动）
- TTBOX Web：`0.0.0.0:8081`（独立，不碰 8080）
- TTBOX Core IPC：`/tmp/ttbox_core.sock`（Unix）或独立 TCP（Windows 用 tcp: 端口）
- 已实测并存：TTBOX Core 与 yu daemon/web 同时运行无冲突（昨晚验证）
