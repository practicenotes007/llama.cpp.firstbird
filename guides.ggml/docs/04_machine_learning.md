# 机器学习基础背景知识补充

> 为阅读 `ggml.c` 所需的机器学习核心概念，重要程度 ★★★★

---

## 1. 梯度下降 (Gradient Descent)

### 1.1 核心思想

梯度下降是最基本的优化算法——沿着损失函数梯度的反方向更新参数，逐步逼近最小值：

```
θ_{t+1} = θ_t - η · ∂L/∂θ
```

其中：
- `θ`：模型参数（权重）
- `η`：学习率 (learning rate)
- `∂L/∂θ`：损失函数对参数的梯度
- `L`：损失函数 (loss function)

### 1.2 直觉理解

想象站在山坡上，梯度指向最陡上升方向。梯度下降就是沿着最陡下降方向走一步：

```
损失函数曲面:

    L(θ)
     ↑
     |    *
     |   / \
     |  /   \
     | /     \
     |/       \
     +----------→ θ
      → 沿负梯度方向走
```

### 1.3 ggml 中的体现

ggml 的优化器 (`ggml_opt`) 实现了多种梯度下降变体：

```c
// 简单 SGD 伪代码
for each parameter p:
    p->data -= lr * p->grad->data;
```

---

## 2. 反向传播 (Backpropagation)

### 2.1 计算图

反向传播的基础是**计算图**——将复杂计算分解为一系列简单操作的链式组合：

```
前向传播 (Forward):
  x → [f] → a → [g] → b → [h] → L(损失)
  
反向传播 (Backward):
  ∂L/∂x ← [f] ← ∂L/∂a ← [g] ← ∂L/∂b ← [h] ← 1
```

### 2.2 链式法则

反向传播的核心是链式法则：

```
∂L/∂x = ∂L/∂a · ∂a/∂x
```

对于计算图中的每条边，梯度 = 上游梯度 × 局部梯度。

### 2.3 ggml 的计算图结构

```c
struct ggml_cgraph {
    int n_nodes;
    int n_leafs;
    struct ggml_tensor * nodes[GGML_MAX_NODES];  // 按拓扑序排列的操作节点
    struct ggml_tensor * grads[GGML_MAX_NODES];  // 对应的梯度节点
    struct ggml_tensor * leafs[GGML_MAX_NODES];  // 叶子节点（输入/参数）
};
```

### 2.4 前向与反向的执行过程

```
1. 前向传播: 按拓扑序执行所有节点
   for i = 0 to n_nodes-1:
       ggml_compute_forward(nodes[i])

2. 反向传播: 逆拓扑序计算梯度
   for i = n_nodes-1 downto 0:
       ggml_compute_backward(graph, nodes[i])
```

### 2.5 ggml 中的反向传播规则

每种操作定义了自己的反向传播规则：

| 前向操作 | 数学表示 | 反向传播规则 |
|---------|---------|-------------|
| ADD | z = x + y | ∂L/∂x += ∂L/∂z, ∂L/∂y += ∂L/∂z |
| MUL | z = x ⊙ y | ∂L/∂x += y·∂L/∂z, ∂L/∂y += x·∂L/∂z |
| MUL_MAT | Z = X·Y | ∂L/∂X += ∂L/∂Z·Yᵀ, ∂L/∂Y += Xᵀ·∂L/∂Z |
| SILU | z = x·σ(x) | ∂L/∂x += σ(x)·(1+x·(1-σ(x)))·∂L/∂z |

关键点：反向传播中梯度使用 **+=**（累加），因为一个张量可能被多个下游操作使用。

---

## 3. 自动微分 (Automatic Differentiation)

### 3.1 三种求导方式对比

| 方法 | 原理 | 精度 | 效率 |
|------|------|------|------|
| 符号微分 | 对数学表达式变换 | 精确 | 表达式膨胀 |
| 数值差分 | (f(x+h)-f(x-h))/(2h) | 近似 (截断误差) | 简单但慢 |
| 自动微分 | 链式法则+计算图 | 精确（到浮点精度） | 高效 |

### 3.2 两种自动微分模式

**前向模式 (Forward-mode AD)**：
- 沿计算图前向方向同时计算值和导数
- 对每个输入变量需要一次前向遍历
- 适合输入维度 < 输出维度的情况

**反向模式 (Reverse-mode AD)**：
- 先做前向传播计算值，再反向传播计算导数
- 一次反向遍历可求出所有输入的梯度
- 适合输入维度 > 输出维度的情况（深度学习的典型场景）

### 3.3 ggml 使用反向模式

ggml 实现的是反向模式自动微分，这是深度学习的标准选择：

```c
// 1. 构建计算图
struct ggml_cgraph * graph = ggml_build_forward(loss);

// 2. 前向传播
ggml_graph_compute(ctx, graph);

// 3. 反向传播 (自动微分)
ggml_graph_backward(ctx, graph);

// 4. 读取梯度
float * grad = (float *)param->grad->data;
```

### 3.4 ggml 的梯度累积机制

```c
void ggml_compute_backward(struct ggml_context * ctx, struct ggml_cgraph * graph, int i) {
    struct ggml_tensor * node = graph->nodes[i];
    struct ggml_tensor * grad = graph->grads[i];

    switch (node->op) {
        case GGML_OP_ADD:
            // src0 的梯度累加
            ggml_vec_acc_f32(ne, src0->grad->data, grad->data);
            // src1 的梯度累加
            ggml_vec_acc_f32(ne, src1->grad->data, grad->data);
            break;
        case GGML_OP_MUL:
            // ∂L/∂src0 += src1 * ∂L/∂node
            ggml_vec_mul_f32(ne, tmp, src1->data, grad->data);
            ggml_vec_acc_f32(ne, src0->grad->data, tmp);
            // ...
            break;
        // ... 其他操作
    }
}
```

### 3.5 数值差分：NORM 的梯度

ggml 中 `NORM` 操作没有解析梯度实现，使用数值差分替代：

```c
// 数值差分求梯度
for (int i = 0; i < ne; i++) {
    float x_orig = ((float *)src0->data)[i];
    ((float *)src0->data)[i] = x_orig + eps;
    ggml_compute_forward(ctx, node);  // 重新计算 f(x+eps)
    float f_plus = ...;
    ((float *)src0->data)[i] = x_orig - eps;
    ggml_compute_forward(ctx, node);  // 重新计算 f(x-eps)
    float f_minus = ...;
    ((float *)src0->data)[i] = x_orig;  // 恢复原值
    ((float *)grad0->data)[i] = (f_plus - f_minus) / (2 * eps);
}
```

虽然精确度不如解析梯度，但对 NORM 这样的简单操作足够使用。

---

## 4. 损失函数 (Loss Functions)

ggml 实现了两种训练损失函数：

### 4.1 均方误差 (MSE)

```
L_MSE = (1/N) Σ(y_pred - y_true)²
```

### 4.2 交叉熵 (Cross Entropy)

```
L_CE = -Σ y_true · log(y_pred)
```

---

## 5. 正则化 (Regularization)

ggml 支持三种正则化方式防止过拟合：

| 类型 | 惩罚项 | 效果 |
|------|--------|------|
| L2 (Ridge) | λ·‖θ‖² | 所有参数趋向较小值 |
| L1 (Lasso) | λ·‖θ‖₁ | 部分参数趋向零（稀疏化） |

```c
// L2 正则化的梯度
// ∂L/∂θ += λ * θ  (权重衰减)
ggml_vec_acc_f32(ne, grad->data, param->data);  // 加上 λ*θ 的贡献
```

---

## 6. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| 梯度下降 | 沿负梯度方向更新参数 | `ggml_opt` 优化器 |
| 反向传播 | 链式法则在计算图上的应用 | `ggml_compute_backward` |
| 计算图 | 运算的有向无环图 | `struct ggml_cgraph` |
| 反向模式 AD | 先前向后反向的高效自动微分 | `ggml_graph_backward` |
| 梯度累积 | 一个张量被多次使用时梯度相加 | `ggml_vec_acc_f32` |
| 数值差分 | 无解析梯度时的替代方案 | NORM 操作的梯度 |
| 正则化 | 防止过拟合 | L1/L2 惩罚项 |
