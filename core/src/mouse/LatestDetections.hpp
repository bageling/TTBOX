// LatestDetections.hpp — A10 共享最新检测结果（Aim 消费）
//
// 仿 LatestFrame 模式：worker 解码后 publish，MouseScheduler 独立线程读取。
// 无锁读（shared_ptr 快照），更新 = 原子替换。
#pragma once

#include <cstdint>
#include <memory>
#include <mutex>
#include <vector>

#include "common/Types.hpp"

namespace ttbox::core::aim {

class LatestDetections {
public:
    struct Snapshot {
        std::vector<DetectionBox> boxes;  // Detection 坐标系（全帧原图像素）
        uint64_t frame_id = 0;
        uint64_t timestamp_us = 0;
    };

    // 发布最新检测结果（每帧调用；空框也发布以便"无目标"判定）
    void publish(std::vector<DetectionBox> boxes, uint64_t frame_id, uint64_t timestamp_us) {
        auto snap = std::make_shared<Snapshot>();
        snap->boxes = std::move(boxes);
        snap->frame_id = frame_id;
        snap->timestamp_us = timestamp_us;
        std::lock_guard<std::mutex> lk(mtx_);
        snap_ = std::move(snap);
    }

    std::shared_ptr<const Snapshot> get() const {
        std::lock_guard<std::mutex> lk(mtx_);
        return snap_;
    }

private:
    mutable std::mutex mtx_;
    std::shared_ptr<const Snapshot> snap_ = std::make_shared<Snapshot>();
};

}  // namespace ttbox::core::aim
