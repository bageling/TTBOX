# AIBOX Filesystem Map

```text
/etc/aibox/                 Core config/models.conf
/usr/bin/aibox              launcher
/usr/lib/aibox/aibox-bl     Core binary
/usr/lib/aibox/model/       models
/var/lib/aibox/             Core state
/var/log/aibox/             Core logs
/run/aibox/                 runtime files
/opt/autobl/                controller/backend/webui/config/logs/scripts
/opt/web-aibox/web/         Flutter static web
/etc/web-aibox/             Web config
```

TTBOX 目标部署根为 `/opt/ttbox`，源码层不在 Windows 创建 Linux 绝对路径；Platform manifest 以相同的 component/current/versions/data/logs/update 语义表达。
