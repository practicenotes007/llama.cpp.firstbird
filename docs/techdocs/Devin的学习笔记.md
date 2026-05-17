# Devin 的 ggml 学习笔记

基于 `/home/ubuntu/2026.llama.cpp/llama.cpp.firstbird/ggml.c` 源码阅读整理

---

## 1. 基础设施层

### 1.1 原子操作与线程管理 (L34-39)

```c
typedef volatile atomic_int ggml_atomic_int;
```

- `volatile` 防止编译器优化掉看似"无用"的内存读写
- `atomic_int` 保证多线程读写的原子性
- 用途: 工作窃取调度器中的任务计数、线程屏障同步

### 1.2 内存对齐 (L50-62)

```c
#define GGML_MEM_ALIGN 16
#define GGML_ASSERT_ALIGNED(ptr) ...
```

- 16 字节对齐是 SIMD 加载指令 (`_mm_load_ps`, `vld1q_f32`) 的硬性要求
- 未对齐访问: `loadu` 可以但更慢; `load` 未对齐会触发 SIGBUS
- `GGML_ASSERT_ALIGNED` 仅在 debug 模式生效, 生产环境无开销

### 1.3 浮点类型处理 (L64-95)

**FP16 (半精度浮点数):**
- IEEE 754 半精度: 1 位符号 + 5 位指数 + 10 位尾数 = 16 bit
- 表示范围: ±65504, 精度约 3.3 位十进制
- 用途: 模型权重存储 (节省 50% 内存)

**FP16 → FP32 转换 (软件实现, 无硬件指令时):**
- 规格化数: 指数偏移 + 尾数扩展
- 非规格化数: 魔术常量技巧 (利用 FP32 的表示特性)
- 约 15-20 条指令

**跨平台策略:**
```
ARM NEON:  vcvt_f32_f16 指令 (1 条, 硬件原生)
x86 F16C:  vcvtps2ph 指令    (1 条, 硬件原生)
其他平台:   软件计算 / 查表
```

**FP32 → FP16 不能查表的原因:**
- FP16 只有 2^16 = 65536 种值 → 查表可行 (256 KB)
- FP32 有 2^32 = 4.3 亿种值 → 查表需要 8 GB, 不可行
- 且 FP32→FP16 是多对一映射, 有精度损失, 需要舍入判断

### 1.4 SIMD 实现体系 (L97-164)

```
平台           | 指令集       | 寄存器宽度 | 向量类型      | 4-bit 解包
ARM            | NEON         | 128 bit   | float32x4_t   | vandq + vshrq
x86 (SSE)      | SSE/AVX      | 128/256   | __m128/__m256 | (SSE 无专用)
x86 (AVX2)     | AVX2         | 256 bit   | __m256i       | bytesFromNibbles
WASM           | WASM SIMD    | 128 bit   | v128_t        | (未实现)
```

**float32x4_t 解读:**
- ARM NEON 的 128 位向量寄存器类型
- 内含 4 个 32 位 float, 所有操作 4 值同时进行
- 命名规则: `{类型}{位宽}x{个数}_t`
- 对应 x86 的 `__m128`

### 1.5 预计算查表 (L243-257)

```c
static ggml_fp16_t table_gelu_f16[1 << 16];   // GELU 查表, 128 KB
static ggml_fp16_t table_silu_f16[1 << 16];   // SiLU 查表, 128 KB
static ggml_fp16_t table_exp_f16[1 << 16];    // exp 查表, 128 KB
static float table_f32_f16[1 << 16];           // FP16→FP32 查表, 256 KB
```

- 激活函数查表: 输入 FP16 的 bit pattern 作下标, 直接得到激活后的 FP16 值
- 类型转换查表: 3 条指令 vs 软件计算 15-20 条指令, 快 5-7 倍
- 代价: 共 640 KB 静态内存 (服务器可忽略, 嵌入式需评估)

### 1.6 计时器 (L284-326)

```
Windows:  QueryPerformanceCounter + QueryPerformanceFrequency
          需要初始化获取频率, 手动换算毫秒/微秒

Linux:    clock_gettime(CLOCK_MONOTONIC)
          内核直接返回 {秒, 纳秒}, 无需初始化

Wall time (墙上时间): 真实经过的时间, 包含等待
CPU time (CPU 时间):  CPU 实际花的时间, sleep 不计数

两者配合可分析瓶颈:
  cpu_time / wall_time < 1 → 瓶颈在等待 (内存/IO), 非计算
```

### 1.7 缓存行对齐 (L340-353)

```c
#define CACHE_LINE_SIZE 64   // x86/ARM 默认
                              // IBM POWER9: 128
```

**伪共享 (False Sharing):**
- 多线程各自写不同变量, 但变量地址落在同一缓存行
- 硬件缓存一致性协议导致互相使对方缓存行失效
- 解决: 变量间填充 padding 到 CACHE_LINE_SIZE
- 代价: 几 KB 空间浪费 vs 几 GB 计算加速

**关键区分:**
- 不需要 padding: 只读共享变量、单线程独占变量、用锁保护的数据
- 需要 padding: 多线程各自频繁写入的不同变量, 且地址太近

---

## 2. 量化体系

### 2.1 基本分块 (L360)

```c
#define QK 32   // 每个量化块包含 32 个 float
```

- 32 个 float = 128 字节 = 2 个缓存行
- SIMD 友好: NEON 8 次 / AVX2 4 次 / AVX-512 2 次
- 模型维度几乎都是 32 的倍数

### 2.2 Q4_0: 对称分块均匀量化 (L401-)

**格式:** 每 32 个 float → 1 个 float(delta) + 16 字节(4-bit packed) = 18 字节

**算法:**
```
1. 找最大绝对值 amax
2. delta = amax / 7    (7 = 2^3-1, 4-bit 有符号范围 -8~+7)
3. q = round(x / delta + 8.5)   // +8.5 偏移到 [0,15]
4. 反量化: x_approx = (q - 8) * delta
```

**内存布局 (AoS):**
```
[d0][packed0] [d1][packed1] [d2][packed2] ...
 ←── block 0 ──→ ←── block 1 ──→
```

**压缩比:** 128 字节 → 18 字节 ≈ 7.1:1 (每参数 4.5 bit)

**局限:** 整块共享一个 delta, 值范围差异大时小值精度丢失

### 2.3 Q4_1: 非对称分块均匀量化 (L603-)

**格式:** 每 32 个 float → 1 个 float(min) + 1 个 float(delta) + 16 字节 = 22 字节

**算法:**
```
1. 找最小值 min 和最大值 max
2. delta = (max - min) / 15    (15 = 2^4-1, 4-bit 无符号范围 0~15)
3. q = round((x - min) / delta)
4. 反量化: x_approx = min + q * delta
```

**内存布局 (SoA):**
```
[min0,min1,...,min_{nb-1}] [d0,d1,...,d_{nb-1}] [packed0,packed1,...]
 ←──── min 区 ────→        ←── delta 区 ──→       ←── packed 区 ──→
```

**优势:** min 自适应偏移, 全部量化级别都在有效范围内

### 2.4 AoS vs SoA 的选择驱动

```
Q4_0 用 AoS:
  只有 1 个 float (delta), dot product 中 d0*d1 逐 block 做
  → 不需要批量加载 delta, broadcast 即可
  → AoS 足够

Q4_1 用 SoA:
  有 2 个 float (min+delta), dot product 展开后参与 4 项运算
  → 需要批量加载 min/delta, SIMD _mm256_load_ps 要求连续内存
  → AoS 的 gather 指令延迟 8-12 周期, 比 load 的 1 周期慢 10 倍
  → SoA 必需

驱动链条:
  算法设计 (多一个 min) → 计算量翻倍 → 需要向量化 → 需要连续内存 → 选择 SoA
```

### 2.5 量化的学术与工业谱系

```
Q4_0/Q4_1 的数学原理 (Symmetric/Asymmetric Uniform Quantization):
  → 业界通用, 非独创
  → Google 2018 年 TFLite 就用了相同的 asymmetric 方案

ggml 的独创价值:
  → 具体 block 大小 (32)、nibble 打包方式、AoS/SoA 布局、手写 SIMD kernel
  → 这些工程落地细节才是核心竞争力

量化演进:
  第一代: Q4_0/Q4_1 (简单均匀量化, 无需校准)
  第二代: GPTQ/AWQ (Hessian/activation 感知, 需要校准数据)
  第三代: QuIP/AQLM (非均匀/码本量化, 2-3 bit)
  ggml 位置: 第一代, 选择了最简单最快最容易在 CPU 落地的方案
```

### 2.6 bytesFromNibbles (L367-382) — 4-bit 解包

**功能:** 把 16 字节 (含 32 个 4-bit 值) 解包成 32 字节 (每值 1 字节)

**当前实现 (andnot + slli):**
```c
inline __m256i bytesFromNibbles(const uint8_t* rsi) {
    __m128i tmp = _mm_loadu_si128(...);
    __m256i bytes = _mm256_cvtepu8_epi16(tmp);       // 扩展到 16 位
    __m256i high = _mm256_andnot_si256(lowMask, bytes); // 提取高 nibble
    __m256i low = _mm256_and_si256(lowMask, bytes);    // 提取低 nibble
    high = _mm256_slli_epi16(high, 4);                 // 位操作技巧
    bytes = _mm256_or_si256(low, high);
    return bytes;
}
```

**优化方案 (extract + unpack):**
```c
inline __m256i bytesFromNibbles(const uint8_t* rsi) {
    __m128i tmp = _mm_loadu_si128(...);
    __m128i lo = _mm_and_si128(tmp, _mm_set1_epi8(0x0F));      // 提取低 nibble
    __m128i hi = _mm_and_si128(_mm_srli_epi16(tmp, 4), ...);   // 提取高 nibble
    __m256i result = _mm256_set_m128i(
        _mm_unpackhi_epi8(lo, hi), _mm_unpacklo_epi8(lo, hi)); // 交错排列
    return result;
}
```

优势: 逻辑更清晰, 前 5 条 128 位操作延迟更低, 仅最后一步升 256 位

### 2.7 dequantize_row_q4_0 (L650-) — 反量化

当前只有标量版本 (代码标注 `// TODO: vectorize`)

**AVX2 向量化思路:**
1. 加载 16 字节 packed → 提取低/高 nibble
2. cvtepu8_epi16 → sub_epi16(减8) → cvtepi16_epi32 → cvtepi32_ps
3. mul_ps(scale) → storeu_ps 写出
4. 一个 block 约 15 条 SIMD 指令, 预期加速 8-10 倍

**注意输出顺序:** 标量版低高交替 [l0,h0,l1,h1...], SIMD 版低半在前高半在后 [l0..l15,h0..h15], 只要全链路一致即可

### 2.8 量化点积 vec_dot_q4_0 (L1296-1565)

**核心概念: 两个 Q4_0 量化向量的点积**

```
常规思路: vec_dot(量化权重, FP32激活)
  → 逐个反量化为 FP32, 再和 FP32 激活相乘
  → 每个值都要做一次反量化

ggml 做法: vec_dot(Q4_0权重, Q4_0激活)
  → 推理时先把 FP32 激活也量化为 Q4_0
  → 两个 Q4_0 向量做纯整数点积
  → 最后一步乘缩放因子
```

**权重 vs 激活:**

```
┌──────────┬─────────────────────┬──────────────────────┐
│          │ 权重 (Weight)        │ 激活 (Activation)     │
├──────────┼─────────────────────┼──────────────────────┤
│ 是什么   │ 模型参数, 训练好的    │ 运行时算出的中间值     │
│ 什么时候有│ 模型加载后一直不变     │ 每次推理时实时计算     │
│ 量化可行性│ ✅ 可以离线量化存盘    │ ⚠️ 运行时才知道值      │
│ 存储位置  │ 硬盘/内存 (长期)      │ GPU/CPU 寄存器 (临时) │
│ 量化误差  │ 较小 (分布均匀)       │ 较大 (有离群值)       │
└──────────┴─────────────────────┴──────────────────────┘
```

**整数域乘加优化:**

```
Q4_0 点积 = d_x * d_y * sum((qi_x - 8) * (qi_y - 8))
                           ↑ 这部分可以纯整数计算!

AVX2 路径:
  __m256i i32 = _mm256_madd_epi16(x16, y16);  // int16 乘加 → int32
  __m256 p = _mm256_cvtepi32_ps(i32);          // 转 FP32
  acc = _mm256_fmadd_ps(scale, p, acc);         // 乘缩放因子

NEON 路径:
  vmull_s8 → int8 乘法 → int16
  vaddq_s16 → int16 累加
  最后 sum0 += d0_0 * d1_0 * vaddvq_s16(p_0)  // 乘缩放因子
```

**三个 SIMD 平台的实现对比:**

```
┌───────────┬───────────────────────────────────────────────┐
│ ARM NEON  │ 16 字节加载 → vandq+vshrq 解包 → vsubq_s8   │
│           │ → vmull_s8 乘法 → vaddq_s16 累加              │
│           │ → vaddvq_s16 归约 × d0*d1                     │
├───────────┼───────────────────────────────────────────────┤
│ AVX2      │ bytesFromNibbles 解包 → sub_epi8              │
│           │ → cvtepi8_epi16 → _mm256_madd_epi16 乘加     │
│           │ → cvtepi32_ps → fmadd_ps 缩放累加             │
├───────────┼───────────────────────────────────────────────┤
│ WASM SIMD │ wasm_v128_and + wasm_u8x16_shr 解包           │
│           │ → wasm_i16x8_mul 乘法 → wasm_i16x8_add 累加  │
│           │ → 逐 lane 提取求和 × d0*d1 (无 vaddvq 等效)  │
├───────────┼───────────────────────────────────────────────┤
│ 标量兜底   │ 逐字节: v0 & 0xf, v0 >> 4, 减8, 乘d, 累加    │
└───────────┴───────────────────────────────────────────────┘
```

**量化激活的误差问题:**

```
激活的离群值问题:
  block: [0.01, 0.02, ..., 0.01, 5.0]  (31 个小值 + 1 个离群值)
  amax = 5.0, delta = 0.714
  0.01 / 0.714 ≈ 0.014 → 量化为 0 → 反量化为 0.0
  → 小值完全丢失!

误差对比:
  只量化权重: 误差 ε_w
  量化权重+激活: 误差 ε_w + ε_a (更大)

权衡:
  速度: Q4×Q4 最快 (整数乘加 1 周期 vs 浮点 4-5 周期)
  精度: LLM 是概率模型, 困惑度上升 0.1~0.5 可接受
  后续方案: 混合精度 (权重 Q4, 激活 Q8/FP32, KV Cache 按需)
```

---

## 3. 性能分析

### 3.1 SIMD 指令延迟与位宽

```
128 位指令:
  - 只占 1 个执行端口, 两个 128 位 ALU 可并行
  - 吞吐量: 2 条/周期
  - 延迟: 位运算 1 周期, 移位 1 周期, 类型转换 3 周期

256 位指令:
  - 占 2 个执行端口
  - 吞吐量: 1 条/周期
  - 延迟: 同上, 但端口占用更多

原则: 能用 128 位做完的事, 不要急着升 256 位
```

### 3.2 指令条数 vs 周期数 vs 实测

```
粗略评估: 指令条数比 (最粗)
中等评估: 关键路径周期比 (更准)
精确评估: 考虑 ILP + 缓存 (需 profiling)

关键: SIMD 的核心优势不是指令数少, 而是 1 条指令处理 8 个值
      这个并行度是实打实的, 不受指令类型差异影响
```

### 3.3 GGML_PERF 宏 (L328-339)

```c
#ifdef GGML_PERF
  #define ggml_perf_time_us()  ggml_time_us()   // 真实调用
#else
  #define ggml_perf_time_us()  0                 // 零开销
#endif
```

编译期开关: `-DGGML_PERF` 开启全量统计, 生产环境关闭零损耗

### 3.4 水平归约 (L751-772)

```c
#define GGML_F32x4_REDUCE(res, x)   // 树形归约
```

**树形归约 vs 逐个累加:**
```
8 个向量逐个累加: 关键路径 7 步 (串行依赖)
8 个向量树形归约: 关键路径 3 步 (log2(8) = 3, 并行)

轮1: x[0]+x[1], x[2]+x[3], x[4]+x[5], x[6]+x[7]  (4 组并行)
轮2: x[0]+x[2], x[4]+x[6]                           (2 组并行)
轮3: x[0]+x[4]                                       (1 组)
最后: REDUCE_ONE(x[0])  → 4 个 lane 求和
```

**ARM_FEATURE_QRDMX 分支:**
- 有 vaddvq 指令 (ARM v8.2+): 1 条指令完成 4 lane 求和
- 无 vaddvq: 手动 vgetq_lane_f32 提取 4 次 + 3 次加法

### 3.5 AVX2 归约的 hadd 优化 (L872-888)

```c
// 当前方案: 用 _mm_hadd_ps 做 256→标量归约
const __m128 t1 = _mm_hadd_ps(t0, t0);      // 延迟 5 周期
res = _mm_cvtss_f32(_mm_hadd_ps(t1, t1));   // 延迟 5 周期

// 更优方案: 用 shuffle + add 替代 hadd
const __m128 t1 = _mm_movehl_ps(t0, t0);   // 延迟 1
const __m128 t2 = _mm_add_ps(t0, t1);      // 延迟 4
const __m128 t3 = _mm_movehdup_ps(t2);     // 延迟 1
res = _mm_cvtss_f32(_mm_add_ss(t2, t3));   // 延迟 4
```

`_mm_hadd_ps` 延迟 5 周期, 吞吐 1 条/2 周期, 比普通 add 慢 4 倍。但归约在循环中只执行一次, 非热点, 优化收益有限。

---

## 3.6 SIMD 向量操作抽象层 (L774-849)

### F32 向量抽象 (L774-782)

```c
#define GGML_F32_VEC        GGML_F32x4       // 向量类型 (ARM: float32x4_t)
#define GGML_F32_VEC_ZERO   GGML_F32x4_ZERO  // 全零向量
#define GGML_F32_VEC_SET1   GGML_F32x4_SET1  // broadcast 标量到每个通道
#define GGML_F32_VEC_LOAD   GGML_F32x4_LOAD  // 从内存加载
#define GGML_F32_VEC_STORE  GGML_F32x4_STORE // 写回内存
#define GGML_F32_VEC_FMA    GGML_F32x4_FMA   // 融合乘加 a*b+c
#define GGML_F32_VEC_ADD    GGML_F32x4_ADD   // 逐元素加
#define GGML_F32_VEC_MUL    GGML_F32x4_MUL   // 逐元素乘
#define GGML_F32_VEC_REDUCE GGML_F32x4_REDUCE // 归约成标量
```

**设计意图:** 上层算子代码写一套, 编译时根据平台自动展开为 NEON/AVX2/SSE 指令。类似 C 标准库 `fopen/fread` 不关心底层文件系统。

### F16 向量抽象 (L784-849) — 两个分支

```
┌─────────────────────────┬────────────────────────────────┐
│ 有 FP16 向量算术          │ 没有 FP16 向量算术               │
│ (ARM v8.2+, A76/M1+)    │ (旧 ARM, Cortex-A53/A72)       │
├─────────────────────────┼────────────────────────────────┤
│ 直接在 FP16 上计算        │ 加载时转 FP32, 算完再转回 FP16   │
│ float16x8_t (8个/128bit) │ float32x4_t (4个/128bit)       │
│ STEP=32 (8值/指令)       │ STEP=16 (4值/指令)              │
│ vld1q_f16 → vmulq_f16   │ vld1_f16 → vcvt_f32_f16 → 乘  │
│ 快一倍                    │ 兼容旧芯片, 精度更高             │
└─────────────────────────┴────────────────────────────────┘

FP16 归约的特殊处理:
  有 FP16 算术: 树形加法用 vaddq_f16, 最后一步转 FP32 求和
  → FP16 累加容易溢出/丢精度, 最终求和必须用 FP32
  无 FP16 算术: 直接复用 F32 归约, 计算全程已是 FP32
```

---

## 4. 架构与算子

### 4.1 ggml.c 的三层结构

```
第一层: 向量原语 (L1201-2000)
  ggml_vec_set_i8 / ggml_vec_add_f32 / ggml_vec_dot_f32 / ggml_vec_norm
  → 对一维数组做底层计算, 不关心数据含义
  → 简单操作 (add/sub/mul) 用标量 for 循环 + 编译器自动向量化
  → 复杂操作 (dot/quantize/nibble 解包) 手写 SIMD

第二层: 算子 (L4171-)
  ggml_compute_forward_mul_mat / gelu / rope / soft_max
  → 对张量做一次完整运算, 是计算图的节点

第三层: 计算图执行
  ggml_graph_compute → 按拓扑序调度各算子
```

### 4.2 向量原语的两种实现策略 (L1201-)

```
手写 SIMD (关键热点):
  - quantize/dequantize: 涉及 nibble 解包, 编译器无法自动向量化
  - vec_dot: 量化点积, 需要特殊处理
  - FP16 向量: 需要 FP16↔FP32 转换

标量 + 编译器自动向量化 (简单操作):
  - ggml_vec_add_f32: for(i) z[i] = x[i] + y[i]
  - ggml_vec_sub_f32: for(i) z[i] = x[i] - y[i]
  - ggml_vec_mul_f32: for(i) z[i] = x[i] * y[i]
  - 编译器 -O2/-O3 自动生成 SIMD 指令, 效果接近手写
  - inline static: 内联消除函数调用开销
  - 不值得为每个平台手写一遍

分界线:
  简单逐元素运算 → 标量 + 自动向量化
  涉及位操作/类型转换/跨 lane 操作 → 手写 SIMD
```

### 4.3 算子的定义

**算子 = 对张量做一次有意义的数学运算的执行单元**

判断标准:
- ✓ 输入输出都是张量 (有形状、类型、数据)
- ✓ 知道自己在算什么 (乘法、激活、归一化...)
- ✓ 在计算图中有位置 (有前驱和后继)
- ✓ 命名为 `ggml_compute_forward_xxx`

不是算子:
- ✗ quantize_row_q4_0 (量化工具函数)
- ✗ ggml_vec_dot_f32 (向量原语)
- ✗ bytesFromNibbles (SIMD 辅助函数)

### 4.4 ggml.c 的开发方式推断

**大方向自顶向下, 小节奏快速迭代:**

```
第1阶段: 让 LLaMA 跑起来
  → 先写 mul_mat / rope / gelu / softmax 算子
  → 搭计算图引擎, 跑通第一个 token

第2阶段: 量化让模型能装进内存
  → 试多种方案 (method 1-5), 留下 Q4_0 和 Q4_1
  → 先保证正确, SIMD 后补

第3阶段: 性能优化 (按瓶颈优先级)
  → Q4_0 vec_dot 是最大热点 → 先加 SIMD
  → 其他路径标 TODO, 后补

特征证据:
  - method 编号跳跃 (4,5,缺1-3) → 试错痕迹
  - SIMD 覆盖不均 → 按优先级补
  - Q4_0 AoS / Q4_1 SoA 不统一 → 逐步演进而非统一设计
```

### 4.5 Q4_1 vec_dot 缺少 SIMD (L1567-1626)

当前只有标量版本, 但 SoA 布局已为 SIMD 预留:
- `_mm256_load_ps(pm0+i)` 可一次加载 8 个 block 的 min
- 外层循环可按 8 个 block 一批处理
- 这是明确的待优化项

### 4.6 SIMD: 自动向量化 vs 手写的边界判断

**核心结论: 经验预判 + Profiling 验证, 双驱动**

#### 经验预判: 编译器能自动向量化的特征

```
✓ 简单逐元素运算 (add, sub, mul)
✓ 无数据依赖的循环 (每次迭代独立)
✓ 连续内存访问 (stride=1)
✓ 固定步长的数组访问
✓ 无函数调用 (inline 的可以)
✓ 无分支 (或分支可转为 cmov)
```

#### 经验预判: 编译器无法自动向量化的特征

```
✗ 位操作 + 跨类型混合 (如 nibble 解包: 4bit→8bit→float)
✗ 查表 (如 FP16→FP32 转换)
✗ 跨 lane 操作 (如 shuffle, hadd, reduce)
✗ 条件分支密集 (如 if/else 在循环内)
✗ 间接寻址 (如 gather/scatter)
✗ 不规则步长 (如 permute 后的非连续访问)
```

#### 编译选项配置

```bash
# 自动向量化所需选项
-O3                    # 启用自动向量化 (-ftree-vectorize)
-march=native          # 使用当前 CPU 的 SIMD 指令集 (SSE4/AVX2/NEON)
-ffast-math            # 允许浮点重排 (FMA 合并, 牺牲 IEEE 严格性)

# 验证向量化是否生效
-fopt-info-vec         # GCC: 报告哪些循环被向量化了
-fopt-info-vec-missed  # GCC: 报告哪些没被向量化, 以及原因
-Rpass=loop-vectorize  # Clang: 同上
```

#### Profiling 驱动的优化工作流

```
1. 先用 -O3 -march=native 编译, 不手写任何 SIMD
2. perf/profiler 跑热点分析
3. 检查热点函数是否被自动向量化 (-fopt-info-vec)
4. 对未向量化的热点手写 SIMD
5. 对比性能: 差异 < 10% → 不值得手写 (维护成本 > 收益)
```

```bash
# perf 热点分析
perf stat -e cycles,instructions,cache-misses ./llama -m model.bin -p "hello"
perf record ./llama -m model.bin -p "hello"
perf report
```

#### 判断决策树

```
需要手写 SIMD 吗?
│
├─ 循环体是否只有简单的 +, -, *, /?
│   ├─ 是 → 编译器自动 (加 -O3 -march=native)
│   └─ 否 → 继续
│
├─ 是否涉及位操作/类型转换/查表?
│   ├─ 是 → 手写 SIMD
│   └─ 否 → 继续
│
├─ 是否有跨 lane 操作 (shuffle/reduce)?
│   ├─ 是 → 手写 SIMD
│   └─ 否 → 继续
│
├─ 是否是热点函数? (profiling 确认)
│   ├─ 不是 → 不值得优化, 标量即可
│   └─ 是 → 继续
│
└─ 灰色地带: 用 -fopt-info-vec 检查编译器是否成功向量化
    ├─ 成功 → 对比性能, 够用就不手写
    └─ 失败 → 手写 SIMD
```

#### ggml 中的实际案例

```
ggml_vec_add_f32:  for(i) z[i] = x[i] + y[i]
  → 经验判断: 简单逐元素, 编译器 100% 能向量化
  → 实际做法: 标量 for 循环, 依赖 -O3

ggml_vec_dot_q4_0:  4-bit 解包 + 整数乘加 + 浮点缩放
  → 经验判断: nibble 解包是位操作, 编译器 0% 能向量化
  → 实际做法: 手写 NEON/AVX2/WASM 三套

ggml_compute_forward_mul_mat_f16_f32:
  → 灰色地带: F16→F32 转换 + 矩阵乘
  → 实际做法: 手写, 用编译器宏抽象

ggml_vec_norm_f32:  dot(x,x) + sqrt
  → 组合已有手写原语, 不需要重新决策
```

#### 算子类型支持与按需实现原则

各算子实现的类型覆盖度不同, 反映使用频率:

```
类型覆盖度  算子                    原因
─────────────────────────────────────────────────
高          mul_mat (矩阵乘)        核心热点, F32/F16/Q4_0/Q4_1 全实现
高          add (加法)              F32/F16 都有 (残差连接常用)
中          dup (拷贝)             F16→F16, F16→F32 (类型转换常用)
低          sub (减法)             只 F32 (推理几乎不用, 反向传播才用)
低          scale (缩放)           只 F32 (只用于注意力缩放)
零          div (除法)             只有构建器, 没有 compute_forward
```

规律: 越是被频繁调用的算子, 类型支持越完整; 越冷门的算子, 只实现够用的最低限度。这也是 ggml "先跑通, 后优化" 开发风格的体现。

---

## 5. FFN (前馈网络)

### 5.1 结构

```
输入 x ──→ W₁ ──→ SiLU/GELU ──→ W₂ ──→ 输出
  (d)       (d→4d)    (4d)        (4d→d)   (d)
```

### 5.2 SwiGLU (LLaMA 使用)

```python
gate  = x × W₁      # 门控分支
value = x × W₃      # 值分支
h = gate * silu(value)
out = h × W₂
```

比传统 FFN 多一个门控分支, 网络可学到"选择性通过"

### 5.3 参数量

```
LLaMA-7B: d=4096, FFN扩展=11008
  一个 FFN 层 ≈ 135M 参数 (W₁+W₂+W₃)
  32 层 × 135M = 4.3B
  FFN 占模型约 60% 参数 → 存储世界知识的主要地方
```

### 5.4 FFN 的直觉理解

- 类比"字典": W₁ 的行是概念检测器, 激活函数决定强度, W₂ 翻译成输出
- 类比"键值存储": W₁ 的列是 Key (匹配输入), W₂ 的行是 Value (输出内容)
- 每个位置的 Token **独立**通过 FFN, 互不相干 (不像 Attention 互相看)

---

## 6. 激活函数

### GELU (Gaussian Error Linear Unit)

```
GELU(x) = x * Φ(x)    其中 Φ 是标准正态分布的累积分布函数

近似: GELU(x) ≈ 0.5 * x * (1 + tanh(√(2/π) * (x + 0.044715*x³)))

特点:
  - 平滑非线性, 在 0 附近近似恒等映射
  - BERT/GPT 原始版本使用
```

### SiLU (Sigmoid Linear Unit / Swish)

```
SiLU(x) = x * σ(x)    其中 σ 是 sigmoid 函数

特点:
  - 非单调性 (负值区有微小下凹)
  - 自门控: sigmoid(x) 控制通过程度
  - LLaMA/GPT-4/Claude 都用
  - 比 GELU 计算更简单
```

---

## 7. 战略思考

### 7.1 Transformer 是手段不是目的

- Transformer 的 O(n²) 复杂度是结构性浪费, 一定会被取代
- Mamba/RWKV/TTT 等已证明不需要 Attention 也能达到同等效果
- ggml 里的 SIMD/量化/缓存优化 → 跨范式的底层能力

### 7.2 底层技术的跨范式价值

```
范式会变, 物理不变:
  矩阵乘法 (GEMM)     → SIMD/NEON/AVX
  量化 (INT4/INT8)    → 查表、移位
  内存带宽            → Cache tiling
  权重加载            → CPU offload, mmap
  并行调度            → 线程池、work stealing
```

### 7.3 通信基站垂直领域

```
优势:
  - 几乎空白, 无红海竞争
  - 功耗约束极端、延迟要求苛刻、数据敏感不上云
  - 专用硬件 (FPGA/DSP) 需要定制优化
  - 与 FPGA 定点化、SIMD 技术天然匹配
```

---

## 8. 内存架构与多 GPU 通信

### 8.1 张量数据的存储位置

所有张量数据一定在 RAM 中 (DDR RAM 或 GPU VRAM), 不可能在磁盘上:
- CPU/GPU 计算指令只能操作 RAM 中的数据
- 磁盘只在模型加载/保存时参与, 推理过程完全不碰磁盘
- 模型权重: 磁盘 → fread → DDR RAM → (可选) PCIe → VRAM

### 8.2 四种硬件架构的总线交互

**纯 CPU (当前 firstbird 版本):**
```
┌──────────┐    DDR 总线    ┌─────────────────────┐
│  CPU     │◄─────────────►│   DDR RAM           │
│  (计算)  │               │  权重 + KV cache    │
│          │               │  临时张量            │
└──────────┘               └─────────────────────┘
  ↑ 数据路径: DDR RAM → L3 Cache → L2 → L1 → CPU 寄存器
```

**CPU + 单 GPU (RAM + VRAM 混合):**
```
┌──────────┐  DDR总线  ┌─────────┐  PCIe x16  ┌─────────┐
│  CPU     │◄────────►│  芯片组  │◄──────────►│  GPU    │
│  (计算)  │          │  /MCH   │  ~64 GB/s  │  (计算)  │
└──────────┘          └─────────┘            └──────────┘
     ↑                     ↑                     ↑
┌────▼────┐          ┌────▼─────┐          ┌────▼─────┐
│L3 Cache │          │ DDR RAM  │          │  VRAM    │
│         │          │ CPU层权重│          │ GPU层权重│
│         │          │ KV cache │          │ KV cache │
└─────────┘          └──────────┘          └──────────┘

数据流:
  加载模型:  磁盘 → DDR RAM → (PCIe) → VRAM
  推理(CPU层): DDR RAM → L3 → L2 → L1 → CPU 寄存器
  推理(GPU层): VRAM → L2 → SM寄存器 → CUDA Core
  层间切换:  GPU VRAM ──(PCIe)──► DDR RAM (或反向)
```

**CPU + 多 GPU (张量并行):**
```
                    ┌──────────────────────┐
                    │    DDR RAM 128GB     │
                    │  模型权重(完整)/KV    │
                    └──────────┬───────────┘
                          DDR总线
                       ┌────▼────┐
                       │   CPU   │
                       │(调度)   │
                       └────┬────┘
                    PCIe总线 │
                 ┌──────────▼──────────┐
                 │    PCIe Switch      │
                 └──┬───────┬───────┬──┘
                    │       │       │ PCIe x16
             ┌──────▼──┐ ┌──▼──────┐┌▼────────┐
             │  GPU 0  │ │  GPU 1  ││  GPU 2  │
             └────┬────┘ └────┬────┘└────┬────┘
             ┌────▼────┐ ┌───▼─────┐┌───▼─────┐
             │ VRAM 0  │ │ VRAM 1  ││ VRAM 2  │
             │层 0-10  │ │层 11-21 ││层 22-31 │
             └─────────┘ └─────────┘└─────────┘
```

**Apple Silicon 统一内存 (UMA):**
```
┌──────────────────────────────────────────────┐
│              M2/M3/M4 SoC 芯片               │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐     │
│  │ P-Core   │ │ E-Core   │ │ GPU      │     │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘     │
│       └──────┬──────┴──────┬─────┘           │
│         ┌────▼─────────────▼────┐             │
│         │   统一内存 (UMA)       │             │
│         │   64/128/192GB        │             │
│         └───────────────────────┘             │
│  CPU 和 GPU 共享同一块物理内存 → 零拷贝!       │
└──────────────────────────────────────────────┘
```

### 8.3 带宽对比

| 路径 | 带宽 | 延迟 |
|------|------|------|
| CPU ↔ DDR5 RAM | ~50-100 GB/s | ~50-100 ns |
| GPU ↔ VRAM (HBM3) | ~1-3 TB/s | ~100-300 ns |
| CPU ↔ GPU (PCIe 4.0 x16) | ~64 GB/s | ~500-1000 ns |
| GPU ↔ GPU (NVLink 4) | ~300-600 GB/s | ~100-200 ns |
| CPU ↔ 统一内存 (Apple M) | ~100-400 GB/s | ~50-100 ns |

PCIe 是 CPU-GPU 混合推理的最大瓶颈, 这就是 llama.cpp 支持 Apple Metal (零拷贝 UMA) 和 CUDA (全 VRAM 推理) 两种路线的原因。

### 8.4 多 GPU 层间数据搬移机制

**核心: CPU 是 "指挥官", 不是 "搬运工"**

CPU 只下发指令 (kernel launch + memcpy 命令), 数据搬移由 GPU 的 DMA 引擎自动完成:

```
CPU 的职责:
  ✓ 决定哪层在哪个 GPU 上计算        (调度)
  ✓ 分配显存, 加载权重               (资源管理)
  ✓ 发出 cudaMemcpyAsync 指令         (下达搬运命令)
  ✓ 发出 kernel launch 指令           (下达计算命令)
  ✓ 等待所有 GPU 完成                 (同步)

CPU 不做的事:
  ✗ 不参与数据搬运                    (DMA 引擎做)
  ✗ 不参与矩阵计算                    (GPU SM 做)
  ✗ 不在数据通路上                    (数据在 VRAM/NVLink/PCIe 间流动)
```

**伪代码 (Pipeline 并行):**

```python
# === 初始化: CPU 搭好路径 ===
gpu_0_weights = cudaMalloc(GPU_0, layer_0_to_10_size)
gpu_1_weights = cudaMalloc(GPU_1, layer_11_to_21_size)
gpu_2_weights = cudaMalloc(GPU_2, layer_22_to_31_size)

stream_0 = cudaStreamCreate(GPU_0)
stream_1 = cudaStreamCreate(GPU_1)
stream_2 = cudaStreamCreate(GPU_2)

recv_buf_01 = cudaMalloc(GPU_1, hidden_size)
recv_buf_12 = cudaMalloc(GPU_2, hidden_size)

# === 推理: CPU 只下指令, 不搬数据 ===
def forward(token_ids):
    # GPU 0: 层 0~10
    hidden = embed(token_ids)
    for layer in range(0, 11):
        hidden = gpu_0_compute_layer(layer, hidden)

    # GPU 0 → GPU 1 (CPU 发指令, DMA 搬数据, 异步不等)
    cudaMemcpyAsync(recv_buf_01, hidden, hidden_size, GPU0_TO_GPU1, stream_0)

    # GPU 1: 层 11~21 (stream_1 自动等 memcpy 完成后开始)
    hidden_1 = recv_buf_01
    for layer in range(11, 22):
        hidden_1 = gpu_1_compute_layer(layer, hidden_1)

    # GPU 1 → GPU 2
    cudaMemcpyAsync(recv_buf_12, hidden_1, hidden_size, GPU1_TO_GPU2, stream_1)

    # GPU 2: 层 22~31
    hidden_2 = recv_buf_12
    for layer in range(22, 32):
        hidden_2 = gpu_2_compute_layer(layer, hidden_2)

    logits = output_layer(hidden_2)
    cudaStreamSynchronize(stream_2)  # 最后才阻塞等全部完成
    return logits
```

**时间线 (异步执行):**

```
────────────────────────────────────────────────→

GPU 0:  [kernel: 层0~10] ──────┐
                               │ cudaMemcpyAsync (DMA 自动搬运)
                               ▼
GPU 1:                    [等事件] ──► [kernel: 层11~21] ──────┐
                                                              │ cudaMemcpyAsync
                                                              ▼
GPU 2:                                               [等事件] ──► [kernel: 层22~31]

CPU:   发指令1 → 发指令2 → 发指令3 → ... → synchronize(等全部完成)
       (微秒级)  (不等)     (不等)          (这里才真正阻塞)
```

**NVLink vs PCIe:**

```
PCIe 搬运:
  数据经 PCIe Switch → CPU → PCIe Switch → 目标 GPU (无 peer access)
  或: 数据经 PCIe P2P 直连 → 目标 GPU (有 peer access, 省去 CPU 中转)

NVLink 搬运:
  数据经 NVLink 直连 → 目标 GPU (不经过 PCIe, 不经过 CPU)
  带宽: NVLink 4.0 = 300 GB/s vs PCIe 4.0 x16 = 64 GB/s

关键: 无论 PCIe 还是 NVLink, CPU 都只发指令, 不搬数据!
      区别只是 DMA 引擎走哪条物理路径。
```

---

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-05-12 | 初始创建, 基于 ggml.c L1-800 区间源码阅读整理 |
| 2026-05-12 | 新增 3.5 AVX2 hadd 优化, 3.6 SIMD 向量操作抽象层 (F32/F16), 4.2 向量原语两种实现策略 |
| 2026-05-12 | 新增 2.8 量化点积 vec_dot_q4_0 (权重vs激活、整数域乘加优化、三平台对比、量化激活误差分析) |
| 2026-05-13 | 新增 8. 内存架构与多 GPU 通信 (四种硬件架构总线交互、带宽对比、层间数据搬移机制与伪代码) |
| 2026-05-13 | 新增 4.6 SIMD: 自动向量化 vs 手写的边界判断 (经验预判特征、编译选项、Profiling 工作流、决策树、ggml 实际案例、算子类型支持按需实现原则) |
