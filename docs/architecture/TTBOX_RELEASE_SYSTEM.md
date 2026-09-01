# TTBOX 发布系统 (Release System)

## 1. 概述

TTBOX 发布系统负责从源码构建到最终部署的全流程。发布系统运行在**发布机器**上，与**生产服务器**物理隔离。

## 2. Release Model

```
TTBOX (Product)
  └── 1.0.0 (Version)
       ├── build-20260901-001 (Build)
       │    ├── Release
       │    │    ├── Component: Core
       │    │    │    └── Package: TTBOX-core-1.0.0-rk3588.tar.gz
       │    │    ├── Component: Web
       │    │    │    └── Package: TTBOX-web-1.0.0-rk3588.tar.gz
       │    │    ├── Component: Gateway
       │    │    │    └── Package: TTBOX-gateway-1.0.0-rk3588.tar.gz
       │    │    ├── Component: Model
       │    │    │    └── Package: TTBOX-model-person-1.0.0-rk3588.tar.gz
       │    │    └── Component: Update Engine
       │    │         └── Package: TTBOX-update-engine-1.0.0-rk3588.tar.gz
       │    └── Channel: stable / beta / developer
       └── Manifest: manifest.json
```

## 3. 发布流水线

```
┌──────────┐   ┌───────┐   ┌────────┐   ┌─────────┐   ┌────────┐
│  Developer │ → │ Build │ → │ Test   │ → │ Package │ → │ Sign   │
└──────────┘   └───────┘   └────────┘   └─────────┘   └────────┘
                                                    ↓
┌──────────┐   ┌────────┐   ┌──────────┐   ┌───────────────┐
│  Deploy   │ ← │ Upload │ ← │ Manifest │ ← │ Release Notes │
└──────────┘   └────────┘   └──────────┘   └───────────────┘
```

### 步骤详情

| 步骤 | 工具 | 输出 | 说明 |
|------|------|------|------|
| 1. Develop | Git | 源代码 | 在本地开发分支 |
| 2. Build | Makefile/CMake | 二进制文件 | 交叉编译 RK3588 目标 |
| 3. Test | pytest/ctest | 测试报告 | 单元测试 + 集成测试 |
| 4. Package | compress | .tar.gz | 按组件打包 |
| 5. Sign | openssl | .sig | Ed25519 签名 |
| 6. Release Notes | 编辑 | .md | 发布说明 |
| 7. Manifest | 生成 | manifest.json | 包含所有元数据 |
| 8. Upload | scp/rsync | — | 上传到服务器 |
| 9. Deploy | 自动化 | — | 服务器发布到通道 |

## 4. 发布角色

| 角色 | 权限 | 职责 |
|------|------|------|
| Developer | 本地开发 | 编码、本地测试 |
| Release Manager | 发布机器 | 构建、签名、上传 |
| Update Server | 生产服务器 | 只读存储、分发 |

## 5. 密钥管理

```
发布机器（安全环境）
├── release-private.pem    # Ed25519 私钥（绝对不离开此机器）
└── release-public.pem     # 公钥（分发到设备）

设备端
└── /var/lib/ttbox/update/trusted_keys/
    └── key-20260901.pub   # 预置公钥
```

## 6. 发布工具

建议实现 `scripts/release.sh`：

```bash
#!/bin/bash
# TTBOX 发布工具
# Usage: ./release.sh <version> <channel>

VERSION=$1
CHANNEL=$2

# 1. 构建
make build TARGET=rk3588

# 2. 打包
./scripts/package.sh $VERSION core
./scripts/package.sh $VERSION web
./scripts/package.sh $VERSION gateway

# 3. 签名
for pkg in dist/*.tar.gz; do
  openssl pkeyutl -sign -inkey release-private.pem \
    -rawin -in <(sha256sum "$pkg" | cut -d' ' -f1) \
    -out "$pkg.sig"
done

# 4. 生成 Manifest
./scripts/gen-manifest.sh $VERSION $CHANNEL

# 5. 上传
rsync -avz dist/ $SERVER:/srv/ttbox/releases/$CHANNEL/$VERSION/
```

## 7. 服务器文件结构

```
/srv/ttbox/
├── releases/
│   ├── stable/
│   │   ├── 1.0.0/
│   │   │   ├── manifest.json
│   │   │   ├── TTBOX-core-1.0.0-rk3588.tar.gz
│   │   │   ├── TTBOX-core-1.0.0-rk3588.tar.gz.sig
│   │   │   ├── TTBOX-web-1.0.0-rk3588.tar.gz
│   │   │   ├── TTBOX-web-1.0.0-rk3588.tar.gz.sig
│   │   │   └── ...
│   │   └── 1.1.0/
│   ├── beta/
│   │   └── ...
│   └── developer/
│       └── ...
├── manifests/
│   └── live/
│       ├── stable.json   → 指向 stable 最新版本 manifest
│       ├── beta.json     → 指向 beta 最新版本 manifest
│       └── developer.json → 指向 developer 最新版本 manifest
└── metadata/
    └── channels.json     # 通道配置
```

## 8. 通道配置

```json
{
  "stable": {
    "current_version": "1.0.0",
    "update_policy": "manual",
    "auto_update": false,
    "rollout_percentage": 100
  },
  "beta": {
    "current_version": "1.1.0-beta.1",
    "update_policy": "optional",
    "auto_update": true,
    "rollout_percentage": 100
  },
  "developer": {
    "current_version": "1.1.0-dev.20260901",
    "update_policy": "automatic",
    "auto_update": true,
    "rollout_percentage": 100
  }
}
```

## 9. 安全隔离

| 操作 | 签名 | 密钥 | 位置 |
|------|------|------|------|
| 构建 | 无 | 无 | 发布机器 |
| 签名 | Ed25519 | 私钥 | 发布机器 |
| 上传 | SSH Key | 服务器密钥 | 发布机器 → 服务器 |
| 存储 | — | — | 服务器 |
| 分发 | — | 公钥 | 服务器 → 设备 |
| 验证 | Ed25519 | 公钥 | 设备 |

## 10. 发布检查清单

- [ ] 所有测试通过
- [ ] 版本号已更新
- [ ] 变更日志已更新
- [ ] 包已签名
- [ ] Manifest 已生成
- [ ] Manifest 已签名
- [ ] 上传到服务器
- [ ] 通道配置已更新
- [ ] 验证测试设备更新成功
- [ ] 验证回滚功能正常