// PreviewModule.hpp — 低帧预览（裁剪范围跟随 RuntimeProfile::capture 的截取尺寸）
#pragma once

#include <atomic>
#include <cstdint>
#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "capture/V4L2Capture.hpp"
#include "common/Types.hpp"
#include "model/RuntimeProfile.hpp"
#include "preview/PreviewRoi.hpp"

namespace ttbox::core {

class PreviewModule {
public:
    struct Params {
        // 预览**输出上限**（来自 preview.width/height）。裁剪范围由 RuntimeProfile::capture
        // 的截取尺寸决定（见 preview/PreviewRoi.hpp），这里只用来限制 JPEG 体量与编码耗时。
        uint32_t crop_width = 640;
        uint32_t crop_height = 640;
        int fps = 15;
        int jpeg_quality = 70;
        RuntimeConfig* runtime_config = nullptr;
        bool draw_detections = false;
        // ★ M2.03：受限预览水印（由 Application 依 LicenseGate 快照计算并写入；本模块**只执行参数，
        //   不查询授权**）。watermark_text 全 ASCII（内嵌 5×7 位图字体可绘），形如 "<BRAND> - LIMITED"。
        bool watermark = false;
        std::string watermark_text;
    };

    PreviewModule() = default;
    ~PreviewModule() { stop(); }
    PreviewModule(const PreviewModule&) = delete;
    PreviewModule& operator=(const PreviewModule&) = delete;

    bool start(const LatestFrame* frame_source, const Params& params, std::string* error = nullptr);
    void stop();
    bool running() const { return running_.load(); }
    bool snapshot(std::vector<uint8_t>* jpeg_out) const;

    using DetectionsProvider = std::function<std::vector<DetectionBox>()>;
    void set_detections_provider(DetectionsProvider provider) {
        std::lock_guard<std::mutex> lock(provider_mutex_);
        detections_provider_ = std::move(provider);
    }

    struct Metrics {
        std::atomic<uint64_t> frames{0};
        std::atomic<uint64_t> dropped{0};
        std::atomic<double> fps{0.0};
        std::atomic<double> encode_ms{0.0};
        std::atomic<uint32_t> width{0};
        std::atomic<uint32_t> height{0};
        std::atomic<uint32_t> bytes{0};
    };
    const Metrics& metrics() const { return metrics_; }

private:
    void loop();
    bool encode_frame(const FrameBuffer& frame, std::vector<uint8_t>* jpeg_out,
                      std::string* error);
    void draw_boxes(uint8_t* crop, uint32_t width, uint32_t height, uint32_t stride,
                    const std::vector<DetectionBox>& boxes, uint32_t origin_x,
                    uint32_t origin_y) const;
    // ★ M2.03：受限态水印绘制（内嵌 5×7 ASCII 位图字体；纯数组写入，**never block 帧输出**）。
    void draw_watermark(uint8_t* crop, uint32_t width, uint32_t height,
                        uint32_t stride) const;
    void smooth_boxes(const std::vector<DetectionBox>& raw, std::vector<DetectionBox>* out);
    // 依据 RuntimeProfile::capture（截取尺寸 + 偏移）算出预览裁剪矩形与输出尺寸。
    // 纯计算在 preview/PreviewRoi.hpp（host 可单测），这里只负责取 profile 快照。
    void resolve_preview_geometry(uint32_t frame_w, uint32_t frame_h, PreviewRoi* roi,
                                  uint32_t* out_width, uint32_t* out_height) const;

    const LatestFrame* latest_ = nullptr;
    Params params_{};
    std::atomic<bool> running_{false};
    std::thread thread_;

    mutable std::mutex provider_mutex_;
    DetectionsProvider detections_provider_;

    std::vector<DetectionBox> smooth_prev_;
    uint64_t smooth_lost_count_ = 0;
    // 预览线程独占，按尺寸复用，避免每帧重复申请 640×640×3 临时缓冲。
    std::vector<uint8_t> crop_buffer_;
    // 仅在 ROI 超过输出上限（如全帧中心正方形 1440）时用于等比缩小，同样按尺寸复用。
    std::vector<uint8_t> scaled_buffer_;

    mutable std::mutex jpeg_mutex_;
    std::vector<uint8_t> jpeg_;

    Metrics metrics_;
};

}  // namespace ttbox::core
