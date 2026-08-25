# AIBOX Web Map

`web-aibox` 是 Flutter Web 静态产物；`autobl` 内含白狼控制台原生 Web UI。控制台实际覆盖服务启停/状态/自启、WebAIBox、键鼠服务、HDMI loopout、EDID、性能档位、模型包上传/安装、系统工具、版本包信息/历史、数据采集和认证。

## 已静态枚举的能力接口

- `/api/aibox/{start,stop,status,autostart}`
- `/api/km-service/{start,stop,status,autostart}`
- `/api/web-aibox/{start,stop,status,url,autostart}`
- `/api/loopout/{start,stop,status}`
- `/api/tools/{install-aibox,upload-aibox-model,version-info,version-package-history,restore-default,reboot,shutdown}`
- `/api/datacollection/*`
- `/api/auth/*`

前端通过 HTTP/HTTPS 调用 5200 控制台后端；Flutter Web 自身有 `/api/v1/user-data/storage/config-{get,put,list,delete}`。
