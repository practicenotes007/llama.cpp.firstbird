# convert_hf_to_pth.py — 将 HuggingFace 格式权重转换为 Meta 原始格式
# 用法: python3 convert_hf_to_pth.py <hf_model_dir> <output_dir>

import sys
import json
import torch

def convert_hf_to_pth(hf_dir, out_dir):
    from transformers import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(hf_dir, torch_dtype=torch.float16)

    state_dict = model.state_dict()

    name_mapping = {
        "model.embed_tokens.weight":                    "tok_embeddings.weight",
        "model.layers.{i}.self_attn.q_proj.weight":     "layers.{i}.attention.wq.weight",
        "model.layers.{i}.self_attn.k_proj.weight":     "layers.{i}.attention.wk.weight",
        "model.layers.{i}.self_attn.v_proj.weight":     "layers.{i}.attention.wv.weight",
        "model.layers.{i}.self_attn.o_proj.weight":     "layers.{i}.attention.wo.weight",
        "model.layers.{i}.mlp.gate_proj.weight":        "layers.{i}.feed_forward.w1.weight",
        "model.layers.{i}.mlp.down_proj.weight":        "layers.{i}.feed_forward.w2.weight",
        "model.layers.{i}.mlp.up_proj.weight":          "layers.{i}.feed_forward.w3.weight",
        "model.layers.{i}.input_layernorm.weight":      "layers.{i}.attention_norm.weight",
        "model.layers.{i}.post_attention_layernorm.weight": "layers.{i}.ffn_norm.weight",
        "model.norm.weight":                            "norm.weight",
        "lm_head.weight":                               "output.weight",
    }

    new_state_dict = {}
    for hf_name, tensor in state_dict.items():
        matched = False
        for hf_pattern, meta_pattern in name_mapping.items():
            if "{i}" in hf_pattern:
                for i in range(32):
                    if hf_name == hf_pattern.format(i=i):
                        new_state_dict[meta_pattern.format(i=i)] = tensor
                        matched = True
                        break
            elif hf_name == hf_pattern:
                new_state_dict[meta_pattern] = tensor
                matched = True
                break
        if not matched:
            print(f"WARNING: unmapped tensor: {hf_name}")

    import os
    os.makedirs(out_dir, exist_ok=True)
    torch.save(new_state_dict, os.path.join(out_dir, "consolidated.00.pth"))

    params = {
        "dim": 4096,
        "multiple_of": 256,
        "n_heads": 32,
        "n_layers": 32,
        "vocab_size": 32000
    }
    with open(os.path.join(out_dir, "params.json"), "w") as f:
        json.dump(params, f, indent=4)

    print(f"Done. Output: {out_dir}/consolidated.00.pth")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 convert-hf-to-pth.py <hf_model_dir> <output_dir>")
        sys.exit(1)
    convert_hf_to_pth(sys.argv[1], sys.argv[2])