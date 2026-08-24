// test_worker_hw.cpp — 板端多 Worker 并发推理硬件测试（RK3588，阶段 A-5）
//
// 目的：验证 640×640 FP16 模型通过 1/2/3 个独立 RKNN context（各绑 NPU core）
//       能否并行利用 RK3588 三个 NPU Core，提升整体吞吐。
//
// 每组（1/2/3 Worker）至少 300 帧，采集：
//   capture FPS / Worker FPS / 总吞吐 FPS / set_input / run / output / total / convert / E2E
//   P50/P95/P99、CPU 使用率、NPU Core0/1/2 利用率、context 数、丢帧、错误数
//
// 用法：test_worker_hw --model <path> [--frames N] [--duration S] [--workers M]
//   --workers M：只跑 M 个 worker（默认依次跑 1/2/3）
#include <atomic>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <thread>
#include <vector>

#include <sys/mman.h>
#include <sys/stat.h>

#include "capture/V4L2Capture.hpp"
#include "common/Stats.hpp"
#include "config/ConfigManager.hpp"
#include "model/RuntimeProfile.hpp"
#include "aim/AimThread.hpp"
#include "output/FifoHidOutput.hpp"
#include "pipeline/AimTargetMailbox.hpp"
#include "rknn/NpuMonitor.hpp"
#include "rknn/WorkerPool.hpp"

using namespace ttbox::core;

#ifndef TTBOX_PROJECT_ROOT
#define TTBOX_PROJECT_ROOT "."
#endif

namespace {

const char* kDefaultModel = TTBOX_PROJECT_ROOT "/models/yolo261n-rk3588.rknn";

void print_stage(const char* tag, const StatsCollector& s) {
    if (s.count() == 0) {
        std::printf("  %-14s N=0\n", tag);
        return;
    }
    std::printf("  %-14s N=%-5zu  min=%6llu  avg=%8.1f  p50=%6llu  p95=%6llu  p99=%6llu  max=%6llu us\n",
                tag, s.count(),
                (unsigned long long)s.min(),
                s.avg(),
                (unsigned long long)s.percentile(50.0),
                (unsigned long long)s.percentile(95.0),
                (unsigned long long)s.percentile(99.0),
                (unsigned long long)s.max());
}

// 保存一帧 BGR888 为 BMP（Web 实时画面用；无第三方编码依赖）
void save_bmp(const char* path, const uint8_t* bgr, uint32_t w, uint32_t h, uint32_t stride) {
    const uint32_t row_bytes = w * 3;
    const uint32_t pad = (4u - (row_bytes & 3u)) & 3u;
    const uint32_t data_size = (row_bytes + pad) * h;
    // 原子写：先写 .tmp 再 rename，避免 Web 端读到半写文件（花屏/坏图）
    std::string tmp_path = std::string(path) + ".tmp";
    std::FILE* f = std::fopen(tmp_path.c_str(), "wb");
    if (!f) return;
    uint8_t hdr[54] = {0};
    hdr[0] = 'B'; hdr[1] = 'M';
    const uint32_t file_size = 54 + data_size;
    std::memcpy(hdr + 2, &file_size, 4);
    const uint32_t off = 54;
    std::memcpy(hdr + 10, &off, 4);
    const uint32_t hdr_size = 40;
    std::memcpy(hdr + 14, &hdr_size, 4);
    std::memcpy(hdr + 18, &w, 4);
    std::memcpy(hdr + 22, &h, 4);
    const uint16_t planes = 1;
    std::memcpy(hdr + 26, &planes, 2);
    const uint16_t bpp = 24;
    std::memcpy(hdr + 28, &bpp, 2);
    const uint32_t compression = 0;
    std::memcpy(hdr + 30, &compression, 4);
    std::memcpy(hdr + 34, &data_size, 4);
    std::fwrite(hdr, 1, sizeof(hdr), f);
    for (uint32_t y = 0; y < h; ++y) {
        std::fwrite(bgr + (h - 1 - y) * stride, 1, row_bytes, f);  // BMP bottom-up
        for (uint32_t p = 0; p < pad; ++p) std::fputc(0, f);
    }
    std::fclose(f);
    std::rename(tmp_path.c_str(), path);
}

// 在 BGR 预览缓冲上画检测框（绿色 2px 边框；坐标为预览图像素）
static void draw_det_box(uint8_t* bgr, int w, int h, int stride, float x1, float y1, float x2, float y2) {
    const int X1 = std::max(0, (int)x1), Y1 = std::max(0, (int)y1);
    const int X2 = std::min(w - 1, (int)x2), Y2 = std::min(h - 1, (int)y2);
    if (X2 <= X1 || Y2 <= Y1) return;
    const int tw = 2;
    for (int y = Y1; y <= Y2; ++y) {
        uint8_t* row = bgr + (size_t)y * stride;
        const bool top = y < Y1 + tw, bot = y > Y2 - tw;
        for (int x = X1; x <= X2; ++x) {
            if (!top && !bot && x >= X1 + tw && x <= X2 - tw) continue;
            row[(size_t)x * 3 + 0] = 0;     // B
            row[(size_t)x * 3 + 1] = 255;   // G
            row[(size_t)x * 3 + 2] = 0;     // R
        }
    }
}

// 读取 /proc/stat 总体 CPU busy/total（jiffies）
bool read_cpu_total(uint64_t* busy, uint64_t* total) {
    FILE* f = std::fopen("/proc/stat", "r");
    if (!f) return false;
    char line[256];
    unsigned long long user = 0, nice = 0, sys = 0, idle = 0;
    unsigned long long iowait = 0, irq = 0, softirq = 0, steal = 0;
    bool ok = false;
    if (std::fgets(line, sizeof(line), f) != nullptr &&
        std::sscanf(line, "cpu %llu %llu %llu %llu %llu %llu %llu %llu",
                    &user, &nice, &sys, &idle, &iowait, &irq, &softirq, &steal) == 8) {
        *busy = static_cast<uint64_t>(user + nice + sys + irq + softirq + steal);
        *total = static_cast<uint64_t>(*busy + idle + iowait);
        ok = true;
    }
    std::fclose(f);
    return ok;
}

// 运行一组 Worker（n_workers 个 context），返回 0 成功
int run_group(const std::string& model_path, int n_workers,
              const std::vector<int>& worker_cores,
              uint32_t in_w, uint32_t in_h, int frames, double duration_guard_s,
              uint32_t num_buffers, const ModelAdapterConfig& acfg, bool use_adapter,
              double report_every_s, RuntimeConfig* runtime_config = nullptr,
              const std::string& mouse_fifo = "") {
    std::printf("\n================  Worker 数=%d  ================\n", n_workers);
    std::printf("  worker_cores=%s\n", [&] {
        std::string s;
        for (size_t i = 0; i < worker_cores.size(); ++i) {
            if (i) s += ",";
            s += std::to_string(worker_cores[i]);
        }
        return s;
    }().c_str());

    V4L2Capture cap;
    std::string err;
    V4L2Capture::Params cp;
    cp.device = "/dev/video0";
    cp.num_buffers = num_buffers;
    if (!cap.configure(cp, &err) || !cap.open(&err) || !cap.start(&err)) {
        std::printf("[FAIL] capture: %s\n", err.c_str());
        return 1;
    }
    const V4L2Capture::FormatInfo& fmt = cap.format();
    std::printf("  V4L2 实际时序: %ux%u fourcc=%s\n", fmt.width, fmt.height,
                fmt.fourcc_str().c_str());

    // A11：新架构 AimTargetMailbox 是唯一控制数据入口。
    std::atomic<uint32_t> frame_dets{0};  // 当前帧目标个数（预览线程更新，METRICS 上报）

    // Web 实时画面：预览保存线程（CPU 拷贝中心 ROI → 平铺 BMP；~1ms/帧，远快于 RGA）
    std::atomic<bool> preview_stop{false};
    std::thread preview_thread([&] {
        const std::string bmp_path = "/run/ttbox-frame.bmp";  // tmpfs：写盘 ~0.2ms（eMMC ~18ms 会拖慢预览）
        std::map<int, void*> map_cache;  // V4L2 dma_fd 固定轮换 → 缓存 mmap 复用，避免每帧重建
        size_t map_len = 0;
        while (!preview_stop.load()) {
            uint32_t pv_w = 320, pv_h = 320, pv_roi_w = 320, pv_roi_h = 320;
            if (runtime_config) {
                auto prof = runtime_config->snapshot();
                if (prof) {
                    pv_w = prof->preview.width;
                    pv_h = prof->preview.height;
                    pv_roi_w = prof->preview.roi_w;
                    pv_roi_h = prof->preview.roi_h;
                }
            }
            if (pv_w == 0 || pv_h == 0) { pv_w = 320; pv_h = 320; }
            if (pv_roi_w == 0 || pv_roi_h == 0) { pv_roi_w = 320; pv_roi_h = 320; }
            auto frame = cap.latest_frame();
            if (frame && frame->info.dma_fd >= 0 && frame->info.width > 0 && frame->info.height > 0) {
                // 屏幕正中心 roi_w×roi_h 方框 → 最近邻缩放平铺到预览
                const uint32_t fw = frame->info.width, fh = frame->info.height;
                const uint32_t fstride = (frame->info.stride >= fw * 3) ? frame->info.stride : fw * 3;
                const uint32_t rx = fw > pv_roi_w ? (fw - pv_roi_w) / 2 : 0;
                const uint32_t ry = fh > pv_roi_h ? (fh - pv_roi_h) / 2 : 0;
                const size_t flen = static_cast<size_t>(fstride) * fh;
                void* s = nullptr;
                const auto it = map_cache.find(frame->info.dma_fd);
                if (it != map_cache.end()) {
                    s = it->second;
                } else {
                    void* m = ::mmap(nullptr, flen, PROT_READ, MAP_SHARED, frame->info.dma_fd, 0);
                    if (m != MAP_FAILED) {
                        s = m;
                        map_cache[frame->info.dma_fd] = m;
                        map_len = flen;
                    }
                }
                if (s) {
                    const uint8_t* src = static_cast<const uint8_t*>(s);
                    std::vector<uint8_t> buf(static_cast<size_t>(pv_w) * pv_h * 3);
                    uint8_t* dst = buf.data();
                    if (pv_roi_w == pv_w && pv_roi_h == pv_h) {
                        // 同尺寸：逐行 memcpy（最快路径）
                        for (uint32_t py = 0; py < pv_h; ++py) {
                            std::memcpy(dst + static_cast<size_t>(py) * pv_w * 3,
                                        src + static_cast<size_t>(ry + py) * fstride + static_cast<size_t>(rx) * 3,
                                        static_cast<size_t>(pv_w) * 3);
                        }
                    } else {
                        // 屏幕正中心 roi_w×roi_h 方框 → 最近邻缩放平铺到预览
                        for (uint32_t py = 0; py < pv_h; ++py) {
                            const uint32_t sy = ry + static_cast<uint32_t>((uint64_t)py * pv_roi_h / pv_h);
                            if (sy >= fh) break;
                            const uint8_t* row = src + static_cast<size_t>(sy) * fstride + static_cast<size_t>(rx) * 3;
                            uint8_t* dr = dst + static_cast<size_t>(py) * pv_w * 3;
                            for (uint32_t px = 0; px < pv_w; ++px) {
                                const uint32_t sx = static_cast<uint32_t>((uint64_t)px * pv_roi_w / pv_w);
                                std::memcpy(dr + static_cast<size_t>(px) * 3, row + static_cast<size_t>(sx) * 3, 3);
                            }
                        }
                    }
                    // A11：叠加真实推理框（LatestDetections 全帧坐标 → ROI 偏移 → 预览缩放）
                    auto snap = latest_dets.get();
                    if (snap && !snap->boxes.empty()) {
                        frame_dets.store(static_cast<uint32_t>(snap->boxes.size()));
                        const float sxf = static_cast<float>(pv_w) / static_cast<float>(pv_roi_w);
                        const float syf = static_cast<float>(pv_h) / static_cast<float>(pv_roi_h);
                        for (const auto& b : snap->boxes) {
                            const float px1 = (b.x1 - static_cast<float>(rx)) * sxf;
                            const float py1 = (b.y1 - static_cast<float>(ry)) * syf;
                            const float px2 = (b.x2 - static_cast<float>(rx)) * sxf;
                            const float py2 = (b.y2 - static_cast<float>(ry)) * syf;
                            if (px2 < 0 || py2 < 0 || px1 >= pv_w || py1 >= pv_h) continue;
                            draw_det_box(dst, static_cast<int>(pv_w), static_cast<int>(pv_h),
                                         static_cast<int>(pv_w * 3), px1, py1, px2, py2);
                        }
                    } else {
                        frame_dets.store(0);
                    }
                    save_bmp(bmp_path.c_str(), dst, pv_w, pv_h, pv_w * 3);
                    // A11：帧同步画面数据（Web 每帧读取，避免 METRICS 聚合延迟）
                    {
                        const auto now_us = std::chrono::duration_cast<std::chrono::microseconds>(
                            std::chrono::steady_clock::now().time_since_epoch()).count();
                        std::FILE* fj = std::fopen("/run/ttbox-frame.json.tmp", "wb");
                        if (fj) {
                            std::fprintf(fj, "{\"frame_dets\":%u,\"ts_us\":%llu}",
                                         frame_dets.load(),
                                         static_cast<unsigned long long>(now_us));
                            std::fclose(fj);
                            std::rename("/run/ttbox-frame.json.tmp", "/run/ttbox-frame.json");
                        }
                    }
                }
            }
            // 与 EDID 刷新率同步（240fps）：处理 ~1ms + sleep 3ms ≈ 4.3ms/帧
            if (!preview_stop.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(3));
            }
        }
        for (const auto& kv : map_cache) ::munmap(kv.second, map_len);
    });

    // A-7：统一 ModelAdapter（可选；构建 metadata + decoder 配置）
    ModelAdapter adapter;
    ModelAdapter* adapter_ptr = nullptr;
    if (use_adapter) {
        RKNNEngine probe;
        RKNNEngine::Params pep;
        pep.model_path = model_path;
        pep.core_mask = 0;
        pep.pass_through = false;
        std::string perr;
        if (!probe.init(pep, &perr)) {
            std::printf("[FAIL] adapter probe init: %s\n", perr.c_str());
            cap.stop();
            cap.close();
            return 1;
        }
        if (!adapter.analyze(probe.info(), acfg, &perr)) {
            std::printf("[FAIL] adapter.analyze: %s\n", perr.c_str());
            probe.destroy();
            cap.stop();
            cap.close();
            return 1;
        }
        probe.destroy();
        adapter_ptr = &adapter;
        std::printf("  ModelAdapter: %ux%u decode=%s classes=%u color=%s\n",
                    adapter.metadata().input_width, adapter.metadata().input_height,
                    ModelAdapter::decode_type_name(adapter.metadata().decode_type),
                    adapter.metadata().class_count,
                    adapter.metadata().color_order == ColorOrder::kRgb ? "RGB" : "BGR");
    }

    // A11：新架构独立 AimThread。控制逻辑不再由旧调度器 承担。
    aim::AimTargetMailbox aim_mailbox(worker_cores.size());
    auto aim_output = mouse_fifo.empty()
        ? std::shared_ptr<output::IHidOutput>(std::make_shared<output::NullHidOutput>())
        : std::shared_ptr<output::IHidOutput>(std::make_shared<output::FifoHidOutput>(mouse_fifo));
    aim::AimThread aim_thread;
    if (!aim_thread.start(&aim_mailbox, aim_output, 4000, runtime_config)) {
        std::printf("[FAIL] AimThread 启动失败\n");
        cap.stop(); cap.close(); return 1;
    }
    std::printf("  AimThread: 新架构启动（目标邮箱→独立控制线程→OutputAction）\n");

    WorkerPool pool;
    WorkerPool::Params pp;
    pp.model_path = model_path;
    pp.worker_cores = worker_cores;
    pp.pass_through = false;  // A-6：runtime 转换（与 Python 对齐，输入正确）
    pp.out_w = in_w;
    pp.out_h = in_h;
    pp.latest = cap.latest_frame_ref();
    pp.conf_thres = acfg.conf_thres;
    pp.iou_thres = acfg.iou_thres;
    pp.frame_w = fmt.width;   // 坐标映射回原图（V4L2 实际时序）
    pp.frame_h = fmt.height;
    pp.color_order = static_cast<int>(acfg.color_order);
    pp.adapter = adapter_ptr;
    pp.runtime_config = runtime_config;
    pp.aim_mailbox = &aim_mailbox;
    if (!pool.start(pp, &err)) {
        std::printf("[FAIL] worker pool(%d): %s\n", n_workers, err.c_str());
        cap.stop();
        cap.close();
        return 1;
    }
    std::printf("  context 数=%zu | 模型输入 %ux%u | pass_through=off | decode(conf=%.2f iou=%.2f)%s\n",
                pool.worker_count(), in_w, in_h, acfg.conf_thres, acfg.iou_thres,
                use_adapter ? " | A7-adapter" : "");

    NpuMonitor npu;
    npu.start(200, &err);

    uint64_t cpu_busy0 = 0, cpu_total0 = 0, cpu_busy1 = 0, cpu_total1 = 0;
    const bool cpu_ok = read_cpu_total(&cpu_busy0, &cpu_total0);

    const auto t0 = std::chrono::steady_clock::now();
    long long last_report_ms = 0;
    long long last_target_ms = 0;  // A10：高频目标状态文件写入节流（8ms≈125Hz，满足标定 120fps 候选反馈）
    while (true) {
        const double elapsed_s =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
        if (pool.total_processed() >= static_cast<uint64_t>(frames) ||
            elapsed_s >= duration_guard_s) {
            break;
        }
        // A10：定期输出 [REPORT] 汇总（供 Web 实时 FPS；不影响推理逻辑）
        if (report_every_s > 0.0) {
            const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                                         std::chrono::steady_clock::now().time_since_epoch())
                                         .count();
            if (now_ms - last_report_ms >= static_cast<long long>(report_every_s * 1000.0)) {
                last_report_ms = now_ms;
                const uint64_t captured_now = cap.metrics().capture_frames.load();
                const uint64_t processed_now = pool.total_processed();
                const uint64_t errors_now = pool.total_errors();
                const uint64_t skipped_now = pool.total_skipped();
                std::printf("  [REPORT] t=%.1fs captured=%llu processed=%llu errors=%llu "
                            "capture_fps=%.1f pool_fps=%.1f\n",
                            elapsed_s,
                            (unsigned long long)captured_now, (unsigned long long)processed_now,
                            (unsigned long long)errors_now,
                            elapsed_s > 0.0 ? static_cast<double>(captured_now) / elapsed_s : 0.0,
                            elapsed_s > 0.0 ? static_cast<double>(processed_now) / elapsed_s : 0.0);
                // A10：完整指标 JSON 行（供 Web 实时监控解析；单行，无嵌套换行）
                {
                    // 汇总各 worker 阶段指标
                    uint64_t run_n = 0, rga_n = 0, e2e_n = 0, decode_n = 0;
                    double run_sum = 0.0, rga_sum = 0.0, e2e_sum = 0.0, decode_sum = 0.0;
                    uint64_t det_sum = 0, cand_sum = 0;
                    for (const auto& w : pool.workers()) {
                        const WorkerStats& ws = w->stats();
                        run_n += ws.stages.run.count();
                        run_sum += ws.stages.run.avg() * static_cast<double>(ws.stages.run.count());
                        rga_n += ws.rga.count();
                        rga_sum += ws.rga.avg() * static_cast<double>(ws.rga.count());
                        e2e_n += ws.e2e.count();
                        e2e_sum += ws.e2e.avg() * static_cast<double>(ws.e2e.count());
                        decode_n += ws.decode_stages.total.count();
                        decode_sum += ws.decode_stages.total.avg() *
                                      static_cast<double>(ws.decode_stages.total.count());
                        det_sum += ws.detections.load();
                        cand_sum += ws.candidates.load();
                    }
                    const NpuLoadSummary npu_s = npu.summary();
                    const V4L2Metrics& vmm = cap.metrics();
                    const double cap_ms = captured_now > 0
                                              ? elapsed_s * 1000.0 / static_cast<double>(captured_now)
                                              : 0.0;
                    const auto as = aim_thread.status();
                    std::printf("  [AIM] running=%s last_frame=%llu consumed=%llu\n",
                                as.running ? "true" : "false",
                                (unsigned long long)as.last_frame,
                                (unsigned long long)as.consumed);
                    std::fflush(stdout);
            }
        }
        // A10：高频目标状态文件（供 Web 实时目标 + 自动标定闭环）。
        // [METRICS] 5s 一次对"注入后位移测量"太慢，这里 200ms 直写 tmpfs。
        {
            const long long now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                                         std::chrono::steady_clock::now().time_since_epoch())
                                         .count();
            if (now_ms - last_target_ms >= 8) {
                last_target_ms = now_ms;
                const auto as = aim_thread.status();
                char tbuf[512];
                const int tl = std::snprintf(tbuf, sizeof(tbuf),
                    "{\"t\":%.1f,\"enabled\":%s,\"found\":%s,\"frame\":%llu,\"consumed\":%llu}\n",
                    elapsed_s, as.running ? "true" : "false", as.has_task ? "true" : "false",
                    (unsigned long long)as.last_frame, (unsigned long long)as.consumed);
