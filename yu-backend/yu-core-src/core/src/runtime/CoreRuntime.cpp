// CoreRuntime.cpp — Capture/Worker/AimThread 统一生命周期。
#include "runtime/CoreRuntime.hpp"
#include "common/Logger.hpp"

#include <chrono>

namespace ttbox::core {

namespace {
int64_t steady_now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}
}  // namespace

bool CoreRuntime::initialize(const Params& p, std::string* error) {
    if (!p.output) {
        if (error) *error = "输出后端不能为空";
        return false;
    }
    if (p.workers.worker_cores.empty() ||
        p.workers.worker_cores.size() > aim::AimTargetMailbox::kMaxWorkers) {
        if (error) *error = "Worker 数量必须为 1~3";
        return false;
    }
    runtime_config_ = p.runtime_config;
    output_ = p.output;
    worker_params_ = p.workers;
    preview_params_ = p.preview;
    mailbox_ = std::make_unique<aim::AimTargetMailbox>(p.workers.worker_cores.size());
    capture_ = std::make_unique<V4L2Capture>();
    workers_ = std::make_unique<WorkerPool>();
    if (!capture_->configure(p.capture, error)) {
        return false;
    }
    return true;
}

bool CoreRuntime::start(std::string* error) {
    if (!capture_ || !workers_ || !mailbox_ || running_.exchange(true)) {
        return false;
    }
    start_steady_ms_.store(steady_now_ms());
    if (!capture_->open(error) || !capture_->start(error)) {
        running_ = false;
        return false;
    }
    worker_params_.latest = capture_->latest_frame_ref();
    worker_params_.aim_mailbox = mailbox_.get();
    worker_params_.runtime_config = runtime_config_;
    const auto& fmt = capture_->format();
    worker_params_.frame_w = fmt.width;
    worker_params_.frame_h = fmt.height;
    if (!workers_->start(worker_params_, error)) {
        capture_->stop();
        capture_->close();
        running_ = false;
        return false;
    }
    if (!aim_thread_.start(mailbox_.get(), output_, 4000, runtime_config_)) {
        workers_->stop();
        capture_->stop();
        capture_->close();
        running_ = false;
        return false;
    }
    // Phase2：启动低帧预览（失败仅告警，不影响 AI 流水线）
    {
        std::string perr;
        preview_ = std::make_unique<PreviewModule>();
        PreviewModule::Params pp = preview_params_;
        // 预览帧率热配置：runtime_profile.preview.fps > 0 时覆盖 config 默认值
        // （yu latency.preview_interval_ms 经网关/bridge 翻译为 preview.fps）
        if (runtime_config_) {
            if (auto snap = runtime_config_->snapshot()) {
                if (snap->preview.fps > 0 && snap->preview.fps <= 60) {
                    pp.fps = static_cast<int>(snap->preview.fps);
                }
            }
        }
        if (!preview_->start(capture_->latest_frame_ref(), pp, &perr)) {
            TTBOX_LOG_WARN("Preview 启动失败（不影响流水线）: " + perr);
            preview_.reset();
        } else {
            TTBOX_LOG_INFO("Preview 已启动: " + std::to_string(pp.out_width) + "x" +
                           std::to_string(pp.out_height) + " @" + std::to_string(pp.fps) + "fps");
        }
    }
    return true;
}

void CoreRuntime::stop() {
    if (!running_.exchange(false)) {
        return;
    }
    start_steady_ms_.store(0);
    if (preview_) {
        preview_->stop();
        preview_.reset();
    }
    aim_thread_.stop();
    if (workers_) {
        workers_->stop();
    }
    if (capture_) {
        capture_->stop();
        capture_->close();
    }
}

// G1：聚合真实运行指标（只读现有统计，无估算）。
//   - capture：V4L2Metrics（capture_frames / dropped / 滚动 capture_fps）
//   - worker：WorkerStats（published / stages / e2e / decode）
//   - 目标数：mailbox 最新任务（detections.size()；无任务 = 0）
// runtime 未启动时 out 保持调用方传入的初始值（全 0 = unavailable）。
void CoreRuntime::collect_metrics(PipelineMetrics* out) const {
    if (out == nullptr || !running_.load()) {
        return;
    }
    if (capture_) {
        const auto& cm = capture_->metrics();
        out->frames_total = cm.capture_frames.load();
        out->dropped_frames = cm.dropped_latest_frames.load();
        out->capture_fps = cm.capture_fps.load();
    }
    if (workers_ && workers_->worker_count() > 0) {
        // 聚合所有 worker：published 累计 → 推理 FPS；耗时 avg 直接平均
        uint64_t published = 0;
        double infer_avg_us = 0.0;
        double decode_avg_us = 0.0;
        double e2e_avg_us = 0.0;
        double convert_avg_us = 0.0;
        size_t n = workers_->worker_count();
        // 分位数：合并各 worker 样本到临时收集器（不污染 worker 统计），
        // 再算统一 P50/P95/P99/Max（跨 worker 真实分位，非分位均值）。
        StatsCollector e2e_all, infer_all, decode_all;
        for (const auto& w : workers_->workers()) {
            if (!w) continue;
            const auto& s = w->stats();
            published += s.published.load();
            infer_avg_us += s.stages.total.avg();
            decode_avg_us += s.decode_stages.total.avg();
            e2e_avg_us += s.e2e.avg();
            convert_avg_us += s.convert.avg();
            e2e_all.absorb(s.e2e);
            infer_all.absorb(s.stages.total);
            decode_all.absorb(s.decode_stages.total);
        }
        out->infer_total = published;
        out->fps = published;
        const int64_t started = start_steady_ms_.load();
        if (started > 0) {
            const double elapsed_s =
                static_cast<double>(steady_now_ms() - started) / 1000.0;
            if (elapsed_s > 0.0) {
                out->fps = static_cast<double>(published) / elapsed_s;
            }
        }
        out->infer_ms = infer_avg_us / static_cast<double>(n) / 1000.0;
        out->decode_ms = decode_avg_us / static_cast<double>(n) / 1000.0;
        out->e2e_ms = e2e_avg_us / static_cast<double>(n) / 1000.0;
        out->resize_ms = convert_avg_us / static_cast<double>(n) / 1000.0;
        // 真实分位数（us → ms；无样本时 percentile 返回 0）
        out->e2e_p50_ms = e2e_all.percentile(50) / 1000.0;
        out->e2e_p95_ms = e2e_all.percentile(95) / 1000.0;
        out->e2e_p99_ms = e2e_all.percentile(99) / 1000.0;
        out->e2e_max_ms = e2e_all.max() / 1000.0;
        out->infer_p50_ms = infer_all.percentile(50) / 1000.0;
        out->infer_p95_ms = infer_all.percentile(95) / 1000.0;
        out->infer_p99_ms = infer_all.percentile(99) / 1000.0;
        out->decode_p50_ms = decode_all.percentile(50) / 1000.0;
        out->decode_p95_ms = decode_all.percentile(95) / 1000.0;
        out->decode_p99_ms = decode_all.percentile(99) / 1000.0;
    }
    if (mailbox_) {
        // 最近任务目标数（取任意 slot 最新帧；mailbox take_latest 按帧号取最新）
        aim::AimTargetTask task;
        if (mailbox_->take_latest(&task)) {
            out->detect_count = task.detections.size();
        }
    }
    if (preview_) {
        const auto& pm = preview_->metrics();
        out->preview_fps = pm.fps.load();
        out->preview_encode_ms = pm.encode_ms.load();
        out->preview_width = pm.width.load();
        out->preview_height = pm.height.load();
        out->preview_bytes = pm.bytes.load();
        out->preview_frames = pm.frames.load();
        out->preview_dropped = pm.dropped.load();
    }
    // G1-2：AimThread 真实瞄准/注入状态（DX/DY/门控帧/目标帧）
    {
        const auto st = aim_thread_.status();
        out->mouse_dx = st.move_x;
        out->mouse_dy = st.move_y;
        out->gated_frames = st.gated_frames;
        out->target_frames = st.target_frames;
        out->no_target_frames = st.no_target_frames;
        out->aim_active = st.has_target;
    }
}

}  // namespace ttbox::core
