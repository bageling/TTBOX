# 已弃用：unit 副本已删除（DEP-07 / T1.05）

本目录**不再存放任何 `.service` 文件**。

## 为什么

`ttbox-web.service` 在这里曾有一份副本，与权威源 `deploy/systemd/ttbox-web.service`
**双份并存且已漂移**（此副本缺 `User=`/`Group=`、缺 `PYTHONPATH`、缺 `[Unit]` 段的
`StartLimitBurst/StartLimitIntervalSec`，且多了一条 `Requires=ttbox-core.service`）。
两份文件"长得像但不一致"是部署事故的温床：照旧路径拷贝会得到行为不同的服务。
按 DEP-07 收敛原则——**仓库内每 unit 有且仅有一份**——此副本已删除。

## 唯一权威源

```
deploy/systemd/ttbox-web.service
```

## 部署时从哪来（DEP-06 / DEP-01）

unit 是**版本产物的一部分**，随发布树交付：

```
/opt/ttbox/current/deploy/systemd/ttbox-web.service      # current -> releases/<ver>/
```

- 安装/切换：`scripts/ttbox_release_install.sh <ver> <payload_dir> --activate`
  （会把 unit 断言 `ExecStart` 经 current 解析后可执行、`WorkingDirectory` 存在）
- 幂等自愈：`scripts/ttbox_ensure_services.sh`（unit 源 = `/opt/ttbox/current/deploy/systemd/`）
  由 `ttbox-ensure.timer` 每 10 分钟巡检，被误删/误改后自动长回。

> 请勿再从本目录拷贝 `*.service` 到 `/etc/systemd/system/`。

---

# `ui_brands.json` — 渠道品牌注册表（schema v2 / M2.04）

> 本目录除"不再放 service"外，**仍是品牌表的权威源**：
> `plugins/web/config/ui_brands.json`（由 `ttbox-web.py::_ui_brands_table()` 按 mtime 热重载）。

## 用途

签名卡下发的 `ui_brand` token（`[A-Za-z0-9_-]` ≤32）经 Web 侧**闭集归一**映射到一条品牌记录，
再投影成前端展示字段。**数据驱动**：新增渠道 = 加一条 JSON，零代码改动。

## schema v2（v1 向后兼容）

```json
{
  "schema": "ttbox-ui-brand-v2",
  "default": "ttbox",
  "brands": {
    "<brand_id>": {
      "brand_name": "渠道名",
      "brand_mark": "两字标识",
      "brand_eyebrow": "顶栏副标题",
      "brand_title": "标题",
      "app_title": "应用名",
      "default_theme": "dark" | "light",
      "allow_theme_switch": true | false,
      "default_local_name": "设备本地名",
      "default_hotspot_ssid": "热点名",
      "fallback_reset_text": "重置文案",
      "brand_accent": "#RRGGBB",
      "brand_logo": "logos/<id>.png" | null,
      "theme": { "mode": "dark" | "light", "accent": "#RRGGBB" },
      "template_dir": "<dir>" | null,
      "static_dir": "<dir>" | null
    }
  }
}
```

### v2 新增（均可选；缺省 ⇒ 由 `default_theme` / 默认强调色派生，故 v1 条目照常工作）

| 字段 | 含义 | 归一规则（越界一律回默认） |
| --- | --- | --- |
| `brand_accent` / `theme.accent` | 品牌强调色 | 仅 `#RRGGBB`（统一大写），否则 `#2F81F7` |
| `brand_logo` | 品牌 logo（**相对**路径） | 空/绝对/含 `..`/含非法字符/超 64 ⇒ `null` |
| `theme.mode` | 主题模式 | 仅 `dark`/`light`，否则取 `default_theme` |

> `theme.*` 优先级高于平铺的 `brand_accent`。`_ui_block()` 会同时输出 `brand_accent` /
> `brand_logo` / `theme{mode,accent}`，前端据此上色与挂 logo；`template_dir` / `static_dir`
> 是**服务端路径控制字段**，**不进入** JSON 投影。

## 新增渠道 checklist（渠道白标上线必查）

1. **加品牌条目**：在本文件 `brands` 下新增一条（参照 `sample` 条目）。
   `brand_id` 必须与签名卡内 `ui_brand` **逐字节一致**（Core 侧 `sanitize_ui_brand` 只放行
   `[A-Za-z0-9_-]`，且首字符为字母/数字）。
2. **品牌表 ↔ SSID 一致**：`default_hotspot_ssid` 只是**面板展示**；板子实际广播的 SSID 由
   `scripts/wifi_manager.py` 的 `DEFAULT_SSIDS` 决定（源 = 环境变量 `TTBOX_WIFI_DEFAULT_SSIDS`）。
   **两者必须一起改**——只改品牌表 ⇒ 面板显示渠道名、板子仍广播 TTBOX（用户找不到热点）。
   `wifi_manager.verify_brand_ssid_consistency()` 提供自检钩子（见该文件）。
3. **皮肤（可选）**：把定制 CSS/图片放到 `plugins/web/static/<static_dir>/`，
   模板放到 `plugins/web/templates/<template_dir>/`（仅 `index.html`/`mobile.html` 换皮；
   `activate.html` 全品牌共用——读卡前品牌未定）。缺目录**不是错误**，静默回退默认。
4. **回归**：`python -m pytest plugins/web/tests/test_web_brand.py -v`
   （含 v2 字段回退与 SSID 耦合判据）。

