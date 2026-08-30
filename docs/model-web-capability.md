# 模型库能力审计（Model Web Capability）

> 对照 `core/src/model/ModelRegistry.{hpp,cpp}` 与 `core/src/ipc/IpcServer.cpp`（MODEL_* v0.3）逐项核实。
> 结论：**Web 模型库页展示的全部能力均有 Core 真实实现 + IPC 通道，无占位按钮。**

## 能力逐项

| 能力 | Core 实现 | IPC 暴露 | Web 入口 | 状态 |
|---|---|---|---|---|
| 模型列表 | `ModelRegistry::list()`（读 installed/*/manifest.json） | `MODEL_LIST` | 模型库页列表 | ✅ 全通 |
| 当前激活模型 | `active_model()`（读 active.json） | `MODEL_LIST` data.active | 总览"当前模型" + 列表"使用中"徽章 | ✅ 全通 |
| 导入（staging） | `import()`：收件目录 → staging/，拒绝与已装冲突 | `MODEL_IMPORT`（src_path 必须位于 `models/_incoming/`） | 上传表单（Gateway 落盘→import） | ✅ 全通 |
| 校验 | `validate()`：validator 加载 rknn 并返回 metadata；Windows 用文件级校验（非空+≥1KB），板端可注入 RKNN 加载校验 | `MODEL_VALIDATE` | 校验按钮（staging 状态显示） | ✅ 全通 |
| 安装 | `install()`：staging → installed（需先 validate 通过） | `MODEL_INSTALL` | （列表状态 staging→installed 由 Core 流转） | ✅ 全通 |
| 激活 | `activate()`：写 active.json，激活前做 validator 加载校验，失败回滚旧值 | `MODEL_ACTIVATE` | 启用按钮 | ✅ 全通 |
| 删除 | `remove()`：删除 installed；**激活中拒绝** | `MODEL_REMOVE` | 删除按钮（激活中禁用） | ✅ 全通 |
| 取消激活 | `deactivate()` | **未暴露 IPC** | 无 | ⚠ 缺口：Core 有能力，IPC 没有。影响小（启用新模型即覆盖 active） |
| 模型信息（输入尺寸/类别数） | validator 返回的 metadata 存 validation/ok.json | 未在 MODEL_LIST 返回 metadata | 显示"暂无数据" | ⚠ 缺口：需 MODEL_LIST 带 metadata 或新消息（板端 RKNN validator 就绪后做） |
| 模型热加载 | **无**（激活后需重启 AI 流水线） | — | UI 明示"更换后需重启 AI" | ⏸ 待 Core 运行时支持 |

## 内部能力（Core 有但 Web 不应直接用）

- `quarantine()`：校验失败模型隔离（Web 无管理场景，staging 失败由 UI 提示即可）
- `ModelRegistryOptions.root`：模型根目录（由 Application 从配置读取，非用户参数）
- `ModelManifest.sha256/signature`：预留字段（未来云端签名）

## 决策

1. 模型库页保持当前真实按钮集（上传/校验/启用/删除），不新增假能力。
2. `deactivate` 不单独做入口——启用新模型即覆盖，符合用户心智。
3. metadata 展示等板端 RKNN validator 落地后补 IPC，届时再开"输入尺寸/类别"卡片。
