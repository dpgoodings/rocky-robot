"""
Standalone RVC v2 (.pth or FP32 .onnx) -> Quantized INT8 ONNX converter.
Invoked on-demand by `add_voice.py` via ephemeral `uv run` so PyTorch/ONNX
never bloat `.venv` at rest.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)


def parse_sr(raw_sr) -> int:
    if isinstance(raw_sr, int):
        return raw_sr
    s = str(raw_sr).lower().strip()
    if s.endswith("k"):
        return int(float(s[:-1]) * 1000)
    try:
        return int(s)
    except ValueError:
        return 48000


def convert_pth_to_int8(pth_path: str, out_int8_path: str) -> int:
    import torch
    from infer_pack.models_onnx import SynthesizerTrnMsNSFsidM
    from onnxruntime.quantization import QuantType, quantize_dynamic

    cpt = torch.load(pth_path, map_location="cpu", weights_only=False)
    sr = parse_sr(cpt.get("sr", "48k"))
    version = cpt.get("version", "v2")
    net = SynthesizerTrnMsNSFsidM(*cpt["config"], version, is_half=False)
    net.load_state_dict(cpt["weight"], strict=False)
    net.eval()

    in_dim = 768 if version == "v2" else 256
    t_len = 240
    dummy = (
        torch.rand(1, t_len, in_dim),
        torch.tensor([t_len]).long(),
        torch.randint(1, 255, (1, t_len)).long(),
        torch.rand(1, t_len) * 200 + 100,
        torch.LongTensor([0]),
        torch.randn(1, 192, t_len),
    )
    names = ["phone", "phone_lengths", "pitch", "nsff0", "ds", "rnd"]
    dyn = {
        "phone": {1: "t"},
        "pitch": {1: "t"},
        "nsff0": {1: "t"},
        "rnd": {2: "t"},
        "audio": {2: "t"},
    }

    with tempfile.NamedTemporaryFile(suffix=".fp32.onnx", delete=False) as tmp:
        fp32_path = tmp.name

    try:
        torch.onnx.export(
            net,
            dummy,
            fp32_path,
            input_names=names,
            output_names=["audio"],
            dynamic_axes=dyn,
            opset_version=17,
            dynamo=False,
        )
        quantize_dynamic(fp32_path, out_int8_path, weight_type=QuantType.QInt8)
    finally:
        if os.path.exists(fp32_path):
            os.unlink(fp32_path)

    return sr


def convert_onnx_to_int8(onnx_path: str, out_int8_path: str) -> int:
    if onnx_path.endswith(".int8.onnx"):
        if os.path.abspath(onnx_path) != os.path.abspath(out_int8_path):
            shutil.copy2(onnx_path, out_int8_path)
        return 48000

    from onnxruntime.quantization import QuantType, quantize_dynamic

    quantize_dynamic(onnx_path, out_int8_path, weight_type=QuantType.QInt8)
    return 48000


def main() -> None:
    if len(sys.argv) < 3:
        raise SystemExit("Usage: convert_to_int8.py <input.pth|input.onnx> <output.int8.onnx>")

    src, dst = sys.argv[1], sys.argv[2]
    os.makedirs(os.path.dirname(os.path.abspath(dst)), exist_ok=True)
    if src.lower().endswith(".pth"):
        sr = convert_pth_to_int8(src, dst)
    else:
        sr = convert_onnx_to_int8(src, dst)

    size_mb = round(os.path.getsize(dst) / (1024 * 1024), 1)
    print(json.dumps({"output": dst, "sample_rate": sr, "size_mb": size_mb}))


if __name__ == "__main__":
    main()
