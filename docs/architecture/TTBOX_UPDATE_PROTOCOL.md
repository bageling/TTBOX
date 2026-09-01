# TTBOX 更新协议 (Update Protocol)

## 1. 概述

TTBOX 更新协议定义设备与更新服务器之间的通信规范。协议支持 OTA（网络更新）和 OTG（USB 更新）两种方式，共享同一套数据格式和安全验证机制。

## 2. 通信协议

| 特性 | 说明 |
|------|------|
| 传输 | HTTPS（公网）/ HTTP（内网） |
| 请求格式 | HTTP GET/POST |
| 响应格式 | JSON |
| 验证 | Manifest 签名验证 |

## 3. API 端点

### GET /api/update/check

检查是否有可用更新。

**请求参数：**

```json
{
  "product": "TTBOX",
  "device_id": "TTBOX-XXXX-XXXX",
  "hardware": "rk3588",
  "version": "0.1.0",
  "channel": "stable",
  "components": ["core", "web", "gateway"]
}
```

**响应：**

```json
{
  "ok": true,
  "data": {
    "update_available": true,
    "latest_version": "0.1.1",
    "channel": "stable",
    "release_date": "2026-09-01T00:00:00Z",
    "release_notes_url": "/api/update/release-notes/0.1.1",
    "manifest_url": "/api/update/manifest/TTBOX/0.1.1/stable/manifest.json",
    "critical": false
  }
}
```

### GET /api/update/manifest/{product}/{version}/{channel}/manifest.json

获取更新清单文件。

**响应：**

```json
{
  "product": "TTBOX",
  "version": "0.1.1",
  "build": "20260901-001",
  "channel": "stable",
  "min_version": "0.1.0",
  "target_hardware": ["rk3588"],
  "release_date": "2026-09-01T00:00:00Z",
  "release_notes": "### 0.1.1\n\n- 修复：预览延迟问题\n- 优化：NPU 调度性能",
  "components": [
    {
      "name": "core",
      "version": "0.1.1",
      "type": "firmware"
    },
    {
      "name": "web",
      "version": "0.1.1",
      "type": "firmware"
    },
    {
      "name": "gateway",
      "version": "0.1.1",
      "type": "firmware"
    }
  ],
  "packages": [
    {
      "package_id": "TTBOX-core-0.1.1-rk3588.tar.gz",
      "component": "core",
      "version": "0.1.1",
      "target": "system",
      "hardware": ["rk3588"],
      "size": 5242880,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "url": "/api/update/package/TTBOX-core-0.1.1-rk3588.tar.gz",
      "required": true
    },
    {
      "package_id": "TTBOX-web-0.1.1-rk3588.tar.gz",
      "component": "web",
      "version": "0.1.1",
      "target": "system",
      "hardware": ["rk3588"],
      "size": 2097152,
      "sha256": "a7ffc6f8bf1ed76651c14756a061d662f580ff4de43b49fa82d80a4b80f8434a",
      "url": "/api/update/package/TTBOX-web-0.1.1-rk3588.tar.gz",
      "required": true
    }
  ],
  "signature": "base64_ed25519_signature_here",
  "signing_key_id": "key-20260901"
}
```

### GET /api/update/package/{package_id}

下载更新包。

**响应：** 二进制文件流（Content-Type: application/octet-stream）

### GET /api/update/release-notes/{version}

获取发布说明。

**响应：** Markdown 文本。

## 4. 设备端更新流程

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  TTBOX 设备  │ ──→ │ Update Server │ ──→ │  Release Storage │
└─────────────┘     └──────────────┘     └──────────────┘
       │
       │ 1. GET /api/update/check
       │ 2. 返回 update_available
       │ 3. GET /api/update/manifest/.../manifest.json
       │ 4. 验证 manifest 签名
       │ 5. 检查兼容性
       │ 6. 下载包文件
       │ 7. 验证 SHA256
       │ 8. 验证签名
       │ 9. 解压到 staging 目录
       │ 10. 备份当前版本
       │ 11. 应用更新
       │ 12. 健康检查
       │ 13. 提交更新
```

## 5. 版本号规范

遵循语义化版本 2.0.0：

```
MAJOR.MINOR.PATCH
```

| 段 | 说明 | 示例 |
|----|------|------|
| MAJOR | 不兼容的 API 变更 | 1.0.0 → 2.0.0 |
| MINOR | 向下兼容的功能新增 | 1.0.0 → 1.1.0 |
| PATCH | 向下兼容的问题修复 | 1.0.0 → 1.0.1 |

构建版本（内部使用）：

```
{version}-{channel}-{build_number}
# 示例：1.0.0-stable-20260901
```

## 6. 更新通道

| 通道 | 用途 | 稳定性 | 更新频率 |
|------|------|--------|----------|
| stable | 生产环境 | 最高 | 低频 |
| beta | 预览测试 | 中等 | 中频 |
| developer | 开发调试 | 最低 | 高频 |

## 7. 兼容性检查

设备在下载前必须检查：

1. 硬件兼容性：硬件型号是否在 `target_hardware` 列表中
2. 版本兼容性：当前版本 >= `min_version`
3. 组件兼容性：所有必需组件是否可更新
4. 空间检查：可用磁盘空间 >= 包大小 × 2（下载 + 备份）

## 8. 更新状态

| 状态 | 说明 |
|------|------|
| up-to-date | 当前版本已是最新 |
| update-available | 有新版本可更新 |
| downloading | 正在下载更新包 |
| verifying | 验证下载包 |
| staging | 准备更新 |
| ready | 已就绪，等待重启 |
| applying | 正在应用更新 |
| health-check | 健康检查中 |
| committed | 更新完成 |
| failed | 更新失败 |
| rolling-back | 正在回滚 |
| rolled-back | 已回滚到旧版本 |