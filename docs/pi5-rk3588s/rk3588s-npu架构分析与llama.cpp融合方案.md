# RK3588S NPU 架构分析与 llama.cpp 融合方案

> 基于 2026.05.18 深度技术讨论整理。涵盖：RK3588S 硬件能力、NPU 数据流架构、RKLLM C API、与 llama.cpp 的融合方案、与昇腾 CANN 控制流架构的对比、多模态可行性、模型选型与微调约束。

---

## 一、RK3588S 硬件概览

### 1.1 SoC 规格

| 模块 | 规格 |
|------|------|
| CPU | 8核64位 ARM: 4×Cortex-A76(2.4GHz) + 4×Cortex-A55(1.8GHz) |
| NPU | 6 TOPS (INT8), 3核心, Rockchip RKNN 架构 |
| GPU | Mali-G610 MP4, OpenGL ES 3.2 / Vulkan 1.1 / OpenCL 2.2 |
| 内存 | LPDDR4/4x, 4GB / 8GB / 16GB / 32GB |
| 存储 | eMMC 5.1 / microSD / NVMe SSD (via M.2) |
| 视频编码 | 8K30 H.265 / H.264 |
| 视频解码 | 8K30 H.265, 4K120 H.265, 8K30 VP9, 4K60 AV1 |
| 网络 | 千兆以太网 |
| USB | USB 3.0 × 2, USB 2.0 × 2 |
| 开发板 | Orange Pi 5 |

### 1.2 操作系统支持

| OS | 版本 | 备注 |
|----|------|------|
| Ubuntu | 20.04 (focal) / 22.04 (jammy) | **推荐 22.04 jammy**，对 llama.cpp 编译工具链支持更好 |
| Debian | 11 (bullseye) / 12 (bookworm) | 稳定但软件包偏旧 |
| Orange Pi OS | Droid / Arch | 社区维护 |
| Android | 12.1 | 不适合 llama.cpp 开发 |

**镜像命名规则**：镜像文件名中 `jammy` = Ubuntu 22.04, `focal` = Ubuntu 20.04

### 1.3 TF 卡系统烧录

1. 下载 Ubuntu 22.04 Server 镜像（如 `Orangepi5pro_1.0.2_ubuntu_jammy_server_linux5.10.160.7z`）
2. 使用 balenaEtcher 烧录到 TF 卡
3. **TF 卡无需预先格式化**——balenaEtcher 会直接写入原始镜像数据，覆盖原有分区和文件系统
4. 插入 Orange Pi 5，上电启动

---

## 二、NPU 硬件架构深度解析

### 2.1 核心结论：NPU 是数据流加速器，不是独立处理器

RK3588S 的 NPU **不是**一个独立可编程处理器。它没有：
- 程序计数器 (PC)
- 取指单元 (IF)
- 分支预测器
- 通用寄存器文件
- 指令解码器

NPU 的工作模式是**配置驱动的数据流加速**：

```
CPU 角色:                    NPU 角色:
┌──────────────────┐         ┌──────────────────┐
│ 1. 加载 .rkllm   │         │                  │
│    模型文件       │ ──────→ │  权重 SRAM/DDR   │
│ 2. 配置数据流图   │ ──────→ │  数据流描述符     │
│ 3. 推送输入数据   │ ──────→ │  输入 Buffer     │
│ 4. 触发推理       │ ──────→ │  启动数据流      │
│ 5. 等待完成       │ ←────── │  推理完成中断     │
│ 6. 读取输出       │ ←────── │  输出 Buffer     │
│ 7. Token采样     │         │                  │
│ 8. 循环3-7       │         │                  │
└──────────────────┘         └──────────────────┘
```

### 2.2 FPGA 数据通路类比

NPU 的工作原理**非常接近基站数字中频 FPGA 的"配置驱动数据流"模式**：

| 对比维度 | 基站数字中频 FPGA | RK3588S NPU |
|----------|-------------------|-------------|
| 处理单元 | DUC/DDC/FFT 硬核 | MatMul/Conv 硬核 |
| 配置方式 | 寄存器配置数据通路 | 数据流图描述符 |
| 触发方式 | 数据有效信号触发 | CPU 推送输入触发 |
| 流程控制 | CPU(ARM/DSP) 配置+调度 | CPU(A76/A55) 配置+调度 |
| 独立运行 | 不能，需要 CPU 配置 | 不能，需要 CPU 配置 |

**关键洞察**：CPU 在整个 LLM 推理过程中全程参与——tokenize、输入推送、输出采样、KV cache 管理、循环控制。NPU 只负责"给定输入，计算输出"这一步。这不是"边角料工作"，而是**系统集成的核心**——决定数据在哪一层处理、何时触发、如何调度，正是嵌入式系统的核心竞争力。

### 2.3 .rkllm 文件格式

`.rkllm` 是 RKLLM 工具链的离线编译产物，包含：

```
.rkllm 文件结构:
┌─────────────────────────┐
│  模型权重 (量化后)        │  ← INT8/INT4 量化权重
│  数据流图配置描述符       │  ← NPU 如何连线、何时计算
│  量化参数 (scale/zeropt) │  ← 反量化/量化参数
│  模型元数据              │  ← 架构、词表、上下文长度等
└─────────────────────────┘
```

这是**编译时确定**的产物，运行时不可修改计算图。这意味着：
- 无法在运行时动态插入自定义算子
- 无法在运行时改变模型结构（如动态分支）
- 所有灵活性必须在 CPU 侧实现

---

## 三、RKLLM 工具链与 C API

### 3.1 工具链组成

```
PC 端 (x86_64 Linux):                     板端 (ARM64 Linux):
┌──────────────────────┐                  ┌──────────────────────┐
│  rkllm-toolkit        │                  │  rkllm-runtime        │
│  (Python包)           │                  │  (C库 + 头文件)       │
│                       │                  │                      │
│  HF/PyTorch 模型      │                  │  librkllmrt.so       │
│      ↓                │                  │  rkllm.h             │
│  量化 + 编译          │  .rkllm 文件     │      ↓               │
│      ↓                │ ────────────→    │  rkllm_init()        │
│  .rkllm 离线产物      │  (adb/scp拷贝)   │  rkllm_run()         │
│                       │                  │  rkllm_destroy()     │
└──────────────────────┘                  └──────────────────────┘
                                          ┌──────────────────────┐
                                          │  rknpu-driver         │
                                          │  (内核驱动)           │
                                          │  /dev/rknpu           │
                                          └──────────────────────┘
```

### 3.2 RKLLM C API 完整列表

```c
// 核心类型
typedef void* LLMHandle;

typedef enum {
    RKLLM_RUN_NORMAL  = 0,  // 正常生成中
    RKLLM_RUN_WAITING = 1,  // 等待中
    RKLLM_RUN_FINISH  = 2,  // 生成完成
    RKLLM_RUN_ERROR   = 3   // 出错
} LLMCallState;

typedef enum {
    RKLLM_INPUT_PROMPT    = 0,  // 文本prompt
    RKLLM_INPUT_TOKEN     = 1,  // token ID序列
    RKLLM_INPUT_EMBED     = 2,  // 嵌入向量
    RKLLM_INPUT_MULTIMODAL = 3  // 多模态(图+文)
} RKLLMInputType;

typedef enum {
    RKLLM_INFER_GENERATE             = 0,  // 完整自回归生成
    RKLLM_INFER_GET_LAST_HIDDEN_LAYER = 1,  // 获取最后隐藏层
    RKLLM_INFER_GET_LOGITS           = 2   // 获取logits(不生成)
} RKLLMInferMode;

// 核心函数
RKLLMParam rkllm_createDefaultParam();
int rkllm_init(LLMHandle* handle, RKLLMParam* param, LLMResultCallback callback);
int rkllm_run(LLMHandle handle, RKLLMInput* rkllm_input, RKLLMInferParam* rkllm_infer_params, void* userdata);
int rkllm_run_async(LLMHandle handle, RKLLMInput* rkllm_input, RKLLMInferParam* rkllm_infer_params, void* userdata);
int rkllm_abort(LLMHandle handle);
int rkllm_destroy(LLMHandle handle);
int rkllm_clear_kv_cache(LLMHandle handle, int keep_system_prompt, int* start_pos, int* end_pos);

// 性能统计
typedef struct {
    float prefill_time_ms;
    int   prefill_tokens;
    float generate_time_ms;
    int   generate_tokens;
    float memory_usage_mb;
} RKLLMPerfStat;

// 多模态输入
typedef struct {
    char*  prompt;
    float* image_embed;
    size_t n_image_tokens;
    size_t n_image;
    size_t image_width;
    size_t image_height;
} RKLLMMultiModalInput;
```

### 3.3 关键 API 语义

**rkllm_run() 的同步/异步**：
- `RKLLMParam.is_async = false`：`rkllm_run()` 阻塞直到生成完成
- `RKLLMParam.is_async = true`：`rkllm_run()` 立即返回，通过 callback 流式返回 token

**RKLLMInferMode 三种模式**：

| 模式 | 行为 | 适用场景 |
|------|------|----------|
| `RKLLM_INFER_GENERATE` (0) | Prefill + 自回归生成 N 个 token | 对话生成、文本补全 |
| `RKLLM_INFER_GET_LOGITS` (2) | 仅 Prefill，返回 logits 向量 | **分类任务**（隐私路由核心！） |
| `RKLLM_INFER_GET_LAST_HIDDEN_LAYER` (1) | Prefill，返回最后隐藏层 | 嵌入提取、RAG |

**rkllm_clear_kv_cache()**：
- `keep_system_prompt=1`：保留 system prompt 的 KV cache，节省重复计算
- `start_pos/end_pos`：可精确清除指定范围的 KV cache

### 3.4 认证的 RKLLM 模型

| 模型系列 | 具体模型 |
|----------|----------|
| TinyLLAMA | TinyLLAMA 1.1B |
| Qwen | Qwen-1.8B, Qwen2-1.5B, Qwen2.5-1.5B/3B/7B |
| Phi | Phi-3-mini-4k |
| ChatGLM | ChatGLM3-6B |
| Gemma | Gemma-2B, **Gemma3-2B** |
| InternLM | InternLM2-1.8B/7B |
| MiniCPM | MiniCPM-2B, **MiniCPM4-0.5B** |

### 3.5 Benchmark 数据（RK3588, 16GB DDR4）

| 模型 | 量化 | Prefill (TTFT) | 生成速度 |
|------|------|----------------|----------|
| Qwen2.5-1.5B-Instruct | w8a8 | 412ms | 16.3 tok/s |
| MiniCPM4-0.5B | w8a8 | 128ms | 45.1 tok/s |

---

## 四、llama.cpp 与 RKLLM 融合方案

### 4.1 为什么无法作为 ggml_backend 接入

llama.cpp 的后端体系（`ggml_backend_cuda`, `ggml_backend_vulkan`, `ggml_backend_cann`）操作的是**单个张量运算**：

```
ggml_backend 抽象:
  ggml_backend_mul_mat()    ← 单次矩阵乘
  ggml_backend_add()        ← 单次加法
  ggml_backend_mul()        ← 单次逐元素乘
  ggml_backend_norm()       ← 单次归一化
  ...
```

而 RKLLM 的 API 操作的是**整个 LLM 推理黑盒**：

```
RKLLM 抽象:
  rkllm_run()               ← 一次调用完成: embed→所有transformer层→lm_head→采样→输出token
```

**根本矛盾**：粒度不匹配。ggml_backend 需要控制每个算子，RKLLM 只暴露整个推理流程。无法将 RKLLM 塞进 `ggml_backend` 接口。

### 4.2 融合架构：在 llama_context 级别接入

正确的融合方式是**在 llama_context 级别**接入，替代整个 ggml 计算图：

```
┌─────────────────────────────────────────────────────────┐
│  llama.cpp 上层 (llama.h API)                            │
│  llama_decode() / llama_tokenize() / llama_sample()     │
├─────────────────────────────────────────────────────────┤
│  llama_context                                           │
│  ┌──────────────────┐  ┌──────────────────────────────┐ │
│  │  ggml 计算图路径  │  │  RKLLM 数据流路径 (新增)     │ │
│  │  (CPU/CUDA/Vulkan)│  │  rkllm_run() 黑盒           │ │
│  └──────────────────┘  └──────────────────────────────┘ │
├─────────────────────────────────────────────────────────┤
│  ggml backends                                          │
│  _cpu / _cuda / _vulkan / _cann                         │
└─────────────────────────────────────────────────────────┘
```

### 4.3 核心数据结构设计

```cpp
struct llama_rkllm_context {
    LLMHandle         handle        = nullptr;
    RKLLMParam        param;
    std::mutex        mtx;
    std::string       pending_output;
    bool              inference_done = false;
    bool              abort_request  = false;
};

static int rkllm_token_callback(RKLLMResult* result, void* userdata, LLMCallState state) {
    auto* ctx = static_cast<llama_rkllm_context*>(userdata);
    std::lock_guard<std::mutex> lock(ctx->mtx);
    switch (state) {
    case RKLLM_RUN_NORMAL:
        if (result->text) ctx->pending_output += result->text;
        if (ctx->abort_request) return 1;
        return 0;
    case RKLLM_RUN_FINISH:
    case RKLLM_RUN_ERROR:
        ctx->inference_done = true;
        return 0;
    default: return 0;
    }
}

bool llama_rkllm_load_model(llama_rkllm_context* ctx, const char* rkllm_path) {
    ctx->param = rkllm_createDefaultParam();
    ctx->param.model_path       = rkllm_path;
    ctx->param.max_context_len  = 4096;
    ctx->param.max_new_tokens   = 512;
    ctx->param.temperature      = 0.7f;
    ctx->param.top_p            = 0.9f;
    ctx->param.top_k            = 50;
    ctx->param.repeat_penalty   = 1.1f;
    ctx->param.is_async         = false;
    int ret = rkllm_init(&ctx->handle, &ctx->param, rkllm_token_callback);
    return ret == 0;
}
```

### 4.4 隐私路由分类优化：GET_LOGITS 模式

这是 RKLLM 对 phoneinloop 隐私路由场景的**杀手级优势**：

```
传统 LLM 分类流程:
  Prompt → Prefill → 自回归生成 "YES"/"NO" → 解析输出文本
  延迟: TTFT(412ms) + 生成1-3个token(~200ms) ≈ 600ms

RKLLM GET_LOGITS 分类流程:
  Prompt → Prefill → 返回 logits 向量 → softmax → 取 top-1 类别
  延迟: TTFT(412ms)  ← 无需自回归生成!
```

**关键优化**：对于隐私分类（"这条消息是否需要上云处理？"），只需要判断 logits 分布，不需要生成任何文本。延迟直接减半。

---

## 五、NPU 数据流 vs 昇腾 CANN 控制流：架构对比

### 5.1 架构模型对比

| 维度 | RK3588S NPU (数据流) | 昇腾 CANN (控制流) |
|------|----------------------|---------------------|
| 架构模型 | 配置驱动数据流 | 类CUDA Host-Device |
| 调度方式 | 一次配置，数据自动流过 | CPU 逐算子调度，每算子一次 kernel launch |
| 计算图 | 编译时静态确定 | 运行时可动态调整 |
| 粒度 | 整模型黑盒 | 单算子级别 |
| 灵活性 | 低（改模型需重新编译） | 高（可插入自定义算子） |
| 开发门槛 | 低（rkllm-toolkit Python 一键转换） | 高（需理解 CANN 算子开发） |
| 生态 | 瑞芯迪私有，模型覆盖有限 | 华为+MindSpore，覆盖广 |
| 硬件成本 | ~¥500 (Orange Pi 5 8GB) | ~¥1000+ (Atlas 200I DK A2) |
| 功耗 | 5-10W | 10-20W |
| CPU 参与 | 全程（tokenize/sampling/调度） | 全程（逐算子调度） |

### 5.2 控制流 vs 数据流：本质区别

**昇腾 CANN 控制流**：
```
CPU (Host):
  for each operator in model:
      aclmdlExecute(model, input, output)  // 一次 kernel launch
      wait_for_completion()
      prepare_next_input()
```

- 类似 CUDA：Host 逐算子调度，每个算子一次执行
- 优势：可以在算子之间插入自定义逻辑（如动态分支、早停）
- 代价：每次 kernel launch 有调度开销

**RK3588S NPU 数据流**：
```
CPU (Host):
  rkllm_init(handle, model, callback)      // 一次性配置
  rkllm_run(handle, input, params, userdata) // 触发整个推理
  // NPU 内部: embed → N×transformer → lm_head → sampling
  // CPU 等待 callback 逐 token 返回
```

- 类似 FPGA：配置一次，数据自动流过
- 优势：零调度开销，NPU 内部流水线极致优化
- 代价：无法在中间插入逻辑，灵活性受限

### 5.3 对隐私路由场景的影响

| 场景需求 | RK3588S NPU | 昇腾 CANN |
|----------|-------------|-----------|
| 纯分类（GET_LOGITS） | ✅ 极佳，跳过生成，延迟<500ms | ✅ 可行，但需逐算子调度 |
| 动态路由（根据中间层结果决定后续） | ❌ 无法实现（黑盒） | ✅ 可在算子间插入判断 |
| 多模型切换 | ⚠️ 需 rkllm_destroy + rkllm_init，较慢 | ✅ 切换模型更快 |
| 自定义采样策略 | ❌ 采样由 RKLLM 内部完成 | ✅ 可在 CPU 侧自定义 |
| KV cache 精细管理 | ⚠️ 仅 rkllm_clear_kv_cache() | ✅ 完全可控 |
| 开发效率 | ✅ 一行 Python 转换 | ❌ 需熟悉 CANN 工具链 |

**结论**：
- **MVP 阶段选 RK3588S**：开发效率高、成本低、GET_LOGITS 模式直击分类需求
- **产品化阶段考虑昇腾**：当需要动态路由、自定义采样、多模型并行时

---

## 六、多模态模型可行性

### 6.1 RKLLM 多模态工作流

RKLLM v1.2.3+ 支持 `RKLLM_INPUT_MULTIMODAL` 模式，工作流程：

```
两阶段流水线:

阶段1: ViT Encoder (RKNN)
  图像 → RKNN ViT 模型 → 视觉嵌入 (image_embed)
                     ↓
阶段2: LLM (RKLLM)
  文本 prompt + image_embed → RKLLM (含交叉注意力) → 生成描述
```

```c
RKLLMInput input;
input.input_type = RKLLM_INPUT_MULTIMODAL;
input.multimodal_input.prompt = "描述这张图片";
input.multimodal_input.image_embed = vit_output;  // 来自 RKNN ViT
input.multimodal_input.n_image_tokens = 256;
input.multimodal_input.image_width = 384;
input.multimodal_input.image_height = 384;
```

### 6.2 Gemma3-2B / 4B 可行性分析

Gemma3 系列已在 RKLLM 认证列表中，可直接在 RK3588S 上部署：

| 检查项 | Gemma3-2B | Gemma3-4B |
|--------|-----------|-----------|
| RKLLM 认证 | ✅ | ✅ |
| 8GB 内存可行性 | ✅ w8a8 可运行 | ⚠️ w8a8 边界，建议 16GB 版本 |
| ViT 部分 | ⚠️ 多模态需单独用 RKNN 转换 ViT encoder | ⚠️ 同左 |
| 中文能力 | 一般（Google 模型中文偏弱） | 一般 |
| 推理速率(估) | ~10-15 tok/s | ~6-8 tok/s |

**Gemma3 部署路径**：
1. 使用 rkllm-toolkit 转换 Gemma3-2B/4B 为 .rkllm
2. 纯文本场景：直接 rkllm_run() 推理
3. 多模态场景：额外使用 rknn-toolkit2 转换 SigLIP/CLIP ViT 为 .rknn，板端两阶段推理：RKNN ViT → RKLLM LLM

**与 Qwen2.5 对比**：Gemma3 中文能力弱于 Qwen2.5，隐私路由的中文消息分类场景仍推荐 Qwen2.5-1.5B；Gemma3 适合英文/代码场景备选

### 6.3 已认证的多模态模型

| 模型 | 视觉部分 | RKLLM 支持 |
|------|----------|------------|
| Qwen2-VL | ViT + LLM | ✅ v1.2.3+ |
| MiniCPM-V | ViT + LLM | ✅ v1.2.3+ |

---

## 七、模型选型与微调约束

### 7.1 推荐模型选型

| 角色 | 模型 | 量化 | 速率 | 用途 |
|------|------|------|------|------|
| **主力** | Qwen2.5-1.5B-Instruct | w8a8 | 16.3 tok/s | 通用对话、分类、摘要 |
| **超快** | MiniCPM4-0.5B | w8a8 | 45.1 tok/s | 低延迟分类/路由决策 |
| **多模态** | Gemma3-2B / Qwen2-VL | w8a8 | ~10 tok/s(估) | 图像理解 |

### 7.2 选型约束

**RK3588S NPU 硬约束**：
- 内存：8GB 版本实际可用 ~4GB（系统+GPU+其他），模型权重需 < 3GB
- 量化：仅支持 w4a8 / w8a8，不支持 FP16 运行
- 架构：仅支持 RKLLM 认证的模型架构
- 上下文长度：建议 max_context_len ≤ 4096（受内存限制）

**GGUF (llama.cpp CPU) 约束**：
- 无架构限制，任何 HuggingFace 模型均可
- 但 CPU-only 推理速度慢：A76 单核约 2-5 tok/s (1.5B Q4)
- 适合 fallback 和开发调试

### 7.3 微调路径

```
本地微调 (QLoRA):
  HF 模型 + LoRA adapter → 合并 → HF 格式

部署分支:
  ┌─→ rkllm-toolkit → .rkllm → NPU 推理 (板端)
  └─→ llama.cpp     → .gguf  → CPU 推理 (板端 fallback)
```

**微调约束对比**：

| 约束 | RK3588S (.rkllm) | 昇腾 (.om) | GGUF |
|------|-------------------|------------|------|
| 训练环境 | GPU 服务器 (x86) | GPU 服务器 / 昇腾 | GPU 服务器 |
| 转换工具 | rkllm-toolkit (Python) | ATC (CANN) | llama.cpp convert |
| 量化支持 | w4a8, w8a8 | FP16, INT8, INT4 | Q2-Q8, FP16 |
| 架构限制 | 仅认证模型 | MindSpore 支持的 | 几乎无限制 |
| 微调后需重转换 | 是 | 是 | 是 |

---

## 八、已知问题与注意事项

### 8.1 IOMMU 内存分配失败

**错误现象**：
```
E RKNN: failed to allocate handle, ret: -1, errno: 14, errstr: Bad address
can not create weight memory for domain0
Error: iommu_context->weight_memory is NULL
```

**修复方法**：`sudo reboot` 重启开发板后重试

**原因推测**：NPU 驱动 IOMMU 映射表在多次 init/destroy 循环后可能泄漏

### 8.2 NPU 驱动版本要求

- 最低版本：rknpu-driver ≥ v0.9.6
- 检查命令：`dmesg | grep rknpu`

---

## 九、待办事项（回到工作室后）

- [ ] 检查 NPU 驱动版本 (≥ v0.9.6)
- [ ] CPU-only llama.cpp 编译验证
- [ ] RKLLM 环境搭建 + NPU 推理验证
- [ ] 双板 (RK3588 + 昇腾) 基线确认 → 进入 Phase 1

---

## 交叉引用

- 隐私路由架构设计决策：`/home/ubuntu/2026.allinloop/phoneinloop/docs/hardware-npu-选型与隐私路由架构决策.md`
- Orange Pi 5 用户手册：`/home/ubuntu/2026.llama.cpp/llama.cpp.firstbird/docs/pi5-rk3588s/rk3588s_用户说明/rk3588s用户说明.md`
- phoneinloop 架构设计：`/home/ubuntu/2026.allinloop/phoneinloop/docs/architecture.md`
