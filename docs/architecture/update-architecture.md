# 更新架构（Update Architecture）

> 目标：Core / Model / Web / Agent / Config 可**独立更新、独立回滚**，带版本校验、SHA256、失败恢复、离线运行。
> 原则：**设备验证与更新分离**；后台异常不得影响已授权 AI Core 运行。

## 1. 独立更新单元

| 单元 | 内容 | 更新粒度 | 是否需停 Core | 回滚方式 |
|---|---|---|---|---|
| Core | `aibox-core` 可执行 + 库 | 版本目录切换 | 是（原子切换后重启） | 切换回旧版本目录 |
| Model | `.rknn` + 模型配置 JSON | 文件替换（新版本目录） | 否（Runtime 热切换或重启 Core） | 保留旧模型目录 |
| Web | `aibox-web` + 静态资源 | 版本目录切换 | 否 | 目录切换 |
| Agent | `aibox-agent` | 版本目录切换 | 否 | 目录切换 |
| Config | `config/*.json` | 文件替换 + schema 校验 | 否（热重载） | 备份恢复 |

## 2. 版本模型

- 每个单元独立版本号（semver：`core=0.3.0`、`model=1.2.0`、`web=0.2.1`、`agent=0.1.0`、`config=1.0.0`）
- 设备整体维护一份 `Update Manifest`（JSON）描述各单元当前版本、可用版本、校验和、兼容矩阵
- Manifest 示例：

```json
{
  "device_id": "aibox-<serial>",
  "units": {
    "core":  { "version": "0.3.0", "sha256": "...", "compat": {"kernel": ">=6.1.99", "rga": ">=1.9.1"} },
    "model": { "version": "1.2.0", "sha256": "...", "compat": {"core": ">=0.2.0"} },
    "web":   { "version": "0.2.1", "sha256": "..." },
    "agent": { "version": "0.1.0", "sha256": "..." },
    "config":{"version": "1.0.0", "sha256": "..."}
  }
}
```

## 3. 兼容性检查

- **内核/驱动**：Core 编译目标 kernel ≥6.1.99、librga ≥1.9.1、librknnrt 版本匹配
- **模型 ↔ Core**：模型输入尺寸/量化/输出结构与 Core 支持的解析器匹配（复用 A-2 模型 metadata 规则）
- **Config ↔ 各单元**：JSON schema 校验，字段白名单
- 不满足兼容 → 拒绝安装（不覆盖）

## 4. 更新流程（每单元一致）

```
1. 检查        Agent 拉取 Backend Manifest / 本地离线包
2. 下载        包 + SHA256（或本地包）
3. 校验        SHA256 匹配；兼容性检查通过
4. 备份        当前版本目录 → update/backup/<unit>/<old-version>/
5. 暂存        新版本 → update/staging/<unit>/<new-version>/
6. 安装        （Core：停 Core → 切换目录 → 启动；其余：原子目录切换/文件替换）
7. 验证        启动后自检（doctor）：通过 → 激活；失败 → 回滚
8. 激活/回滚   失败自动回滚到备份版本；激活后更新 Manifest
```

## 5. 失败恢复与回滚

- 每个更新单元保留最近 **N（默认 2）个版本** 备份
- 启动自检（`aibox doctor` 等价物）失败 → systemd/Agent 自动切回上一版本重启
- 掉电中断：staging 半成品不影响 current；启动时清理 staging
- 回滚命令：`aibox-update rollback <unit>`

## 6. 存储布局

```
/opt/aibox2/
├── core/current -> core/v0.3.0/
├── core/v0.3.0/   core/v0.2.0/          # 版本目录
├── models/current -> models/m1.2.0/
├── models/m1.2.0/*.rknn + config
├── web/current -> web/v0.2.1/
├── agent/current -> agent/v0.1.0/
├── config/current -> config/c1.0.0/     # 含运行时覆盖 data/config-runtime.json
├── data/                                # 授权缓存/状态/日志（不随单元升级）
└── update/
    ├── staging/<unit>/<version>/        # 未激活
    └── backup/<unit>/<version>/         # 可回滚
```

## 7. 离线运行

- AI Core 运行**不依赖**网络/后台/更新（离线自治）
- 离线包：`.aibox-update`（tar + manifest + sha256）可由 U 盘/Web 上传导入
- 离线期间授权凭本地缓存 + 宽限期运行（见 `device-validation.md`）

## 8. 安全

- 包签名：SHA256 + 非对称签名（阶段 C 实现）
- 拒绝降级保护（可选策略）、拒绝未知来源包
- 更新日志（audit）：每次更新/回滚记录到 data/update-audit.log

## 9. 与现有实现的关系

- 当前仓库 `aibox/core/` 为源码形态；版本化部署目录在阶段 C/D 落地
- 现有 `scripts/build/*`、`scripts/systemd/*` 为部署基础；Update Manager 在其上构建
