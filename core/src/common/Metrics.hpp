// Metrics.hpp — 流水线指标 / 系统状态（供 IPC GET_STATUS 使用）
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace ttbox::core {

// 流水线性能指标。
// G1 纪律：全部字段来自真实统计（V4L2Capture / WorkerPool / AimTargetMailbox），
// 无估算值；runtime 未启动或对应阶段无样本时保持 0（语义 = unavailable）。
struct PipelineMetrics {
    double fps = 0.0;          // 推理 FPS（worker 完成帧数 / 运行秒数，累计均值）
    double capture_fps = 0.0;  // 采集 FPS（V4L2 滚动统计，真实发布到 latest 的帧）
    double capture_ms = 0.0;   // 采集耗时（现有统计未细分，恒 0 = unavailable）
    double buffer_age_ms = 0.0;        // 采集排队：最新帧龄（steady_now - 帧时间戳）
    uint64_t last_dequeued_count = 0;  // 采集排队：当前被占用（DQBUF 后未归还）的 buffer 数
    uint32_t buffer_count = 0;         // 采集排队：驱动 buffer 总数
    double resize_ms = 0.0;    // 预处理耗时（uint8->FP16 转换 avg；INT8 模型为 0）
    double infer_ms = 0.0;     // 推理耗时（RKNN set_input+run+output avg）
    double infer_set_input_ms = 0.0; // 推理分段：输入拷贝+量化 avg
    double infer_run_ms = 0.0;       // 推理分段：NPU 纯计算 avg
    double infer_output_ms = 0.0;    // 推理分段：输出取回 avg
    double decode_ms = 0.0;    // 后处理耗时（decode+NMS avg）
    double aim_ms = 0.0;       // 自瞄耗时（现有统计未细分，恒 0 = unavailable）
    double e2e_ms = 0.0;       // 端到端耗时（帧采集→推理完成 avg）
    // 分位数（真实样本统计，ms；无样本 = 0）
    double e2e_p50_ms = 0.0, e2e_p95_ms = 0.0, e2e_p99_ms = 0.0, e2e_max_ms = 0.0;
    double infer_p50_ms = 0.0, infer_p95_ms = 0.0, infer_p99_ms = 0.0;
    double decode_p50_ms = 0.0, decode_p95_ms = 0.0, decode_p99_ms = 0.0;
    size_t detect_count = 0;   // 最近一帧检测目标数（mailbox 最新任务）
    uint32_t tracks = 0;       // 跟踪中的目标数（YU detection.tracks 同语义）
    size_t dropped_frames = 0; // 丢弃帧数（latest-frame 语义，被新帧覆盖）
    uint64_t frames_total = 0; // 已发布帧总数（capture_frames）
    uint64_t infer_total = 0;  // 推理完成帧总数（worker published 累计）
    // 鼠标/瞄准链路（G1-2：AimThread 真实状态）
    int32_t mouse_dx = 0;      // 最近一次注入 DX（int16 语义）
    int32_t mouse_dy = 0;      // 最近一次注入 DY
    uint64_t gated_frames = 0; // 热键门控拦截帧数（无热键不注入）
    uint64_t target_frames = 0;// 有目标帧数
    uint64_t no_target_frames = 0; // 无目标帧数
    bool aim_active = false;   // 热键按下（AI 控制激活中）
    double aim_error_x = 0.0;  // 瞄准误差 X（AimThread 实时，诊断用）
    double aim_error_y = 0.0;  // 瞄准误差 Y
    // Phase2：预览指标（PreviewModule 真实统计）
    double preview_fps = 0.0;
    double preview_encode_ms = 0.0;
    uint32_t preview_width = 0;
    uint32_t preview_height = 0;
    uint32_t preview_bytes = 0;
    uint64_t preview_frames = 0;
    uint64_t preview_dropped = 0;
};

// 系统运行状态（IPC GET_STATUS 返回体）
struct SystemStatus {
    bool running = false;        // core 是否在运行（Application 层标志）
    bool runtime_running = false; // CoreRuntime 流水线是否启动
    std::string app_name;
    std::string version;
    double uptime_ms = 0.0;      // 自 initialize 起的运行时长
    std::string ipc_socket;      // 当前 IPC socket 路径
    std::string config_file;     // 当前加载的配置文件
    PipelineMetrics metrics;
};

}  // namespace ttbox::core
