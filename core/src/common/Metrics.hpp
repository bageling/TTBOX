// Metrics.hpp — 流水线指标 / 系统状态（占位结构，供 IPC GET_STATUS 使用）
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace ttbox::core {

// 流水线性能指标（占位：视觉链路接入后填充真实数据）
struct PipelineMetrics {
    double fps = 0.0;
    double capture_ms = 0.0;   // 采集耗时
    double resize_ms = 0.0;    // 预处理耗时
    double infer_ms = 0.0;     // 推理耗时
    double decode_ms = 0.0;    // 后处理耗时
    double aim_ms = 0.0;       // 自瞄耗时
    double e2e_ms = 0.0;       // 端到端耗时
    size_t detect_count = 0;   // 检测数
    size_t dropped_frames = 0; // 丢弃帧数（latest-frame 语义）
    uint64_t frames_total = 0; // 已处理帧总数
};

// 系统运行状态（IPC GET_STATUS 返回体）
struct SystemStatus {
    bool running = false;      // core 是否在运行
    std::string app_name;
    std::string version;
    double uptime_ms = 0.0;    // 自 initialize 起的运行时长
    std::string ipc_socket;    // 当前 IPC socket 路径
    std::string config_file;   // 当前加载的配置文件
    PipelineMetrics metrics;
};

}  // namespace ttbox::core
