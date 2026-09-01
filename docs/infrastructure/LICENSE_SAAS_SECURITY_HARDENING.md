# License SaaS 安全加固方案

## 当前风险

| 风险 | 等级 | 说明 |
|------|------|------|
| 默认管理员密码 | P0 | admin/admin123，未修改 |
| 默认 JWT 密钥 | P0 | prod-test-secret，可伪造 Token |
| 无反向代理 | P0 | Go HTTP 直接暴露，无 WAF/限流 |
| 无备份 | P0 | SQLite 数据库无备份策略 |
| 所有端口开放 | P1 | 防火墙未配置 |
| 日志无轮转 | P2 | server.log 持续增长 |

## 1. 管理员密码重置

建议立即修改 License SaaS 环境变量：

```bash
# 编辑 /etc/systemd/system/license-saas.service
Environment=APP_ADMIN_USER=admin
Environment=APP_ADMIN_PASS=<新密码>  # 修改此处

# 重启服务
systemctl daemon-reload
systemctl restart license-saas
```

## 2. JWT 密钥重置

```bash
# 生成新密钥
openssl rand -hex 32

# 更新服务配置
Environment=APP_JWT_SECRET=<新密钥>
```

## 3. 反向代理（推荐 Caddy）

```bash
# 安装 Caddy
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list
apt update
apt install caddy

# /etc/caddy/Caddyfile
:8080 {
    reverse_proxy 127.0.0.1:8080 {
        header_up X-Real-IP {remote_host}
    }
}
```

## 4. SQLite 备份

创建定时备份脚本 `/opt/license-saas/backup.sh`：

```bash
#!/bin/bash
BACKUP_DIR="/srv/backups/license-saas"
DB_PATH="/opt/license-saas/license-saas.db"
RETENTION_DAYS=30

mkdir -p $BACKUP_DIR

# 备份
sqlite3 $DB_PATH ".backup $BACKUP_DIR/license-saas-$(date +%Y%m%d-%H%M%S).db"

# 完整性检查
sqlite3 $BACKUP_DIR/license-saas-$(date +%Y%m%d-%H%M%S).db "PRAGMA integrity_check;"

# 删除过期备份
find $BACKUP_DIR -name "license-saas-*.db" -mtime +$RETENTION_DAYS -delete
```

```bash
# 添加到 crontab
0 3 * * * /opt/license-saas/backup.sh
```

## 5. 防火墙配置

```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 10015/tcp # 自定义 SSH
ufw --force enable
```

## 6. 日志轮转

创建 `/etc/logrotate.d/license-saas`：

```
/opt/license-saas/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
```

## 7. 实施优先级

| 优先级 | 操作 | 影响 |
|--------|------|------|
| P0 (立即) | 修改管理员密码 + JWT 密钥 | 需重启 License SaaS |
| P0 (立即) | 配置防火墙 | 可能影响 Cloudflared 隧道 |
| P1 (本周) | 配置 SQLite 备份 | 无影响 |
| P1 (本周) | 配置日志轮转 | 无影响 |
| P2 (本月) | 部署 Caddy 反向代理 | 需要修改 Cloudflared 配置 |

## 8. 注意事项

- 修改密码/JWT 密钥后，所有现有 Token 将失效，用户需重新登录
- 防火墙配置前确认 Cloudflared 隧道端口（20241）已放行
- 备份文件不应和数据库放在同一磁盘