# YU 完整功能地图（按用户功能整理）

> 数据来源：192.168.0.53 板端渲染页（114808B 实测）+ app.py 7059 行路由 + /api/state 实测。
> 这不是 API 列表，是**用户功能**视角的完整盘点。yu 官方导航 12 页 + 风扇独立页。

## 01 总览（home-page）
| 功能 | 说明 | 依赖能力 |
|---|---|---|
| 运行开关 | 启动/停止 AI 流水线 | control/start\|stop |
| 低帧预览 | 实时画面 JPEG/MJPEG | 视频编码+HTTP 出流 |
| 实时状态 | 采集FPS/推理FPS/延迟/丢帧/检测数 | 流水线指标聚合 |
| 实时锁定 | 当前瞄准目标状态 | TargetSelector 状态 |
| 板载资源 | CPU/内存/温度 | 系统采集 |
| 快速调整 | 截取尺寸/偏移/倍率/置信度/IOU/范围 | SET_CONFIG |
| 设备授权入口 | 卡密激活跳转 | license |

## 02 热键控制（profiles-page）
| 功能 | 说明 |
|---|---|
| 热键档案（aim_profiles） | 多套热键方案，每套含：主/副热键、触发方式(any/all)、瞄准点偏移(offset_x/y 0~1)、备用偏移、FOV 缩放、灵敏度、类别过滤掩码 |
| 屏蔽物理按键 | 瞄准时屏蔽物理鼠标 X/Y |
| 新增热键 | 增加档案 |

## 03 移动控制（control-page）
| 功能块 | 参数 |
|---|---|
| PID 控制器 | X/Y 拉力(kp)、预判(predict)、积分(ki)、刹车(kd)、跟随(rate)、基础死区、转火延迟、移动倍率(sens) |
| 目标锁定 | selector 丢失宽限、搜索半径 |
| 开火延迟释放Y轴 | y_axis_fire_hotkey + release_delay_sec |
| 拉枪曲线 | enabled + 强度/抖动/启用距离 |
| 持续提前量 | enabled + 进入距离/系数/渐入/渐出/近距禁用 |
| 屏蔽物理移动 | block_physical_mouse_x/y_while_aiming |
| 自动标定 | calibration start/cancel/clear（增益自动测定） |

## 04 辅助功能（assist-page）
| 功能块 | 参数 | 说明 |
|---|---|---|
| 压枪 | enabled+12 参数 | 检测到开火自动下压补偿 |
| 连点 | enabled+3 参数 | 按住热键循环左键点击 |
| 自动背闪 | enabled+9 参数 | 受击自动后撤转身 |
| 准星找色 | enabled+17 参数 | 准星区域颜色识别触发 |

## 05 模型库（model-page）
| 功能 | 说明 |
|---|---|
| 本地模型列表 | backend(rknn/hailo)/加密状态/输入尺寸/类别数 |
| 导入模型 | 上传 .rknn（支持云加密包 .enc） |
| 连接 Windows 电脑 | 远程导入（remote/*） |
| 模型切换/删除 | select/delete |
| 编辑类别名称 | class-names 中文 |
| 游戏配置/绑定预设/并发/RKNN帧格式 | 模型级参数 |

## 06 显示与鼠标（hardware-page）
| 功能 | 说明 |
|---|---|
| 显示器模式 | EDID 模式列表/分辨率切换/loopout |
| USB 鼠标硬件 | VID/PID/描述符/协议定时 |

## 07 Hailo-8 加速（hailo-page）
设备状态 + 依赖安装（TTBOX 用 RKNN 无此硬件）

## 08 键鼠盒子（kmbox-page）
kmboxNet / CatNet / MAKCU / Ferrum / kmboxB+ 五种外接盒子协议配置

## 09 网络配置（wifi-page）
Wi-Fi 扫描连接 / AP 热点 / 局域网黑名单

## 10 预设参数（preset-page）
保存当前全部配置为预设 / 加载 / 导入导出 / 重命名 / 与模型绑定

## 11 主题商店（theme-store-page）
视觉主题预览/兑换/安装（运营向）

## 12 系统状态（license-page）
设备授权 / 系统更新 / 切换版本 / 存储容量扩容 / 风扇控制 / 公告 / 重启关机

## 13 风扇控制（fan-page，独立入口）
温度源选择(SoC/CPU/GPU/NPU/Hailo) + 启动温度/全速温度/PWM 占比
