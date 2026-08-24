# TTBOX 当前主线源码

本目录是当前 TTBOX 主线的整理副本。

## 主线核心
- `core/`：唯一主线 C++ 核心源码，包含 capture、RGA、RKNN、model、target、mouse、HID、IPC、runtime、tests。

## 配套源码
- `scripts/`：当前部署、EDID、HID、转换工具脚本。
- `config/`：当前配置模板。
- `hid/`：当前 HID 描述和配置。
- `docs/`：架构、协议、性能和升级文档。

## 非主线
原目录中的 `vendor/legacy/`、`backup-*`、历史调试脚本不纳入本目录。

## 约定
以后修改优先在本目录验证，再同步到部署目录。
