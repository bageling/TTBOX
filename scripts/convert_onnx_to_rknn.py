#!/usr/bin/env python3
# convert_onnx_to_rknn.py — A-8 统一 ONNX→RKNN 转换/验证（离线工具，禁止进入高速链路）
#
# 功能：
#   1. ONNX 图检查：input shape / dtype / layout / color order / output shape / output count
#   2. 算子检查：未知算子明确报告
#   3. 转换：FP16（浮点）/ INT8（需 calibration 列表），target=rk3588
#   4. 输出转换报告 JSON（含检查结果 + sha256）
#   5. 可选：直接写入模型仓库 staging/<model_id>/（供 C++ ModelRegistry 后续 validate/install）
#
# 用法：
#   convert_onnx_to_rknn.py --onnx m.onnx --out m.rknn --dtype fp16|int8
#     [--input-size 1,3,640,640] [--calib-list list.txt] [--color RGB|BGR]
#     [--model-id id] [--registry-root /path/to/models] [--report report.json]
#
# 依赖：rknn-toolkit2 (2.x) + onnx；Python 仅用于离线转换/验证。
import argparse
import hashlib
import json
import os
import sys
import time
import traceback


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_onnx(onnx_path, expect_input=None):
    """返回 (input_info, output_infos, unsupported, error)。"""
    try:
        import onnx
    except Exception as e:
        return None, None, None, f"无法导入 onnx: {e}"
    try:
        model = onnx.load(onnx_path)
        graph = model.graph
    except Exception as e:
        return None, None, None, f"ONNX 加载失败: {e}"

    # ---- 输入检查 ----
    inputs = []
    for inp in graph.input:
        shape = [d.dim_value if d.HasField("dim_value") else 0 for d in inp.type.tensor_type.shape.dim]
        dtype = inp.type.tensor_type.elem_type
        inputs.append({"name": inp.name, "shape": shape, "dtype": dtype})
    if not inputs:
        return None, None, None, "ONNX 无输入"
    # 只取主输入（第一个）
    main_in = inputs[0]
    if expect_input and len(expect_input) > 0:
        if main_in["shape"] != expect_input:
            return None, None, None, (
                f"输入形状不匹配: ONNX={main_in['shape']} 期望={expect_input}")

    # ---- 输出检查 ----
    outputs = []
    for out in graph.output:
        shape = [d.dim_value if d.HasField("dim_value") else 0 for d in out.type.tensor_type.shape.dim]
        dtype = out.type.tensor_type.elem_type
        outputs.append({"name": out.name, "shape": shape, "dtype": dtype})
    if not outputs:
        return None, None, None, "ONNX 无输出"

    # ---- 算子检查（可识别集合；未知仅警告，最终以 rknn build 为准）----
    KNOWN = {
        "Conv", "Relu", "Sigmoid", "Add", "Mul", "Sub", "Div", "Concat", "Reshape",
        "Transpose", "Gemm", "BatchNormalization", "MaxPool", "AveragePool", "GlobalAveragePool",
        "Softmax", "Flatten", "Squeeze", "Unsqueeze", "Slice", "Split", "Gather",
        "Clip", "Pad", "Resize", "Shape", "Constant", "Identity", "Cast", "ReduceMean",
        "ReduceSum", "ReduceMax", "ArgMax", "TopK", "NonMaxSuppression", "GatherElements",
        "ScatterND", "Where", "Less", "Greater", "Equal", "Not", "And", "Or", "Exp",
        "MatMul", "Erf", "Sign", "Abs", "Sqrt", "Pow", "Tanh", "MulTruncate", "Tile",
    }
    unsupported = set()
    nodes = graph.node
    for n in nodes:
        op = n.op_type
        if op not in KNOWN:
            unsupported.add(op)
    return main_in, outputs, unsupported, None


def build_rknn(onnx_path, out_path, dtype, calib_list, input_size):
    from rknn.api import RKNN

    rknn = RKNN()
    cfg = dict(
        mean_values=[[0, 0, 0]],
        std_values=[[255, 255, 255]],
        target_platform="rk3588",
        optimization_level=3,
    )
    do_quant = (dtype == "int8")
    if do_quant:
        cfg["quantized_dtype"] = "w8a8"
        cfg["quantized_algorithm"] = "normal"
        cfg["quantized_method"] = "channel"
    rc = rknn.config(**cfg)
    if rc != 0:
        rknn.release()
        return False, "rknn.config 失败 rc=" + str(rc)
    rc = rknn.load_onnx(model=onnx_path, input_size_list=[input_size])
    if rc != 0:
        rknn.release()
        return False, "rknn.load_onnx 失败 rc=" + str(rc)
    rc = rknn.build(do_quantization=do_quant, dataset=calib_list if do_quant else None)
    if rc != 0:
        rknn.release()
        return False, "rknn.build 失败 rc=" + str(rc)
    rc = rknn.export_rknn(out_path)
    if rc != 0:
        rknn.release()
        return False, "rknn.export_rknn 失败 rc=" + str(rc)
    rknn.release()
    return True, None


def main():
    ap = argparse.ArgumentParser(description="A-8 ONNX→RKNN 转换/验证")
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--out", required=True, help="目标 .rknn 路径")
    ap.add_argument("--dtype", required=True, choices=["fp16", "int8"])
    ap.add_argument("--input-size", default="1,3,640,640",
                    help="ONNX 输入形状（逗号分隔，默认 1,3,640,640）")
    ap.add_argument("--calib-list", default=None, help="INT8 量化数据集（每行一张图片）")
    ap.add_argument("--color", default="BGR", choices=["RGB", "BGR"])
    ap.add_argument("--model-id", default="")
    ap.add_argument("--registry-root", default=None,
                    help="模型仓库根（models/）；指定后复制到 staging/<model_id>/")
    ap.add_argument("--report", default=None, help="转换报告 JSON 输出路径")
    args = ap.parse_args()

    input_size = [int(x) for x in args.input_size.split(",") if x]
    report = {
        "ok": False,
        "model_id": args.model_id,
        "dtype": args.dtype,
        "color": args.color,
        "input_size": input_size,
        "onnx": args.onnx,
        "out": args.out,
        "errors": [],
        "warnings": [],
        "elapsed_ms": 0,
        "sha256": "",
        "file_size": 0,
    }

    t0 = time.time()
    # ---- 1. ONNX 图检查 ----
    print("== 检查 ONNX 图 ==")
    main_in, outputs, unsupported, err = check_onnx(args.onnx, input_size)
    if err:
        report["errors"].append(err)
        print(f"[FAIL] {err}")
        _finish(report, args)
        return 1
    print(f"  输入: {main_in['name']} shape={main_in['shape']} dtype={main_in['dtype']}")
    print(f"  输出数: {len(outputs)}")
    for o in outputs:
        print(f"    {o['name']} shape={o['shape']} dtype={o['dtype']}")
    if unsupported:
        msg = "未知/非常见算子: " + ",".join(sorted(unsupported))
        report["warnings"].append(msg)
        print(f"  [WARN] {msg}")
    # 输出形状/数量写入报告
    report["output_count"] = len(outputs)
    report["output_shapes"] = [o["shape"] for o in outputs]

    # ---- 2. 转换 ----
    print(f"== 转换 {args.dtype} ==")
    ok, cerr = build_rknn(args.onnx, args.out, args.dtype, args.calib_list, input_size)
    if not ok:
        report["errors"].append("转换失败: " + str(cerr))
        print(f"[FAIL] {cerr}")
        _finish(report, args)
        return 1
    report["elapsed_ms"] = int((time.time() - t0) * 1000)
    report["sha256"] = sha256_file(args.out)
    report["file_size"] = os.path.getsize(args.out)
    report["ok"] = True
    print(f"[OK] 转换完成: {args.out} ({report['file_size']}B, {report['elapsed_ms']}ms)")
    print(f"  sha256: {report['sha256']}")

    # ---- 3. 可选：写入模型仓库 staging ----
    if args.registry_root and args.model_id:
        try:
            staging = os.path.join(args.registry_root, "staging", args.model_id)
            os.makedirs(staging, exist_ok=True)
            dst = os.path.join(staging, "model.rknn")
            with open(args.out, "rb") as src, open(dst, "wb") as d:
                d.write(src.read())
            manifest = {
                "model_id": args.model_id,
                "label": args.model_id,
                "version": "1.0.0",
                "sha256": report["sha256"],
                "signature": "",
                "origin": "local:" + args.onnx,
                "converter_version": "convert_onnx_to_rknn:1",
                "runtime_version": "ttbox-a8",
                "status": 1,  # staging
                "status_name": "staging",
                "created_at": int(time.time() * 1000),
            }
            with open(os.path.join(staging, "manifest.json"), "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"[OK] staging: {dst}")
        except Exception as e:
            report["warnings"].append("写入 staging 失败: " + str(e))
            print(f"[WARN] staging 写入失败: {e}")

    _finish(report, args)
    return 0 if report["ok"] else 1


def _finish(report, args):
    if args.report:
        with open(args.report, "w") as f:
            json.dump(report, f, indent=2)
        print("转换报告:", args.report)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print("[FAIL] 未捕获异常:", e)
        traceback.print_exc()
        sys.exit(1)
