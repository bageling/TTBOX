# AIBOX Update Map

AIBOX 控制台把上传包放入工作目录后按包类型安装；`autobl` 的 postinst/force-reload 体现了：安装、等待 dpkg/apt 锁释放、停止旧后端、daemon-reload、启动新服务、检查 active。配置中还暴露版本信息、版本包文件 URL、版本历史和恢复默认入口。

## TTBOX 对齐实现边界

本次先落地组件状态和声明式更新接口，不执行包安装、不接云端、不删除 current。真实 Update/Rollback 属于后续独立阶段。
