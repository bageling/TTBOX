# AIBOX Model Map

AIBOX Core 默认模型目录是 `/usr/lib/aibox/model`；控制台配置 `aibox.modelDirectory=/usr/lib/aibox/model`，模型可按游戏目录或扁平文件组织。控制台提供模型上传/文件列表/安装入口，Core 通过 `/etc/aibox` 配置和运行目录加载。

TTBOX 保留现有 `ModelRegistry`，Platform 只提供 staging → validate → install → activate → reload 的控制面；不把模型内容编译进 Core，也不修改 Decode/PID。
