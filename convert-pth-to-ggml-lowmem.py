# convert-pth-to-ggml-lowmem.py
# 逐张量加载 .pth 文件，峰值内存 ~3 GB
# 用法: python3 convert-pth-to-ggml-lowmem.py models/7B 1

import sys, json, struct, os
import numpy as np
import torch
from sentencepiece import SentencePieceProcessor

dir_model = sys.argv[1]
ftype = int(sys.argv[2]) if len(sys.argv) > 2 else 1
ftype_str = ["f32", "f16"]

fname_hparams   = dir_model + "/params.json"
fname_tokenizer = dir_model + "/../tokenizer.model"
fname_out       = dir_model + "/ggml-model-" + ftype_str[ftype] + ".bin"

with open(fname_hparams, "r") as f:
    hparams = json.load(f)

tokenizer = SentencePieceProcessor(fname_tokenizer)
hparams.update({"vocab_size": tokenizer.vocab_size()})

fout = open(fname_out, "wb")

fout.write(struct.pack("i", 0x67676d6c))
fout.write(struct.pack("i", hparams["vocab_size"]))
fout.write(struct.pack("i", hparams["dim"]))
fout.write(struct.pack("i", hparams["multiple_of"]))
fout.write(struct.pack("i", hparams["n_heads"]))
fout.write(struct.pack("i", hparams["n_layers"]))
fout.write(struct.pack("i", hparams["dim"] // hparams["n_heads"]))
fout.write(struct.pack("i", ftype))

for i in range(32000):
    text = tokenizer.decode([29889, i]).encode('utf-8')
    text = text[1:]
    fout.write(struct.pack("i", len(text)))
    fout.write(text)

# 使用 mmap 模式逐张量读取，不一次性加载整个文件
pth_path = dir_model + "/consolidated.00.pth"
state_dict = torch.load(pth_path, map_location="cpu", mmap=True, weights_only=False)

for name, tensor in state_dict.items():
    if name[-5:] == "freqs":
        continue

    data = tensor.numpy().squeeze()
    n_dims = len(data.shape)

    ftype_cur = 1
    if ftype == 0 or n_dims == 1:
        data = data.astype(np.float32)
        ftype_cur = 0

    sname = name.encode('utf-8')
    fout.write(struct.pack("iii", n_dims, len(sname), ftype_cur))
    for i in range(n_dims):
        fout.write(struct.pack("i", data.shape[n_dims - 1 - i]))
    fout.write(sname)
    data.tofile(fout)

    print(f"  {name}: {list(data.shape)}")

fout.close()
print("Done: " + fname_out)