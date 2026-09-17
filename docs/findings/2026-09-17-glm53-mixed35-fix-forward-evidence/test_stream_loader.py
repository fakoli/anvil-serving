"""CPU-only control-flow fixture for the pinned R7 transfer patch; no GPU use."""
import ast
import gc
import os
from pathlib import Path
from types import MethodType
import weakref

import torch

source = Path("/opt/infernal-invocation/vllm/vllm/model_executor/layers/quantization/exl3.py")
node = next(node for node in ast.parse(source.read_text()).body if isinstance(node, ast.ClassDef) and node.name == "Exl3MoEParameter")
methods = [node for node in node.body if isinstance(node, ast.FunctionDef) and node.name in {"_slice_loaded_weight", "load_exl3_weight"}]
namespace = {"torch": torch, "os": os}
exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)

class Parameter:
    pass

p = Parameter()
p.exl3_tp_slice = (0, 1, 2, 1)
p.exl3_preallocate = False
p.exl3_tensors = {}
p.device = torch.device("cuda:0")
for name in ("_slice_loaded_weight", "load_exl3_weight"):
    setattr(p, name, MethodType(namespace[name], p))
os.environ["VLLM_EXL3_STREAM_R7_TO_DEVICE"] = "1"
original = torch.Tensor.to
transfers = []

def transfer(tensor, *, device, non_blocking, copy):
    assert str(device) == "cuda:0" and non_blocking is False and copy is True
    transfers.append(tuple(tensor.shape))
    return tensor.clone()

try:
    torch.Tensor.to = transfer
    for expert, width in enumerate((3, 4)):
        tensor = torch.arange(4 * width).reshape(4, width)
        ref = weakref.ref(tensor)
        expected = tensor[1:3].clone()
        p.load_exl3_weight(tensor, expert_id=expert, shard_id="w1")
        assert torch.equal(p.exl3_tensors[(expert, "w1")], expected)
        assert p.exl3_tensors[(expert, "w1")].data_ptr() != tensor.data_ptr()
        del tensor
        gc.collect()
        assert ref() is None
    assert transfers == [(2, 3), (2, 4)]
    p.device = torch.device("cpu")
    try:
        p.load_exl3_weight(torch.zeros(4, 3), expert_id=2, shard_id="w1")
    except RuntimeError as error:
        assert "initialized CUDA" in str(error)
    else:
        raise AssertionError("CPU target must fail closed")
finally:
    torch.Tensor.to = original
print("PASS R7 mixed widths, TP slicing, source lifetime, transfer arguments, CPU target refusal; CUDA execution untested")
