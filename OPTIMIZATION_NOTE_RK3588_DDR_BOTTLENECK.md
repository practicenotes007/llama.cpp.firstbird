# RK3588 DDR4 带宽瓶颈优化方案

> **⚠️ 实测修正（2026-06-08）**：方案 B（SDOT 指令替换）已在 RK3588S 上实测验证——无效。
> 瓶颈确认为纯 bandwidth bound，compute 侧任何优化（SDOT、NEON 微调等）均无法提升 decode 吞吐量。
> CPU 在 decode 阶段一直 idle 等 DDR4 数据，加速计算不解决任何问题。
> 
> **正确方向**：减少 per-token 数据搬运量——换小模型（3B→2.2GB）或更激进量化（Q2_K→2.2GB），
> 而非优化计算速度。本文件其余分析（瓶颈定量、权重搬运量核算、DDR 天花板计算）仍然有效。
> 
> 以下为原始分析（2026-05），SDOT 方案的优先级评估已不适用。

针对 llama.cpp.firstbird（最早期 ggml 版本），RK3588 开发板上 7B Q4_0 模型 decode 阶段 1.5 tok/s vs 理论 6-7 tok/s 的差距分析，以及可行改造路径评估。

---

## 一、瓶颈定量分析

### 1.1 当前性能数据

| 指标 | 数值 |
|------|------|
| 模型 | LLaMA 7B Q4_0 |
| 模型权重体积 | ~3.9 GB |
| 实测 decode 速率 | ~1.5 tok/s |
| DDR4 带宽 | ~35 GB/s |
| 理论带宽利用率 | ~17% |

### 1.2 每个 token 需要搬运多少数据

LLaMA 7B 单层权重矩阵（Q4_0）：

| 权重 | 维度 | Q4_0 体积 |
|------|------|-----------|
| wq | 4096x4096 | 9.0 MB |
| wk | 4096x4096 | 9.0 MB |
| wv | 4096x4096 | 9.0 MB |
| wo | 4096x4096 | 9.0 MB |
| w1 | 4096x11008 | 24.2 MB |
| w2 | 11008x4096 | 24.2 MB |
| w3 | 4096x11008 | 24.2 MB |
| 单层合计 | | ~108.6 MB |
| 32层合计 | | ~3.48 GB |
| tok_embeddings + output + norm | | ~0.4 GB |
| 总计 | | ~3.9 GB |

decode 阶段每次推理，所有权重必须至少读一遍。理论上限：

- 35 GB/s / 3.9 GB = 9.0 tok/s
- 考虑 NEON 计算开销、非 matmul 算子（norm/silu/softmax/rope）和 KV cache 访问，合理上限约 6-7 tok/s
- 1.5 tok/s 意味着带宽利用率仅约 17%，83% 的带宽被浪费

LLaMA是纯decoder-only Transformer，没有任何偏置项。整体流程如下：

Token → tok_embeddings → 
  [ for 每层：
      RMSNorm → Self-Attention(wq, wk, wv, wo) → 残差 →
      RMSNorm → SwiGLU-FFN(w1, w3, w2) → 残差
  ] →
  最终 RMSNorm → output(lm_head) → 概率

注：中间维度 11008 是 LLaMA 特意选取的，大约等于 8/3 × 4096 ≈ 10923，取整为 11008，是为了与硬件对齐。

### 1.3 带宽去哪了

RK3588 Cortex-A76 缓存层次：

| 层级 | 容量 | 带宽 |
|------|------|------|
| L1 D$ | 64 KB/核 | 数百 GB/s |
| L2 | 512 KB/核 (A76) | 50-100 GB/s |
| L3 (共享) | 2-4 MB | 20-40 GB/s |
| DDR4 | 8-16 GB | ~35 GB/s |

7B Q4_0 模型 3.9 GB 权重远超 L3 容量，每个权重行读入 L1/L2 后下次再需要时早已被驱逐。结果：

- 每个 weight row 在 decode 阶段都来自 DDR4，无一命中 L3
- 多线程并行加剧 L3 争抢，4 个 A76 核各自搬运不同的权重行，L3 被反复冲刷
- DRAM 行缓冲（row buffer）命中率低，权重矩阵按行遍历但不同线程交叉访问

### 1.4 代码层面的根本原因

ggml_graph_compute() 的执行模型（ggml.c:9363）严格串行：遍历计算图所有节点，每个节点依次执行 INIT / COMPUTE / FINALIZE。COMPUTE 内部线程池并行（按行/列分割），但节点之间没有重叠。

三个层面的带宽浪费：

1. 计算图层面没有流水线：计算节点 N 时，节点 N+1 的权重仍在 DDR4，没有提前搬入缓存
2. MUL_MAT 内核对 DRAM 不友好：ggml_compute_forward_mul_mat_q4_0_f32（ggml.c:6165）按行切分线程，不同线程访问的行在内存中相距甚远
3. vec_dot 内核层面没有预取：ggml_vec_dot_q4_0（ggml.c:1312）每次迭代加载 2 个 Q4_0 block，没有 prefetch 指令提前发起内存请求

---

## 二、优化方案总览

| 方案 | 预期收益 | 改动量 | 风险 | 优先级 |
|------|----------|--------|------|--------|
| A: NEON 内核软件预取 | 1.1-1.3x | 小 | 极低 | P0 |
| B: ARM SDOT 指令替换 | 1.5-2.0x | 中 | 低 | P0 |
| C: 层间流水线预取 | 1.3-1.5x | 中 | 中 | P1 |
| D: 线程调度交错化 | 1.1-1.2x | 小 | 低 | P1 |
| E: DRAM 友好的权重行分组 | 1.1-1.2x | 中 | 中 | P2 |
| F: 减少 per-token 权重搬运量 | 2-4x | 大 | 高 | P2 |

---

## 三、方案 A：NEON 内核软件预取

### 3.1 思路

在 ggml_vec_dot_q4_0 的循环体内，当前迭代计算 block i 时，提前发起 block i+k 的 cache line 预取。当循环到达 block i+k 时，数据已从 DDR4 搬到 L2/L3，避免 load stall。

### 3.2 改动位置

ggml.c 第 1312-1394 行，ggml_vec_dot_q4_0 的 NEON 路径。

### 3.3 具体改造

在循环体开头加入 __builtin_prefetch，预取 PREFETCH_DISTANCE 个 block 之后的数据。预取距离的经验值计算：DDR4 延迟约 70-100ns，A76 @2.4GHz 一个周期约 0.42ns，一次 vec_dot 迭代约 20-30 个周期，需要提前预取 70/0.42/25 约 6-7 次迭代，取 8。

改造方式：在 for 循环体开头加 4 条 __builtin_prefetch 调用（对 pd0/pb0/pd1/pb1 各预取一条 cache line），原有 NEON 计算代码不变。

### 3.4 收益与局限

- 预期 1.1-1.3x 加速
- decode 阶段（batch=1）效果最明显，计算量最小、带宽瓶颈最严重
- 局限：预取距离需要针对特定硬件调优，预取本身消耗 DRAM 带宽
- 验证方法：perf stat 采集 L1-dcache-load-misses、LLC-load-misses，对比改动前后 decode token/s

---

## 四、方案 B：ARM SDOT 指令替换（最推荐）

### 4.1 思路

Cortex-A76 支持 ARMv8.2-A dotprod 扩展，提供 SDOT（Signed Dot Product）指令，单条指令完成 4x8-bit 整数点积并累加到 32-bit 累加器。

当前 ggml_vec_dot_q4_0 的 NEON 实现需要 6 步：nibble 解包 -> 减 8 -> int8xint8 乘法 -> int16 累加 -> int32 扩展 -> float 缩放。SDOT 把中间三步合并成一条指令。

这不仅是计算加速。SDOT 减少了每个 block 需要执行的指令数，CPU 在等待内存时有更多时间发出预取请求，间接提升带宽利用率。

### 4.2 改动位置

ggml.c 第 1312-1394 行，ggml_vec_dot_q4_0 的 NEON 路径。新增一个 `ggml_vec_dot_q4_0_dotprod` 函数，在 `#if defined(__ARM_FEATURE_DOTPROD)` 条件下编译。

### 4.3 核心改造逻辑

nibble 解包和减 8 的步骤不变（vandq + vshrq + vsubq）。关键变化在点积累加部分：

当前实现（4 条 vmull + 4 条 vaddq + 手动 lane 提取求和）替换为：

- 分配 int32x4_t 累加器初始化为 0
- 用 vdotq_s32 代替 vmull_s8 + vaddq_s16，每个 block 只需 2 条 vdotq 指令
- 用 vaddvq_s32 做水平求和，替代手动 lane 提取

指令数从约 20 条降到约 12 条，减少 40%。

### 4.4 编译选项修改

Makefile 第 143-146 行，aarch64 部分：

当前：`CFLAGS += -mcpu=native`
改为：`CFLAGS += -mcpu=native+dotprod`

### 4.5 收益与验证

- 预期 1.5-2.0x 加速
- 间接提升带宽利用率：更少的计算指令让 CPU 有更多时间发出预取请求
- 验证方法：
  1. 确认 A76 核支持 dotprod：`cat /proc/cpuinfo | grep dotprod`
  2. 对比原始 NEON 和 SDOT 版本的输出数值一致性
  3. 测量 decode token/s 差异

---

## 五、方案 C：层间流水线预取

### 5.1 思路

这是你原始想法的直接回应——在计算当前层的同时，提前将下一层的权重从 DDR4 搬入 L3 缓存。

当前 llama_eval（main.cpp:561-689）逐层串行执行。计算完 layer 0 的全部算子后，才开始 layer 1。而 layer 1 的权重（约 108 MB）在 DDR4 中需要重新搬入。

如果能在计算 layer 0 的 FFN 阶段时，用一个独立的预取线程把 layer 1 的 attention 权重提前搬入 L3，那么 layer 1 的 attention 计算就可以从 L3 命中而非 DDR4 重新加载。

### 5.2 改动位置

1. ggml.c 的 ggml_graph_compute 函数（添加预取线程支持）
2. main.cpp 的 llama_eval 函数（标注可预取的权重范围）

### 5.3 实现思路

在 ggml_graph_compute 中引入一个专用的预取线程。主线程每完成一个 MUL_MAT 节点的 INIT 阶段后，通知预取线程开始预取下一个 MUL_MAT 节点的 src0（权重矩阵）。预取线程用 __builtin_prefetch 以 cache line（64 字节）为粒度扫过权重数据。

### 5.4 关键限制

1. L3 容量 2-4 MB，而单个权重矩阵 9-24 MB。预取只能覆盖矩阵的一小部分
2. 预取线程本身消耗 DRAM 带宽，可能与计算线程争抢
3. 预取的数据可能在计算线程用到之前就被计算线程自己的权重冲刷掉
4. 效果高度依赖 L3 容量和权重矩阵尺寸的比例

### 5.5 收益估算

- 预期 1.3-1.5x 加速
- 对 attention 权重（9 MB/个）效果优于 FFN 权重（24 MB/个），因为 L3 能容纳更大比例
- 最有效的场景：预取线程跑在 A55 小核上，不占用 A76 大核的计算资源
- 与方案 A+B 组合后效果叠加：SDOT 减少计算时间，预取减少等待时间

### 5.6 验证方法

1. 用 perf top 观察预取线程的 CPU 占用
2. 用 perf stat 对比 LLC-load-misses 比例
3. 测量 decode token/s，确认预取线程不引入额外延迟

---

## 六、方案 D：线程调度交错化

### 6.1 思路

当前 ggml_compute_forward_mul_mat_q4_0_f32（ggml.c:6170-6178）按行连续分配给线程：Thread 0 处理行 [0, dr)，Thread 1 处理行 [dr, 2*dr)，依此类推。这导致 Thread 0 和 Thread 1 访问的内存地址相距 dr * nb01，对 4096x4096 Q4_0 矩阵约 1.1 MB。

交错分配：Thread 0 处理行 0, 4, 8, ...，Thread 1 处理行 1, 5, 9, ...。同一时刻不同线程访问的行在内存中相邻，对 DRAM row buffer 更友好。

### 6.2 改动位置

ggml.c 第 6170-6204 行，ggml_compute_forward_mul_mat_q4_0_f32 中按行分配的逻辑。

### 6.3 具体改造

将连续分配改为交错分配：

    // 当前
    const int ir0 = dr * ith;
    const int ir1 = MIN(ir0 + dr, nr);

    // 改为交错
    for (int ir = ith; ir < nr; ir += nth) {
        // 原有循环体不变
    }

### 6.4 收益与风险

- 预期 1.1-1.2x 加速
- 风险极低：只改变遍历顺序，不改变计算逻辑
- 需要验证：交错访问是否在 RK3588 的 DDR4 控制器上确实提高 row buffer 命中率
- 可能与硬件预取器（streaming prefetcher）的行为冲突，需要实测

---

## 七、方案 E：DRAM 友好的权重行分组

### 7.1 思路

当前 Q4_0 权重矩阵按行存储（row-major），每行 4096 个元素占 2.3 KB。一个 DRAM row buffer 通常 8 KB，意味着 3-4 个权重行共享一个 DRAM row。

但 4 个线程同时访问不同行时，这些行可能分散在不同的 DRAM row buffer 中，导致频繁的 row buffer miss（激活/预充电周期约 35-50ns）。

如果将权重矩阵重新组织为"行分组"格式——每 GROUP_SIZE 行打包成连续的 8 KB 对齐块——可以确保同一组的行在同一个 DRAM row buffer 中，减少激活/预充电次数。

### 7.2 改动量

这需要修改：
1. 量化格式（quantize_row_q4_0）
2. 反量化格式（dequantize_row_q4_0）
3. vec_dot 内核（ggml_vec_dot_q4_0）
4. 模型文件格式

改动面大，且与现有量化格式不兼容，需要重新量化所有模型。

### 7.3 收益估算

- 预期 1.1-1.2x 加速
- 需要精确了解 RK3588 DDR4 控制器的 row buffer 大小和 bank 数量才能设计最优分组
- 更适合在新量化格式设计时一并考虑，而非在现有格式上改造

---

## 八、方案 F：减少 per-token 权重搬运量

### 8.1 子方案

**F1: MoE 权重共享/稀疏化**

MoE（Mixture of Experts）模型天然只激活部分权重。例如 Mixtral 8x7B 每个 token 只用 2/8 的专家，等效搬运量是密集模型的 25%。但 LLaMA 7B 是密集模型，无法直接利用。

**F2: 更激进的量化**

从 Q4_0（4.5 bit/elem）降到 Q2_K（2.56 bit/elem），权重体积从 3.9 GB 降到约 2.2 GB。带宽需求减少 44%，但精度损失显著，需要针对具体场景评估可接受度。

**F3: Speculative Decoding**

用一个小模型（如 TinyLLaMA 1.1B）快速生成候选 token，大模型只做验证。假设 60% 的 token 被小模型正确预测，大模型只需验证而非完整前向，等效带宽需求减少。但这需要同时运行两个模型，内存压力增加。

### 8.2 收益与风险

| 子方案 | 收益 | 风险 |
|--------|------|------|
| F1: MoE 模型 | 2-4x | 需要换模型架构 |
| F2: 更激进量化 | 1.5-1.8x | 精度下降，需场景验证 |
| F3: Speculative Decoding | 1.5-2x | 实现复杂度高，需双模型内存 |

---

## 九、推荐实施路径

### Phase 1（立即可做，1-2 天）

1. **方案 B：SDOT 指令替换**——改动集中、风险低、收益最高
2. **方案 A：内核级预取**——改动极小、无风险、与 SDOT 互补

预期效果：1.5 tok/s -> 2.5-3.0 tok/s

### Phase 2（短期优化，1 周）

3. **方案 C：层间流水线预取**——需要理解 ggml_graph_compute 的线程调度
4. **方案 D：线程调度交错化**——改动极小、可快速验证

预期效果：2.5-3.0 tok/s -> 3.5-4.0 tok/s

### Phase 3（中长期方向，需评估）

5. 方案 E：DRAM 友好存储格式——需修改量化格式，影响面大
6. 方案 F：减少搬运量——需要换模型或换量化策略，属于架构级决策

### 关键判断

在 RK3588 这个平台上，DDR4 带宽是硬约束。CPU+NEON 的理论峰值就是 6-7 tok/s，即使用尽所有优化手段也不可能超过这个天花板。如果场景要求持续 5+ tok/s，需要考虑：

1. RK3588 的 NPU（6 TOPS INT8）不能缓解带宽瓶颈——NPU 片上 SRAM 仅 3-6 MB，权重同样存储在 DDR4 中、通过同一条总线读取。NPU 的优势是计算吞吐而非带宽节省。在带宽受限场景下，NPU 与 CPU 共享 DDR4 总线可能反而加剧争抢
2. 换用更小的模型（Qwen2.5-1.5B + Q4_0 约只需 0.9 GB 权重，理论上限 35/0.9 = 38 tok/s）
3. 等待带宽更高的硬件平台（LPDDR5 可达 60-80 GB/s）

---

## 附录 A：DDR 实测带宽校准方法

本文分析中使用的 35 GB/s 带宽是理论值，实际可用带宽受 DDR 频率、总线仲裁、CPU 降频等因素影响可能显著偏低。必须用实测值替换理论值，否则天花板计算失准。推荐用 STREAM 和 mbw 两个工具互相验证，直接在板子上操作。

### A.1 准备工作

SSH 登录到 RK3588 开发板，确认系统信息：

    uname -a
    cat /proc/cpuinfo | grep -E "model|processor|Hardware"   # 确认是 Cortex-A76/A55
    free -h                                                   # 看总内存和可用内存

确保散热良好、没有后台重负载（关掉其他应用），避免因降频导致带宽测不准。

### A.2 方法一：STREAM（最推荐，反映持续带宽）

STREAM 是测量内存持续带宽的工业标准，基于四种向量操作：Copy、Scale、Add、Triad。其中 Triad 最接近矩阵乘对权重的访问模式（每次迭代两次读、一次写，且计算量极小），能较好地反映推理时的内存瓶颈。

**获取并编译**：

    wget https://www.cs.virginia.edu/stream/FTP/Code/stream.c

编译命令（单线程基础带宽）：

    gcc -O3 -march=armv8-a -mtune=cortex-a76 -fopenmp \
        -DSTREAM_ARRAY_SIZE=100000000 -DNTIMES=20 -o stream stream.c

参数说明：
- `-DSTREAM_ARRAY_SIZE=100000000`：数组大小 1 亿个 double，三个数组约 2.4 GB，远超 L3 缓存（约 3 MB），确保测到的是 DDR 带宽
- `-DNTIMES=20`：重复 20 次取平均
- `-fopenmp`：不加则只跑单核

如果板子内存较小（4 GB），降低数组大小比如 50000000（约 1.2 GB），否则会 OOM。

**单线程测试**：

    ./stream

输出中记录 Triad 行的 Best Rate MB/s，除以 1024 得到 GB/s。

**多线程测试（反映推理时的聚合带宽）**：

LLaMA 推理时通常是 4 个大核并行，必须测多线程聚合带宽：

    export OMP_NUM_THREADS=4
    export OMP_PROC_BIND=close
    taskset -c 4-7 ./stream    # 绑定 A76 大核，先确认: cat /sys/devices/system/cpu/cpu*/cpu_capacity

如果散热不好，跑多几次取稳定值，避免降频。

如果多线程数值比单线程高得多，说明单核无法占满 DDR 总线；如果变化不大，说明总线本身已经是瓶颈。

### A.3 方法二：mbw（简洁直观的内存拷贝带宽）

mbw 测试内存拷贝带宽，使用更简单，得到的是 memcpy 类操作的带宽，比 STREAM Triad 略低但有参考价值。

**安装**：

    sudo apt-get install mbw

**运行**：

    ./mbw 1024   # 测试 1024 MB 块的拷贝

如果报内存不足，减小比如 `./mbw 256`。

输出显示 memcpy 的速率（MB/s），转换成 GB/s。

### A.4 校准理论天花板

得到实测多线程 Triad 带宽（假设为 X GB/s）后，重新计算 decode 理论上限：

- 模型权重总体积：约 3.9 GB
- 理论 tok/s = X / 3.9
- 还要留 10-15% 给 KV cache、norm、激活值等，可用解码上限约 (X / 3.9) * 0.85

举例：
- 实测多线程带宽 25 GB/s（常见于 LPDDR4-3200）：上限 = 25 / 3.9 * 0.85 约 **5.4 tok/s**
- 实测 22 GB/s（可能降频或软件开销）：上限 = 22 / 3.9 * 0.85 约 **4.8 tok/s**

这就能解释为什么实际只能跑 1.5 tok/s——软件实现的带宽利用率太低。本文中假设的 35 GB/s 以及 6-7 tok/s 上限，需要修正为板子的实测结果。

### A.5 快速诊断清单

    # 查看当前 CPU 频率（确保不是锁定在低频）
    cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq

    # 实时观察带宽（perf 工具）
    perf stat -e cache-misses,bus-cycles ./stream

    # 或者简单用 htop 观察跑 stream 时 CPU 是否全部 100%

---

## 附录 B：关键代码位置索引

| 位置 | 文件 | 行号 | 说明 |
|------|------|------|------|
| Q4_0 block 结构 | ggml.c | 401-454 | quantize_row_q4_0 定义，QK=32，block = 4字节float + 16字节packed |
| Q4_0 vec_dot NEON | ggml.c | 1312-1394 | ggml_vec_dot_q4_0 的 NEON SIMD 路径 |
| Q4_0 vec_mad NEON | ggml.c | 1737-1791 | ggml_vec_mad_q4_0 的 NEON SIMD 路径 |
| Q4_0 MUL_MAT compute | ggml.c | 5970-6268 | ggml_compute_forward_mul_mat_q4_0_f32 |
| 图计算调度 | ggml.c | 9363-9506 | ggml_graph_compute 主循环 |
| 线程调度（行分配） | ggml.c | 6170-6204 | MUL_MAT 按行分配给线程 |
| llama_eval 逐层循环 | main.cpp | 561-689 | Transformer 层的前向计算循环 |
| 模型加载 | main.cpp | 73-499 | llama_model_load 权重加载 |
| Makefile aarch64 | Makefile | 143-146 | ARM 编译选项 |
