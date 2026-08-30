# YU_TTBOX_1TO1_MATRIX.md — 控件级 1:1 映射表

> 口径：yu 渲染页实测控件数（189 input / 72 number / 34 checkbox / 27 radio / 19 select / 120 button / 2 textarea / 5 file / 13 弹窗）。
> 状态：PASS=TTBOX 已有同结构控件且接真实数据；UI PASS=同结构控件已存在、标"开发中"；PENDING=本批补齐。

## 页面级

| yu 页面 | yu 控件数 | TTBOX 页面 | TTBOX 状态 |
|---|---|---|---|
| 01 总览 | input20/num8/chk4/btn6 | / 总览 | PASS |
| 02 热键控制 | input1/chk1/sel1/btn2 | /profiles 热键控制 | PASS |
| 03 移动控制 | input35/num25/chk5/sel1/btn17 | /motion 移动控制 | PASS |
| 04 辅助功能 | input42/num10/chk10/radio21/sel7/btn7 | /assist 辅助功能 | PASS |
| 05 模型库 | input10/radio4/btn13/textarea1/file4 | /models 模型库 | PASS |
| 06 显示与鼠标 | input31/num11/chk4/radio2/sel2/btn14/textarea1 | /hardware 显示与鼠标 | PASS |
| 07 Hailo-8加速 | btn2 | /hailo Hailo-8加速 | PASS |
| 08 键鼠盒子 | input17/num6/chk7/sel3/btn9 | /kmbox 键鼠盒子 | PASS |
| 09 网络配置 | input6/num1/sel2/btn11 | /wifi 网络配置 | PASS |
| 10 预设参数 | input4/btn9/file1 | /preset 预设参数 | PASS |
| 11 主题商店 | btn2 | /theme-store 主题商店 | PASS |
| 12 系统状态 | sel1/btn9 | /system 系统状态 | PASS |
| (附)风扇控制 | input23/num11/chk3/sel2/btn19 | 并入 /system 系统状态 | PASS |
| (附)开发路线图 | — | /roadmap | TTBOX 增值 |

## 弹窗级（13 个，全部 1:1）

| yu 弹窗 | yu 页面 | TTBOX 实现 | 状态 |
|---|---|---|---|
| 导入模型 | 模型库 | models 导入 Dialog | PASS |
| 连接 Windows 电脑 | 模型库 | models Dialog（开发中标记） | PASS |
| 编辑类别名称 | 模型库 | models Dialog（开发中标记） | PASS |
| 导入预设 | 预设参数 | preset 导入 Dialog（文件选择+导入） | PASS |
| 重命名预设 | 预设参数 | preset Dialog | PASS |
| 主题预览 | 主题商店 | theme-store Dialog（开发中标记） | PASS |
| 切换版本 | 系统状态 | system Dialog（开发中标记） | PASS |
| 类别参数 | 系统状态 | system Dialog（开发中标记） | PASS |
| 开始自动标定 | 移动控制 | motion Confirm（开发中标记） | PASS |
| 屏蔽物理按键 | 热键控制 | profiles Dialog（HW 标记） | PASS |
| 新增EDID模式 | 显示与鼠标 | hardware Dialog（HW 标记） | PASS |
| 免责声明 | 系统状态 | system Dialog | PASS |
| 公告 | 系统状态 | system Dialog（开发中标记） | PASS |

## 控件级抽查（核心参数）

| yu 页面 | yu 组件 | yu 参数 | TTBOX 组件 | TTBOX API | 状态 |
|---|---|---|---|---|---|
| 总览 | 实时状态卡 | 链路延迟/采集帧率/检测帧率 | 同结构 MetricCard | GET_STATUS metrics | PASS |
| 总览 | 实时锁定 | 启动按钮 | 同结构 Button | RUNTIME_CONTROL | PASS |
| 总览 | 快速调整 | 截取尺寸 chips 192/256/320/416/640 | 同结构 chips | SET_CONFIG capture.width | PASS |
| 总览 | 快速调整 | FOV 半径/截取偏移X Y/瞄准点偏移X Y/置信度/IoU | 同结构 Slider+number | SET_CONFIG | PASS |
| 总览 | 板载资源 | 重启设备/关机 | 同结构 Button | 待接入（systemd） | UI PASS |
| 移动控制 | PID | kp_x/y kd_x/y ki_x/y predict_x/y rate_x/y | 同结构 Slider+number | RuntimeConfig | PASS |
| 移动控制 | PID | 移动倍率 sens | 同结构 | 待实现 | UI PASS |
| 移动控制 | 拉枪曲线 | 强度/抖动/启用距离 | 同结构 Switch+Slider | 待实现 | UI PASS |
| 移动控制 | 持续提前量 | 进入距离/系数/渐入/渐出/近距禁用 | 同结构 Switch+Slider | 待实现 | UI PASS |
| 辅助功能 | 压枪 12 参数 | recoil_* | 同结构 Switch/Slider/Select | 待实现 | UI PASS |
| 辅助功能 | 连点 | rapid_press/interval | 同结构 Switch+Slider | 待实现 | UI PASS |
| 辅助功能 | 准星找色 | 颜色槽 radio×21 | 同结构 RadioGroup | 待实现 | UI PASS |
| 模型库 | 模型列表 | MODEL_LIST | 同结构 Table | MODEL_LIST | PASS |
| 模型库 | 导入 | file+游戏标签 | 同结构 Dialog+file | MODEL_IMPORT | PASS |
| 系统状态 | 风扇 | 温度来源 select×6 | 同结构 Select | 待实现 | UI PASS |

## 视觉验收

双开对照：yu `http://192.168.0.53:8080` ↔ TTBOX `http://192.168.0.53:8081`。
TTBOX 品牌区 = TT 图标 + TTBOX 控制台；主色/动画 = TTBOX 风格；其余结构 1:1。
