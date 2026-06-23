# Lessons Learned: 从 LLaMA-7B 到 Qwen2.5-1.5B 的全流程复盘

> 实验 #2: 下载 → 转换 → 量化 → 推理 → 报告
> 分支: iq3-experiment (llama.cpp 4c4cb3073 + first_bird merge)
> 历时: ~4 小时，约 10 轮迭代

---

## Timeline

| 轮次 | 步骤 | 耗时 | 结果 | 问题 |
|------|------|------|------|------|
| 1 | `hf download` | 5 min | 停滞 | CLI 已废弃，无输出 |
| 2 | `hf` (新 CLI) | 10 min | 停滞 | 输出被缓冲，实际在下载 |
| 3 | `snapshot_download` (Python) | 10 min | 停滞 | 默认 HF 源极慢 |
| 4 | `HF_ENDPOINT=hf-mirror.com` | 8 min | **成功** | — |
| 5 | convert HF→GGUF F16 | 3 min | 报错 | 缺少 `transformers`, `regex`, `safetensors` |
| 6 | pip install 依赖 | 2 min | 报错 | `transformers 5.10.1` 版本太新，API 不兼容 |
| 7 | 重新 convert | 3 min | 转换成功但推理失败 | `output.weight` 缺失 (tie_word_embeddings bug) |
| 8 | 修 convert 脚本 | 1 min | 修完但验证失败 | grep 过滤掉了关键输出行 |
| 9 | 删了 safetensors 再重下 | 15 min | 浪费时间 | 不应该提前清理中间产物 |
| 10 | 手动 patch GGUF 二进制 | 30 min | **失败** | GGUF 格式比预期复杂，numpy 2.0 兼容性 |
| 11 | 下载社区预量化 GGUF | 5 min | **成功** | 14.6 tok/s |

---

## Insight 1: 不要提前清理中间产物

> "完工后再清理" > "一边做一边清理"

第 8 轮修完 convert 脚本后，在第 9 轮之前的命令中顺手 `rm -f model.safetensors`，导致需要重新下载 3 GB。同样的错误在第 10 轮后重复了一次。

**规则**: 直到最终推理验证通过，**绝对不能删除**任何中间文件。磁盘空间足够的情况下，冗余比重复下载便宜得多。

---

## Insight 2: 下载镜像的价值不对等

| 方案 | 速度 | 可靠性 |
|------|------|--------|
| `huggingface.co` 直连 | 极慢 / 超时 | 不可靠 |
| `hf-mirror.com` | **5-10 MB/s** | 可靠 |
| 社区预量化 GGUF | 直接可用 | **最高** |

**结论**: 在受限网络环境下，始终优先使用镜像。如果目标是量化模型，直接下载社区预量化 GGUF 比"下载原始权重 → 转换 → 量化"快 10 倍。

---

## Insight 3: Python 包版本漂移是无声杀手

```
transformers 5.10.1 (2026) ← 和 2024 年的转换脚本不兼容
numpy 2.0             ← removed newbyteorder, broke gguf reader
torch 2.12            ← ok but unnecessarily new
```

`convert-hf-to-gguf.py` 是 2024 年 2 月的代码。pip 默认安装最新的 transformers/numpy 导致 API 不兼容。

**规则**: 对老代码仓库使用 `requirements.txt` 锁定版本，或先 `pip install transformers==4.38` 再跑转换。

---

## Insight 4: `grep` 过滤输出会隐藏关键错误

```bash
# 错误做法 (本次)
python3 convert.py ... 2>&1 | grep -E "output\.weight|successfully"
# → 转换成功但没有 output.weight，grep 静默跳过，难以排查

# 正确做法
python3 convert.py ... 2>&1 | tee convert.log
grep "output\.weight" convert.log
```

**规则**: 脚本输出先全量保存到日志文件，再用 grep 分析。管道过滤适合已知正确的流程，不适合首次调试。

---

## Insight 5: 手动 patch 二进制格式是最后手段

花 30 分钟尝试手动给 GGUF 添加 `output.weight` tensor —— 失败。GGUF V3 的 KV pair 格式有 8 种类型，tensor info 需要对齐，还有 numpy 2.0 兼容性问题。

**规则**: 当遇到格式兼容性问题时，优先级顺序：
1. 修复上游工具（本次: 修 convert-hf-to-gguf.py）← 一次修好，永久受益
2. 使用社区预构建产物（本次: huggingface GGUF）← 5 分钟解决
3. 手动 patch 二进制 ← 仅当前两者都不可行时

---

## Insight 6: 下载的是 Instruct 模型，测试的是 base prompt

```
模型: Qwen2.5-1.5B-**Instruct**
测试: ./main -p "Hello"  ← 没用 chat template
```

Instruct 模型需要用 `<|im_start|>system\n...<|im_end|>\n<|im_start|>user\n...<|im_end|>` 格式才能发挥最佳效果。简单 prompt 也能跑但质量可能下降。

**规则**: 区分 base model vs instruct model。Instruct 模型需要对应的 chat template。

---

## Insight 7: 空间与时间的经济学

| 操作 | 磁盘占用 | 时间 | 可跳过？ |
|------|---------|------|---------|
| 下载 HF 权重 | 3 GB | 5-10 min | 如果用预量化 GGUF: **是** |
| 转换 HF→F16 | 3 GB | 3 min | 如果用预量化 GGUF: **是** |
| 量化 F16→IQ3_XXS | 0.6 GB | 2.5 min | 如果用预量化 GGUF: **是** |
| 下载 Q4_K_M GGUF | 1.1 GB | 5 min | **一步到位** |

总链条: 下载 3 GB + 转换 3 GB + 量化 0.6 GB = 中转需 **~6 GB** 额外空间。
捷径: 直接下载 1.1 GB GGUF = **只需 1.1 GB**。

**当板子磁盘有限时（如 Orange Pi 5 的 57 GB），捷径的价值不是时间而是空间。**

---

## 总结

1. **中间产物守则**: 验证通过前不删除
2. **镜像优先**: hf-mirror.com, Modelscope
3. **版本锁定**: 老代码 + 老依赖 = 安全
4. **日志先行**: tee 全量 + grep 后分析
5. **捷径至上**: 社区预构建 > 修工具 > patch 二进制
6. **模型与模板匹配**: Instruct 模型需要 chat template
7. **空间即时间**: 直接下载 GGUF 省 5× 中转空间

---

*2026-06-05, iq3-experiment*
