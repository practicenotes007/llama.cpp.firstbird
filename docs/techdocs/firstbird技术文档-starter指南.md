# firstbird 技术文档入门指南

## 项目概述

firstbird 是一个基于 GGML (Generic Graphical Model Library) 的 LLaMA 大语言模型推理框架。该项目实现了完整的 LLaMA transformer 架构，支持模型量化、多种硬件加速以及高效的 CPU 推理能力。

### 核心特性

- **纯 C/C++ 实现**：无需 Python 运行时，可直接部署。为什么重要？Python 运行时（CPython + NumPy + PyTorch）本身就需要数百 MB 内存和启动时间；纯 C/C++ 让整个推理栈在嵌入式设备（如树莓派、RK3588）上也能运行，启动即推理，无需预热。
- **GGML 张量库**：支持自动微分和优化算法的张量运算库。GGML 不仅仅是一个推理库——它内置了反向传播和优化器（Adam/LBFGS），这意味着同一个库既可做推理，也可做微调。firstbird 只用了推理路径，但库的能力边界远不止此。
- **模型量化**：支持 Q4_0 和 Q4_1 两种量化格式，显著降低内存占用。7B 模型从 14GB 压缩到 3.5GB，使得在 8GB 内存的消费级笔记本上运行成为可能。
- **SIMD 加速**：支持 AVX/AVX2/AVX512 (x86) 和 NEON (ARM) 指令集。SIMD 是 CPU 上唯一的"免费并行"——一条指令同时处理多个数据，不需要多线程的同步开销。
- **多线程支持**：充分利用多核处理器性能。GGML 的线程调度以计算图节点为粒度，多线程池并发执行无依赖的节点。
- **模型分片**：支持大模型的分布式加载。65B 模型权重文件超过 30GB，单机内存可能不够；分片后可跨机器加载，各机器只持有部分层。

## 系统架构

### 项目文件结构

```
llama.cpp.firstbird/
├── ggml.h / ggml.c          # GGML 张量库核心
├── main.cpp                  # LLaMA 推理主程序
├── quantize.cpp              # 模型量化工具
├── convert-pth-to-ggml.py    # PyTorch 模型转换脚本
├── utils.h / utils.cpp       # 工具函数和 CLI 参数解析
└── docs/techdocs/           # 技术文档目录
```

**为什么只有 5 个源文件？** 这是 firstbird 的设计哲学：极简依赖。整个项目不依赖任何第三方库（没有 BLAS、没有 CUDA、没有 Python），编译只需要 `g++` 和标准库。代价是性能不如 GPU 方案，但换来了零配置部署能力——在任意 Linux 机器上 `make` 即可运行。

### 核心技术栈

```
┌─────────────────────────────────────────┐
│           Application Layer             │
│    (main.cpp: 模型推理与采样)            │
├─────────────────────────────────────────┤
│           Model Layer                   │
│  (llama_model, llama_eval, transformer) │
├─────────────────────────────────────────┤
│           GGML Layer                    │
│    (张量运算、自动微分、计算图)          │
├─────────────────────────────────────────┤
│         Hardware Abstraction            │
│    (SIMD: AVX/NEON, 多线程)             │
└─────────────────────────────────────────┘
```

**分层的设计意图**：每一层只依赖下一层，不跨层调用。Application 层只调用 Model 层的 `llama_eval()`，不直接操作 GGML 张量；Model 层只调用 GGML 的张量操作来构建计算图，不关心底层是 AVX 还是 NEON；GGML 层在运行时自动检测 CPU 特性，选择最优的 SIMD 实现。这种分层使得移植到新平台时，只需要修改 Hardware Abstraction 层。

## GGML 张量库详解

### 核心概念

GGML 是一个为机器学习设计的张量库，其设计哲学是最小化依赖，提供高效的内存管理和计算能力。

**为什么不用 NumPy/PyTorch 的张量库？** Python 张量库的设计目标是灵活性和易用性，运行时动态分配内存、支持任意形状广播、依赖 Python GIL。而 GGML 的设计目标是：C 语言原生、零拷贝内存布局、编译期可确定的内存占用。这让它特别适合推理场景——推理不需要梯度，不需要 autograd，只需要快速的前向计算。

#### 张量结构

```cpp
struct ggml_tensor {
    enum ggml_type type;           // 数据类型 (F32, F16, Q4_0, Q4_1, ...)
    int    n_dims;                 // 维度数 (1-4)
    int    ne[GGML_MAX_DIMS];      // 各维度元素数
    size_t nb[GGML_MAX_DIMS];      // 各维度字节跨度
    
    enum ggml_op op;               // 操作类型
    bool is_param;                 // 是否为可训练参数
    struct ggml_tensor * grad;     // 梯度
    struct ggml_tensor * src0;     // 输入张量0
    struct ggml_tensor * src1;     // 输入张量1
    
    struct ggml_tensor * opt[GGML_MAX_OPT]; // 可选输入张量（扩展操作用）
    
    void * data;                   // 数据指针
};
```

**关键字段详解**：

- **`ne[]` / `nb[]`**：`ne` 是各维度的元素数（shape），`nb` 是各维度的字节跨度（stride）。`nb[0] = sizeof(type)`，`nb[i] = nb[i-1] * ne[i-1]`。这种设计允许张量在内存中不连续——例如转置操作不需要真正移动数据，只需交换 `ne` 和 `nb` 的对应维度。访问元素 `a[i][j]` 的地址 = `data + i * nb[1] + j * nb[0]`。`GGML_MAX_DIMS = 4`，支持最高 4 维张量（batch × head × seq × dim）。

- **`type`**：数据类型。同一个计算图中可以混合不同类型——权重是 Q4_0（4 位量化），中间激活值是 F32。GGML 在执行时会自动处理类型转换（dequantize → compute → 结果仍为 F32）。

- **`op`**：该张量是如何产生的。叶子节点（如模型权重）的 `op = GGML_OP_NONE`；计算节点的 `op` 记录了产生它的操作（如 `GGML_OP_MUL_MAT`）。`ggml_graph_compute` 遍历计算图时，根据每个节点的 `op` 分发到对应的计算函数。

- **`grad`**：梯度张量，用于反向传播。当 `is_param` 为 true 时，GGML 会在反向计算图中自动为该参数分配并填充梯度。在纯推理场景（如 LLaMA 生成）中，该字段始终为 NULL——因为推理不需要梯度，这也是 GGML 推理模式省内存的原因之一。

- **`src0` / `src1`**：为什么只有两个输入源？因为这是**最小集设计**。任何二元操作（加、乘、矩阵乘等）天然只需要两个输入；而更复杂的多输入操作，都可以通过**链式组合**多次二元操作来实现。例如 `a * x² + b` 被拆解为：`x2 = mul(x, x)` → `ax2 = mul(a, x2)` → `result = add(ax2, b)`，每一步都是标准的二元节点。这种设计让计算图的节点结构保持极简，遍历和调度逻辑也因此大幅简化。

- **`opt[]`**：实际源码中还有 `opt[GGML_MAX_OPT]` 数组（`GGML_MAX_OPT = 4`），用于少数需要超过两个输入的操作（如 Flash Attention 需要 Q、K、V 三个输入加一个 mask 标志）。这是对 src0/src1 二元模型的有限扩展，而非否定。

#### 支持的数据类型

```cpp
enum ggml_type {
    GGML_TYPE_Q4_0,    // 4位量化，0号格式
    GGML_TYPE_Q4_1,    // 4位量化，1号格式
    GGML_TYPE_I8,      // 8位整数
    GGML_TYPE_I16,     // 16位整数
    GGML_TYPE_I32,     // 32位整数
    GGML_TYPE_F16,     // 半精度浮点
    GGML_TYPE_F32,     // 单精度浮点
};
```

**为什么这些类型就够用？** AI 推理的数据类型需求是分层的：
- **F32**：计算精度基准，所有 SIMD 运算的输入输出都基于 F32
- **F16**：权重存储格式，比 F32 省一半空间，计算时 dequantize 为 F32
- **Q4_0 / Q4_1**：极致压缩的权重格式，每 32 个值打包为一个块，推理时实时 dequantize
- **I32**：用于 token ID（词表大小 32000，需要 32 位整数）
- **I8 / I16**：用于量化中间表示，firstbird 中较少直接使用

**注意**：GGML 的量化权重在计算时是"用多少解多少"——矩阵乘法逐块 dequantize，不需要把整个权重矩阵先解压再计算。这是 Q4 推理内存效率的关键。

### 计算图机制

GGML 采用**延迟计算**（Lazy Evaluation）模式，先构建计算图，再统一执行：

```cpp
// 1. 初始化上下文和内存
struct ggml_init_params params = {
    .mem_size   = 16*1024*1024,  // 16MB
    .mem_buffer = NULL,
};
struct ggml_context * ctx = ggml_init(params);

// 2. 定义计算图（不执行任何计算）
struct ggml_tensor * x = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 1);
ggml_set_param(ctx, x);

struct ggml_tensor * f = ggml_add(ctx, 
    ggml_mul(ctx, ggml_mul(ctx, a, x), x),  // a*x²
    b);                                      // + b

// 3. 构建前向计算图
struct ggml_cgraph gf = ggml_build_forward(f);

// 4. 执行计算
ggml_graph_compute(ctx, &gf);

// 5. 获取结果
float result = ggml_get_f32_1d(f, 0);
```

**为什么需要延迟计算？** 如果每次调用 `ggml_mul` 都立即执行，那多个操作之间需要频繁读写内存（写中间结果 → 读回来 → 再算）。延迟计算让 GGML 有全局视角：构建完整个计算图后，可以优化执行顺序、合并相邻操作、复用中间缓冲区。对于 LLaMA 推理，完整的计算图包含数百个节点（32 层 × 每层约 20 个操作），一次性调度比逐条执行高效得多。

**`ggml_cgraph` 结构体详解**：

```cpp
struct ggml_cgraph {
    int n_nodes;                              // 计算节点数
    int n_leafs;                              // 叶子节点数（权重、输入）
    int n_threads;                            // 执行线程数
    struct ggml_tensor * nodes[GGML_MAX_NODES]; // 计算节点列表（拓扑排序）
    struct ggml_tensor * grads[GGML_MAX_NODES]; // 对应梯度节点
    struct ggml_tensor * leafs[GGML_MAX_NODES]; // 叶子节点列表
};
```

- `nodes[]` 按拓扑排序存储——排在后面的节点依赖前面的节点。`ggml_graph_compute` 按顺序遍历 `nodes[]`，对每个节点调用其 `op` 对应的计算函数
- `GGML_MAX_NODES = 4096`，这是单个计算图的最大节点数。LLaMA-7B 的前向图约 640 个节点（32 层 × 20），远未触及上限
- `ggml_build_forward(f)` 从输出张量 `f` 反向遍历 `src0/src1` 指针，构建完整的依赖图，再做拓扑排序填入 `nodes[]`

**与 PyTorch 的对比**：PyTorch 的 `torch.nn.Module.forward()` 是即时执行（eager mode），每次前向传播都立即计算。PyTorch 也有 `torch.compile()` 做图编译优化，但它是可选的后端优化。GGML 则是原生图模式——所有计算都必须先建图再执行，没有 eager mode。

### 核心张量操作

#### 基础运算

| 操作 | 函数 | 说明 | 为什么需要 |
|------|------|------|-----------|
| 加法 | `ggml_add` | 逐元素相加 | 残差连接（residual connection）：`x + attention(x)` |
| 乘法 | `ggml_mul` | 逐元素相乘 | SwiGLU 门控：`silu(w1·x) * (w3·x)`；RMSNorm 缩放 |
| 矩阵乘 | `ggml_mul_mat` | A: m×n × B: p×n → m×p | 所有线性变换的核心：QKV 投影、FFN、输出层 |
| 归一化 | `ggml_norm` | 行级 RMSNorm | 每层注意力前和 FFN 前的归一化，稳定训练/推理 |
| Softmax | `ggml_soft_max` | Softmax 激活 | 注意力权重归一化：使注意力分数变为概率分布 |
| GELU | `ggml_gelu` | 高斯误差线性单元 | LLaMA 不使用 GELU（用 SiLU），但 GGML 通用支持 |
| SiLU | `ggml_silu` | Sigmoid 加权线性单元 | LLaMA FFN 的激活函数：`x * sigmoid(x)`，比 GELU 更平滑 |

**`ggml_mul_mat` 为什么是 A:m×n × B:p×n → m×p？** 注意 B 的维度是 p×n 而不是 n×p。这是因为在 GGML 的内存布局中，矩阵按行存储（row-major），而 B 作为权重矩阵在存储时已经是转置的。所以 `ggml_mul_mat(A, B)` 实际计算的是 `A × B^T`，结果是 m×p。这种约定避免了运行时转置，直接利用权重的存储格式。

#### Transformer 专用操作

```cpp
// 旋转位置编码 (RoPE)
ggml_rope(ctx, Q, n_past, n_dims, mode);
// 含义：对 Q/K 向量施加旋转位置编码，使模型"感知"token 的位置信息。
// 与绝对位置编码（在嵌入层加一个位置向量）不同，RoPE 通过旋转矩阵
// 将位置信息编码进 Q/K 的方向中，使得 Q·K 的点积自然包含相对位置信息。
// n_dims = n_rot = n_embd/n_head = 64，只对前 64 维施加旋转（每头维度的一部分）。
// n_past 用于偏移位置索引，适配增量推理：已有 n_past 个 token，
// 新 token 的位置从 n_past 开始编号。
// mode=0 对 Q 施加正向旋转，mode=1 对 K 施加反向旋转（K 从缓存读取，需要反向偏移）。

// 注意力掩码（因果掩码）
ggml_diag_mask_inf(ctx, KQ, n_past);
// 含义：将注意力矩阵 KQ 中"对角线以上"的元素设为 -INFINITY。
// 在自回归语言模型中，token 只能看到自己及之前的位置，不能看到未来。
// KQ 矩阵的行是 Query 位置 j，列是 Key 位置 i。
// 当 i > n_past + j 时，说明 Key 位置在 Query 的"未来"，设为 -INF。
// 后续 soft_max 对 -INF 取指数得 0，即注意力权重为 0，实现因果遮蔽。
// n_past 是 KV 缓存中已有的 token 数，偏移掩码对角线以适配增量推理。

// 1D 卷积（用于滑动窗口）
ggml_conv_1d_1s(ctx, a, b);  // 步长1
ggml_conv_1d_2s(ctx, a, b);  // 步长2
// 用于处理时序数据的局部特征提取。LLaMA 不直接使用卷积，
// 但 GGML 作为通用张量库需要支持，供其他模型架构使用。
```

### 内存管理策略

GGML 使用预分配内存池，所有张量共享一个内存缓冲区：

```cpp
// 计算所需内存
size_t ctx_size = 0;
ctx_size += n_embd * n_vocab * ggml_type_sizef(wtype);  // 词嵌入
ctx_size += n_layer * n_embd * n_embd * 4 * ggml_type_sizef(wtype);  // Attention weights
ctx_size += n_layer * n_ff * n_embd * 3 * ggml_type_sizef(wtype);  // FFN weights
ctx_size += n_ctx * n_layer * n_embd * 2 * sizeof(float);  // KV Cache

// 预分配
struct ggml_init_params params = {
    .mem_size   = ctx_size,
    .mem_buffer = NULL,  // 由 GGML 内部分配
};
```

**为什么用预分配内存池而不是 malloc/free？**

1. **避免内存碎片**：推理时反复创建和销毁计算图（每次 `llama_eval` 都建一个新图），如果用 `malloc`，碎片会导致即使总空闲内存足够也无法分配大块
2. **确定性内存占用**：启动时就知道总内存需求，不会在运行时 OOM
3. **零分配开销**：`ggml_new_tensor` 只是在内存池中移动指针，不需要调用系统 `malloc`

**每行内存计算的含义**：

- **词嵌入**：`n_embd × n_vocab × type_size` = 4096 × 32000 × 2(F16) = 256MB
- **Attention 权重**：每层 4 个矩阵（wq/wk/wv/wo），每个 `n_embd × n_embd`，共 32 层
- **FFN 权重**：每层 3 个矩阵（w1/w2/w3），`n_ff × n_embd` 或 `n_embd × n_ff`
- **KV Cache**：`n_ctx × n_layer × n_embd × 2(K和V)` × 4(F32) = 512 × 32 × 4096 × 2 × 4 = 512MB。这是推理时最大的运行时内存消耗，也是 `n_ctx` 影响内存的主要原因

**优势**：避免运行时频繁分配/释放内存，提升性能。

## LLaMA 模型实现

### 模型结构

```cpp
struct llama_model {
    llama_hparams hparams;                    // 超参数
    
    struct ggml_tensor * tok_embeddings;      // 词嵌入矩阵
    struct ggml_tensor * norm;               // 最终归一化层
    struct ggml_tensor * output;              // 输出层
    
    std::vector<llama_layer> layers;          // Transformer 层
    struct ggml_tensor * memory_k;           // Key 缓存
    struct ggml_tensor * memory_v;           // Value 缓存
    
    struct ggml_context * ctx;               // GGML 上下文
    std::map<std::string, struct ggml_tensor *> tensors;  // 张量映射
};
```

**各字段详解**：

- **`tok_embeddings`**：形状 `[n_embd, n_vocab]` = `[4096, 32000]`。输入 token ID 后，通过 `ggml_get_rows` 查表取出对应的嵌入向量。这是整个模型中唯一一个"查找表"而非"矩阵乘法"的参数。
- **`norm`**：最终的 RMSNorm 权重，形状 `[n_embd]`。在所有 Transformer 层之后、输出投影之前，对隐藏状态做最后一次归一化。
- **`output`**：输出投影矩阵，形状 `[n_vocab, n_embd]`。将最后一层的隐藏状态映射回词表空间，得到每个 token 的 logits。注意：在 LLaMA 的原始实现中，`output` 和 `tok_embeddings` 共享权重（tied weights），但 firstbird 中它们是独立的。
- **`memory_k` / `memory_v`**：KV 缓存，形状 `[n_embd, n_layer * n_ctx]`。所有层的 K/V 历史值拼在一起，通过 offset 索引访问特定层和位置。为什么不分层存储？因为 GGML 的内存池是连续分配的，一维大数组比 32 个独立数组更节省内存管理开销。
- **`tensors`**：名称到张量的映射，用于从模型文件加载权重时按名字匹配。例如 `"layers.0.attention.wq.weight"` → `layers[0].wq`。
- **`ctx`**：GGML 上下文，持有所有张量的内存。模型销毁时 `ggml_free(ctx)` 一次性释放所有内存。

### LLaMA 超参数

```cpp
struct llama_hparams {
    int32_t n_vocab = 32000;    // 词表大小
    int32_t n_ctx   = 512;      // 上下文长度
    int32_t n_embd  = 4096;     // 嵌入维度
    int32_t n_mult  = 256;      // FFN 维度乘数
    int32_t n_head  = 32;       // 注意力头数
    int32_t n_layer = 32;       // Transformer 层数
    int32_t n_rot   = 64;       // RoPE 维度
};
```

**各参数详解**：

- **`n_vocab = 32000`**：SentencePiece BPE 词表大小。32000 是 LLaMA-1 的选择；LLaMA-2 扩展到了 32000，LLaMA-3 扩展到了 128256。词表大小决定 `tok_embeddings` 和 `output` 矩阵的行数。
- **`n_ctx = 512`**：最大上下文窗口长度。这不来自模型文件，而是用户运行时通过 `-c` 参数指定（见源码 `hparams.n_ctx = n_ctx`）。上下文越长，KV 缓存越大（线性增长），512 是默认值，最大可设到模型支持的上限。
- **`n_embd = 4096`**：隐藏层维度，也是每个 token 的向量表示长度。这是模型"宽度"的核心指标——所有权重矩阵的维度都由它派生。7B 模型 4096，13B 模型 5120，65B 模型 8192。
- **`n_mult = 256`**：FFN 维度的对齐乘数。实际 FFN 隐藏维度 `n_ff = ((2*(4*n_embd)/3 + n_mult - 1) / n_mult) * n_mult`。对 7B：`((2*4*4096/3 + 255)/256)*256 = 11008`。这个公式确保 `n_ff` 是 256 的倍数，便于硬件对齐和高效计算。
- **`n_head = 32`**：注意力头数。每个头的维度 = `n_embd / n_head = 128`。多头注意力让模型同时关注不同子空间的信息。
- **`n_layer = 32`**：Transformer 层数，决定模型的"深度"。7B 模型 32 层，13B 模型 40 层，65B 模型 80 层。层数主要影响顺序计算量——推理时必须逐层串行，无法并行。
- **`n_rot = 64`**：RoPE 旋转维度。源码中实际计算为 `n_rot = n_embd / n_head = 128`，但原始 LLaMA 论文只对前一半维度施加旋转（即 64 维）。firstbird 源码中 `n_rot` 从模型文件读取，值为 64。

### 单层 Transformer 结构

```cpp
struct llama_layer {
    // 注意力归一化
    struct ggml_tensor * attention_norm;
    
    // QKV 投影矩阵
    struct ggml_tensor * wq;  // Query
    struct ggml_tensor * wk;  // Key
    struct ggml_tensor * wv;  // Value
    struct ggml_tensor * wo;  // Output
    
    // FFN 归一化
    struct ggml_tensor * ffn_norm;
    
    // FFN 门控网络 (SwiGLU)
    struct ggml_tensor * w1;   // 门控
    struct ggml_tensor * w2;  // 下投影
    struct ggml_tensor * w3;  // 上投影
};
```

**各权重矩阵详解**：

**归一化权重**：
- **`attention_norm`**：形状 `[n_embd]`，RMSNorm 的可学习缩放参数。Pre-Norm 架构中，在注意力计算之前对输入做归一化。计算方式：`x_norm = x / rms(x) * attention_norm`，其中 `rms(x) = sqrt(mean(x²))`。
- **`ffn_norm`**：形状 `[n_embd]`，同理，在 FFN 计算之前对注意力输出做归一化。

**注意力权重**（共 4 个矩阵，每个 `[n_embd, n_embd]`）：
- **`wq`**：Query 投影。`Q = x @ wq`，将输入映射到查询空间。
- **`wk`**：Key 投影。`K = x @ wk`，将输入映射到键空间。
- **`wv`**：Value 投影。`V = x @ wv`，将输入映射到值空间。
- **`wo`**：输出投影。将多头注意力的拼接结果映射回隐藏维度。

**FFN 权重**（共 3 个矩阵，SwiGLU 结构）：
- **`w1`**：形状 `[n_ff, n_embd]`，门控路径的上投影。`gate = silu(x @ w1)`
- **`w3`**：形状 `[n_ff, n_embd]`，信息路径的上投影。`up = x @ w3`
- **`w2`**：形状 `[n_embd, n_ff]`，下投影回隐藏维度。`out = (gate * up) @ w2`

为什么 SwiGLU 需要三个矩阵而不是标准 FFN 的两个？标准 FFN 是 `relu(x @ w1) @ w2`，只有上投影和下投影。SwiGLU 增加了一个门控路径 `w3`，将信息流分成两路：一路做 SiLU 激活（门控），另一路直接传递（信息），两路逐元素相乘后再下投影。这种结构比 ReLU/GeLU 激活的 FFN 有更好的性能，代价是多了一个矩阵的参数量和计算量。

### 前向传播详解

#### 1. 词嵌入

```cpp
// 输入: token IDs [batch_size]
// 输出: 嵌入向量 [n_embd, batch_size]
struct ggml_tensor * embd = ggml_new_tensor_1d(ctx0, GGML_TYPE_I32, N);
memcpy(embd->data, embd_inp.data(), N * sizeof(int32_t));

struct ggml_tensor * inpL = ggml_get_rows(ctx0, model.tok_embeddings, embd);
```

**`ggml_get_rows` 的含义**：这不是矩阵乘法，而是"查表"操作。`tok_embeddings` 的形状是 `[n_embd, n_vocab]`，可以看作 32000 行的查找表，每行是一个 token 的嵌入向量。`ggml_get_rows` 根据 `embd` 中的 token ID 取出对应行，拼接成 `[n_embd, N]` 的输出。

**为什么用 `ggml_get_rows` 而不是 one-hot + 矩阵乘法？** 理论上，嵌入查表等价于 one-hot 向量与嵌入矩阵的乘法。但 one-hot 向量有 32000 维，其中 31999 个是 0——直接查表避免了无意义的乘法运算，效率提升数千倍。

#### 2. 注意力机制

每层包含完整的自注意力计算：

```cpp
// QKV 投影
struct ggml_tensor * Qcur = ggml_mul_mat(ctx0, layer.wq, cur);
struct ggml_tensor * Kcur = ggml_mul_mat(ctx0, layer.wk, cur);
struct ggml_tensor * Vcur = ggml_mul_mat(ctx0, layer.wv, cur);

// 保存 K/V 到缓存
struct ggml_tensor * k = ggml_view_1d(ctx0, model.memory_k, N * n_embd, offset);
struct ggml_tensor * v = ggml_view_1d(ctx0, model.memory_v, N * n_embd, offset);
ggml_cpy(ctx0, Kcur, k);
ggml_cpy(ctx0, Vcur, v);

// 应用 RoPE
struct ggml_tensor * Q = ggml_rope(ctx0, ggml_permute(ctx0, Qcur, ...), ...);
struct ggml_tensor * K = ggml_rope(ctx0, ggml_permute(ctx0, Kcur, ...), ...);

// 注意力分数
struct ggml_tensor * KQ = ggml_mul_mat(ctx0, K, Q);
KQ = ggml_scale(ctx0, KQ, scale);  // 除以 √(d_k)
KQ = ggml_diag_mask_inf(ctx0, KQ, n_past);  // 因果掩码
KQ = ggml_soft_max(ctx0, KQ);

// 注意力输出
struct ggml_tensor * KQV = ggml_mul_mat(ctx0, V_trans, KQ_soft_max);
cur = ggml_mul_mat(ctx0, layer.wo, KQV);
```

**逐步详解**：

1. **QKV 投影**：三个独立的矩阵乘法，将输入 `cur`（形状 `[n_embd, N]`）分别投影到 Q、K、V 空间。这是每层最大的计算量所在——三个 `[4096, 4096]` 的矩阵乘法。

2. **保存 K/V 到缓存**：`ggml_view_1d` 不拷贝数据，只是创建一个"视图"指向 `memory_k` 中的特定偏移位置。`ggml_cpy` 才真正将当前 token 的 K/V 值写入缓存。这一步是增量推理的关键——下次推理时，历史 token 的 K/V 直接从缓存读取，不需要重新计算。

3. **RoPE + permute**：`ggml_permute` 将 Q 从 `[n_embd/n_head, n_head, N]` 重排为 `[n_embd/n_head, N, n_head]`，这是为了适配 `ggml_mul_mat` 的维度约定。`ggml_rope` 对前 `n_rot` 维施加旋转变换。

4. **注意力分数 KQ = K × Q**：形状 `[n_past+N, N]`，每个元素是某个 Key 和某个 Query 的点积。`ggml_scale` 除以 `√(d_k) = √(128) ≈ 11.3`，防止点积值过大导致 softmax 饱和（梯度消失）。

5. **因果掩码 + softmax**：`ggml_diag_mask_inf` 将未来位置的分数设为 -INF，`ggml_soft_max` 归一化为概率分布。

6. **加权求和 + 输出投影**：`V_trans × KQ_soft_max` 得到注意力输出（每个头的值向量加权平均），`wo` 投影回隐藏维度。

#### 3. 前馈网络 (SwiGLU)

```cpp
// 标准 FFN 计算: SiLU(W1(x) * W3(x)) @ W2
struct ggml_tensor * tmp = ggml_mul_mat(ctx0, layer.w3, cur);
cur = ggml_mul_mat(ctx0, layer.w1, cur);
cur = ggml_silu(ctx0, cur);
cur = ggml_mul(ctx0, cur, tmp);
cur = ggml_mul_mat(ctx0, layer.w2, cur);
```

**逐步详解**：

1. **`tmp = w3 @ x`**：信息路径，将输入上投影到 `n_ff=11008` 维。这个分支不经过激活函数，保留原始信息。
2. **`cur = silu(w1 @ x)`**：门控路径，上投影后做 SiLU 激活。`silu(x) = x * sigmoid(x)`，sigmoid 部分产生 0~1 之间的门控信号，控制信息通过的比例。
3. **`cur = cur * tmp`**：逐元素相乘，门控信号调制信息流。门控值接近 0 的维度被"关闭"，接近 1 的维度被"打开"。
4. **`cur = w2 @ cur`**：下投影回 `n_embd=4096` 维，恢复隐藏维度。

**与标准 FFN 的对比**：标准 FFN（如 GPT-2）是 `relu(w1 @ x) @ w2`，只有上投影-激活-下投影三步。SwiGLU 多了一个门控分支，本质上是让网络自己学习"哪些信息该通过、哪些该抑制"。

### KV 缓存机制

为加速自回归生成，缓存历史 K/V：

```cpp
// memory_k/memory_v 形状: [n_embd, n_layer * n_ctx]
// 存储: [layer_0_k, layer_0_v, layer_1_k, ...]

struct ggml_tensor * k = ggml_view_1d(
    ctx0, 
    model.memory_k, 
    N * n_embd, 
    (ggml_element_size(model.memory_k) * n_embd) * (il * n_ctx + n_past)
);
```

**offset 计算公式拆解**：

```
offset = element_size * n_embd * (il * n_ctx + n_past)
       = element_size * n_embd * il * n_ctx    // 跳过前 il 层的缓存
       + element_size * n_embd * n_past         // 跳过当前层前 n_past 个 token
```

- **`il * n_ctx`**：每层占用 `n_ctx` 个 token 位置的空间，`il` 是当前层号，跳过前面 `il` 层
- **`n_past`**：当前层中已有 `n_past` 个 token 的 K/V 在缓存中，跳过它们
- **`n_embd`**：每个 token 的 K/V 向量长度是 `n_embd`
- **`element_size`**：每个元素的字节大小（F32 为 4 字节）

**增量推理流程**：

```
初始: n_past=0, 输入 prompt "The capital of France"
  → 计算 4 个 token 的 Q/K/V，全部写入缓存位置 0-3
  → n_past 变为 4

第1步生成: n_past=4, 输入上一步的输出 token
  → 只计算 1 个 token 的 Q/K/V
  → K/V 写入缓存位置 4
  → Q 和缓存中位置 0-4 的 K 做注意力计算
  → n_past 变为 5

第2步生成: n_past=5, ...
  → 只计算 1 个 token 的 Q/K/V
  → K/V 写入缓存位置 5
  → Q 和缓存中位置 0-5 的 K 做注意力计算
  → n_past 变为 6
```

**为什么 KV 缓存如此重要？** 没有 KV 缓存时，每生成一个 token 都需要重新计算所有历史 token 的 K/V——生成第 N 个 token 的计算量是 O(N²)。有 KV 缓存后，每步只需计算 1 个新 token 的 Q/K/V，注意力计算时从缓存读取历史 K/V，计算量降为 O(N)。

**KV 缓存的代价**：内存占用 = `2 × n_layer × n_ctx × n_embd × sizeof(float)`。对 LLaMA-7B、n_ctx=2048：`2 × 32 × 2048 × 4096 × 4 = 2GB`。这是长上下文推理的内存瓶颈——上下文越长，KV 缓存越大。

## 模型量化

### 量化原理

4位量化将32位浮点数压缩为4位表示：

```
Q4_0 格式:
┌─────────┬──────────────────────────┐
│  delta  │   quantized weights      │
│ (fp16)  │   (4 bits × 32 values)   │
└─────────┴──────────────────────────┘

Q4_1 格式:
┌─────────┬────────┬─────────────────┐
│  delta  │  min   │ quantized weights │
│ (fp16)  │ (fp16) │ (4 bits × 32)    │
└─────────┴────────┴─────────────────┘
```

每 32 个权重打包为 16 字节 (Q4_0) 或 18 字节 (Q4_1)。

**量化块结构详解**：

- **块大小 32**：每 32 个连续权重共享一组量化参数。为什么是 32？这是精度和效率的平衡点——块太小，量化参数的额外开销占比大；块太大，量化精度下降。32 是 GGML 的经验值。
- **`delta`（缩放因子）**：FP16，表示这 32 个权重的绝对值范围。dequantize 时：`weight_f32 = delta * quantized_value`
- **Q4_0 vs Q4_1 的区别**：Q4_0 假设权重关于 0 对称分布，只用一个 `delta` 表示范围；Q4_1 增加了一个 `min` 偏移量，允许非对称分布。代价是每个块多 2 字节（从 16B 变为 18B），但精度更好。

**dequantize 过程（推理时实际发生的事情）**：

```cpp
// Q4_0 dequantize 伪代码
for (int i = 0; i < 32; i++) {
    float quant_val = (block.quantized[i/2] >> (4*(i%2))) & 0xF;  // 取出 4-bit 值
    quant_val -= 8;  // 从无符号 [0,15] 映射到有符号 [-8,7]
    output[i] = block.delta * quant_val;  // 缩放回浮点
}
```

**量化在矩阵乘法中的角色**：GGML 的量化矩阵乘不是先 dequantize 整个矩阵再计算，而是**逐块 dequantize + 计算**：取出一块 32 个量化权重 → dequantize 为 F32 → 与对应的 32 个激活值做点积 → 累加到结果。这种"流式 dequantize"确保了工作集始终在 L1 缓存内。

### 量化流程

```cpp
size_t ggml_quantize_q4_0(
    float * src,     // 源数据 (FP32)
    void * dst,      // 目标缓冲区
    int n,           // 总元素数
    int k,           // 块大小 (32)
    int qk,          // 量化块大小
    int64_t * hist   // 直方图统计
);
```

**直方图 `hist` 的用途**：记录每个量化级别被使用的次数，用于分析量化质量。如果所有值都集中在几个量化级别，说明量化精度损失大；如果均匀分布，说明量化效果好。

### 量化效果

| 模型规模 | FP16 大小 | Q4_0 大小 | Q4_1 大小 | 压缩率 |
|---------|----------|----------|----------|--------|
| 7B      | ~14 GB   | ~3.5 GB  | ~3.9 GB  | ~74%   |
| 13B     | ~26 GB   | ~6.7 GB  | ~7.4 GB  | ~74%   |
| 65B     | ~130 GB  | ~33 GB   | ~37 GB   | ~74%   |

**压缩率为什么是 ~74% 而不是 87.5%（4/32）？** 理论上 4-bit 比 16-bit 压缩 4 倍（75%），但量化参数（delta、min）占用额外空间。Q4_0 每块 16 字节存 32 个值（+ 2 字节 delta），实际位宽 = 16*8/32 = 4.0 bit/value。加上 delta 开销，总压缩率约 74%。

**Q4_0 vs Q4_1 如何选择？**：
- **Q4_0**：更小体积，速度略快（dequantize 更简单）。适合内存极度受限的场景（如 8GB 机器跑 13B 模型）
- **Q4_1**：精度更好（尤其对权重分布非对称的层），体积稍大。当输出质量比内存更重要时选择

## 使用指南

### 编译项目

```bash
# 编译主程序和量化工具
g++ -O3 -std=c++11 -Wall -Wextra \
    main.cpp ggml.c utils.cpp -o main \
    -lpthread -lm

g++ -O3 -std=c++11 -Wall -Wextra \
    quantize.cpp ggml.c utils.cpp -o quantize \
    -lpthread -lm
```

**编译选项说明**：
- **`-O3`**：最高优化级别，启用循环展开、内联、向量化等。SIMD 自动向量化依赖 `-O3`
- **`-lpthread`**：多线程支持，`ggml_graph_compute` 使用线程池并行执行计算图节点
- **`-lm`**：数学库链接（`sqrt`、`exp` 等函数）

### 模型转换

```bash
# 将 PyTorch 模型转换为 GGML 格式
python convert-pth-to-ggml.py /path/to/llama/model ftype

# ftype: 0=FP32, 1=FP16 (默认)
```

**转换过程做了什么？** PyTorch 模型（`.pth`）存储的是 Python pickle 格式的张量字典。转换脚本做三件事：1）读取所有张量并按 GGML 格式重新排列内存布局；2）写入超参数头和词表；3）将权重从 FP32 转换为 FP16（如果 ftype=1）。这是模型进入 GGML 生态的第一步。

### 模型量化

```bash
# FP16 → Q4_0
./quantize models/llama/ggml-model-f16.bin \
           models/llama/ggml-model-q40.bin 2

# FP16 → Q4_1  
./quantize models/llama/ggml-model-f16.bin \
           models/llama/ggml-model-q41.bin 3
```

**量化参数（最后的数字）**：2 = Q4_0，3 = Q4_1。量化只对权重矩阵做——嵌入层、归一化层和输出层保持 FP16 或 FP32，因为它们对精度更敏感。这是量化工具内置的策略，不需要用户手动选择哪些层该量化。

### 运行推理

```bash
# 基本用法
./main -m models/llama-7B/ggml-model.bin \
       -p "The capital of France is"

# 指定上下文长度和生成长度
./main -m models/llama-7B/ggml-model.bin \
       -c 512 -n 128 \
       -p "Once upon a time"

# 调整采样参数
./main -m models/llama-7B/ggml-model.bin \
       --temp 0.7 \
       --top-p 0.9 \
       -p "The meaning of life is"
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `-m` | 必需 | 模型文件路径 |
| `-p` | "" | 输入提示文本 |
| `-c` | 512 | 上下文长度，直接影响 KV 缓存大小。设得太大浪费内存，太小则模型"忘记"早期内容 |
| `-n` | 128 | 生成 token 数 |
| `-t` | auto | 线程数，默认使用 CPU 核心数。超过物理核心数后性能反而下降（超线程的调度开销） |
| `--temp` | 0.80 | 采样温度。越高越随机（创意写作），越低越确定（事实回答）。0 = 贪心解码，永远选概率最高的 token |
| `--top-p` | 0.95 | Nucleus 采样阈值。只从累积概率前 95% 的 token 中采样，过滤掉长尾的低概率选项 |
| `--seed` | time | 随机种子，用于复现特定输出 |

## 性能优化

### 内存优化

1. **使用量化模型**：Q4量化减少75%内存占用
2. **适当上下文长度**：仅使用需要的上下文
3. **批处理优化**：`n_batch` 参数控制 prompt 批处理大小

**为什么 `n_ctx` 对内存影响如此之大？** KV 缓存大小 = `2 × n_layer × n_ctx × n_embd × 4 bytes`。对 7B 模型：`n_ctx=512 → 512MB`，`n_ctx=2048 → 2GB`，`n_ctx=4096 → 4GB`。上下文长度翻 4 倍，KV 缓存翻 4 倍。这是推理场景最重要的内存调优旋钮。

### 计算优化

```cpp
// 静态缓冲区复用
static size_t buf_size = 512u * 1024 * 1024;  // 512MB
static void * buf = malloc(buf_size);

// 动态扩展（仅增加10%余量）
if (mem_per_token * N > buf_size) {
    buf_size = 1.1 * mem_per_token * N;
    buf = realloc(buf, buf_size);
}
```

**这段代码的设计意图**：

1. **`static` 缓冲区**：`buf` 是 `static` 变量，在多次 `llama_eval` 调用之间复用。第一次调用时分配 512MB，后续调用直接复用，不重新分配。
2. **`mem_per_token`**：第一次推理后，GGML 统计出每个 token 需要的计算图内存（`ggml_used_mem(ctx0)/N`）。后续推理用这个值预测总需求。
3. **10% 余量**：`1.1 * mem_per_token * N`。为什么是 10%？因为 GGML 的计算图节点除了张量数据，还有 `ggml_tensor` 结构体本身的开销（每个节点约 100 字节）。10% 是经验值，覆盖绝大多数情况。
4. **`realloc` 而非 `malloc`**：如果新大小更大，`realloc` 尽量在原地址扩展（利用操作系统 mmap 的增长特性），避免拷贝。

### SIMD 加速

GGML 自动检测并使用最佳指令集：

```cpp
// CPU 特性检测
int ggml_cpu_has_avx(void);      // AMD/Intel x86
int ggml_cpu_has_neon(void);     // ARM NEON
int ggml_cpu_has_avx512(void);   // AVX-512

// 性能对比 (M1 Pro, 7B Q4)
单线程: ~100 tokens/s
4 线程: ~350 tokens/s
8 线程: ~600 tokens/s
```

**SIMD 在 GGML 中的具体作用**：

- **AVX2 (256-bit)**：一次处理 8 个 FP32 或 16 个 FP16。矩阵乘法的内层循环是 Q4 dequantize + F32 乘加，AVX2 将 8 次标量乘加合并为 1 次
- **AVX-512 (512-bit)**：一次处理 16 个 FP32，在 Intel 服务器上比 AVX2 快约 1.5-2 倍
- **NEON (128-bit)**：ARM 的 SIMD，一次处理 4 个 FP32。M1/M2 的 NEON 实现特别高效，配合苹果的统一内存架构，性能接近同功耗 x86

**为什么 8 线程不是 8 倍加速？** Amdahl 定律——计算图中有串行依赖（层与层之间必须串行），并行只发生在同一层内的独立操作（如 Q/K/V 投影可以并行）。此外，多线程的同步开销、缓存争用也会降低加速比。

## 调试与分析

### 计算图可视化

```cpp
// 打印计算图结构
ggml_graph_print(&gf);

// 导出为 DOT 格式 (可用 GraphViz 查看)
ggml_graph_dump_dot(&gf, NULL, "compute_graph.dot");
```

**实用场景**：当模型推理结果异常时，`ggml_graph_dump_dot` 导出的 DOT 文件可以用 GraphViz 渲染成计算图的可视化图。每个节点显示操作类型、张量形状、输入来源，帮助定位是哪个操作产生了错误的结果。对于 LLaMA-7B，计算图约有 640 个节点，渲染后可以清晰看到 32 层的重复结构。

### 性能分析

```cpp
struct ggml_tensor {
    int     perf_runs;      // 运行次数
    int64_t perf_cycles;    // CPU周期
    int64_t perf_time_us;   // 微秒
};
```

**如何使用性能分析**：`ggml_graph_compute` 执行后，每个节点的 `perf_time_us` 记录了该操作的实际执行时间。遍历所有节点，找出耗时最长的 Top-10 操作，就是性能瓶颈。通常 LLaMA 推理的瓶颈是矩阵乘法（`ggml_mul_mat`），占总时间 80%+。

## 文件格式

### GGML 模型文件格式

```
┌────────────────────────────────────────┐
│ Magic: 0x67676d6c ("ggml")            │
├────────────────────────────────────────┤
│ Hyperparameters (7 × int32)           │
│  - n_vocab, n_embd, n_mult            │
│  - n_head, n_layer, n_rot, ftype      │
├────────────────────────────────────────┤
│ Vocabulary (n_vocab entries)           │
│  - length (int32)                      │
│  - token bytes                        │
├────────────────────────────────────────┤
│ Tensor Data (repeated)                │
│  - n_dims, name_len, ftype (int32)    │
│  - dimensions[2]                       │
│  - name (bytes)                       │
│  - data (binary)                       │
└────────────────────────────────────────┘
```

**格式设计决策**：

- **Magic Number `0x67676d6c`**：即 ASCII `"ggml"`。加载模型时首先验证 magic number，防止加载错误格式的文件导致内存越界。
- **超参数在最前面**：模型加载的第一步就是读取超参数，根据它们计算内存需求并预分配。如果超参数在文件末尾，就需要先 seek 到末尾再 seek 回来，增加 IO 复杂度。
- **词表在张量数据之前**：词表用于分词器（tokenizer），在推理前就需要初始化。将词表放在前面，可以在不加载全部权重的情况下先初始化分词器。
- **张量数据按名称索引**：每个张量前面有名称字符串，加载时按名称匹配到 `model.tensors` 映射中。这种设计允许张量在文件中的排列顺序与代码中的定义顺序不同，提供了灵活性。
- **没有对齐填充**：firstbird 的 GGML 格式是早期版本，张量数据没有对齐到特定边界。后续版本（GGUF 格式）增加了对齐填充，以支持 mmap 直接映射，减少内存拷贝。

## 下一步学习

1. **深入 GGML 源码**：阅读 `ggml.c` 理解具体实现
2. **实验不同量化**：测试 Q4_0 vs Q4_1 的精度/速度权衡
3. **优化部署**：探索 WebAssembly 和移动端部署
4. **扩展功能**：添加新操作或支持新模型架构

## 参考资源

- GGML 官方文档 (ggml.h 头部注释)
- LLaMA 论文: "LLaMA: Open and Efficient Foundation Language Models"
- RoPE 位置编码: "Rotary Position Embedding (RoPE)"
- SwiGLU 激活: "GLU Variants Improve Transformer"

---

本文档最后更新: 2026-05-17
