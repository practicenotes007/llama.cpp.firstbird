# GGML.C 阅读 Roadmap (熟练级)

---

## 背景知识补充

| 主题 | 建议学习内容 | 重要程度 |
|------|-------------|---------|
| **C语言** | 结构体、指针、内存管理、`restrict` 关键字、`alloca` 栈分配、`union` 类型双关 | ★★★★★ |
| **SIMD intrinsics** | ARM NEON (`float32x4_t`, `vld1q_f32`, `vmulq_f32`)、x86 AVX2/SSE3 (`__m256`, `__m128`, `_mm256_loadu_ps`)、WASM SIMD (`v128_t`) | ★★★★★ |
| **多线程** | POSIX `pthread`、Windows `CreateThread`、原子操作 (`atomic_int`, `atomic_fetch_add`)、自旋锁 | ★★★★ |
| **机器学习基础** | 梯度下降、反向传播 (Backpropagation)、自动微分 (Reverse-mode AD) | ★★★★ |
| **线性代数** | 矩阵乘法、向量点积、范数计算 | ★★★★ |
| **数值计算** | FP16/FP32 浮点格式 (IEEE 754)、量化 (Quantization) 原理、GELU/SILU 激活函数 | ★★★★ |
| **DDR 带宽与推理** | [Embedding 占比对带宽利用率的影响](docs/09_ddr_bandwidth_insight.md) — 实践验证: 为什么小模型带宽利用率天然更低 | ★★★★★ |
| **优化算法** | L-BFGS 算法 (拟牛顿法)、Adam 优化器 (自适应学习率)、线搜索 (Line Search) | ★★★ |
| **编译预处理** | 宏定义、条件编译 (`#if defined(...)`)、平台检测 | ★★★ |

### 推荐前置阅读材料

| 材料 | 用途 |
|------|------|
| [FP16 算法参考](https://github.com/Maratyszcza/FP16) | 理解软件 FP16↔FP32 转换 |
| [L-BFGS 参考](https://github.com/chokkan/liblbfgs) | ggml 的 L-BFGS 实现基于此 |
| [Adam 论文](https://arxiv.org/pdf/1412.6980.pdf) | Adam 优化器原始论文 |
| [ggml.h](../ggml.h) | **必须先读**：所有类型定义和 API 声明在此 |

---

## 前置阅读：ggml.h (约 740 行)

**目标**：掌握所有公共类型定义和 API 接口

ggml.h 是 ggml.c 的头文件，包含所有核心类型定义。**强烈建议在阅读 ggml.c 之前先通读 ggml.h**。

| 区块 | 行号 | 内容 |
|------|------|------|
| H.1 | L1-L60 | 文件头部注释，使用示例代码 |
| H.2 | L100-L200 | 常量定义：`GGML_MAX_DIMS=4`, `GGML_MAX_NODES=4096`, `GGML_MAX_OPT=3`, `GGML_MAX_CONTEXTS=64`, `GGML_MAX_NAME=32`, `GGML_MAX_PARAMS=64` |
| H.3 | L200-L252 | **`enum ggml_type`** (Q4_0, Q4_1, I8, I16, I32, F16, F32) 和 **`enum ggml_op`** (34 种操作) |
| H.4 | L256-L288 | **`struct ggml_tensor`** — 核心张量结构体 (`type`, `ne[]`, `nb[]`, `op`, `grad`, `src0`, `src1`, `opt[]`, `data`) |
| H.5 | L289-L300 | **`struct ggml_cgraph`** — 计算图 (`nodes[]`, `grads[]`, `leafs[]`, `n_nodes`, `n_leafs`) |
| H.6 | L301-L320 | `struct ggml_scratch`, `struct ggml_init_params` |
| H.7 | L320-L640 | 所有公共 API 函数声明 |
| H.8 | L645-L740 | 优化器类型和参数结构体 (`ggml_opt_params`, `ggml_opt_result`) |

**ggml_tensor 核心字段解读**：
```
type       : 数据类型 (Q4_0/Q4_1/I8/I16/I32/F16/F32)
ne[4]      : 每维元素数 (number of elements)
nb[4]      : 每维字节跨度 (stride in bytes)
             nb[0] = sizeof(type)
             nb[1] = nb[0] * ne[0] (+ padding)
             nb[i] = nb[i-1] * ne[i-1]
op         : 操作类型
is_param   : 是否为可训练参数
grad       : 梯度张量指针
src0/src1  : 操作的输入张量
opt[3]     : 操作的额外参数
data       : 实际数据指针
```

---

## 阶段一：基础设施与平台抽象 (L1-L399)

**目标**：理解跨平台基础设施、FP16 转换、查找表

| 区块 | 行号 | 内容 | 难度 |
|------|------|------|------|
| 1.1 | L1-L17 | 头文件包含 (`alloca.h`, `assert.h`, `math.h`, `stdint.h` 等) | ★ |
| 1.2 | L18-L98 | **平台检测与原子操作封装** — Windows (`InterlockedExchange`, `CreateThread`) vs POSIX (`stdatomic.h`, `pthread`) | ★★ |
| 1.3 | L99-L117 | 通用宏定义 (`UNUSED`, `SWAP`, `GGML_ASSERT`, `MIN`, `MAX`)、`ggml_float` (累加类型为 `double`) | ★ |
| 1.4 | L118-L230 | **FP16 ↔ FP32 转换** — ARM NEON 直接转换 vs F16C 指令 vs 纯软件实现 (`fp32_from_bits`, `fp32_to_bits`, `ggml_compute_fp16_to_fp32`, `ggml_compute_fp32_to_fp16`) | ★★★★ |
| 1.5 | L231-L270 | 预计算查找表初始化 — `table_gelu_f16[1<<16]` (128KB), `table_silu_f16[1<<16]` (128KB), `table_exp_f16[1<<16]` (128KB), `table_f32_f16[1<<16]` (256KB) | ★★ |
| 1.6 | L271-L340 | **时间函数** — `ggml_time_init`, `ggml_time_ms`, `ggml_time_us`, `ggml_cycles` (Windows/POSIX 双实现)、性能宏 `GGML_PERF` | ★ |
| 1.7 | L341-L399 | 缓存行大小 (`CACHE_LINE_SIZE=64`, Power9 为 128)、量化常量 `#define QK 32`、**AVX2 辅助函数** (`bytesFromNibbles`, `packNibbles`) | ★★ |

**关键概念**：
- **跨平台策略**：用条件编译 + 宏封装所有平台差异，核心算法代码不感知平台
- **FP16 软件转换**：利用 IEEE 754 浮点位操作实现，避免了硬件依赖。理解此段有助于理解量化精度
- **查找表优化**：将高频运算 (GELU/SILU/EXP) 预计算为查找表，用空间换时间
- **QK=32**：量化块大小，每 32 个元素为一组，用一个 float (delta) + 16 字节 (16 个 int4) 表示

---

## 阶段二：量化与基础向量运算 (L400-L1979)

**目标**：掌握量化/反量化流程、SIMD 抽象层、向量运算库

### 2.1 量化/反量化实现 (L400-L650)

| 区块 | 行号 | 函数 | 说明 |
|------|------|------|------|
| 2.1.1 | L400-L530 | `quantize_row_q4_0` | Q4_0 量化 (ARM NEON + AVX2 + scalar 三实现) |
| 2.1.2 | L530-L650 | `quantize_row_q4_1` | Q4_1 量化 (多平台实现) |
| 2.1.3 | L650-L750 | `dequantize_row_q4_0`, `dequantize_row_q4_1` | 反量化 (目前为 scalar 实现，带 TODO 标记) |

**Q4_0 vs Q4_1 量化格式对比**：
```
Q4_0: 每块 = 1×float(Δ) + 16×uint8(32个4-bit值)
      反量化: value = (int4 - 8) * Δ        // 对称量化

Q4_1: 每块 = 1×float(m) + 1×float(Δ) + 16×uint8(32个4-bit值)
      反量化: value = int4 * Δ + m           // 非对称量化
```

### 2.2 SIMD 抽象层 (L750-L1200) ★ 专家版遗漏的关键区段

这一区段定义了跨平台 SIMD 宏，是整个向量运算的基石。**理解此层是理解所有后续 SIMD 代码的关键**。

| 区块 | 行号 | 平台 | 关键宏 |
|------|------|------|--------|
| 2.2.1 | L750-L900 | **ARM NEON** (+FMA) | `GGML_F32x4` = `float32x4_t`, 步长=16, 寄存器宽=4; FP16 向量算术或 FP32 转换两种模式 |
| 2.2.2 | L900-L1080 | **WASM SIMD** | `GGML_F32x4` = `v128_t`, 步长=16, 自定义 f16 load/store |
| 2.2.3 | L1080-L1200 | **x86 SSE3** (含 FMA) | `GGML_F32x4` = `__m128`, 步长=32, 自定义 f16 load/store |

**统一抽象模式**：
```c
GGML_F32_VEC_LOAD    → 平台相关加载
GGML_F32_VEC_STORE   → 平台相关存储
GGML_F32_VEC_FMA     → 平台相关融合乘加
GGML_F32_VEC_ADD     → 平台相关加法
GGML_F32_VEC_MUL     → 平台相关乘法
GGML_F32_VEC_REDUCE  → 平台相关规约求和
```

> **设计模式**：通过预处理器宏将平台特有 intrinsic 封装为统一接口，核心算法代码只使用 `GGML_F32_VEC_*` 宏，实现一次编写多平台运行。

### 2.3 基础向量运算 (L1200-L1979)

| 区块 | 行号 | 函数族 | 说明 |
|------|------|--------|------|
| 2.3.1 | L1200-L1222 | `ggml_vec_set_*`, `ggml_vec_add_f32`, `ggml_vec_sub_f32`, `ggml_vec_mul_f32`, `ggml_vec_div_f32`, `ggml_vec_acc_f32` | 标量 fallback 实现 |
| 2.3.2 | L1223-L1260 | `ggml_vec_dot_f32` | **点积** — SIMD 加速 (GGML_F32_STEP 步长) + 标量尾处理 |
| 2.3.3 | L1260-L1296 | `ggml_vec_dot_f16` | F16 点积 — SIMD + FP16↔FP32 转换 |
| 2.3.4 | L1296-L1567 | `ggml_vec_dot_q4_0` | **Q4_0 量化点积** — ARM NEON / AVX2 / WASM / scalar 四实现 (最长的函数之一) |
| 2.3.5 | L1567-L1613 | `ggml_vec_dot_q4_1` | Q4_1 量化点积 (scalar 实现) |
| 2.3.6 | L1613-L1666 | `ggml_vec_dot_f16_unroll` | F16 批量点积 (用于注意力计算) |
| 2.3.7 | L1666-L1728 | `ggml_vec_mad_f32` | **F32 乘累加** (MAD = Multiply-Add, y += x*v) — SIMD 加速 |
| 2.3.8 | L1728-L1857 | `ggml_vec_mad_f16`, `ggml_vec_mad_q4_0`, `ggml_vec_mad_q4_1` | 各类型乘累加实现 |
| 2.3.9 | L1858-L1887 | `ggml_vec_scale_f32`, `ggml_vec_norm_f32` | 缩放和范数 |
| 2.3.10 | L1888-L1955 | `ggml_vec_sqr`, `ggml_vec_sqrt`, `ggml_vec_abs`, `ggml_vec_sgn`, `ggml_vec_step`, `ggml_vec_relu`, `ggml_vec_gelu_f16/f32`, `ggml_vec_silu_f16/f32` | 逐元素运算和激活函数 |
| 2.3.11 | L1955-L1979 | `ggml_vec_sum_f32`, `ggml_vec_max_f32`, `ggml_vec_norm_inv_f32` | 规约运算 |

**关键概念**：
- **SIMD 加速模式**：所有向量运算都采用 "SIMD 处理对齐部分 + 标量处理尾部" 的模式
- **GGML_F32_STEP / GGML_F16_STEP**：每次迭代处理的元素数，随平台变化 (16/32)
- **ggml_float = double**：累加用 double 精度，减少浮点误差

---

## 阶段三：类型系统与核心数据结构 (L1980-L2249)

**目标**：掌握类型元数据、对象模型、Context 结构

| 区块 | 行号 | 内容 |
|------|------|------|
| 3.1 | L1980-L2030 | 调试宏 (`GGML_PRINT_DEBUG`, `GGML_PRINT`) |
| 3.2 | L2031-L2070 | **类型元数据表** — `GGML_BLCK_SIZE[7]`, `GGML_TYPE_SIZE[7]` (每个类型的块大小和字节大小) |
| 3.3 | L2070-L2100 | **操作标签/符号表** — `GGML_OP_LABEL[34]`, `GGML_OP_SYMBOL[34]` (用于调试和可视化) |
| 3.4 | L2124-L2141 | **`struct ggml_object`** — 内存池中的对象节点 (`offs`, `size`, `next` 链表指针) |
| 3.5 | L2142-L2155 | **`struct ggml_context`** — 核心上下文 (`mem_size`, `mem_buffer`, `objects_begin/end`, `scratch`, `scratch_save`) |
| 3.6 | L2156-L2165 | `struct ggml_context_container` — 全局 Context 容器 (`used` 标记 + `context`) |
| 3.7 | L2166-L2171 | **`enum ggml_task_type`** — 任务类型 (INIT / COMPUTE / FINALIZE) |
| 3.8 | L2172-L2185 | **`struct ggml_compute_params`** — 计算参数 (`type`, `ith`=线程号, `nth`=总线程数, `wsize`/`wdata`=工作缓冲区) |
| 3.9 | L2186-L2249 | **`struct ggml_state`** + 全局状态 `g_state`、**临界区屏障** (`g_state_barrier`, `ggml_critical_section_start/end`) |

**关键概念**：
- **内存池架构**：Context 管理一块预分配内存，所有 tensor 通过 object 链表从中分配，无运行时 malloc
- **三阶段计算**：INIT (初始化/分配输出) → COMPUTE (并行计算) → FINALIZE (归约/收尾)
- **临界区**：用原子操作实现的自旋锁屏障，保护全局状态初始化

---

## 阶段四：Context 初始化与 Tensor 管理 (L2250-L3020)

**目标**：理解内存分配机制和 Tensor 生命周期

| 区块 | 行号 | 内容 |
|------|------|------|
| 4.1 | L2250-L2348 | **工具函数** — `ggml_blck_size`, `ggml_type_size`, `ggml_type_sizef`, `ggml_is_scalar`, `ggml_is_vector`, `ggml_is_matrix`, `ggml_can_mul_mat`, `ggml_is_contiguous`, `ggml_are_same_shape`, `ggml_can_repeat`, `ggml_up32/64`, `ggml_assert_aligned` |
| 4.2 | L2349-L2440 | **`ggml_init`** — Context 初始化：从全局容器获取槽位，设置内存池大小/缓冲区，初始化 object 链表 |
| 4.3 | L2440-L2483 | **`ggml_free`** — Context 销毁：释放内存缓冲区，归还全局容器槽位 |
| 4.4 | L2483-L2603 | **`ggml_new_tensor_impl`** — 核心 Tensor 创建：从内存池分配空间，设置 type/ne/nb，挂入 object 链表，设置 padding |
| 4.5 | L2603-L2648 | **Tensor 工厂函数** — `ggml_new_tensor_1d/2d/3d/4d` (设置维度和步长) |
| 4.6 | L2648-L2743 | **标量 Tensor** — `ggml_new_i32`, `ggml_new_f32`, `ggml_dup_tensor` |
| 4.7 | L2678-L2743 | **Tensor 赋值** — `ggml_set_zero`, `ggml_set_i32`, `ggml_set_f32` (逐元素设置) |
| 4.8 | L2743-L2984 | **数据访问** — `ggml_get_f32_1d`, `ggml_set_f32_1d`, `ggml_get_data`, `ggml_get_data_f32`, `ggml_get_i32_1d` |
| 4.9 | L2984-L3020 | **Tensor 视图** — `ggml_view_tensor` (共享数据，不复制), `ggml_dup_impl` (创建副本) |

**内存分配流程**：
```
ggml_init(mem_size) → 分配/使用内存池
    ↓
ggml_new_tensor_impl() → 在内存池中分配 sizeof(ggml_object) + sizeof(ggml_tensor) + data_size
    ↓  (内存池指针后移，object 链表追加)
ggml_free() → 释放内存池，归还全局槽位
```

**nb[] 步长计算规则**：
```
nb[0] = GGML_TYPE_SIZE[type]              // 一个元素的字节大小
nb[1] = (nb[0] * ne[0]) / GGML_BLCK_SIZE[type]  // 一行的字节跨度 (考虑量化块对齐)
nb[2] = nb[1] * ne[1]                    // 一个平面的字节跨度
nb[3] = nb[2] * ne[2]                    // 一个3D体的字节跨度
```

---

## 阶段五：操作符注册 (L3020-L4170)

**目标**：理解每个操作符如何创建计算图节点

每个操作符遵循统一的三函数模式：
1. `_impl` 函数：创建 tensor 节点，设置 op/src0/src1，返回新节点
2. 公共函数 (无后缀)：调用 `_impl(inplace=false)` — 创建新输出 tensor
3. `_inplace` 函数：调用 `_impl(inplace=true)` — 复用输入 tensor 的数据

| 区块 | 行号 | 操作 | 说明 |
|------|------|------|------|
| 5.1 | L3020-L3060 | **DUP** | 复制张量 |
| 5.2 | L3028-L3060 | **ADD** | 逐元素加法 |
| 5.3 | L3067-L3100 | **SUB** | 逐元素减法 |
| 5.4 | L3106-L3140 | **MUL** | 逐元素乘法 (Hadamard 积) |
| 5.5 | L3149-L3185 | **DIV** | 逐元素除法 |
| 5.6 | L3192-L3220 | **SQR** | 平方 |
| 5.7 | L3226-L3255 | **SQRT** | 平方根 |
| 5.8 | L3260-L3280 | **SUM** | 求和 (规约) |
| 5.9 | L3281-L3305 | **MEAN** | 均值 (规约) |
| 5.10 | L3304-L3332 | **REPEAT** | 沿维度复制 (广播) |
| 5.11 | L3332-L3435 | **ABS / SGN / NEG** | 绝对值、符号、取反 |
| 5.12 | L3435-L3469 | **STEP** | 阶跃函数 |
| 5.13 | L3469-L3503 | **RELU** | 线性整流 |
| 5.14 | L3503-L3537 | **GELU** | 高斯误差线性单元 |
| 5.15 | L3537-L3571 | **SILU** | Sigmoid 加权线性单元 |
| 5.16 | L3571-L3606 | **NORM** | 归一化 |
| 5.17 | L3606-L3631 | **MUL_MAT** ★ | **矩阵乘法** — 最核心的操作 |
| 5.18 | L3631-L3665 | **SCALE** | 逐元素缩放 |
| 5.19 | L3665-L3715 | **CPY** | 复制/类型转换 |
| 5.20 | L3715-L3795 | **RESHAPE** | 重塑维度 (1d/2d/3d) |
| 5.21 | L3795-L3845 | **VIEW** | 视图 (1d/2d) — 不复制数据 |
| 5.22 | L3845-L3906 | **PERMUTE** | 维度重排 (4维通用) |
| 5.23 | L3906-L3934 | **TRANSPOSE** | 转置 (2D 特化) |
| 5.24 | L3934-L3961 | **GET_ROWS** | 按索引获取行 |
| 5.25 | L3961-L3987 | **DIAG_MASK_INF** | 注意力掩码 (对角线设 -INF) |
| 5.26 | L3987-L4011 | **SOFT_MAX** | Softmax |
| 5.27 | L4011-L4044 | **ROPE** | 旋转位置编码 |
| 5.28 | L4044-L4098 | **CONV_1D_1S / CONV_1D_2S** | 1D 卷积 (步长1/2) |
| 5.29 | L4098-L4130 | **FLASH_ATTN** ★ | Flash Attention (融合注意力) |
| 5.30 | L4129-L4170 | **FLASH_FF** ★ | Flash Feed-Forward (融合前馈) |

---

## 阶段六：操作符前向计算实现 (L4170-L8583)

**目标**：掌握每个操作符的实际计算逻辑，理解 SIMD 调度

这是 ggml.c 中最长的区段 (约 4400 行)，包含所有操作的 `ggml_compute_forward_*` 实现。

### 6.1 基础操作 (L4170-L5410)

| 区块 | 行号 | 函数 | 说明 |
|------|------|------|------|
| 6.1.1 | L4173-L4277 | `compute_forward_dup_f16` | F16 复制 (含量化类型转换) |
| 6.1.2 | L4277-L4381 | `compute_forward_dup_f32` | F32 复制 |
| 6.1.3 | L4381-L4408 | `compute_forward_dup` | DUP 调度 |
| 6.1.4 | L4408-L4486 | `compute_forward_add_f32` | **F32 加法** — 多线程分块，SIMD 加速 |
| 6.1.5 | L4486-L4642 | `compute_forward_sub/mul/div_f32` | 逐元素算术运算 (模式同 ADD) |
| 6.1.6 | L4642-L4738 | `compute_forward_sqr/sqrt_f32` | 平方/平方根 |
| 6.1.7 | L4738-L4873 | `compute_forward_sum/mean_f32` | 规约运算 — **注意线程分工**：ith=0 做最终归约 |
| 6.1.8 | L4873-L4937 | `compute_forward_repeat_f32` | 广播复制 |
| 6.1.9 | L4937-L5081 | `compute_forward_abs/sgn/neg_f32` | 逐元素数学运算 |
| 6.1.10 | L5081-L5177 | `compute_forward_step/relu_f32` | 阶跃/ReLU |
| 6.1.11 | L5177-L5244 | `compute_forward_gelu_f32` | GELU (使用查找表或公式) |
| 6.1.12 | L5244-L5310 | `compute_forward_silu_f32` | SILU (使用查找表或公式) |
| 6.1.13 | L5310-L5419 | `compute_forward_norm_f32` | **归一化** — 减均值除方差，关键实现 |

### 6.2 矩阵乘法 (L5419-L6570) ★★★ 最核心区段

| 区块 | 行号 | 函数 | 说明 |
|------|------|------|------|
| 6.2.1 | L5419-L5664 | `compute_forward_mul_mat_f32` | **F32 矩阵乘法** — 行分块并行，调用 `ggml_vec_dot_f32` |
| 6.2.2 | L5664-L5970 | `compute_forward_mul_mat_f16_f32` | **F16×F32 混合精度矩阵乘** — 转换后计算 |
| 6.2.3 | L5970-L6270 | `compute_forward_mul_mat_q4_0_f32` | **Q4_0×F32 量化矩阵乘** — 直接用量化点积 |
| 6.2.4 | L6270-L6570 | `compute_forward_mul_mat_q4_1_f32` | **Q4_1×F32 量化矩阵乘** — 直接用量化点积 |
| 6.2.5 | L6570-L6632 | `compute_forward_mul_mat` | MUL_MAT 调度 — 根据 src0 类型分发 |

**矩阵乘法并行策略**：
```
对于 C = A × B (A: ne0×ne1, B: ne0×1):
- 沿 A 的行维度 (ne1) 分块给不同线程
- 每个线程处理 ith 到 ne1 步 nth 的行
- 每行调用 ggml_vec_dot_* 做内积
- INIT 阶段：分配工作缓冲区
- COMPUTE 阶段：并行计算
- FINALIZE 阶段：空操作
```

### 6.3 结构操作与高级运算 (L6632-L8583)

| 区块 | 行号 | 函数 | 说明 |
|------|------|------|------|
| 6.3.1 | L6632-L6692 | `compute_forward_scale/cpy` | 缩放和类型转换 |
| 6.3.2 | L6692-L6743 | `compute_forward_reshape/view/permute/transpose` | 结构操作 (零计算，仅修改元数据) |
| 6.3.3 | L6743-L6852 | `compute_forward_get_rows_*` | 按索引获取行 (Q4_0/Q4_1/F16/F32 多实现) |
| 6.3.4 | L6904-L6965 | `compute_forward_diag_mask_inf_f32` | **注意力掩码** — 对角线以上设为 -INFINITY |
| 6.3.5 | L6965-L7059 | `compute_forward_soft_max_f32` | **Softmax** — 数值稳定实现 (减最大值)，4路展开 |
| 6.3.6 | L7059-L7200 | `compute_forward_rope_f32/f16` | **旋转位置编码** — 二维旋转矩阵应用于相邻元素对 |
| 6.3.7 | L7200-L7736 | `compute_forward_conv_1d_1s/2s_*` | **1D 卷积** — F16/F32 多实现，步长1和步长2 |
| 6.3.8 | L7736-L8191 | `compute_forward_flash_attn_f32/f16` | **★ Flash Attention** — 融合 Q·K^T + softmax + V·S，避免中间结果显存占用 |
| 6.3.9 | L8221-L8449 | `compute_forward_flash_ff_f16` | **★ Flash Feed-Forward** — 融合线性层 + GELU + 线性层 |

### 6.4 前向计算总调度 (L8449-L8583)

| 区块 | 行号 | 内容 |
|------|------|------|
| 6.4.1 | L8449-L8583 | **`ggml_compute_forward`** — 巨型 `switch` 语句，根据 `tensor->op` 分发到对应的 `compute_forward_*` 函数 |

> 这是整个前向计算的"中央调度室"。每执行一个计算图节点，都经过这个 switch 分发。

---

## 阶段七：自动微分与反向传播 (L8584-L9020)

**目标**：理解梯度如何在计算图中反向流动

### 7.1 反向传播实现 (L8584-L8855)

| 区块 | 行号 | 内容 |
|------|------|------|
| 7.1.1 | L8584-L8855 | **`ggml_compute_backward`** — 对每种操作实现梯度计算规则 |

**反向传播规则摘要** (对应前向操作)：

| 前向操作 | 反向传播规则 |
|---------|-------------|
| `z = ADD(x, y)` | `∂L/∂x += ∂L/∂z`, `∂L/∂y += ∂L/∂z` |
| `z = SUB(x, y)` | `∂L/∂x += ∂L/∂z`, `∂L/∂y -= ∂L/∂z` |
| `z = MUL(x, y)` | `∂L/∂x += y * ∂L/∂z`, `∂L/∂y += x * ∂L/∂z` |
| `z = DIV(x, y)` | `∂L/∂x += ∂L/∂z / y`, `∂L/∂y -= (z/y) * ∂L/∂z` |
| `z = SQR(x)` | `∂L/∂x += 2x * ∂L/∂z` |
| `z = SUM(x)` | `∂L/∂x[i] += ∂L/∂z` (广播) |
| `z = MEAN(x)` | `∂L/∂x[i] += ∂L/∂z / N` (广播) |
| `z = REPEAT(x)` | `∂L/∂x += sum_over_repeats(∂L/∂z)` |
| `z = NORM(x)` | 通过数值差分求梯度 (没有解析实现) |
| `z = MUL_MAT(x, y)` | `∂L/∂x += ∂L/∂z × y^T`, `∂L/∂y += x^T × ∂L/∂z` |
| `z = SCALE(x, v)` | `∂L/∂x += v * ∂L/∂z` |
| `z = SILU(x)` | `∂L/∂x += σ(x) * (1 + x*(1-σ(x))) * ∂L/∂z` |
| `z = ROPE(x)` | 梯度计算使用共轭旋转 |

> **注意**：NORM 的梯度使用**数值差分** (`ggml_vec_norm_inv_f32`)，而非解析表达式。这是潜在的精度问题。

### 7.2 计算图构建 (L8856-L9020)

| 区块 | 行号 | 内容 |
|------|------|------|
| 7.2.1 | L8856-L8890 | **`ggml_visit_parents`** — 递归访问张量的所有父节点，构建拓扑排序 |
| 7.2.2 | L8890-L8927 | **`ggml_build_forward_impl`** — 前向图构建：调用 `visit_parents`，将节点/叶子填入 `cgraph` |
| 7.2.3 | L8927-L8907 | **`ggml_build_forward`** — 创建空的 `ggml_cgraph`，调用 `build_forward_impl` |
| 7.2.4 | L8907-L8970 | **`ggml_build_backward`** — 反向图构建：倒序遍历前向节点，为每个有梯度的节点调用 `compute_backward`，再构建参数梯度的前向图 |
| 7.2.5 | L8970-L9020 | `ggml_build_forward_expand` — 扩展已有计算图 (不清空) |

**计算图构建流程**：
```
1. ggml_build_forward(loss_tensor)
   → 递归遍历所有父节点
   → 按拓扑排序填入 cgraph->nodes[]
   → 叶子节点 (无 op 或无 grad) 填入 cgraph->leafs[]

2. ggml_build_backward(ctx, &forward_graph, keep=false)
   → 倒序遍历 forward_graph->nodes[]
   → 对每个有 grad 的节点调用 compute_backward()
   → 为参数节点的梯度构建前向计算图
```

---

## 阶段八：计算图执行引擎 (L9020-L9550)

**目标**：理解多线程调度和工作窃取机制

| 区块 | 行号 | 内容 |
|------|------|------|
| 8.1 | L9020-L9035 | **`struct ggml_compute_state_shared`** — 共享状态：自旋锁、就绪计数器、工作标志、停止标志 |
| 8.2 | L9035-L9044 | **`struct ggml_compute_state`** — 每线程状态：线程句柄、计算参数、当前节点、共享状态指针 |
| 8.3 | L9044-L9092 | **`ggml_graph_compute_thread`** — Worker 线程函数：忙等待→获取工作→执行→通知完成→等待下一轮 |
| 8.4 | L9092-L9280 | **`ggml_graph_compute`** — 主函数：创建线程池、INIT 遍历 (计算工作缓冲区大小)、COMPUTE 遍历 (分发节点到线程)、FINALIZE 遍历 |
| 8.5 | L9280-L9540 | 图工具函数：`ggml_graph_find`, `ggml_graph_get_parent`, `ggml_graph_reset` |
| 8.6 | L9550-L9618 | **`ggml_graph_print`** — 打印计算图信息 (节点/叶子/性能) |
| 8.7 | L9618-L9700 | **`ggml_graph_dump_dot`** — 导出 Graphviz DOT 格式 (可视化计算图) |

**多线程调度流程**：
```
ggml_graph_compute():
  1. 创建 n_threads-1 个 worker 线程
  2. INIT 阶段：顺序遍历所有节点，计算工作缓冲区需求
  3. 分配工作缓冲区
  4. COMPUTE 阶段：对每个节点
     a. 设置所有线程的 params (ith, nth, wdata)
     b. 设置 shared.has_work = true
     c. 主线程自己执行 ith=0 的任务
     d. 等待所有线程完成
  5. FINALIZE 阶段：同 COMPUTE
  6. 停止 worker 线程
```

**调试技巧**：用 `ggml_graph_dump_dot` 导出计算图，然后用 `dot -Tpng graph.dot -o graph.png` 生成可视化图。

---

## 阶段九：优化器实现 (L9700-L10402)

**目标**：掌握 Adam 和 L-BFGS 优化算法的实现

### 9.1 优化器基础设施 (L9700-L9774)

| 区块 | 行号 | 内容 |
|------|------|------|
| 9.1.1 | L9700-L9774 | `ggml_opt_set_params` / `ggml_opt_get_params` / `ggml_opt_get_grad` — 参数和梯度的扁平化读写 |

### 9.2 Adam 优化器 (L9774-L9938)

| 区块 | 行号 | 内容 |
|------|------|------|
| 9.2.1 | L9774-L9938 | **`ggml_opt_adam`** — Adam 算法实现 |

**Adam 算法流程**：
```
初始化: m = 0 (一阶矩), v = 0 (二阶矩), t = 0
每步迭代:
  1. 前向计算: ggml_graph_compute(gf)
  2. 反向传播: ggml_graph_compute(gb)
  3. 更新参数:
     t += 1
     m = β1*m + (1-β1)*g          // 一阶矩估计
     v = β2*v + (1-β2)*g²         // 二阶矩估计
     m̂ = m / (1 - β1^t)           // 偏差修正
     v̂ = v / (1 - β2^t)           // 偏差修正
     x -= α * m̂ / (√v̂ + ε)       // 参数更新
```

### 9.3 L-BFGS 优化器 (L9938-L10290)

| 区块 | 行号 | 内容 |
|------|------|------|
| 9.3.1 | L9938-L9961 | `struct ggml_lbfgs_iteration_data` — L-BFGS 迭代数据 (alpha, ys, s[], y[]) |
| 9.3.2 | L9961-L10067 | **`linesearch_backtracking`** — 回溯线搜索 (Wolfe 条件) |
| 9.3.3 | L10067-L10290 | **`ggml_opt_lbfgs`** — L-BFGS 算法实现 |

### 9.4 优化器入口 (L10290-L10402)

| 区块 | 行号 | 内容 |
|------|------|------|
| 9.4.1 | L10290-L10351 | **`ggml_opt_default_params`** — 默认参数初始化 (Adam: α=0.001, β1=0.9, β2=0.999; L-BFGS: m=6, ε=1e-5) |
| 9.4.2 | L10351-L10402 | **`ggml_opt`** — 优化器入口：根据 `params.type` 分发到 `ggml_opt_adam` 或 `ggml_opt_lbfgs` |

---

## 阶段十：SIMD 优化全景 (分散各处)

**目标**：建立对跨平台 SIMD 策略的整体理解

SIMD 代码分布在 ggml.c 的多个位置，以下按平台汇总：

| 平台 | 宏定义层 | 量化 | 点积/乘累加 | 矩阵乘 |
|------|---------|------|-----------|--------|
| **ARM NEON** | L750-L900 | L404-L530 | L1296-L1390 | L5419-L5664 |
| **x86 AVX2** | L330-L399 (辅助函数) | L488-L590 | L1390-L1480 | L5419-L5664 |
| **x86 SSE3** | L1080-L1200 | 无专用 | L1223-L1260 (通过宏) | L5419-L5664 (通过宏) |
| **WASM SIMD** | L900-L1080 | L1480-L1560 | L1480-L1560 | L5970-L6270 |
| **Power9** | 无 (用 altivec.h) | 无 | 无 | 无 |
| **scalar** | 无 | L590-L650 | L1540-L1567 | L5419-L5664 |

**代码阅读策略**：
1. 先读 scalar 实现 — 理解算法逻辑
2. 再读 NEON 实现 — 最清晰的 SIMD 代码
3. 最后读 AVX2/WASM — 理解平台差异

---

## 推荐阅读顺序

```
前置: ggml.h (约740行)         → 理解所有类型定义和 API
  ↓
阶段一 (L1-399)                → 基础设施和 FP16 转换
  ↓
阶段二 (L400-1979)             → 量化、SIMD 抽象、向量运算 【重点】
  ↓
阶段三 (L1980-2249)            → 类型系统和核心数据结构
  ↓
阶段四 (L2250-3020)            → Context 和 Tensor 管理
  ↓
阶段五 (L3020-4170)            → 操作符注册 (快速浏览，了解模式即可)
  ↓
阶段六 (L4170-8583)            → 前向计算实现 【重点，可按需深读】
  ↓  建议重点: MUL_MAT (L5419-6632), SOFT_MAX (L6965), ROPE (L7059), FLASH_ATTN (L7736)
  ↓
阶段七 (L8584-9020)            → 自动微分与反向传播 【重点】
  ↓
阶段八 (L9020-9550)            → 计算图执行引擎 (多线程)
  ↓
阶段九 (L9700-10402)           → 优化器实现
```

---

## 熟练级标准

达到熟练级意味着能够：

1. **理解内存布局**：能计算任意形状 Tensor 在内存池中的偏移量，理解 `ne[]`/`nb[]` 关系
2. **添加新操作**：按照 `_impl` → 公共函数 → `_inplace` → `compute_forward_*` → `compute_backward_*` → `ggml_compute_forward switch` 的模式添加新操作
3. **优化性能**：理解 SIMD 调度策略，选择正确的量化和计算路径
4. **调试计算图**：能使用 `ggml_graph_dump_dot` 导出图、追踪前向/反向传播中的 bug
5. **理解多线程调度**：理解 INIT/COMPUTE/FINALIZE 三阶段和线程池工作模式
6. **集成到应用**：能将 GGML 集成到新的 ML 应用中，正确使用 Context/Graph/Compute 流程
7. **修改优化器**：能调整 Adam/L-BFGS 参数或实现新的优化算法

---

## 建议的深入研究路径

| 优先级 | 主题 | 行号 | 理由 |
|--------|------|------|------|
| ★★★★★ | **MUL_MAT 量化矩阵乘** | L5419-L6632 | 推理性能的核心瓶颈，理解量化推理的关键 |
| ★★★★★ | **SIMD 抽象层** | L750-L1200 | 理解跨平台 SIMD 封装的设计模式 |
| ★★★★ | **Flash Attention** | L7736-L8191 | 现代 Transformer 的核心优化，融合算子减少显存 |
| ★★★★ | **自动微分** | L8584-L8855 | 理解如何从计算图自动求梯度 |
| ★★★ | **Q4_0/Q4_1 量化** | L400-L750, L1296-L1613 | 量化推理的基础，理解精度-速度权衡 |
| ★★★ | **ROPE 旋转位置编码** | L7059-L7200 | LLaMA 等模型的核心位置编码方案 |
| ★★ | **计算图执行引擎** | L9044-L9280 | 多线程调度的工程实现 |
| ★★ | **L-BFGS 优化器** | L10067-L10290 | 比较少用，但理解拟牛顿法有价值 |