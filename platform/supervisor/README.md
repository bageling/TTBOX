# Supervisor Boundary

服务目录根据 AIBOX 的 `aibox.service`、`aiboxkm.service`、`web-aibox.service`、`cloud-file-manager.service` 能力抽取。当前只实现声明式服务规格和依赖模型；尚未调用 systemd，避免在 Windows 主机伪造设备运行结果。