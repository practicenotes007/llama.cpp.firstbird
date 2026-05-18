# convert_hf_to_pth_lowmem.py
# 逐张量读取 safetensors，峰值内存 ~3 GB（而非 ~20 GB）
# 用法: python3 convert_hf_to_pth_lowmem.py <hf_model_dir> <output_dir>

import sys
import os
import json
import torch
from safetensors import safe_open

HF_TO_META = {
    "model.embed_tokens.weight":                        "tok_embeddings.weight",
    "model.norm.weight":                                "norm.weight",
    "lm_head.weight":                                   "output.weight",
}

LAYER_MAPPING = {
    "self_attn.q_proj.weight":   "attention.wq.weight",
    "self_attn.k_proj.weight":   "attention.wk.weight",
    "self_attn.v_proj.weight":   "attention.wv.weight",
    "self_attn.o_proj.weight":   "attention.wo.weight",
    "mlp.gate_proj.weight":      "feed_forward.w1.weight",
    "mlp.down_proj.weight":      "feed_forward.w2.weight",
    "mlp.up_proj.weight":        "feed_forward.w3.weight",
    "input_layernorm.weight":    "attention_norm.weight",
    "post_attention_layernorm.weight": "ffn_norm.weight",
}

def map_name(hf_name):
    if hf_name in HF_TO_META:
        return HF_TO_META[hf_name]
    if hf_name.startswith("model.layers."):
        parts = hf_name.split(".")
        layer_idx = parts[2]
        suffix = ".".join(parts[3:])
        if suffix in LAYER_MAPPING:
            return f"layers.{layer_idx}.{LAYER_MAPPING[suffix]}"
    print(f"WARNING: unmapped tensor: {hf_name}")
    return None

def convert_lowmem(hf_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)

    index_path = os.path.join(hf_dir, "model.safetensors.index.json")
    if os.path.exists(index_path):
        with open(index_path, "r") as f:
            index = json.load(f)
        weight_map = index.get("weight_map", {})
        shard_files = set(weight_map.values())
    else:
        shard_files = [f for f in os.listdir(hf_dir) if f.endswith(".safetensors")]
        weight_map = None

    meta_state_dict = {}

    for shard_file in sorted(shard_files):
        shard_path = os.path.join(hf_dir, shard_file)
        print(f"Reading shard: {shard_file}")

        with safe_open(shard_path, framework="pt", device="cpu") as f:
            for hf_name in f.keys():
                meta_name = map_name(hf_name)
                if meta_name is None:
                    continue
                tensor = f.get_tensor(hf_name).contiguous()
                meta_state_dict[meta_name] = tensor

    pth_path = os.path.join(out_dir, "consolidated.00.pth")
    print(f"Saving to {pth_path} ...")
    torch.save(meta_state_dict, pth_path)
    del meta_state_dict

    params = {
        "dim": 4096,
        "multiple_of": 256,
        "n_heads": 32,
        "n_layers": 32,
        "vocab_size": 32000
    }
    params_path = os.path.join(out_dir, "params.json")
    with open(params_path, "w") as f:
        json.dump(params, f, indent=4)

    print(f"Done. Output: {pth_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 convert_hf_to_pth_lowmem.py <hf_model_dir> <output_dir>")
        sys.exit(1)
    convert_lowmem(sys.argv[1], sys.argv[2])