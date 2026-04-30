# SIMD Intrinsics 背景知识补充

> 为阅读 `ggml.c` 所需的 SIMD 编程知识，重要程度 ★★★★★

---

## 1. SIMD 基本概念

### 1.1 什么是 SIMD

SIMD (Single Instruction, Multiple Data) 是一种并行计算范式：**一条指令同时处理多个数据元素**。

```
标量处理:  4 次乘法 → a1*b1, a2*b2, a3*b3, a4*b4
SIMD处理:  1 次乘法 → [a1,a2,a3,a4] * [b1,b2,b3,b4] = [c1,c2,c3,c4]
```

### 1.2 SIMD 的性能意义

在 ggml 的核心运算（点积、矩阵乘、向量加法）中，SIMD 可以带来 4x~8x 的加速：

| 平台 | 寄存器宽度 | F32 同时处理元素数 | 理论加速比 |
|------|-----------|-------------------|-----------|
| ARM NEON | 128-bit | 4 | 4x |
| x86 SSE | 128-bit | 4 | 4x |
| x86 AVX2 | 256-bit | 8 | 8x |
| x86 AVX-512 | 512-bit | 16 | 16x |
| WASM SIMD | 128-bit | 4 | 4x |

### 1.3 Intrinsics 函数

Intrinsics 是编译器提供的内建函数，直接映射到一条或几条 SIMD 汇编指令：

```c
// ARM NEON: 4 个 float 同时相乘
float32x4_t result = vmulq_f32(a, b);

// x86 SSE: 4 个 float 同时相乘
__m128 result = _mm_mul_ps(a, b);
```

这些函数不是库函数——编译器直接将它们翻译为对应的 SIMD 指令。

---

## 2. ARM NEON

### 2.1 寄存器与数据类型

ARM NEON 提供 32 个 128-bit 寄存器 (Q0-Q31)，也可作为 64-bit (D0-D31) 使用：

| C 类型 | 位数 | 元素类型 | 元素数 |
|--------|------|---------|--------|
| `float32x4_t` | 128 | float | 4 |
| `int32x4_t` | 128 | int32 | 4 |
| `int16x8_t` | 128 | int16 | 8 |
| `int8x16_t` | 128 | int8 | 16 |
| `float16x4_t` | 128 (低64) | float16 | 4 |
| `uint8x16_t` | 128 | uint8 | 16 |

### 2.2 核心操作

```c
// 加载：从内存加载到寄存器
float32x4_t a = vld1q_f32(ptr);        // 加载 4 个 float
float32x4_t b = vld1q_f32(ptr + 4);    // 加载下 4 个 float

// 算术运算
float32x4_t sum  = vaddq_f32(a, b);    // 逐元素加法
float32x4_t prod = vmulq_f32(a, b);    // 逐元素乘法
float32x4_t res  = vmlaq_f32(c, a, b); // c += a * b (融合乘加)

// 存储：从寄存器写回内存
vst1q_f32(dst, sum);

// 规约：4 个元素求和
float result = vaddvq_f32(sum);  // ARMv8 支持
// ARMv7 需要:
// float result = (vgetq_lane_f32(sum, 0) + vgetq_lane_f32(sum, 1) +
//                 vgetq_lane_f32(sum, 2) + vgetq_lane_f32(sum, 3));
```

### 2.3 ggml NEON 宏映射

```c
// ggml 中的 ARM NEON 抽象
#define GGML_F32x4         float32x4_t
#define GGML_F32_VEC_LOAD  vld1q_f32
#define GGML_F32_VEC_STORE vst1q_f32
#define GGML_F32_VEC_FMA(a, b, c) vmlaq_f32(a, b, c)  // a += b*c
#define GGML_F32_VEC_ADD   vaddq_f32
#define GGML_F32_VEC_MUL   vmulq_f32
#define GGML_F32_STEP      16  // 步长 = 4元素 * 4字节 * 1 = 16字节
```

### 2.4 NEON FP16 支持

ARMv8.2-A 引入了原生 FP16 SIMD 运算：

```c
// 有硬件 FP16 支持时
float16x4_t a_f16 = vld1_f16(ptr);
// 直接做 FP16 算术（精度较低但速度更快）

// 无硬件 FP16 支持时
float16x4_t a_f16 = vld1_f16(ptr);
float32x4_t a_f32 = vcvt_f32_f16(a_f16);  // 先转 FP32
// 在 FP32 上做运算，结果再转回 FP16
```

---

## 3. x86 SSE/AVX

### 3.1 SSE3 (128-bit)

| C 类型 | 位数 | 元素类型 | 元素数 |
|--------|------|---------|--------|
| `__m128` | 128 | float | 4 |
| `__m128d` | 128 | double | 2 |
| `__m128i` | 128 | int (各种宽度) | 4/8/16 |

```c
// 核心操作
__m128 a = _mm_loadu_ps(ptr);          // 非对齐加载 4 个 float
__m128 b = _mm_loadu_ps(ptr + 4);
__m128 sum = _mm_add_ps(a, b);         // 逐元素加法
__m128 prod = _mm_mul_ps(a, b);        // 逐元素乘法
_mm_storeu_ps(dst, sum);               // 非对齐存储

// FMA (需要 FMA3 支持)
__m128 res = _mm_fmadd_ps(a, b, c);   // c + a*b
```

### 3.2 AVX2 (256-bit)

| C 类型 | 位数 | 元素类型 | 元素数 |
|--------|------|---------|--------|
| `__m256` | 256 | float | 8 |
| `__m256d` | 256 | double | 4 |
| `__m256i` | 256 | int (各种宽度) | 8/16/32 |

```c
__m256 a = _mm256_loadu_ps(ptr);       // 加载 8 个 float
__m256 b = _mm256_loadu_ps(ptr + 8);
__m256 sum = _mm256_add_ps(a, b);
__m256 prod = _mm256_mul_ps(a, b);
_mm256_storeu_ps(dst, sum);
```

### 3.3 ggml 中 SSE3 的使用

ggml 的 SIMD 抽象层基于 SSE3（128-bit），而非 AVX2：

```c
#define GGML_F32x4         __m128
#define GGML_F32_VEC_LOAD  _mm_loadu_ps
#define GGML_F32_VEC_STORE _mm_storeu_ps
#define GGML_F32_VEC_ADD   _mm_add_ps
#define GGML_F32_VEC_MUL   _mm_mul_ps
#define GGML_F32_STEP      32  // 步长 = 4元素 * 4字节 * 2 = 32字节
```

AVX2 主要用于量化操作（如 `packNibbles`、量化点积中的专用路径）。

### 3.4 F16C 指令

F16C (AVX 扩展) 提供硬件 FP16↔FP32 转换：

```c
// F16C: 半精度到单精度
__m128 f32 = _mm_cvtph_ps(f16_x86);   // 4个F16 → 4个F32
__m256 f32_256 = _mm256_cvtph_ps(f128); // 8个F16 → 8个F32

// F16C: 单精度到半精度
__m128i f16 = _mm_cvtps_ph(f32, 0);   // 4个F32 → 4个F16
```

---

## 4. WASM SIMD

### 4.1 概述

WebAssembly SIMD 128-bit 是 WASM 的向量扩展，操作类似 NEON/SSE：

```c
#define GGML_F32x4         v128_t
#define GGML_F32_VEC_LOAD  wasm_v128_load
#define GGML_F32_VEC_STORE wasm_v128_store
```

### 4.2 自定义 FP16 load/store

WASM SIMD 没有原生 FP16 支持，ggml 通过自定义函数实现：

```c
// 加载 4 个 FP16 并转换为 FP32
static inline v128_t ggml_v128_load_f16(const ggml_fp16_t * ptr) {
    // 从内存加载 8 字节 (4 个 FP16)
    v128_t v = wasm_v128_load(ptr);
    // 使用 WASM SIMD 的 i16→f32 转换
    return wasm_i16x8_extend_high_i8x16_to_i16x8(v); // 简化示例
}
```

---

## 5. ggml 的 SIMD 抽象设计模式

### 5.1 统一抽象层

ggml 通过预处理器宏构建了一套跨平台 SIMD 抽象层：

```
┌─────────────────────────────────────────┐
│        核心算法代码                      │
│  使用 GGML_F32_VEC_LOAD/STORE/FMA/...  │
├─────────────────────────────────────────┤
│      预处理器宏展开                      │
│  #if defined(__ARM_NEON) → NEON        │
│  #elif defined(__wasm_simd128__) → WASM │
│  #elif defined(__SSE3__) → SSE3        │
│  #else → 标量 fallback                 │
├─────────────────────────────────────────┤
│      平台特定 intrinsic 调用             │
│  vld1q_f32 / wasm_v128_load / _mm_loadu_ps │
└─────────────────────────────────────────┘
```

### 5.2 通用向量运算模式

所有向量运算遵循统一模式：

```c
void ggml_vec_xxx_f32(const int n, float * y, const float * x, ...) {
    // 1. SIMD 处理对齐部分
    for (int i = 0; i < n; i += GGML_F32_STEP) {
        if (i + GGML_F32_STEP > n) break;
        GGML_F32x4 vx = GGML_F32_VEC_LOAD(x + i);
        // ... SIMD 运算 ...
        GGML_F32_VEC_STORE(y + i, vy);
    }
    // 2. 标量处理尾部
    for (int i = n & ~(GGML_F32_STEP/sizeof(float) - 1); i < n; i++) {
        y[i] = x[i]; // 标量 fallback
    }
}
```

### 5.3 规约 (Reduction) 的平台差异

规约是最需要平台适配的操作：

```c
// ARM NEON 规约
static inline float ggml_vec_reduce_f32(float32x4_t v) {
    // 成对相加 → 水平求和
    v = vaddq_f32(v, vrev64q_f32(v));    // [a+b, c+d, c+d, a+b]
    v = vaddq_f32(v, vextq_f32(v, v, 2)); // [a+b+c+d, ...]
    return vgetq_lane_f32(v, 0);
}

// x86 SSE 规约
static inline float ggml_vec_reduce_f32(__m128 v) {
    v = _mm_add_ps(v, _mm_movehl_ps(v, v));  // [a+c, b+d, ...]
    v = _mm_add_ss(v, _mm_shuffle_ps(v, v, 1)); // [a+b+c+d, ...]
    return _mm_cvtss_f32(v);
}
```

---

## 6. SIMD 在量化运算中的特殊应用

### 6.1 Q4_0 量化点积 (ARM NEON)

这是 ggml 中最复杂的 SIMD 应用之一：

```c
void ggml_vec_dot_q4_0(const int n, float * s, const void * x, const void * y) {
    // 1. 每 32 个元素为一组 (QK=32)
    // 2. 加载量化参数: delta (float) 和 nibbles (16 bytes)
    // 3. 解包 4-bit 到 8-bit/16-bit
    // 4. 乘以 delta 并累加
    // 5. 多组累加结果求和
}
```

### 6.2 AVX2 量化辅助函数

```c
// 从字节数组提取 nibble (低4位)
static inline __m128i bytesFromNibbles(const uint8_t * r) {
    // 加载 16 字节，每个字节的低 4 位就是一个 int4 值
}

// 将 int8 值打包为 nibble (4-bit)
static inline __m128i packNibbles(__m128i bytes1, __m128i bytes2) {
    // 两个 128-bit 寄存器（各16个int8）打包为一个 128-bit（16个int4）
}
```

---

## 7. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| SIMD 寄存器 | 宽度决定并行度 | 128-bit (NEON/SSE) 或 256-bit (AVX2) |
| Intrinsics | 编译器内建函数，直接映射到指令 | `vmulq_f32`, `_mm_mul_ps` |
| 宏抽象 | 统一接口隐藏平台差异 | `GGML_F32_VEC_*` 宏族 |
| 尾部处理 | SIMD 处理对齐部分，标量处理尾部 | 所有向量运算函数 |
| FMA | 融合乘加，一次完成 a*b+c | `vmlaq_f32`, `_mm_fmadd_ps` |
| FP16↔FP32 | 硬件加速或软件转换 | `vcvt_f32_f16`, `_mm_cvtph_ps` |
| 规约 | 向量内元素求和 | `vaddvq_f32`, `_mm_cvtss_f32` |
| 量化 SIMD | 直接在量化数据上做运算 | `ggml_vec_dot_q4_0` 的 NEON/AVX2 路径 |
