# firstbird 技术文档入门指南

## 项目概述

firstbird 是一个基于 GGML (Generic Graphical Model Library) 的 LLaMA 大语言模型推理框架。该项目实现了完整的 LLaMA transformer 架构，支持模型量化、多种硬件加速以及高效的 CPU 推理能力。

### 核心特性

- **纯 C/C++ 实现**：无需 Python 运行时，可直接部署
- **GGML 张量库**：支持自动微分和优化算法的张量运算库
- **模型量化**：支持 Q4_0 和 Q4_1 两种量化格式，显著降低内存占用
- **SIMD 加速**：支持 AVX/AVX2/AVX512 (x86) 和 NEON (ARM) 指令集
- **多线程支持**：充分利用多核处理器性能
- **模型分片**：支持大模型的分布式加载

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

## GGML 张量库详解

### 核心概念

GGML 是一个为机器学习设计的张量库，其设计哲学是最小化依赖，提供高效的内存管理和计算能力。

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
    
    void * data;                   // 数据指针
};
```

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

### 核心张量操作

#### 基础运算

| 操作 | 函数 | 说明 |
|------|------|------|
| 加法 | `ggml_add` | 逐元素相加 |
| 乘法 | `ggml_mul` | 逐元素相乘 |
| 矩阵乘 | `ggml_mul_mat` | 矩阵乘法，A: m×n × B: p×n → m×p |
| 归一化 | `ggml_norm` | 行级归一化 |
| Softmax | `ggml_soft_max` | Softmax 激活 |
| GELU | `ggml_gelu` | 高斯误差线性单元 |
| SiLU | `ggml_silu` | Sigmoid 加权线性单元 |

#### Transformer 专用操作

```cpp
// 旋转位置编码 (RoPE)
ggml_rope(ctx, Q, n_past, n_dims, mode);

// 注意力掩码
ggml_diag_mask_inf(ctx, KQ, n_past);

// 1D 卷积（用于滑动窗口）
ggml_conv_1d_1s(ctx, a, b);  // 步长1
ggml_conv_1d_2s(ctx, a, b);  // 步长2
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

### 前向传播详解

#### 1. 词嵌入

```cpp
// 输入: token IDs [batch_size]
// 输出: 嵌入向量 [n_embd, batch_size]
struct ggml_tensor * embd = ggml_new_tensor_1d(ctx0, GGML_TYPE_I32, N);
memcpy(embd->data, embd_inp.data(), N * sizeof(int32_t));

struct ggml_tensor * inpL = ggml_get_rows(ctx0, model.tok_embeddings, embd);
```

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

#### 3. 前馈网络 (SwiGLU)

```cpp
// 标准 FFN 计算: SiLU(W1(x) * W3(x)) @ W2
struct ggml_tensor * tmp = ggml_mul_mat(ctx0, layer.w3, cur);
cur = ggml_mul_mat(ctx0, layer.w1, cur);
cur = ggml_silu(ctx0, cur);
cur = ggml_mul(ctx0, cur, tmp);
cur = ggml_mul_mat(ctx0, layer.w2, cur);
```

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

每次推理只需计算新的 Q，K/V 从缓存读取。

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

### 量化效果

| 模型规模 | FP16 大小 | Q4_0 大小 | Q4_1 大小 | 压缩率 |
|---------|----------|----------|----------|--------|
| 7B      | ~14 GB   | ~3.5 GB  | ~3.9 GB  | ~74%   |
| 13B     | ~26 GB   | ~6.7 GB  | ~7.4 GB  | ~74%   |
| 65B     | ~130 GB  | ~33 GB   | ~37 GB   | ~74%   |

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

### 模型转换

```bash
# 将 PyTorch 模型转换为 GGML 格式
python convert-pth-to-ggml.py /path/to/llama/model ftype

# ftype: 0=FP32, 1=FP16 (默认)
```

### 模型量化

```bash
# FP16 → Q4_0
./quantize models/llama/ggml-model-f16.bin \
           models/llama/ggml-model-q40.bin 2

# FP16 → Q4_1  
./quantize models/llama/ggml-model-f16.bin \
           models/llama/ggml-model-q41.bin 3
```

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
| `-c` | 512 | 上下文长度 |
| `-n` | 128 | 生成token数 |
| `-t` | auto | 线程数 |
| `--temp` | 0.80 | 采样温度 |
| `--top-p` | 0.95 | Nucleus采样阈值 |
| `--seed` | time | 随机种子 |

## 性能优化

### 内存优化

1. **使用量化模型**：Q4量化减少75%内存占用
2. **适当上下文长度**：仅使用需要的上下文
3. **批处理优化**：`n_batch` 参数控制prompt批处理大小

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

## 调试与分析

### 计算图可视化

```cpp
// 打印计算图结构
ggml_graph_print(&gf);

// 导出为 DOT 格式 (可用 GraphViz 查看)
ggml_graph_dump_dot(&gf, NULL, "compute_graph.dot");
```

### 性能分析

```cpp
struct ggml_tensor {
    int     perf_runs;      // 运行次数
    int64_t perf_cycles;    // CPU周期
    int64_t perf_time_us;   // 微秒
};
```

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
