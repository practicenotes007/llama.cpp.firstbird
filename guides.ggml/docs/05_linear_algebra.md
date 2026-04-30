# 线性代数背景知识补充

> 为阅读 `ggml.c` 所需的线性代数核心概念，重要程度 ★★★★

---

## 1. 向量 (Vector)

### 1.1 基本概念

向量是一维有序数组。在 ggml 中，向量对应一维张量 (`ne[0] = N, ne[1]=ne[2]=ne[3]=1`)。

```
v = [v₁, v₂, v₃, ..., vₙ]ᵀ    (列向量，n 维)
```

### 1.2 向量运算

**加法**：逐元素相加
```
a + b = [a₁+b₁, a₂+b₂, ..., aₙ+bₙ]
```
ggml: `ggml_vec_add_f32(n, y, a, b)`

**标量乘法**：每个元素乘以标量
```
α·v = [α·v₁, α·v₂, ..., α·vₙ]
```
ggml: `ggml_vec_scale_f32(n, y, v, α)`

**逐元素乘法 (Hadamard 积)**：
```
a ⊙ b = [a₁·b₁, a₂·b₂, ..., aₙ·bₙ]
```
ggml: `ggml_vec_mul_f32(n, y, a, b)`

---

## 2. 点积 (Dot Product)

### 2.1 定义

两个向量的点积是它们对应元素乘积之和：

```
a · b = Σᵢ aᵢ·bᵢ = a₁·b₁ + a₂·b₂ + ... + aₙ·bₙ
```

### 2.2 几何意义

```
a · b = ‖a‖ · ‖b‖ · cos(θ)
```

点积衡量两个向量的"方向一致性"：
- 正值：方向大致相同
- 零：正交（垂直）
- 负值：方向大致相反

### 2.3 ggml 中的点积实现

点积是 ggml 中最基础、最重要的运算之一，所有矩阵乘法都建立在点积之上：

```c
// F32 点积 (简化版)
void ggml_vec_dot_f32(const int n, float * s, const float * x, const float * y) {
    ggml_float sum = 0.0;  // ggml_float = double，高精度累加
    for (int i = 0; i < n; i += GGML_F32_STEP) {
        // SIMD 加速部分
        GGML_F32x4 vx = GGML_F32_VEC_LOAD(x + i);
        GGML_F32x4 vy = GGML_F32_VEC_LOAD(y + i);
        sum += GGML_F32_VEC_REDUCE(GGML_F32_VEC_MUL(vx, vy));
    }
    // 标量尾部处理
    *s = (float)sum;
}
```

**关键设计**：使用 `double` (ggml_float) 累加，减少大量浮点数相加时的精度损失。

### 2.4 量化点积

ggml 最重要的优化之一是直接在量化数据上做点积，避免反量化开销：

```
标准流程:  量化数据 → 反量化 → FP32点积 (慢，需要临时内存)
优化流程:  量化数据 → 量化点积 (快，无需额外内存)

Q4_0 量化点积:
  result = Σ (quant_a[i] - 8) * Δ_a * (quant_b[i] - 8) * Δ_b
         = Δ_a * Δ_b * Σ (quant_a[i] - 8) * (quant_b[i] - 8)
```

---

## 3. 范数 (Norm)

### 3.1 L2 范数

```
‖v‖₂ = √(v₁² + v₂² + ... + vₙ²) = √(v · v)
```

### 3.2 ggml 中的范数计算

```c
void ggml_vec_norm_f32(const int n, float * s, const float * x) {
    ggml_vec_dot_f32(n, s, x, x);  // 先算点积 (即平方和)
    *s = sqrtf(*s);                 // 再开方
}
```

### 3.3 归一化 (Normalization)

将向量缩放为单位长度：

```
v̂ = v / ‖v‖₂
```

ggml 中 LayerNorm 的核心步骤：
```c
// compute_forward_norm_f32 简化逻辑
float mean = sum / n;                     // 计算均值
for (i) x[i] -= mean;                     // 减均值
float norm = sqrt(sum_sqr / n);           // 计算 RMS
for (i) y[i] = x[i] / norm;              // 归一化
```

---

## 4. 矩阵 (Matrix)

### 4.1 基本概念

矩阵是二维数组。在 ggml 中，矩阵对应二维张量 (`ne[0] = 列数, ne[1] = 行数`)。

```
A = | a₁₁  a₁₂  a₁₃ |
    | a₂₁  a₂₂  a₂₃ |
     ↑ ne[0]=3 (列)
     ne[1]=2 (行)
```

### 4.2 ggml 中矩阵的内存布局

ggml 使用**行优先 (row-major)** 存储：

```
内存: [a₁₁, a₁₂, a₁₃, a₂₁, a₂₂, a₂₃]
       ←── 第0行 ──→  ←── 第1行 ──→

nb[0] = sizeof(float) = 4      // 元素间步长
nb[1] = nb[0] * ne[0] = 12     // 行间步长
```

访问元素 `A[i][j]`：
```c
float val = *(float *)((char *)A->data + i * A->nb[1] + j * A->nb[0]);
```

---

## 5. 矩阵乘法 (Matrix Multiplication)

### 5.1 定义

```
C = A × B
C[i][j] = Σₖ A[i][k] · B[k][j]

维度要求: A(M×K) × B(K×N) = C(M×N)
```

### 5.2 为什么矩阵乘法是核心

矩阵乘法是神经网络中最核心的计算：

```
线性层:  Y = W · X        (权重矩阵 × 输入向量)
注意力:  Q = W_q · X, K = W_k · X, V = W_v · X
前馈:    FFN = W₂ · GELU(W₁ · X)
```

在 LLM 推理中，矩阵乘法占 90% 以上的计算量。

### 5.3 ggml 中矩阵乘法的实现策略

ggml 为不同数据类型提供了专用矩阵乘法实现：

**a) F32 矩阵乘** (`compute_forward_mul_mat_f32`)
```
对于 C = A × B (A: ne0×ne1, B: ne0×1):
- 沿 A 的行维度分块给不同线程
- 每行调用 ggml_vec_dot_f32 做内积
```

**b) F16×F32 混合精度** (`compute_forward_mul_mat_f16_f32`)
```
A 是 F16，B 是 F32:
- 将 A 的每行从 F16 转为 F32（存入工作缓冲区）
- 用 F32 点积计算
```

**c) Q4_0×F32 量化矩阵乘** (`compute_forward_mul_mat_q4_0_f32`)
```
A 是 Q4_0 量化，B 是 F32:
- 直接调用 ggml_vec_dot_q4_0_f32
- 在量化点积内部完成反量化+乘累加
- 避免了预先反量化整个矩阵的开销
```

**d) Q4_1×F32 量化矩阵乘** (`compute_forward_mul_mat_q4_1_f32`)
- 类似 Q4_0，使用 Q4_1 量化点积

### 5.4 矩阵乘法的并行化

```
C = A × B，A 有 M 行

线程0: 计算 C 的第 0, 3, 6, ... 行
线程1: 计算 C 的第 1, 4, 7, ... 行
线程2: 计算 C 的第 2, 5, 8, ... 行

每行 = A 的一行 与 B 的点积
```

---

## 6. 转置 (Transpose)

### 6.1 定义

矩阵转置交换行和列：

```
A = | 1 2 3 |    Aᵀ = | 1 4 |
    | 4 5 6 |          | 2 5 |
                       | 3 6 |
```

### 6.2 ggml 中的高效转置

ggml 中转置是零计算操作——只修改元数据（`ne[]` 和 `nb[]`）：

```c
// 转置 = 交换维度 0 和 1
struct ggml_tensor * t = ggml_transpose(ctx, a);
// t->ne[0] = a->ne[1], t->ne[1] = a->ne[0]
// t->nb[0] = a->nb[1], t->nb[1] = a->nb[0]
// t->data = a->data  (共享数据!)
```

---

## 7. 维度操作

### 7.1 Reshape (重塑)

改变张量的维度，不改变数据：

```c
// [2,3] → [3,2] 或 [6] 或 [1,2,3] 等
struct ggml_tensor * r = ggml_reshape_2d(ctx, a, 3, 2);
```

### 7.2 Permute (维度重排)

重新排列维度顺序：

```c
// [ne0, ne1, ne2, ne3] → [ne1, ne0, ne2, ne3] (交换维度0和1)
struct ggml_tensor * p = ggml_permute(ctx, a, 1, 0, 2, 3);
```

### 7.3 View (视图)

创建共享数据的新张量，带偏移量：

```c
// 从 a 的第 5 个元素开始创建视图
struct ggml_tensor * v = ggml_view_1d(ctx, a, new_len, 5 * sizeof(float));
```

---

## 8. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| 向量运算 | 加/乘/点积 | `ggml_vec_*` 函数族 |
| 点积 | 最核心的运算 | `ggml_vec_dot_f32/q4_0/q4_1` |
| 范数与归一化 | L2范数、LayerNorm | `ggml_vec_norm_f32`, `compute_forward_norm_f32` |
| 矩阵乘法 | 神经网络计算核心 | `compute_forward_mul_mat_*` 四种实现 |
| 量化点积 | 避免反量化开销 | `ggml_vec_dot_q4_0/q4_1` |
| 转置 | 零计算元数据操作 | `ggml_transpose` |
| 维度操作 | reshape/permute/view | `ggml_reshape_*`, `ggml_permute`, `ggml_view_*` |
