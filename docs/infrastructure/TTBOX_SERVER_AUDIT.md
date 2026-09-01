# TTBOX 基础设施审计报告

## 1. 服务器

| 项目 | 值 |
|------|-----|
| 主机 | 38.127.133.6:10015 |
| OS | Ubuntu 22.04.5 LTS (Jammy) |
| Kernel | 5.15.0-135-generic x86_64 |
| CPU | 2 vCPU (Intel Xeon Icelake) |
| RAM | 1.9GB (可用 1.5GB) |
| 磁盘 | 20GB SSD (已用 3.4GB，可用 16GB) |
| 分区 | /dev/vda1 19.9G ext4，/boot/efi 106M |
| 主机名 | i-6a9397b804797a1278112d11 |
| 防火墙 | ufw: inactive，iptables: ACCEPT all |
| Python | 3.10.12 |
| Go | 已安装（/root/go/pkg） |
| Docker | 未安装 |
| Nginx | 未安装 |
| Caddy | 未安装 |

## 2. 现有服务

| 服务 | 端口 | 类型 | 说明 |
|------|------|------|------|
| SSH | 22 | systemd | OpenSSH |
| License SaaS | 8080 | systemd | Go 二进制授权服务 |
| Cloudflared | 20241(tunnel) | 手动 | 内网穿透，映射到外部域名 |
| systemd-resolve | 53 | systemd | DNS 解析 |

## 3. License SaaS

| 项目 | 值 |
|------|-----|
| 位置 | /opt/license-saas/ |
| 二进制 | license-saas-server（Go 编译，35MB） |
| 数据库 | SQLite: /opt/license-saas/license-saas.db |
| 端口 | 127.0.0.1:8080 |
| 服务 | license-saas.service（systemd） |
| 反向代理 | 无（通过 cloudflared 直接暴露） |
| 前端 | 内嵌 Web（Vue 构建） |
| 后端 | Go（Gin 框架） |
| 认证 | JWT（密钥: prod-test-secret） |
| 管理员 | admin / admin123 |
| 日志 | /opt/license-saas/server.log |
| Cloudflared | cloudflared tunnel --url http://127.0.0.1:8080 |

### License SaaS API 测试

```
GET / → 返回 Vue 前端页面
GET /api/ → {"message":"api not found","ok":false}
```

### 关键发现

- License SaaS 是独立 Go 二进制，无外部依赖
- 使用 SQLite 数据库，无需额外数据库服务
- 通过 Cloudflared 暴露到公网，无域名/DNS/HTTPS
- 未使用反向代理，直接暴露 Go 原生 HTTP

## 4. 网络架构

```
用户
  ↓ 浏览器访问 Cloudflare 域名
Cloudflare
  ↓
cloudflared tunnel（服务器 127.0.0.1:20241）
  ↓
License SaaS（127.0.0.1:8080）
```

## 5. TTBOX Update Server 设计

### 推荐架构

```
TTBOX Git → Build → Package → Sign → Upload
                                              ↓
                                    /srv/ttbox/releases/
                                              ↓
                                    TTBOX Update Server（Python Flask）
                                              ↓
                                    cloudflared tunnel（复用现有）
                                              ↓
                                    设备 OTA 请求
```

### 目录规划

```
/opt/ttbox/
├── update-server/    # Update Server 程序
├── tools/            # 管理工具
└── services/         # 服务文件

/srv/ttbox/
├── releases/
│   ├── stable/       # 稳定版发布
│   ├── beta/         # 测试版
│   └── developer/    # 开发版
├── manifests/        # 发布清单
├── packages/         # 发布包
└── metadata/         # 元数据
```

### 权限规划

| 用户 | 用途 |
|------|------|
| ttbox | Update Server 运行用户 |
| ttbox-release | 发布工具专用用户 |

- Update Server 只读 /srv/ttbox/
- 发布工具通过 SSH 上传

## 6. HTTPS

当前状态：

- 无域名
- 无 HTTPS 证书
- 通过 Cloudflared 隧道提供 HTTPS（Cloudflare 侧自动 TLS）

推荐方案：

- 继续使用 Cloudflared 隧道（免费 TLS）
- 如需自定义域名：配置 Cloudflare DNS 指向隧道
- 后续可添加 Caddy 作为反向代理 + 自动 Let's Encrypt

## 7. 发布流水线

```
TTBOX 本地仓库
  ↓ git push
GitHub
  ↓ CI（可选）
构建
  ↓
打包（web、core、model）
  ↓
SHA256 校验
  ↓
签名（私钥）
  ↓
上传到服务器
  ↓
更新 Manifest
  ↓
设备检测到更新
```

## 8. 设备 OTA

```
TTBOX 设备
  ↓
Update Engine（设备端）
  ↓ HTTP GET
TTBOX Update Server（/api/update/check、/api/update/manifest、/api/update/download）
  ↓
/srv/ttbox/releases/
```

## 9. OTG

```
USB 存储
  ↓ 插入
TTBOX 设备检测 USB
  ↓ 读取 Manifest
Update Engine 验证
  ↓ 安装
```

## 10. License 集成

```
TTBOX 设备
  ↓
License Client（TTBOX 内置）
  ↓ HTTP
License SaaS（38.127.133.6:8080，通过 cloudflared）
```

License Client 与 Core 解耦：

- License 检查失败不影响 Core 启动
- Core 正常运行，仅限制 UI 功能

## 11. 安全

| 项目 | 当前 | 建议 |
|------|------|------|
| HTTPS | Cloudflared（免费 TLS） | 保持 |
| 包签名 | 无 | 添加 ECDSA 签名 |
| SHA256 | 无 | 添加 |
| 服务器防火墙 | 全部放行 | 添加 ufw 仅开放 22 和 10015 |
| 更新服务器认证 | 无 | 添加 API Key |

## 12. 需要修改的 TTBOX 文件

| 文件 | 修改内容 | 优先级 |
|------|---------|--------|
| scripts/ttbox_web.py | 添加 update/check 真实实现 | P1 |
| core/src/ipc/IpcServer.cpp | 添加 UPDATE_CHECK 命令 | P2 |
| docs/product/TTBOX_API_CONTRACT.md | 添加 Update API 定义 | P1 |

## 13. 服务器需要安装/修改的服务

| 服务 | 操作 | 优先级 |
|------|------|--------|
| TTBOX Update Server | 新建 systemd 服务 | P1 |
| 目录 /srv/ttbox/ | 创建 | P1 |
| 用户 ttbox | 创建 | P1 |

## 14. 风险

| 等级 | 风险 | 说明 |
|------|------|------|
| P0 | License SaaS 无备份 | SQLite 数据库无备份策略 |
| P0 | License SaaS 无反向代理 | 直接暴露 Go HTTP，无 WAF/限流 |
| P1 | 无域名 | Cloudflared 隧道域名可能变更 |
| P1 | 更新服务器无认证 | 需添加 API Key 防滥用 |
| P2 | 防火墙未配置 | 所有端口暴露 |

## 15. 下一阶段（Phase 4）

1. 创建 /srv/ttbox/ 目录结构
2. 创建 ttbox 系统用户
3. 部署 TTBOX Update Server（Python Flask，轻量级）
4. 实现 /api/update/check 和 /api/update/manifest
5. 配置 License SaaS 反向代理（Caddy）
6. 实现 maniest 签名和验证
7. 设备端 Update Engine 集成