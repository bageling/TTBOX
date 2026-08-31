# AI Handoff — TTBOX 项目交接文档

## 项目定位

TTBOX 是**独立产品**，不是 YU 的兼容版，不是 AIBox 的复刻版。

历史项目（YU / AIBox）只提供产品设计参考，不参与 TTBOX 架构定义。

## 核心原则

1. **TTBOX 拥有自己的：**
   - Product Blueprint（产品蓝图）
   - Feature Matrix（功能矩阵）
   - Domain Model（领域模型）
   - API Contract（API 契约）
   - Core Architecture（核心架构）
   - Runtime Model（运行时模型）

2. **禁止以下行为：**
   - 自行复制历史产品 API
   - 自行复制历史产品字段
   - 增加临时兼容层作为永久方案
   - 通过假成功隐藏未实现功能
   - 绕过 Domain Model 直接修改 Core
   - 因为当前没有实现就删除产品规划

3. **新功能开发流程：**
   ```
   用户需求 → 判断是否为 TTBOX Feature → 建立/更新 Feature Spec → 更新 Product Blueprint → 更新 Domain Model → 更新 API Contract → 实现 Core → 实现 Gateway → 连接 Web → 真机测试 → Feature Matrix 更新
   ```

## 参考资料

历史项目文档存放在 `yu-backend/` 目录，仅用于：
- 功能需求参考
- 设计思路参考
- 参数默认值参考

## 读取顺序

新的 AI 接手后，按以下顺序阅读：

1. `docs/AI_HANDOFF.md`（本文档）
2. `docs/product/TTBOX_PRODUCT_BLUEPRINT.md`（产品蓝图）
3. `docs/product/TTBOX_FEATURE_MATRIX.md`（功能矩阵）
4. `docs/product/TTBOX_DOMAIN_MODEL.md`（领域模型）
5. `docs/product/TTBOX_API_CONTRACT.md`（API 契约）
6. `README.md`（项目概述）
7. `docs/TTBOX_CODE_MAP_CN.md`（代码地图）