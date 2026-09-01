# TTBOX 软件包格式

## 1. Package Model

```
TTBOX-{component}-{version}-{hardware}.tar.gz
```

示例：

```
TTBOX-core-1.0.0-rk3588.tar.gz
TTBOX-web-1.1.0-rk3588.tar.gz
TTBOX-gateway-1.0.0-rk3588.tar.gz
TTBOX-model-person-1.0.0-rk3588.tar.gz
TTBOX-full-1.0.0-rk3588.tar.gz
```

## 2. 包内目录结构

### 固件包（core/web/gateway/update-engine）

```
TTBOX-core-1.0.0-rk3588/
├── package.json          # 包元数据
├── preinstall.sh         # 安装前脚本（可选）
├── postinstall.sh        # 安装后脚本（可选）
├── files/
│   ├── usr/
│   │   └── local/
│   │       └── bin/ttbox-core
│   ├── etc/
│   │   └── ttbox/
│   └── lib/
│       └── systemd/
│           └── system/ttbox-core.service
└── config/
    └── migration.json    # 配置迁移（可选）
```

### 模型包

```
TTBOX-model-{name}-{version}-{hardware}/
├── package.json
├── model.rknn
├── model_config.json
├── labels.txt
└── preinstall.sh
```

### 完整包

```
TTBOX-full-1.0.0-rk3588/
├── package.json
├── preinstall.sh
├── postinstall.sh
├── core/
│   └── ... (同固件包结构)
├── web/
│   └── ...
├── gateway/
│   └── ...
└── config/
    └── migration.json
```

## 3. package.json（包元数据）

```json
{
  "package_id": "TTBOX-core-1.0.0-rk3588",
  "product": "TTBOX",
  "component": "core",
  "version": "1.0.0",
  "build": "20260901-001",
  "target": "system",
  "hardware": ["rk3588"],
  "arch": "aarch64",
  "format": "tar.gz",
  "size": 5242880,
  "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "signature": "base64_ed25519_signature",
  "signing_key_id": "key-20260901",
  "min_version": "0.1.0",
  "type": "firmware",
  "critical": false,
  "release_notes": "### 1.0.0\n- 初始生产版本",
  "dependencies": [],
  "conflicts": [],
  "requires_reboot": true,
  "config_migration": true
}
```

## 4. 字段说明

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| package_id | string | ✅ | 唯一包标识 |
| product | string | ✅ | 产品名称 |
| component | string | ✅ | 组件名称 |
| version | string | ✅ | 语义化版本 |
| build | string | ✅ | 构建号 |
| target | string | ✅ | 安装目标（system、model） |
| hardware | string[] | ✅ | 兼容硬件列表 |
| arch | string | ✅ | 架构 |
| format | string | ✅ | 打包格式 |
| size | int | ✅ | 包大小（字节） |
| sha256 | string | ✅ | 文件校验和 |
| signature | string | ✅ | 数字签名 |
| signing_key_id | string | ✅ | 签名密钥标识 |
| min_version | string | ✅ | 最低兼容版本 |
| type | string | ✅ | 包类型 |
| critical | bool | ✅ | 是否关键更新 |
| release_notes | string | - | 发布说明 |
| dependencies | string[] | - | 依赖包 |
| conflicts | string[] | - | 冲突包 |
| requires_reboot | bool | ✅ | 是否需要重启 |
| config_migration | bool | ✅ | 是否需要配置迁移 |

## 5. 包类型

| 类型 | 说明 | 包含内容 |
|------|------|----------|
| firmware | 固件 | 二进制、库、systemd 服务 |
| model | 模型 | .rknn 文件、标签、配置 |
| full | 完整包 | 所有组件 + 配置迁移 |

## 6. 安装目标

| 目标 | 安装路径 |
|------|----------|
| system | /usr/local/bin/、/etc/ttbox/、/lib/systemd/system/ |
| model | /var/lib/ttbox/models/ |
| web | /usr/local/share/ttbox/web/ |

## 7. 配置迁移

```json
{
  "migration_version": "1.0.0",
  "changes": [
    {
      "from": "1.0.0",
      "to": "1.1.0",
      "config_keys": {
        "renamed": {
          "old_key": "new_key"
        },
        "removed": ["deprecated_key"],
        "added": {
          "new_key": "default_value"
        },
        "changed_type": {
          "key": "string→int"
        }
      }
    }
  ]
}
```

## 8. 校验流程

```
下载包 → 计算 SHA256 → 与 package.json 比较
  → 匹配 → 验证 Ed25519 签名
    → 匹配 → 解压
    → 不匹配 → 拒绝
  → 不匹配 → 拒绝
```

## 9. 签名生成（发布机器）

```bash
# 生成密钥对
openssl genpkey -algorithm ED25519 -out release-private.pem
openssl pkey -in release-private.pem -pubout -out release-public.pem

# 签名包
sha256sum package.tar.gz | cut -d' ' -f1 > package.sha256
openssl pkeyutl -sign -inkey release-private.pem \
  -rawin -in package.sha256 -out package.sig

# 验证
openssl pkeyutl -verify -pubin -inkey release-public.pem \
  -rawin -in package.sha256 -sigfile package.sig
```

## 10. 设备端公钥存储

```
/var/lib/ttbox/update/
├── trusted_keys/
│   └── key-20260901.pub   # Ed25519 公钥
└── current-manifest.json   # 当前已安装的 manifest