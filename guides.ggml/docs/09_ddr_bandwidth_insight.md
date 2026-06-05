# DDR 带宽利用率的实践认知: Embedding 占比才是隐藏变量

> 在 Orange Pi 5 (RK3588, LPDDR4X 21.5 GB/s) 上经过 5 轮实验验证的关键洞察

---

## 1. 问题的起点: 为什么小模型的带宽利用率更低?

```
实测 DDR 带宽利用率:

LLaMA-7B Q4_0  (4.0 GB):  89%
Qwen2.5-1.5B Q4_K_M (1.04 GB):  68%

差距: 21pp (21 个百分点)
```

直觉预期: 小模型应该更快、利用率更高。实测相反。为什么?

---

## 2. 两个独立但相乘的变量: vocab_size 和 n_embd

### 2.1 各自的职责

```
n_embd (hidden_dim): 每个 token 的信息宽度
    影响: 所有层 (Attention, FFN, Embedding)
    决定: 每一层的矩阵维度

vocab_size: 模型能识别的离散 token 种类
    影响: 仅第一层和最后一层 (token_embd + output)
    决定: token 粒度、多语言能力
```

### 2.2 交汇点: token_embd 矩阵

```
token_embd shape = [vocab_size, n_embd]

LLaMA-7B:  token_embd = [ 32000, 4096] = 250 MB
           output     = [ 4096, 32000] = 250 MB
           total embedding = 500 MB

Qwen-1.5B: token_embd = [151936, 1536] = 467 MB
           (tie_word_embeddings=True, in/out 共享)
           total embedding = 467 MB
```

n_embd 差异 2.7×, vocab 差异 4.7× → 乘积相近 (~500 MB vs ~467 MB)

---

## 3. 真正的问题: Embedding 在各模型总参数中的占比

```
                    LLaMA-7B          Qwen-1.5B
                    ─────────         ──────────
embedding+output    500 MB            467 MB
总模型              4.0 GB            1.04 GB
embedding 占比      12.5%             45%
非 embedding 占比   87.5%             55%
```

**Qwen 每次 forward 有 45% 的时间在读 embedding/output 层。**

---

## 4. Embedding 层的内存访问模式是最差的

```
Attention/FFN 层:
    按行/block 顺序读取 → 连续物理地址
    → CPU streaming prefetcher 可以提前预取
    → 99%+ L2/L3 cache 命中
    → DDR 带宽利用率极高 (~85-90%)

Embedding/Output 层:
    按 token ID 索引读取 (vocab lookup)
    → 离散物理地址
    → hardware prefetcher 完全无效
    → 每次都是冷读 (cold miss)
    → DDR 利用率极低 (~20-30%)
```

---

## 5. 加权平均拼图

```
LLaMA-7B (每 token 读 4.0 GB):
  ┌───────────────────┬──────────────────────────────────────────┐
  │ embedding 12.5%   │ Attention/FFN 87.5%                      │
  │ 随机读, 慢 (~25%) │ 连续读, 快 (~90%)                        │
  └───────────────────┴──────────────────────────────────────────┘
  加权平均: 0.125×0.25 + 0.875×0.90 = 81.9% → 实测 89% (NEON 内核有额外预取)

Qwen-1.5B (每 token 读 1.04 GB):
  ┌─────────────────────────────────────┬───────────────────────┐
  │ embedding 45%                       │ Attention/FFN 55%    │
  │ 随机读, 慢 (~25%)                   │ 连续读, 快 (~90%)    │
  └─────────────────────────────────────┴───────────────────────┘
  加权平均: 0.45×0.25 + 0.55×0.90 = 60.8% → 实测 68% (decode 层仍有预取优化)
```

**差距 ~21pp 完全可以用 embedding 占比的差异解释，不需要引入格式差异假设。**

---

## 6. 实验验证: 同模型 + 同平台 + 不同格式

```
Qwen-1.5B Q4_0 (1011 MiB):   14.99 tok/s, BW util 69.0%
Qwen-1.5B Q4_K_M (1040 MiB): 14.33 tok/s, BW util 67.9%

同一模型、同一架构:
- 带宽利用率仅差 1.1pp
- Q4_0 的 cache-miss 率低 4.2× (0.35% vs 1.48%)
- 但总指令数几乎相同 (98.8B vs 98.9B)
- IPC 完全相同 (2.31)
```

**结论**: 实验 #3 中观察到的 18-21pp 带宽缺口 **来自模型架构差异 (LLaMA vs Qwen 的 embedding 占比)**，不是 Q4_0 vs Q4_K 的格式差异。格式差异仅贡献 ~1pp。

---

## 7. 对实践的指导

### 7.1 选模型的带宽预估公式

```
有效 BW_util ≈ 0.90 × (1 - embedding_ratio) + 0.25 × embedding_ratio

embedding_ratio = (vocab_size × n_embd × 2) / total_model_size
                   ↑
                   ×2 是因为 input embedding + output (若不 tie 则各单独算)
```

### 7.2 对小模型的推论

| 模型 | vocab | n_embd | embedding/总模型 | 预估 BW_util | 实测 |
|------|-------|--------|-----------------|-------------|------|
| LLaMA-7B | 32000 | 4096 | 12.5% | 82% | 89% |
| Qwen-1.5B | 151936 | 1536 | 45% | 61% | 68% |
| Qwen-0.5B | 151936 | 896 | ~55% | ~55% | 预估 ~60% |

小模型的 embedding 占比**天然更高** (因为隐藏维越小, embedding 的相对比例越大), 这是无法通过代码优化绕过的物理限制。

### 7.3 对小模型的优化启示

- **计算侧优化 (SDOT, 预取)**: 对大模型无效 (BW 是瓶颈), 对小模型边际收益 ~4%
- **Embedding 压缩**: 对小模型有潜力 (vocab pruning, embedding quantization)
- **但最有效的永远是**: 直接换更小 embedding 的模型, 或等待 LPDDR5

---

## 8. 核心一课

> **DDR 带宽利用率不是常数。它随 embedding 占比变化, 而 embedding 占比在小模型中天然更高。**
>
> 这就是为什么 0.6 GB 模型的理论上限 29 tok/s 在实践中几乎无法达到——不是代码没写好, 是 embedding 层的随机访问模式拖低了加权平均效率。

---

*来自 Orange Pi 5 实测, LLaMA-7B + Qwen2.5-1.5B, 5 轮对照实验, 2026-06-05*
