// RKNNEngine.cpp — RKNN C API 推理实现（librknnrt）
/*
 * TTBOX 文件说明
 *
 * 文件：RKNNEngine.cpp
 *
 * 作用：
 *   NPU（神经网络处理器）推理引擎。
 *   负责把图片数据送入 NPU 执行 AI 计算，并取回结果。
 *
 * 小白理解：
 *   NPU 是专门用来跑 AI 的芯片。这个模块负责：
 *   1. 把图片数据送进 NPU
 *   2. 让 NPU 执行 AI 模型计算
 *   3. 把计算结果取出来供后续处理
 *
 * 注意：
 *   本注释仅用于说明代码，不改变程序逻辑。
 */

#include "rknn/RKNNEngine.hpp"

#if defined(_WIN32)
// Windows 占位：无 NPU 硬件，CMake 仅在 Unix 编译本文件。
namespace ttbox::core {
}
#else

#include <chrono>
#include <cstring>
#include <cstdio>

#include "rknn_api.h"
#include "common/Logger.hpp"

namespace ttbox::core {

namespace {

using clock = std::chrono::steady_clock;

uint32_t elapsed_us(const clock::time_point& a, const clock::time_point& b) {
    return static_cast<uint32_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(b - a).count());
}

const char* type_name(int t) {
    switch (t) {
        case RKNN_TENSOR_FLOAT32: return "FLOAT32";
        case RKNN_TENSOR_FLOAT16: return "FLOAT16";
        case RKNN_TENSOR_INT8: return "INT8";
        case RKNN_TENSOR_UINT8: return "UINT8";
        case RKNN_TENSOR_INT16: return "INT16";
        default: return "OTHER";
    }
}

const char* fmt_name(int f) {
    switch (f) {
        case RKNN_TENSOR_NCHW: return "NCHW";
        case RKNN_TENSOR_NHWC: return "NHWC";
        case RKNN_TENSOR_UNDEFINED: return "UNDEFINED";
        default: return "OTHER";
    }
}

}  // namespace

// ---------------------------------------------------------------------------
// Impl
// ---------------------------------------------------------------------------

struct RKNNEngine::Impl {
    rknn_context ctx = 0;  // 0 = 未初始化
};

// ---------------------------------------------------------------------------
// 生命周期
// ---------------------------------------------------------------------------

RKNNEngine::RKNNEngine() : impl_(std::make_unique<Impl>()) {}

RKNNEngine::~RKNNEngine() {
    destroy();
}

bool RKNNEngine::init(const Params& params, std::string* error) {
    if (inited_) {
        if (error) *error = "RKNNEngine 已初始化";
        return false;
    }
    if (params.model_path.empty()) {
        if (error) *error = "model_path 为空";
        return false;
    }
    params_ = params;

    const auto t0 = clock::now();

    // ---- 1. rknn_init：加载模型 + 初始化 runtime ----
    int rc = rknn_init(&impl_->ctx, const_cast<char*>(params_.model_path.c_str()), 0, 0, nullptr);
    if (rc != RKNN_SUCC) {
        if (error) *error = "rknn_init 失败（rc=" + std::to_string(rc) + "）: " + params_.model_path;
        return false;
    }
    TTBOX_LOG_INFO("rknn_init OK: " + params_.model_path);

    // ---- 2. 查询输入/输出数量 ----
    rknn_input_output_num io_num{};
    rc = rknn_query(impl_->ctx, RKNN_QUERY_IN_OUT_NUM, &io_num, sizeof(io_num));
    if (rc != RKNN_SUCC) {
        if (error) *error = "rknn_query(IN_OUT_NUM) 失败 rc=" + std::to_string(rc);
        destroy();
        return false;
    }
    info_.n_inputs = io_num.n_input;
    info_.n_outputs = io_num.n_output;
    if (info_.n_inputs < 1) {
        if (error) *error = "模型无输入";
        destroy();
        return false;
    }

    // ---- 3. 查询输入 0 属性 ----
    rknn_tensor_attr input_attr{};
    input_attr.index = 0;
    rc = rknn_query(impl_->ctx, RKNN_QUERY_INPUT_ATTR, &input_attr, sizeof(input_attr));
    if (rc != RKNN_SUCC) {
        if (error) *error = "rknn_query(INPUT_ATTR) 失败 rc=" + std::to_string(rc);
        destroy();
        return false;
    }
    info_.input_type = input_attr.type;
    info_.input_fmt = input_attr.fmt;
    info_.input_dims.assign(input_attr.dims, input_attr.dims + input_attr.n_dims);
    // dims 顺序：NCHW 时 [1,C,H,W]；NHWC 时 [1,H,W,C]
    if (input_attr.fmt == RKNN_TENSOR_NHWC && input_attr.n_dims >= 4) {
        info_.input_height = input_attr.dims[1];
        info_.input_width = input_attr.dims[2];
    } else if (input_attr.n_dims >= 4) {
        info_.input_height = input_attr.dims[2];
        info_.input_width = input_attr.dims[3];
    }
    info_.input_size = input_attr.size;  // 对齐后字节数

    // ---- 4. 查询输出属性 ----
    info_.output_n_elems.clear();
    info_.output_sizes.clear();
    info_.outputs.clear();
    for (uint32_t i = 0; i < info_.n_outputs; ++i) {
        rknn_tensor_attr out_attr{};
        out_attr.index = i;
        rc = rknn_query(impl_->ctx, RKNN_QUERY_OUTPUT_ATTR, &out_attr, sizeof(out_attr));
        if (rc != RKNN_SUCC) {
            if (error) *error = "rknn_query(OUTPUT_ATTR[" + std::to_string(i) + "]) 失败";
            destroy();
            return false;
        }
        info_.output_n_elems.push_back(out_attr.n_elems);
        info_.output_sizes.push_back(out_attr.size);
        RknnOutputInfo oi;
        oi.n_elems = out_attr.n_elems;
        oi.size = out_attr.size;
        oi.type = out_attr.type;
        oi.fmt = out_attr.fmt;
        oi.scale = out_attr.scale;
        oi.zp = out_attr.zp;
        oi.dims.assign(out_attr.dims, out_attr.dims + out_attr.n_dims);
        info_.outputs.push_back(std::move(oi));
    }

    // ---- 5. core_mask（来自 config/model 配置，不写死）----
    if (params_.core_mask != 0) {
        rc = rknn_set_core_mask(impl_->ctx, static_cast<rknn_core_mask>(params_.core_mask));
        if (rc != RKNN_SUCC) {
            TTBOX_LOG_WARN("rknn_set_core_mask(" + std::to_string(params_.core_mask) +
                           ") 失败 rc=" + std::to_string(rc) + "（继续用默认）");
        } else {
            TTBOX_LOG_INFO("core_mask 已设置: " + std::to_string(params_.core_mask));
        }
    }

    load_ms_ = static_cast<double>(elapsed_us(t0, clock::now())) / 1000.0;
    inited_ = true;

    {
        std::string log = "模型信息: 输入 " + std::to_string(info_.n_inputs) +
                          " 输出 " + std::to_string(info_.n_outputs) +
                          " | 输入 " + std::to_string(info_.input_width) + "x" +
                          std::to_string(info_.input_height) + " " +
                          type_name(info_.input_type) + " " + fmt_name(info_.input_fmt) +
                          " size=" + std::to_string(info_.input_size) + "B | 加载 " +
                          std::to_string(load_ms_) + "ms";
        TTBOX_LOG_INFO(log);
    }
    return true;
}

void RKNNEngine::destroy() {
    if (impl_ && impl_->ctx != 0) {
        rknn_destroy(impl_->ctx);
        impl_->ctx = 0;
    }
    inited_ = false;
}

void RKNNEngine::reset_stats() {
    stats_.set_input.clear();
    stats_.run.clear();
    stats_.output.clear();
    stats_.total.clear();
}

// ---------------------------------------------------------------------------
// 推理
// ---------------------------------------------------------------------------

bool RKNNEngine::set_input(const void* buf, size_t size, std::string* error) {
    if (!inited_) {
        if (error) *error = "引擎未初始化";
        return false;
    }
    if (buf == nullptr || size == 0) {
        if (error) *error = "输入 buffer 无效";
        return false;
    }
    rknn_input in{};
    in.index = 0;
    // A-6 实测（黄瓦 INT8 模型 + OIP 对齐）：INT8 模型若以 in.type=INT8 喂 0-255 原始像素，
    // runtime 会把像素当"已量化 INT8 值"，128-255 溢出为负 → 输出错乱（1 检测 vs Python 0）。
    // rknnlite 喂 UINT8 原始像素由 runtime 量化；此处 INT8 模型同样改喂 UINT8 像素，与 rknnlite 对齐。
    in.type = static_cast<rknn_tensor_type>(info_.input_type);
    if (info_.input_type == 2) {  // RKNN_TENSOR_INT8：喂原始像素（0-255），让 runtime 量化
        in.type = RKNN_TENSOR_UINT8;
    }
    in.size = static_cast<uint32_t>(size);
    in.fmt = static_cast<rknn_tensor_format>(info_.input_fmt);
    in.buf = const_cast<void*>(buf);
    in.pass_through = params_.pass_through ? 1 : 0;

    const auto t0 = clock::now();
    const int rc = rknn_inputs_set(impl_->ctx, 1, &in);
    stats_.set_input.add(elapsed_us(t0, clock::now()));
    if (rc != RKNN_SUCC) {
        if (error) *error = "rknn_inputs_set 失败 rc=" + std::to_string(rc);
        return false;
    }
    pass_through_active_ = params_.pass_through;
    return true;
}

bool RKNNEngine::run(std::string* error) {
    if (!inited_) {
        if (error) *error = "引擎未初始化";
        return false;
    }
    const auto t0 = clock::now();
    const int rc = rknn_run(impl_->ctx, nullptr);
    stats_.run.add(elapsed_us(t0, clock::now()));
    if (rc != RKNN_SUCC) {
        if (error) *error = "rknn_run 失败 rc=" + std::to_string(rc);
        return false;
    }
    return true;
}

namespace {

// 通用输出获取：want_float 控制是否转 float32（1=转换，0=原生零转换）
bool outputs_get_impl(RKNNEngine::Impl* impl, rknn_context ctx,
                      uint32_t n_outputs, int want_float,
                      void** out_bufs, size_t* out_sizes,
                      StatsCollector* output_stats, std::string* error) {
    std::vector<rknn_output> outs(n_outputs);
    for (uint32_t i = 0; i < n_outputs; ++i) {
        outs[i].index = i;
        outs[i].want_float = want_float;
        outs[i].is_prealloc = 1;
        outs[i].buf = out_bufs[i];
        outs[i].size = static_cast<uint32_t>(out_sizes[i]);  // 字节数
    }
    const auto t0 = clock::now();
    const int rc = rknn_outputs_get(ctx, n_outputs, outs.data(), nullptr);
    output_stats->add(elapsed_us(t0, clock::now()));
    if (rc != RKNN_SUCC) {
        if (error) *error = std::string("rknn_outputs_get 失败 rc=") + std::to_string(rc);
        return false;
    }
    // 预分配模式下需主动 release（释放 runtime 内部引用）
    rknn_outputs_release(ctx, n_outputs, outs.data());
    (void)impl;
    return true;
}

}  // namespace

bool RKNNEngine::get_outputs(void** out_bufs, size_t* out_sizes, std::string* error) {
    if (!inited_) {
        if (error) *error = "引擎未初始化";
        return false;
    }
    if (out_bufs == nullptr || out_sizes == nullptr) {
        if (error) *error = "输出参数无效";
        return false;
    }
    return outputs_get_impl(impl_.get(), impl_->ctx, info_.n_outputs, 1,
                            out_bufs, out_sizes, &stats_.output, error);
}

bool RKNNEngine::get_raw_outputs(void** out_bufs, size_t* out_sizes, std::string* error) {
    if (!inited_) {
        if (error) *error = "引擎未初始化";
        return false;
    }
    if (out_bufs == nullptr || out_sizes == nullptr) {
        if (error) *error = "输出参数无效";
        return false;
    }
    return outputs_get_impl(impl_.get(), impl_->ctx, info_.n_outputs, 0,
                            out_bufs, out_sizes, &stats_.output, error);
}

bool RKNNEngine::infer(const void* input_buf, size_t input_size,
                       std::vector<std::vector<float>>& outputs,
                       std::string* error) {
    if (!inited_) {
        if (error) *error = "引擎未初始化";
        return false;
    }
    const auto t0 = clock::now();
    if (!set_input(input_buf, input_size, error)) {
        return false;
    }
    if (!run(error)) {
        return false;
    }
    // 预分配输出 buffer（float32）
    outputs.resize(info_.n_outputs);
    std::vector<void*> bufs(info_.n_outputs);
    std::vector<size_t> sizes(info_.n_outputs);
    for (uint32_t i = 0; i < info_.n_outputs; ++i) {
        const size_t n_float = info_.output_n_elems[i];
        outputs[i].resize(n_float);
        bufs[i] = outputs[i].data();
        sizes[i] = n_float * sizeof(float);
    }
    if (!get_outputs(bufs.data(), sizes.data(), error)) {
        return false;
    }
    stats_.total.add(elapsed_us(t0, clock::now()));
    return true;
}

}  // namespace ttbox::core

#endif  // !_WIN32
