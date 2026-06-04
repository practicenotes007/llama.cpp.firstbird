# 数值计算背景知识补充

> 为阅读 `ggml.c` 所需的数值计算核心概念，重要程度 ★★★★

---

## 1. IEEE 754 浮点格式

### 1.1 浮点数表示

IEEE 754 标准定义了浮点数的位级表示：

```
float (FP32): 1位符号 + 8位指数 + 23位尾数 = 32位
half  (FP16): 1位符号 + 5位指数 + 10位尾数 = 16位

FP32: [-1]^S × 2^(E-127) × (1 + M/2^23)
FP16: [-1]^S × 2^(E-15)  × (1 + M/2^10)
```

```
FP32 位布局 (32 bit):
┌───┬────────┬───────────────────────┐
│ S │ E[7:0] │      M[22:0]          │
│1bit│ 8bit  │      23bit            │
└───┴────────┴───────────────────────┘

FP16 位布局 (16 bit):
┌───┬────────┬─────────────┐
│ S │ E[4:0] │   M[9:0]    │
│1bit│ 5bit  │   10bit     │
└───┴────────┴─────────────┘
```

### 1.2 数值范围与精度

| 格式 | 最大值 | 最小正规数 | 精度 (十进制位) |
|------|--------|-----------|----------------|
| FP32 | 3.4×10³⁸ | 1.2×10⁻³⁸ | ~7.2 |
| FP16 | 6.5×10⁴ | 6.1×10⁻⁵ | ~3.3 |

FP16 的有限范围是实际开发中必须注意的问题——超过 65504 的值会变成 `inf`。

### 1.3 特殊值

| 值 | FP32 位模式 | FP16 位模式 |
|----|-----------|-----------|
| +0 | 0x00000000 | 0x0000 |
| -0 | 0x80000000 | 0x8000 |
| +∞ | 0x7F800000 | 0x7C00 |
| -∞ | 0xFF800000 | 0xFC00 |
| NaN | 0x7FC00000 | 0x7E00 |

### 1.4 FP16↔FP32 软件转换

这是 ggml 中 FP16 支持的基础，理解此过程对阅读阶段一至关重要：

**FP32 → FP16 转换原理**：

```c
ggml_fp16_t ggml_compute_fp32_to_fp16(float f) {
    // 1. 提取 FP32 的位模式
    uint32_t w = fp32_to_bits(f);
    
    // 2. 提取符号、指数、尾数
    uint32_t sign = (w >> 16) & 0x00008000;  // 符号位直接截取到16位位置
    int32_t exponent = ((w >> 23) & 0xFF) - 127;  // 去偏移
    uint32_t mantissa = w & 0x007FFFFF;
    
    // 3. 处理各种情况
    if (exponent > 15) {
        // 溢出 → 返回 ±Inf
        return sign | 0x7C00;
    }
    if (exponent <= -15) {
        // 下溢 → 返回 ±0 (简化处理)
        return sign;
    }
    
    // 4. 重新打包为 FP16
    uint16_t h = sign | ((exponent + 15) << 10) | (mantissa >> 13);
    
    // 5. 舍入处理 (round to nearest even)
    if (mantissa & 0x1FFF) {
        h += 1;  // 简化舍入
    }
    
    return h;
}
```

**FP16 → FP32 转换原理**：

```c
float ggml_compute_fp16_to_fp32(ggml_fp16_t h) {
    // 1. 提取 FP16 的位模式
    uint16_t h_bits = h;
    
    // 2. 提取符号、指数、尾数
    uint32_t sign = (h_bits >> 15) << 31;  // 符号位移到 FP32 位置
    int32_t exponent = ((h_bits >> 10) & 0x1F) - 15;  // 去偏移
    uint32_t mantissa = h_bits & 0x03FF;
    
    // 3. 处理特殊值
    if (exponent == -15) {
        if (mantissa == 0) return fp32_from_bits(sign);  // ±0
        // 次正规数处理...
    }
    if (exponent == 16) {
        if (mantissa == 0) return fp32_from_bits(sign | 0x7F800000);  // ±Inf
        return fp32_from_bits(sign | 0x7FC00000);  // NaN
    }
    
    // 4. 重新打包为 FP32
    uint32_t w = sign | ((exponent + 127) << 23) | (mantissa << 13);
    return fp32_from_bits(w);
}
```

### 1.5 舍入与精度损失

FP32→FP16 转换中的精度损失来源：

1. **尾数截断**：23-bit → 10-bit，丢失 13 位精度
2. **指数范围缩小**：8-bit → 5-bit，大数值会溢出
3. **次正规数处理**：FP16 次正规数范围极小

---

## 2. 量化 (Quantization)

### 2.1 量化的目的

将高精度 (FP32) 权重压缩为低精度 (INT4/INT8) 表示，以减少：
- **存储空间**：FP32 需 4 字节/元素，Q4_0 仅需 0.5625 字节/元素 (约 7 倍压缩)
- **内存带宽**：推理时的瓶颈通常是内存带宽而非计算
- **推理延迟**：更少的数据传输 = 更快的推理

### 2.2 Q4_0 量化格式

Q4_0 是 ggml 最基本的量化格式——**对称量化**：

```
每块 (block) = 32 个元素

存储布局:
┌──────────┬──────────────────────────────────────┐
│ delta(4B)│  16 × uint8 (32个4-bit值, 每字节2个) │
│  float   │  ql[0..15]                           │
└──────────┴──────────────────────────────────────┘
总大小: 4 + 16 = 18 字节 / 32 元素 = 0.5625 字节/元素

反量化公式:
  value = (int4(ql) - 8) × delta

  int4 范围: [0, 15], 中心化后: [-8, 7]
  delta = max(|x|) / 7  (块内最大绝对值归一化)
```

**对称量化的含义**：量化值关于零对称，零点固定为 8（int4 范围的中点）。

### 2.3 Q4_1 量化格式

Q4_1 是**非对称量化**，增加了一个偏移量：

```
每块 (block) = 32 个元素

存储布局:
┌──────────┬──────────┬──────────────────────────────┐
│ delta(4B)│   m(4B)  │  16 × uint8 (32个4-bit值)    │
│  float   │  float   │  ql[0..15]                   │
└──────────┴──────────┴──────────────────────────────┘
总大小: 4 + 4 + 16 = 24 字节 / 32 元素 = 0.75 字节/元素

反量化公式:
  value = int4(ql) × delta + m

  int4 范围: [0, 15]
  delta = (max - min) / 15
  m = min  (块内最小值作为偏移)
```

**非对称量化的优势**：
- 更好地处理非零中心分布（如 ReLU 后全是正值的激活）
- 量化误差更小（多了一个自由度 m）
- 代价：每块多 4 字节（多了 33% 存储开销）

### 2.4 量化过程 (quantize_row_q4_0)

```c
void quantize_row_q4_0(const float * x, void * vy, int k) {
    const int nb = k / QK;  // QK=32, 块数
    
    for (int i = 0; i < nb; i++) {
        // 1. 找块内最大绝对值
        float amax = 0.0f;
        for (int j = 0; j < QK; j++) {
            amax = MAX(amax, fabsf(x[i*QK + j]));
        }
        
        // 2. 计算 delta
        float delta = amax / 7.0f;  // 对称量化，范围 [-8, 7]
        if (delta == 0) delta = 1.0f;
        
        // 3. 量化每个元素
        for (int j = 0; j < QK; j += 2) {
            // 量化到 [0, 15]
            int q0 = roundf(x[i*QK + j + 0] / delta) + 8;  // +8 中心化
            int q1 = roundf(x[i*QK + j + 1] / delta) + 8;
            q0 = MAX(0, MIN(15, q0));  // 钳位到 [0, 15]
            q1 = MAX(0, MIN(15, q1));
            
            // 两个 4-bit 值打包到一个 uint8
            y->qs[j/2] = q0 | (q1 << 4);
        }
        
        // 4. 存储 delta
        y->d = delta;
    }
}
```

### 2.5 量化精度与块大小

| 块大小 (QK) | 精度 | 存储效率 | 备注 |
|-------------|------|---------|------|
| 32 | 中等 | 高 | ggml 默认选择 |
| 64 | 较低 | 更高 | delta 代表更大范围 |
| 16 | 较高 | 较低 | delta 更精细 |

块大小越小，每块有自己的 delta，能更精确地适应局部数值范围，但存储效率降低。

---

## 3. GELU 激活函数

### 3.1 定义

GELU (Gaussian Error Linear Unit) 是 Transformer 模型中常用的激活函数：

```
GELU(x) = x · Φ(x) = x · ½(1 + erf(x/√2))
```

其中 Φ(x) 是标准正态分布的累积分布函数。

### 3.2 近似实现

精确的 erf 函数计算代价高，通常使用近似：

**Tanh 近似**（ggml 使用的方式）：
```
GELU(x) ≈ 0.5 · x · (1 + tanh(√(2/π) · (x + 0.044715 · x³)))
```

### 3.3 ggml 中的 GELU 实现

ggml 提供两种 GELU 计算方式：

**a) 查找表方式**（使用预计算的 `table_gelu_f16`）：
```c
void ggml_vec_gelu_f16(const int n, ggml_fp16_t * y, const ggml_fp16_t * x) {
    for (int i = 0; i < n; i++) {
        // 直接用 FP16 值作为索引查表
        y[i] = table_gelu_f16[x[i]];
    }
}
```
优势：极快（O(1) 查表），但需要 128KB 查找表。

**b) 公式计算方式**：
```c
void ggml_vec_gelu_f32(const int n, float * y, const float * x) {
    for (int i = 0; i < n; i++) {
        float v = x[i];
        y[i] = 0.5f * v * (1.0f + tanhf(0.7978845608f * (v + 0.044715f * v * v * v)));
    }
}
```

### 3.4 GELU vs ReLU

```
GELU: 平滑过渡，允许小的负值通过
ReLU: 硬截断，所有负值归零

     GELU        ReLU
      │  /        │  /
      │ /         │ /
   ───┤───     ───┤───
     /│           /│
    / │          / │
```

---

## 4. SILU 激活函数

### 4.1 定义

SILU (Sigmoid Linear Unit，也叫 Swish)：

```
SILU(x) = x · σ(x) = x / (1 + e^(-x))
```

其中 σ(x) = 1/(1+e^(-x)) 是 sigmoid 函数。

### 4.2 特性

- 非单调：负值区域有一个小的下凹
- 平滑：处处可导
- 自门控：σ(x) 充当门控信号
- LLaMA 等现代模型使用 SILU 替代 GELU

### 4.3 ggml 中的 SILU 实现

同样有查找表和公式两种方式：

```c
// 查找表方式
y[i] = table_silu_f16[x[i]];

// 公式方式
float v = x[i];
y[i] = v / (1.0f + expf(-v));
```

### 4.4 SILU 的导数

反向传播需要 SILU 的导数：

```
d/dx [x · σ(x)] = σ(x) + x · σ(x) · (1 - σ(x))
                 = σ(x) · (1 + x · (1 - σ(x)))
```

---

## 5. 其他数值计算相关

### 5.1 数值稳定的 Softmax

直接计算 softmax 可能溢出（exp 大数 → inf），ggml 使用**减最大值**技巧：

```c
void compute_forward_soft_max_f32(...) {
    // 1. 找最大值
    float max_val = -INFINITY;
    for (int i = 0; i < n; i++) max_val = MAX(max_val, x[i]);
    
    // 2. 减最大值后计算 exp（保证所有值 ≤ 0，不会溢出）
    float sum = 0.0f;
    for (int i = 0; i < n; i++) {
        y[i] = expf(x[i] - max_val);
        sum += y[i];
    }
    
    // 3. 归一化
    for (int i = 0; i < n; i++) y[i] /= sum;
}
```

### 5.2 双精度累加

ggml 定义 `ggml_float = double` 用于累加：

```c
typedef double ggml_float;

// 在点积等运算中使用 double 累加
ggml_float sum = 0.0;
for (int i = 0; i < n; i++) {
    sum += (ggml_float)x[i] * (ggml_float)y[i];
}
```

原因：大量 FP32 数相加时，FP32 的 23-bit 尾数不足以精确表示部分和，使用 double (52-bit 尾数) 可大幅减少累加误差。

### 5.3 查找表优化

ggml 为高频运算预计算查找表：

| 查找表 | 大小 | 用途 |
|--------|------|------|
| `table_gelu_f16[1<<16]` | 128 KB | GELU(FP16) → FP16 |
| `table_silu_f16[1<<16]` | 128 KB | SILU(FP16) → FP16 |
| `table_exp_f16[1<<16]` | 128 KB | exp(FP16) → FP16 |
| `table_f32_f16[1<<16]` | 256 KB | FP16→FP32 转换辅助 |

**原理**：FP16 共 2¹⁶=65536 种可能值，预计算所有结果后，运行时只需 O(1) 查表。

**初始化**：
```c
void ggml_init_tables() {
    for (int i = 0; i < (1 << 16); i++) {
        float f = ggml_compute_fp16_to_fp32((ggml_fp16_t)i);
        table_gelu_f16[i] = ggml_compute_fp32_to_fp16(gelu_f32(f));
        table_silu_f16[i] = ggml_compute_fp32_to_fp16(silu_f32(f));
        table_exp_f16[i]  = ggml_compute_fp32_to_fp16(expf(f));
    }
}
```

---

## 6. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| IEEE 754 | 浮点数位级表示 | FP16↔FP32 软件转换 |
| FP16↔FP32 | 软件位操作转换 | `ggml_compute_fp16_to_fp32/fp16` |
| 对称量化 (Q4_0) | 以零为中心，1个缩放因子 | `quantize_row_q4_0` |
| 非对称量化 (Q4_1) | 有偏移量，2个参数 | `quantize_row_q4_1` |
| GELU | Transformer 激活函数 | 查找表 + 公式双实现 |
| SILU | LLaMA 激活函数 | 查找表 + 公式双实现 |
| 数值稳定 Softmax | 减最大值防溢出 | `compute_forward_soft_max_f32` |
| 双精度累加 | 减少浮点累加误差 | `ggml_float = double` |
| 查找表 | 空间换时间 | GELU/SILU/EXP 预计算表 |
