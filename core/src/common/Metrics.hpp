// Metrics.hpp — 流水线指标 / 系统状态（供 IPC GET_STATUS 使用）
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "common/Types.hpp"

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
    uint32_t input_width = 0;
    uint32_t input_height = 0;
    double resize_ms = 0.0;
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
    uint32_t tracks = 0;       // 跟踪中的目标数（detection.tracks 同语义）
    size_t dropped_frames = 0; // 丢弃帧数（latest-frame 语义，被新帧覆盖）
    uint64_t frames_total = 0; // 已发布帧总数（capture_frames）
    uint64_t infer_total = 0;  // 推理完成帧总数（worker published 累计）
    uint64_t last_frame = 0;        // 最近一次 AimThread 消费帧号（诊断）
    uint64_t last_timestamp_us = 0; // 该帧采集时间戳（us，诊断）
    // 鼠标/瞄准链路（G1-2：AimThread 真实状态）
    int32_t mouse_dx = 0;      // 最近一次注入 DX（int16 语义）
    int32_t mouse_dy = 0;      // 最近一次注入 DY
    uint64_t gated_frames = 0; // 热键门控拦截帧数（无热键不注入）
    uint64_t target_frames = 0;// 有目标帧数
    uint64_t no_target_frames = 0; // 无目标帧数
    bool aim_active = false;   // 热键按下（AI 控制激活中）
    bool injection_allowed = false;
    bool mouse_control_connected = false;
    uint64_t mouse_control_socket_write_ok = 0;
    uint64_t mouse_control_socket_write_fail = 0;
    uint64_t mouse_control_send_count = 0;
    int32_t last_mouse_control_dx = 0;
    int32_t last_mouse_control_dy = 0;
    int32_t last_mouse_control_wheel = 0;
    uint64_t last_mouse_control_timestamp_us = 0;
    double aim_error_x = 0.0;  // 瞄准误差 X（AimThread 实时，诊断用）
    double aim_error_y = 0.0;  // 瞄准误差 Y
    double target_point_x = 0.0;
    double target_point_y = 0.0;
    double reference_x = 0.0;
    double reference_y = 0.0;
    double pid_output_x = 0.0;
    double pid_output_y = 0.0;
    double scheduler_input_x = 0.0;
    double scheduler_input_y = 0.0;
    double aim_pos_x = 0.0;    // 目标中心 X（crop 系 px，AimThread 实时；标定/诊断用）
    double aim_pos_y = 0.0;    // 目标中心 Y
    bool aim_has_target = false; // 当前帧是否检测到目标（标定状态机用）
    int32_t aim_target_id = -1;   // 当前选择目标的稳定 ID
    int32_t aim_target_class_id = -1;
    double aim_target_width = 0.0;
    double aim_target_height = 0.0;
    double aim_target_x1 = 0.0;
    double aim_target_y1 = 0.0;
    double aim_target_x2 = 0.0;
    double aim_target_y2 = 0.0;
    // 当前帧全部有效检测框（仅用于预览识别框显示，不参与控制链）。
    std::vector<DetectionBox> detection_boxes;
    // Phase2：预览指标（PreviewModule 真实统计）
    double preview_fps = 0.0;
    double preview_encode_ms = 0.0;
    uint32_t preview_width = 0;
    uint32_t preview_height = 0;
    uint32_t preview_bytes = 0;
    uint64_t preview_frames = 0;
    uint64_t preview_dropped = 0;
    // ★ M2.03：预览降级水印 —— 投影到 /api/state.preview.watermark，使"受限降级"可外部观测。
    // 全功能（capture∧inference∧aim）⇒ false；受限 ⇒ true。runtime 未启动/预览未起 ⇒ false。
    bool preview_watermark = false;
    // ---- 模型输入通路诊断（T1.15；来源 = WorkerStats.input_path，跨 worker 聚合纯函数
    //      rknn/InputPathSummary.hpp::summarize_input_path）----
    // 语义：回答"换了模型之后，每帧输入实际走的是哪条路径"。runtime 未启动 / 无 worker 时
    // 全部保持 unknown / 0 / false（= unavailable），绝不臆造成 "compatible"。
    // ★ 这些字段**只增不删**，名字是 IPC/Web 契约（Web 侧 state.model_input 按名消费）。
    std::string model_input_pass_mode = "unknown";  // compatible | xor_shift128 | uint8_native | unknown
    int model_input_type = -1;                      // rknn_tensor_type 数值（0=FP32 1=FP16 2=INT8 3=UINT8 4=INT16）
    std::string model_input_type_name = "unknown";  // 同上，名字（面板直接显示）
    int model_input_fmt = -1;                       // rknn_tensor_format 数值（0=NCHW 1=NHWC ...）
    std::string model_input_fmt_name = "unknown";
    int model_input_qnt_type = -1;                  // 0=NONE 1=DFP 2=AFFINE_ASYMMETRIC
    std::string model_input_qnt_name = "unknown";
    int model_input_zp = 0;                         // 反量化 zero point（XOR 快路径要求 == -128）
    double model_input_scale = 0.0;                 // 反量化 scale
    uint32_t model_input_width = 0;                 // 模型输入宽（与 input_width=采集分辨率 比对可发现配置漂移）
    uint32_t model_input_height = 0;
    bool model_zero_copy_ready = false;             // 零拷贝输入 mem 已建好（至少一个 worker）
    bool model_fast_path_active = false;            // XOR 快路径已激活（至少一个 worker）
    bool model_external_dma_requested = false;      // config 开关 rknn_external_dma_input
    bool model_external_dma_bound = false;          // 实际绑定成功（至少一个 worker）
    uint32_t model_workers_total = 0;               // worker 总数
    uint32_t model_workers_zero_copy = 0;           // 其中零拷贝就绪的个数
    uint32_t model_workers_fast_path = 0;           // 其中真正走 XOR 快路径的个数
    // 输入通路判定为"慢"的原因（人话，面板直接显示；空串 = 无异常）。
    // 例："INT8 但 zp=-4（非零均值）⇒ 快路径不可用，回落兼容 I/O"。
    std::string model_input_note;
};

// ---- 授权投影（T1.10②，唯一来源 = LicenseGate 快照；禁止任一层重算）----
// 契约（§3.1）：字段名写死，Web/工具据此消费。expires_at/grace_until 为 unix 秒（0 = 无期限/无）。
// M2 增补：ui_brand（只增不删，既有字段语义不变）。features 的权威闭集见
//   auth/LicenseStateMachine.hpp 的 known_features()，本注释不再各写一份。
struct LicenseStatusBlock {
    // ---- M2.03：features 的布尔投影（★ core 唯一裁决，web 只读、零推导）----
    // 由 features 派生（capture ∈ features 等），计算点 = core（to_snapshot 之后的映射）。
    struct Capabilities {
        bool capture = false;
        bool inference = false;
        bool aim = false;
        bool ota = false;
    };
    bool        activated = false;   // == state ∈ {active, expiring_soon, trial_active}
    std::string state;               // lowercase 状态名（§3.2）：unactivated|trial_active|active|...
    std::string plan = "none";       // none|trial|subscription|permanent
    bool        is_pro = false;
    std::vector<std::string> features;  // known_features() 子集；空数组合法（未激活/权威否定必为空）
    std::string ui_brand = "ttbox";  // M2：UI 品牌，已过字符集校验；不可信态回落 "ttbox"
    // M2.05：卡号可读短码（"TTB-XXXX-XXXX"）；可信态非空、不可信态 ""（只增不删）。
    std::string short_code;
    Capabilities capabilities;       // M2.03：能力位投影（只增不删；wire 名 license.capabilities.*）
    int64_t     expires_at = 0;      // unix 秒；0 = 无期限/未知
    int64_t     grace_until = 0;     // unix 秒；0 = 无
    std::string last_error;
    int         heartbeat_interval_s = 60;
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
    std::string current_model_id; // 当前实际运行/激活的模型 ID（active.json 真相源）
    PipelineMetrics metrics;
    // T1.10②：授权投影（唯一来源 = LicenseGate 快照）。既有字段语义不变（只增不删）。
    LicenseStatusBlock license;
};

}  // namespace ttbox::core
