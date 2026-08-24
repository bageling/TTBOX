// WorkerPool.cpp — 多 Worker 并发推理实现
#include "rknn/WorkerPool.hpp"

#if defined(_WIN32)
namespace ttbox::core {
}
#else

#include <algorithm>
#include <chrono>
#include <cstring>
#include <thread>

#include "common/Logger.hpp"

namespace ttbox::core {

namespace {

using clock = std::chrono::steady_clock;

// float -> IEEE half（就近舍入，A-6 检测精度路径：u8->FP16 输入必须精确）
uint16_t float_to_half(float f) {
    uint32_t b;
    std::memcpy(&b, &f, sizeof(b));
    const uint32_t sign = (b >> 16) & 0x8000u;
    int32_t exp = static_cast<int32_t>((b >> 23) & 0xFF) - 127 + 15;
    uint32_t man = b & 0x7FFFFFu;
    if (exp >= 31) return static_cast<uint16_t>(sign | 0x7C00u);  // Inf/NaN
    if (exp <= 0) {
        if (exp < -10) return static_cast<uint16_t>(sign);         // 下溢为 0
        man = (man | 0x800000u) >> (1 - exp);                      // denormal
        return static_cast<uint16_t>(sign | (man >> 13));
    }
    // 就近舍入到 10 位尾数
    const uint32_t rem = man & 0x1FFFu;
    man >>= 13;
    if (rem > 0x1000u || (rem == 0x1000u && (man & 1u))) ++man;
    if (man > 0x3FFu) {
        man = 0;
        ++exp;
    }
    if (exp >= 31) return static_cast<uint16_t>(sign | 0x7C00u);
    return static_cast<uint16_t>(sign | (static_cast<uint32_t>(exp) << 10) | man);
}

// uint8 -> IEEE half 查表（0..255 精确 FP16，A-6 检测精度路径）
void build_u8_to_half(uint16_t lut[256]) {
    for (int i = 0; i < 256; ++i) {
        lut[i] = float_to_half(static_cast<float>(i));
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// InferenceWorker
// ---------------------------------------------------------------------------

InferenceWorker::~InferenceWorker() {
    stop();
}

bool InferenceWorker::start(const Params& params, std::string* error) {
    if (running_.load()) {
        if (error) *error = "worker 已在运行";
        return false;
    }
    if (params.latest == nullptr || params.model_path.empty()) {
        if (error) *error = "worker 参数无效（latest/model 缺失）";
        return false;
    }
    if (params.total_workers < 1) {
        if (error) *error = "total_workers 无效";
        return false;
    }
    params_ = params;
    id_ = params.id;

    // 模型加载先行：RGA 输出尺寸/decode 输入尺寸一律以模型实际输入为准
    // （不猜、不硬编码；黄瓦=320x320、yolo261n=640x640）
    engine_ = std::make_unique<RKNNEngine>();
    std::string eng_err;
    RKNNEngine::Params ep;
    ep.model_path = params.model_path;
    ep.core_mask = params.core_mask;
    ep.pass_through = params.pass_through;
    if (!engine_->init(ep, &eng_err)) {
        if (error) *error = "worker RKNN init 失败: " + eng_err;
        engine_.reset();
        return false;
    }
    const uint32_t in_w = engine_->info().input_width;
    const uint32_t in_h = engine_->info().input_height;

    rga_ = std::make_unique<RgaProcessor>();
    std::string rga_err;
    if (!rga_->init({in_w, in_h, true, params.color_order}, &rga_err)) {
        if (error) *error = "worker RGA init 失败: " + rga_err;
        rga_.reset();
        engine_.reset();
        return false;
    }
    fp16_buf_.resize(engine_->info().input_size / 2);
    build_u8_to_half(u8_to_half_lut_);

    // ---- A-6：原生输出 buffer（want_float=0）+ Decode/NMS ----
    raw_outputs_.clear();
    raw_buf_ptrs_.clear();
    raw_sizes_.clear();
    for (const auto& oi : engine_->info().outputs) {
        raw_outputs_.emplace_back(oi.size, 0);
        raw_buf_ptrs_.push_back(raw_outputs_.back().data());
        raw_sizes_.push_back(oi.size);
    }
    // ---- A-6/A-7：解码器（优先 ModelAdapter 创建；否则默认 DecodeNMS）----
    std::string derr;
    if (params.adapter != nullptr) {
        decoder_ = params.adapter->create_decoder(&derr);
        if (!decoder_) {
            if (error) *error = "worker decoder 创建失败: " + derr;
            rga_.reset();
            engine_.reset();
            return false;
        }
        decoder_->set_frame(params.frame_w, params.frame_h);
    } else {
        DecodeParams dp;
        dp.conf_thres = params.conf_thres;
        dp.iou_thres = params.iou_thres;
        dp.classwise = true;
        dp.input_w = in_w;  // 模型实际输入尺寸（DFL stride 计算依赖）
        dp.input_h = in_h;
        dp.frame_w = params.frame_w;
        dp.frame_h = params.frame_h;
        auto d = std::make_unique<DecoderImpl>();
        if (!d->configure(dp, &derr)) {
            if (error) *error = "worker DecodeNMS 配置失败: " + derr;
            rga_.reset();
            engine_.reset();
            return false;
        }
        decoder_ = std::move(d);
    }

    TTBOX_LOG_INFO("worker[" + std::to_string(id_) + "] 就绪: core_mask=" +
                   std::to_string(params.core_mask) + " 模型加载 " +
                   std::to_string(engine_->load_ms()) + "ms");

    running_.store(true);
    thread_ = std::thread(&InferenceWorker::loop, this);
    return true;
}

void InferenceWorker::stop() {
    if (!running_.exchange(false)) return;
    if (thread_.joinable()) thread_.join();
    if (engine_) engine_->destroy();
    if (rga_) rga_->destroy();
    engine_.reset();
    rga_.reset();
}

// A-8：应用最新 RuntimeProfile（仅当配置快照变化时）。
// conf/iou/class_filter/max_detections/FOV → decoder；ROI → decoder+RGA（安全点）。
void InferenceWorker::apply_runtime_profile() {
    if (params_.runtime_config == nullptr || !decoder_) return;
    auto prof = params_.runtime_config->snapshot();
    if (!prof || prof == applied_profile_) return;

    decoder_->apply_runtime(prof->inference, prof->fov);
    const uint32_t rw = prof->capture.width;
    const uint32_t rh = prof->capture.height;
    const uint32_t fw = params_.frame_w, fh = params_.frame_h;
    if (rw > 0 && rh > 0 && fw > 0 && fh > 0 && rw <= fw && rh <= fh) {
        // ROI 中心 = 屏幕中心 + offset（offset 语义=相对屏幕中心偏移），
        // 转左上角起点并 clamp 到全帧内。
        const int32_t cx = static_cast<int32_t>(fw / 2) + prof->capture.offset_x;
        const int32_t cy = static_cast<int32_t>(fh / 2) + prof->capture.offset_y;
        const int32_t rx = std::max<int32_t>(0, std::min<int32_t>(
            cx - static_cast<int32_t>(rw / 2), static_cast<int32_t>(fw - rw)));
        const int32_t ry = std::max<int32_t>(0, std::min<int32_t>(
            cy - static_cast<int32_t>(rh / 2), static_cast<int32_t>(fh - rh)));
        decoder_->set_roi(static_cast<uint32_t>(rx), static_cast<uint32_t>(ry), rw, rh);
        if (rga_) rga_->set_roi(static_cast<uint32_t>(rx), static_cast<uint32_t>(ry), rw, rh);
    }
    applied_profile_ = std::move(prof);
}

void InferenceWorker::loop() {
    using clock = std::chrono::steady_clock;
    while (running_.load()) {
        auto frame = params_.latest->get();
        if (!frame) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        const uint32_t seq = frame->info.sequence;
        if (seq == last_seq_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;  // 本 worker 已处理过该帧
        }
        // 认领规则：seq % N == id（帧只被一个 worker 处理，无重复）
        if (seq % static_cast<uint32_t>(params_.total_workers) !=
            static_cast<uint32_t>(params_.id)) {
            stats_.skipped.fetch_add(1);
            last_seq_ = seq;  // 该帧由其他 worker 处理
            continue;
        }
        last_seq_ = seq;

        // A-8：热更新配置（仅变化时应用，无逐帧 JSON/IPC）
        apply_runtime_profile();

        // ---- E2E 起点：帧采集时刻（v4l2 单调时钟，与 steady_clock 同基准）----
        const double recv_ms = frame->info.timestamp_ms;
        const auto e2e_t0 = clock::now();

        // ---- RGA：DMA-BUF fd → 模型输入尺寸（本 worker 独立实例）----
        RgaOutput rga_out;
        std::string perr;
        const auto t_rga0 = clock::now();
        if (!rga_->process(*frame, &rga_out, &perr)) {
            stats_.errors.fetch_add(1);
            continue;
        }
        stats_.rga.add(
            std::chrono::duration_cast<std::chrono::microseconds>(clock::now() - t_rga0).count());

        // ---- 输入类型分流（以模型实际输入类型为准，不猜）----
        //   INT8/UINT8（黄瓦 320x320）：RGA 输出 uint8 直喂，零转换
        //   FLOAT16（yolo261n 640x640）：uint8 -> FP16 查表转换
        const uint8_t* input_ptr = nullptr;
        size_t input_bytes = 0;
        const int itype = engine_->info().input_type;
        if (itype == 2 || itype == 3) {  // RKNN_TENSOR_INT8 / UINT8
            input_ptr = static_cast<const uint8_t*>(rga_out.vir_addr);
            input_bytes = static_cast<size_t>(rga_out.width) * rga_out.height * 3;
        } else {
            const auto tc0 = clock::now();
            const uint8_t* src = static_cast<const uint8_t*>(rga_out.vir_addr);
            const uint32_t w = rga_out.width;
            const uint32_t h = rga_out.height;
            const uint32_t src_stride = rga_out.stride;
            const uint32_t dst_stride_px = src_stride / 3;
            for (uint32_t y = 0; y < h; ++y) {
                const uint8_t* srow = src + static_cast<size_t>(y) * src_stride;
                uint16_t* drow = fp16_buf_.data() + static_cast<size_t>(y) * dst_stride_px * 3;
                for (uint32_t x = 0; x < w * 3; ++x) {
                    drow[x] = u8_to_half_lut_[srow[x]];
                }
            }
            stats_.convert.add(
                std::chrono::duration_cast<std::chrono::microseconds>(clock::now() - tc0).count());
            input_ptr = reinterpret_cast<const uint8_t*>(fp16_buf_.data());
            input_bytes = engine_->info().input_size;
        }

        // ---- RKNN 推理（本 worker 独立 context）+ 原生输出 + Decode/NMS ----
        std::string ierr;
        const auto t_infer0 = clock::now();
        if (!engine_->set_input(input_ptr, input_bytes, &ierr)) {
            stats_.errors.fetch_add(1);
            continue;
        }
        if (!engine_->run(&ierr)) {
            stats_.errors.fetch_add(1);
            continue;
        }
        // want_float=0 原生输出（A-6：禁止无意义 float 转换，直供 decode）
        if (!engine_->get_raw_outputs(raw_buf_ptrs_.data(), raw_sizes_.data(), &ierr)) {
            stats_.errors.fetch_add(1);
            continue;
        }
        std::string derr;
        if (!decoder_->process(engine_->info(), raw_buf_ptrs_.data(), &detections_, &derr)) {
            stats_.errors.fetch_add(1);
            continue;
        }
        stats_.processed.fetch_add(1);
        const uint64_t now_us = static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::microseconds>(clock::now().time_since_epoch()).count());
        // 兼容旧 MouseScheduler：继续发布 LatestDetections。
        if (params_.latest_dets) {
            params_.latest_dets->publish(detections_, seq, now_us);
        }
        if (params_.aim_mailbox) {
            aim::AimTargetTask task;
            task.frame_number = seq;
            task.timestamp_us = now_us;
            task.worker_id = id_;
            task.frame_width = params_.frame_w;
            task.frame_height = params_.frame_h;
            task.detections = detections_;
            if (!detections_.empty()) {
                task.has_target = true;
                task.target = detections_.front();
                task.aim_point = {
                    (task.target.x1 + task.target.x2) * 0.5f,
                    (task.target.y1 + task.target.y2) * 0.5f};
                task.target_width = task.target.x2 - task.target.x1;
                task.target_height = task.target.y2 - task.target.y1;
            }
            params_.aim_mailbox->offer(static_cast<std::size_t>(id_), std::move(task));
        }
        // 帧级 total = set_input + run + output（与 A-4 infer() total 语义一致）
        stats_.stages.total.add(
            std::chrono::duration_cast<std::chrono::microseconds>(clock::now() - t_infer0).count());
        // 吸收本帧 RKNNEngine 阶段统计（absorb 后 reset，避免重复累计）
        {
            const auto& est = engine_->stats();
            stats_.stages.set_input.absorb(est.set_input);
            stats_.stages.run.absorb(est.run);
            stats_.stages.output.absorb(est.output);
            stats_.stages.total.absorb(est.total);
            engine_->reset_stats();
        }
        // 吸收本帧 Decode/NMS 统计 + 候选/目标计数
        {
            const auto& ds = decoder_->stats();
            stats_.decode_stages.decode.absorb(ds.decode);
            stats_.decode_stages.nms.absorb(ds.nms);
            stats_.decode_stages.total.absorb(ds.total);
            stats_.candidates.fetch_add(ds.candidates.load());
            stats_.detections.fetch_add(ds.detections.load());
            decoder_->reset_stats();
        }
        // E2E（us）= 帧采集→认领（单调毫秒差值，v4l2 与 steady_clock 同基准）
        //          + 认领→完成（处理耗时）
        const double claim_ms =
            std::chrono::duration<double, std::milli>(e2e_t0.time_since_epoch()).count();
        const uint64_t e2e_us =
            static_cast<uint64_t>((claim_ms - recv_ms) * 1000.0) +
            static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::microseconds>(
                                      clock::now() - e2e_t0).count());
        stats_.e2e.add(e2e_us);
    }
}

// ---------------------------------------------------------------------------
// WorkerPool
// ---------------------------------------------------------------------------

bool WorkerPool::start(const Params& params, std::string* error) {
    if (!workers_.empty()) {
        if (error) *error = "WorkerPool 已在运行";
        return false;
    }
    if (params.worker_cores.empty()) {
        if (error) *error = "worker_cores 为空（worker 数量必须 ≥1）";
        return false;
    }
    const size_t n = params.worker_cores.size();
    for (size_t i = 0; i < n; ++i) {
        auto worker = std::make_unique<InferenceWorker>();
        InferenceWorker::Params wp;
        wp.id = static_cast<int>(i);
        wp.core_mask = params.worker_cores[i];
        wp.model_path = params.model_path;
        wp.pass_through = params.pass_through;
        wp.out_w = params.out_w;
        wp.out_h = params.out_h;
        wp.latest = params.latest;
        wp.total_workers = static_cast<int>(n);
        wp.conf_thres = params.conf_thres;
        wp.iou_thres = params.iou_thres;
        wp.frame_w = params.frame_w;
        wp.frame_h = params.frame_h;
        wp.color_order = params.color_order;
        wp.adapter = params.adapter;
        wp.runtime_config = params.runtime_config;
        wp.latest_dets = params.latest_dets;
        wp.aim_mailbox = params.aim_mailbox;
        std::string werr;
        if (!worker->start(wp, &werr)) {
            stop();
            if (error) *error = "worker[" + std::to_string(i) + "] 启动失败: " + werr;
            return false;
        }
        workers_.push_back(std::move(worker));
    }
    return true;
}

void WorkerPool::stop() {
    for (auto& w : workers_) {
        if (w) w->stop();
    }
    workers_.clear();
}

uint64_t WorkerPool::total_processed() const {
    uint64_t s = 0;
    for (const auto& w : workers_) s += w->stats().processed;
    return s;
}

uint64_t WorkerPool::total_errors() const {
    uint64_t s = 0;
    for (const auto& w : workers_) s += w->stats().errors;
    return s;
}

uint64_t WorkerPool::total_skipped() const {
    uint64_t s = 0;
    for (const auto& w : workers_) s += w->stats().skipped;
    return s;
}

}  // namespace ttbox::core

#endif  // !_WIN32
