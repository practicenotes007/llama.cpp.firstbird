# 优化算法背景知识补充

> 为阅读 `ggml.c` 所需的优化算法知识，重要程度 ★★★

---

## 1. 优化问题概述

### 1.1 什么是优化

在机器学习训练中，优化是指找到使损失函数最小的参数值：

```
min_θ L(θ)
```

其中 θ 是模型参数（权重矩阵、偏置等），L 是损失函数。

### 1.2 ggml 中的优化器

ggml 在阶段八（L9020-L10502）实现了训练优化功能，支持以下优化算法：

- **SGD** (Stochastic Gradient Descent)
- **Adam** (Adaptive Moment Estimation)
- **L-BFGS** (Limited-memory Broyden–Fletcher–Goldfarb–Shanno)

---

## 2. SGD (随机梯度下降)

### 2.1 基本形式

```
θ_{t+1} = θ_t - η · g_t
```

其中 g_t = ∂L/∂θ 是当前梯度，η 是学习率。

### 2.2 带动量的 SGD

```
v_{t+1} = μ · v_t + g_t
θ_{t+1} = θ_t - η · v_{t+1}
```

其中 μ 是动量系数 (通常 0.9)，v 是速度（动量）。

动量的作用：
- 加速沿一致梯度方向的移动
- 抑制梯度震荡
- 帮助逃出局部最小值和鞍点

### 2.3 ggml 中的 SGD 实现

```c
// 简化的 SGD 更新逻辑
for (int i = 0; i < n_params; i++) {
    // 带动量的更新
    float * grad = (float *)params[i]->grad->data;
    float * data = (float *)params[i]->data;
    float * mom  = (float *)momentum[i]->data;  // 动量缓冲区

    for (int j = 0; j < ne; j++) {
        mom[j] = momentum * mom[j] + grad[j];  // 更新动量
        data[j] -= lr * mom[j];                 // 更新参数
    }
}
```

---

## 3. Adam 优化器

### 3.1 核心思想

Adam 为每个参数维护两个动量估计：
- **一阶矩 (m)**：梯度的指数移动平均（方向）
- **二阶矩 (v)**：梯度平方的指数移动平均（幅度）

自适应学习率 = 基础学习率 / sqrt(二阶矩)

### 3.2 算法步骤

```
输入: 学习率 η, β₁=0.9, β₂=0.999, ε=1e-8

初始化: m₀=0, v₀=0, t=0

每步迭代:
  t = t + 1
  g_t = ∇L(θ_t)                        // 计算梯度

  m_t = β₁·m_{t-1} + (1-β₁)·g_t       // 更新一阶矩 (梯度的移动平均)
  v_t = β₂·v_{t-1} + (1-β₂)·g_t²      // 更新二阶矩 (梯度平方的移动平均)

  m̂_t = m_t / (1 - β₁^t)              // 一阶矩偏差校正
  v̂_t = v_t / (1 - β₂^t)              // 二阶矩偏差校正

  θ_{t+1} = θ_t - η · m̂_t / (√v̂_t + ε)  // 更新参数
```

### 3.3 偏差校正的意义

初始化时 m₀=0, v₀=0，导致初期估计偏向零。偏差校正使初期步长更大：

```
未校正: m₁ = 0.1·g₁  (太小，因为 0.9×0 + 0.1×g₁)
校正后: m̂₁ = g₁      (恢复了真实幅度)

m̂_t = m_t / (1 - β₁^t)
当 t→∞: (1-β₁^t)→1, 校正消失
当 t=1:  (1-0.9)=0.1, m̂₁ = m₁/0.1 = 10×m₁ = g₁  ✓
```

### 3.4 Adam 的优势

| 特性 | 说明 |
|------|------|
| 自适应学习率 | 每个参数有不同的有效学习率 |
| 对学习率不敏感 | 默认 η=0.001 适用于多数问题 |
| 快速收敛 | 初期收敛通常比 SGD 快 |
| 处理稀疏梯度 | 适合 NLP 等稀疏梯度场景 |

### 3.5 ggml 中的 Adam 实现

```c
struct ggml_opt_params {
    ggml_opt_type type;
    int n_threads;
    float lr;           // 学习率 η
    // Adam 参数
    float adam_beta1;   // β₁ = 0.9
    float adam_beta2;   // β₂ = 0.999
    float adam_eps;     // ε = 1e-8
    // ...
};

void ggml_opt_adam(struct ggml_context * ctx, struct ggml_opt_params params, struct ggml_tensor * loss) {
    // 为每个参数分配 m 和 v 缓冲区
    for (int i = 0; i < n_params; i++) {
        // m[i] = ggml_new_tensor_... (一阶矩)
        // v[i] = ggml_new_tensor_... (二阶矩)
    }

    for (int step = 0; step < max_steps; step++) {
        // 1. 前向传播 + 反向传播
        ggml_graph_compute(ctx, gf);
        ggml_graph_backward(ctx, gf);

        // 2. Adam 更新
        float t = step + 1;
        for (int i = 0; i < n_params; i++) {
            float * m = (float *)m_buf[i]->data;
            float * v = (float *)v_buf[i]->data;
            float * g = (float *)params[i]->grad->data;
            float * p = (float *)params[i]->data;

            for (int j = 0; j < ne; j++) {
                m[j] = beta1 * m[j] + (1 - beta1) * g[j];
                v[j] = beta2 * v[j] + (1 - beta2) * g[j] * g[j];
                float mh = m[j] / (1 - powf(beta1, t));
                float vh = v[j] / (1 - powf(beta2, t));
                p[j] -= lr * mh / (sqrtf(vh) + eps);
            }
        }
    }
}
```

---

## 4. L-BFGS 算法

### 4.1 背景

L-BFGS 属于**拟牛顿法** (Quasi-Newton Methods)，用近似 Hessian 矩阵（二阶导数）来加速收敛。

牛顿法：
```
θ_{t+1} = θ_t - H_t⁻¹ · g_t
```

其中 H_t 是 Hessian 矩阵（损失函数的二阶导数矩阵）。

问题：Hessian 是 N×N 矩阵（N=参数量），直接计算和存储不可行。

### 4.2 L-BFGS 的核心思想

L-BFGS (Limited-memory BFGS) 不显式存储 Hessian，而是保存最近 m 步的梯度差和参数差，通过**双循环递归** (two-loop recursion) 隐式计算 H⁻¹·g：

```
保存最近 m 步的历史:
  s_t = θ_{t+1} - θ_t   (参数差)
  y_t = g_{t+1} - g_t   (梯度差)
  ρ_t = 1 / (y_tᵀ · s_t)

H⁻¹·g 的双循环递归:
  q = g
  for i = t-1, t-2, ..., t-m:    // 反向循环
      α_i = ρ_i · s_iᵀ · q
      q = q - α_i · y_i

  r = H₀ · q                      // H₀ 是初始近似 (通常为对角矩阵)

  for i = t-m, t-m+1, ..., t-1:  // 正向循环
      β_i = ρ_i · y_iᵀ · r
      r = r + s_i · (α_i - β_i)

  r ≈ H⁻¹ · g
```

### 4.3 L-BFGS 的优势

| 特性 | L-BFGS | Adam | SGD |
|------|--------|------|-----|
| 内存 | O(m·N) | O(N) | O(N) 或 O(N) (带动量) |
| 二阶信息 | 近似利用 | 无 | 无 |
| 收敛速度 | 超线性 | 较快 | 线性 |
| 适合场景 | 小到中规模凸优化 | 大规模非凸 (深度学习) | 通用 |
| 实现复杂度 | 高 | 中 | 低 |

### 4.4 ggml 中的 L-BFGS 实现

ggml 的 L-BFGS 参考了 [liblbfgs](https://github.com/chokkan/liblbfgs) 的实现：

```c
struct ggml_opt_lbfgs {
    int m;                // 历史长度 (通常 6)
    int n;                // 参数数量
    float * x;            // 当前参数
    float * g;            // 当前梯度
    float * gp;           // 上一步梯度
    float * d;            // 搜索方向
    float * xp;           // 上一步参数
    float * s[m];         // 参数差历史 s_t = x_{t+1} - x_t
    float * y[m];         // 梯度差历史 y_t = g_{t+1} - g_t
    float rho[m];         // ρ_t = 1 / (y_tᵀ · s_t)
    float * pf;           // 工作缓冲区
};
```

---

## 5. 线搜索 (Line Search)

### 5.1 为什么需要线搜索

L-BFGS 确定了搜索方向 d = H⁻¹·g 后，需要确定沿此方向走多远：

```
θ_{t+1} = θ_t + α · d_t
```

线搜索寻找最优步长 α，使得：

```
α = argmin L(θ_t + α · d_t)
```

### 5.2 Wolfe 条件

ggml 使用 Wolfe 条件判断步长是否合适：

**充分下降条件 (Armijo 条件)**：
```
L(θ + α·d) ≤ L(θ) + c₁ · α · ∇L(θ)ᵀ · d
```
保证函数值有足够的下降。

**曲率条件**：
```
∇L(θ + α·d)ᵀ · d ≥ c₂ · ∇L(θ)ᵀ · d
```
保证步长不会太小（c₂ 通常取 0.9）。

### 5.3 ggml 中的线搜索实现

```c
// 简化的线搜索流程
float ggml_opt_line_search(...) {
    float alpha = 1.0f;  // 初始步长
    
    while (true) {
        // 计算 x_new = x + alpha * d
        // 计算 L(x_new) 和 ∇L(x_new)
        
        // 检查 Armijo 条件
        if (f_new <= f + c1 * alpha * dg_init) {
            // 检查 Wolfe 曲率条件
            if (dg_new >= c2 * dg_init) {
                return alpha;  // 找到满足条件的步长
            }
        }
        
        // 缩小或放大步长
        // 使用三次插值估算新的 alpha
    }
}
```

---

## 6. 优化器选择指南

| 场景 | 推荐优化器 | 原因 |
|------|-----------|------|
| 大模型训练 (LLM) | Adam | 自适应学习率，稳定 |
| 小模型微调 | Adam | 收敛快 |
| 凸优化问题 | L-BFGS | 超线性收敛 |
| 超参数搜索 | L-BFGS | 二阶信息加速 |
| 极简实现 | SGD+momentum | 实现简单 |

---

## 7. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| SGD | 最基本的梯度下降 | 带 momentum 的 SGD |
| Adam | 自适应学习率，一阶+二阶矩 | `ggml_opt_adam` |
| L-BFGS | 拟牛顿法，近似二阶信息 | `ggml_opt_lbfgs` |
| 偏差校正 | Adam 初期矩估计的修正 | `m̂ = m / (1-β₁^t)` |
| 线搜索 | 确定最优步长 | Wolfe 条件 |
| 双循环递归 | L-BFGS 隐式计算 H⁻¹·g | 保存最近 m 步历史 |
