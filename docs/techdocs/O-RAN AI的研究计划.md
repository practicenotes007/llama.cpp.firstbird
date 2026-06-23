# O-RAN AI 研究计划：基于边缘 ARM+NPU 平台的 AI-Native RAN 研究

> 基于 2026.05.19 技术调研整理。涵盖：硬件平台评估、srsRAN ZeroMP 部署可行性、3GPP AI-RAN 标准演进、六个核心研究方向、双板协同架构、实验路线图。

---

## 一、研究动机与定位

### 1.1 为什么是"边缘 NPU + 开源 RAN"

传统 AI-RAN 研究依赖 NVIDIA Aerial + GPU 服务器（单台 >$8000），聚焦云化 vRAN 场景。但在 O-RAN "白盒化 + 边缘智能"的愿景下，**低成本 ARM SoC + NPU** 作为边缘 AI 推理载体，是一个被严重低估的方向：

- **成本差 30 倍**：昇腾 340B1（~$150）+ RK3588S（~$100）vs NVIDIA L40S（~$8000）
- **功耗差 100 倍**：整系统 <25W vs GPU 服务器 >300W
- **学术空白**：Edge NPU 加速开源 5G RAN AI 推理的交叉领域几乎无先例工作
- **产业趋势**：3GPP Rel-18/19 将 AI 纳入标准，O-RAN Alliance 定义 Non-RT / Near-RT RIC 体系，但现有 rApp/xApp 均运行在 x86 服务器上，ARM 边缘部署尚属空白

### 1.2 研究定位

| 维度 | 本方案 | 业界主流方案 |
|------|--------|-------------|
| 计算平台 | ARM SoC + NPU（<25W） | x86 Server + GPU（>300W） |
| RAN 软件 | srsRAN（开源，ZMQ 无 RF） | NVIDIA Aerial / OAI（需 USRP） |
| AI 推理 | NPU INT8/FP16 边缘推理 | GPU CUDA 大规模推理 |
| 研究范式 | AI for Network（轻量模型） | AI for Network + Network for AI（大模型） |
| 目标产出 | 学术论文 + 开源工具链 | 商业产品 |

**核心命题：** 在算力受限的边缘 NPU 上，能否以可接受的精度损失，替代传统 RAN 中的关键算法模块？精度-延迟-功耗的 trade-off 曲线是什么样的？

---

## 二、硬件平台评估

### 2.1 设备规格

| 维度 | 昇腾 340B1 | RK3588S (Orange Pi 5) |
|------|-----------|----------------------|
| CPU | 4Core ARM (Taishan) | 4×A76(2.4GHz) + 4×A55(1.8GHz) = 8核 |
| RAM | 24GB | 16GB |
| NPU | 20 TOPS (INT8) | 6 TOPS (INT8), 3核心 RKNN |
| NPU FP16 | ~5 TFLOPS | ~1.5 TFLOPS |
| GPU | 昇腾自研 | Mali-G610 MP4 |
| OS | Ubuntu (aarch64) | Ubuntu 22.04 (aarch64) |
| 功耗 | <15W | <10W |
| NPU SDK | 华为 CANN | Rockchip RKNN / RKLLM |
| 存储 | 待确认 | eMMC / microSD / NVMe SSD |

### 2.2 srsRAN 部署可行性评估

| 场景 | 可行性 | 说明 |
|------|:------:|------|
| srsRAN_4G LTE (ZMQ) 单板全套 | ✅ 完全可行 | 4核+完全够用，RPi4 已证明 |
| srsRAN_Project 5G UE 模拟 (ZMQ) | ✅ 完全可行 | 资源消耗低，最低 1核/2GB |
| srsRAN_Project 5G gNB (ZMQ) 单板 | ⚠️ 需实测 | 4核可能卡在启动校验，8核没问题 |
| 双板分布式部署 (gNB + UE + CN) | ✅ 推荐 | 两板互补，组成完整研发平台 |
| O-RAN Split 7.2 (带 O-RU) | ❌ 不适用 | 需要 25GE NIC + PTP + 高端 x86 |

#### srsRAN 官方硬件要求（ZMQ 模式）

| 项目 | srsRAN_4G 最低 | srsRAN_Project 5G 最低 |
|------|:--------------:|:---------------------:|
| CPU | 1 Core | 2~5 Core |
| RAM | 2GB | 4GB |
| HDD | 10GB | 10GB |
| OS | Ubuntu 20.04+ | Ubuntu 24.04 |

> 关键信息：srsRAN 官方声明 "Portable across processor architectures, the software has been optimized for **x86 and ARM**"。Raspberry Pi 4B（4核 A72, 4GB）已成功运行 srsRAN 4G eNodeB + EPC 24小时+稳定。

#### 5G gNB 核心数风险点

社区实测（s5uishida, build_srsran_5g_zmq）指出：

> "When srsRAN_Project gNodeB is configured with the ZMQ-based RF driver, **5 CPU cores or more are required** to start up and accept connections from UEs."

此要求在 VM 环境下可降至 2 核，有弹性空间。**建议 RK3588S（8核）运行 gNB，昇腾 340B1（4核）运行 UE + 核心网。**

### 2.3 NPU 架构关键特征

两块 NPU 均为**配置驱动的数据流加速器**，不是独立可编程处理器：

```
CPU 角色:                    NPU 角色:
┌──────────────────┐         ┌──────────────────┐
│ 1. 加载模型文件    │ ──────→ │  权重 SRAM/DDR   │
│ 2. 配置数据流图    │ ──────→ │  数据流描述符     │
│ 3. 推送输入数据    │ ──────→ │  输入 Buffer     │
│ 4. 触发推理        │ ──────→ │  启动数据流      │
│ 5. 等待完成        │ ←────── │  推理完成中断     │
│ 6. 读取输出        │ ←────── │  输出 Buffer     │
└──────────────────┘         └──────────────────┘
```

**对 AI-RAN 的意义：** NPU 擅长固定拓扑的前向推理（MatMul/Conv 流水线），不擅长分支逻辑和动态控制流。RAN 中的 AI 推理模块（信道估计、波束预测、调度决策）恰好是"输入固定形状张量 → 输出固定形状张量"的前向推理，与 NPU 数据流模型高度匹配。

---

## 三、3GPP AI-RAN 标准演进

### 3.1 标准时间线

| 阶段 | 时间 | 内容 | 与本研究的关联 |
|------|------|------|--------------|
| Rel-17 | 2021-2023 | 研究阶段（Study Item），探索 AI/ML 在 NR 空口可行性 | 背景 |
| **Rel-18** | 2024 冻结 | 首批规范性工作：网络节能、负载均衡、移动性优化 | **核心对标** |
| Rel-19 | 进行中 | NWDAF/MDA 增强、信道估计/波束管理 AI 增强 | 前瞻方向 |
| Rel-20 / 6G | ~2028 | AI-Native 设计，AI 成为协议栈原生组件 | 长期愿景 |

### 3.2 3GPP 定义的两大范式

| 范式 | 含义 | 本研究的映射 |
|------|------|------------|
| **AI for Network** | AI 优化网络功能（信道估计、调度、节能等） | 赛道 1~6 的核心内容 |
| **Network for AI** | 网络支撑 AI 应用（QoS 保障、边缘推理分发等） | Phase 4+ 的扩展方向 |

### 3.3 O-RAN Alliance 架构映射

```
┌─────────────────────────────────────────────────────────┐
│                    Service Management & Orchestration    │
│                    (SMO / Non-RT RIC)                    │
│    ┌──────────────┐  ┌──────────────┐                    │
│    │  rApp:       │  │  rApp:       │                    │
│    │  网络节能     │  │  切片编排     │                    │
│    └──────┬───────┘  └──────┬───────┘                    │
│           │ A1 Interface          │                       │
├───────────┼───────────────────────┼───────────────────────┤
│           ▼                       ▼                       │
│              Near-RT RIC                                  │
│    ┌──────────────┐  ┌──────────────┐                    │
│    │  xApp:       │  │  xApp:       │                    │
│    │  波束预测     │  │  异常检测     │                    │
│    └──────┬───────┘  └──────┬───────┘                    │
│           │ E2 Interface          │                       │
├───────────┼───────────────────────┼───────────────────────┤
│           ▼                       ▼                       │
│           O-CU / O-DU / O-RU                               │
│    ┌──────────────────────────────────────────┐           │
│    │  PHY: 信道估计 / MIMO检测 / 译码          │           │
│    │  MAC: 调度 / HARQ                        │           │
│    │  RLC / PDCP / RRC / F1                   │           │
│    └──────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────┘

本研究覆盖范围:
  Non-RT RIC (rApp)  →  赛道4 (切片编排), 赛道5 (节能)
  Near-RT RIC (xApp) →  赛道2 (波束预测), 赛道5 (异常检测)
  O-DU PHY/MAC       →  赛道1 (信道估计), 赛道3 (调度), 赛道6 (神经接收机)
```

---

## 四、六大核心研究方向

### 赛道 1：AI 信道估计与预测（Channel Estimation / CSI Prediction）

**痛点：** 传统方法（LS、MMSE）在导频稀疏、高移动性场景下性能急剧下降。5G NR 的导频密度有限，高频段（mmWave）信道时变更严重。

**AI 解法：**
- 输入：时频域稀疏导频符号（14 OFDM symbols × 12 subcarriers 量级）
- 模型：CNN / ResNet / Transformer，从稀疏导频重建完整 CSI
- 输出：完整信道矩阵估计值
- 性能基准：相比 MMSE，在低 SNR 下 NMSE 降低 2~5 dB

**模型规模与 NPU 适配：**

| 指标 | 轻量级 | 中等 | 重型 |
|------|--------|------|------|
| 参数量 | 10K~50K | 50K~200K | 200K~1M |
| 推理计算量 | 0.1~0.5 MFLOPs | 0.5~2 MFLOPs | 2~10 MFLOPs |
| 推理延迟 (20TOPS) | <50μs | <200μs | <1ms |
| 推理延迟 (6TOPS) | <150μs | <600μs | <3ms |
| 5G NR 实时约束 | 1 slot = 0.5ms | | |
| **结论** | **两板均可** | **两板均可** | **仅昇腾** |

**关键论文：**
- ETH Zürich, "A Compute&Memory Efficient Model-Driven Neural 5G Receiver for Edge AI-assisted RAN" (arXiv:2508.12892, 2025)：FLOPs 减少 66×，参数减少 396×，证明边缘 NPU 部署可行性
- HELENA, "High-Efficiency Learning-based channel Estimation using dual Neural Attention" (arXiv:2506.13408, 2025)：0.11M 参数，推理 0.175ms，NMSE = -16.78dB，轻量级 SOTA
- Luan & Thompson, "Achieving Robust Channel Estimation Neural Networks by Designed Training Data" (arXiv:2507.12630, 2025)：合成训练数据设计准则，解决模型泛化问题

#### 赛道 1 数据工程与实验方法论

##### A. 训练三元组定义

AI 信道估计的监督学习需要如下三元组：

```
┌──────────────────────────────────────────────────────────┐
│                    训练三元组 (X, H, Y)                    │
│                                                          │
│  X: 发送端导频符号 (已知, DM-RS, 由3GPP标准定义)           │
│     形状: [N_symbol × N_subcarrier], complex              │
│     例: 14 OFDM symbols × 624 subcarriers (52 PRB)       │
│     其中只有 DM-RS 位置有非零值，其余为0                    │
│                                                          │
│  H: 真实信道矩阵 (Ground Truth / 标签)                    │
│     形状: [N_symbol × N_subcarrier], complex              │
│     每个元素 H[k,i] = 该子载波在该OFDM符号上的复数增益      │
│                                                          │
│  Y: 接收端信号 (观测值 / 模型输入)                          │
│     Y = H ⊙ X + N  (逐元素相乘 + AWGN噪声)               │
│     形状: [N_symbol × N_subcarrier], complex              │
│                                                          │
│  训练目标:                                                │
│  模型输入 = Y 在导频位置的采样 (稀疏, ~6% REs是DM-RS)       │
│  模型输出 = Ĥ (完整信道矩阵的估计)                          │
│  Loss = ||Ĥ - H||²  (与 Ground Truth 的 MSE)             │
└──────────────────────────────────────────────────────────┘
```

**核心难点：** `X`（导频）是已知的（3GPP TS 38.211 定义了 DM-RS 序列和位置），`Y`（接收信号）是可观测的，**`H`（真实信道）是标签获取的关键难题**——在真实网络中你永远无法得到完美的 H。

##### B. 数据来源三条路径

| 路径 | 描述 | H 可获得性 | 难度 | 推荐度 |
|------|------|:----------:|:----:|:------:|
| **A: 离线信道模拟器** | QuaDRiGa/DeepMIMO/MATLAB 生成 | ✅ 完美 | ★ | **首选** |
| **B: srsRAN ZMQ + 信道注入** | 在 ZMQ 链路中插入已知信道 | ✅ 完美 | ★★★ | 推荐 |
| **C: 纯 O-RAN 在线采集** | 从运行中的 O-RAN 提取 | ❌ 仅有估计 | ★★★★★ | 暂不推荐 |

**路径 A：离线信道模拟器（推荐首选，不需要 O-RAN 运行）**

这是学术界 95% 以上论文使用的方法：

```
┌──────────┐    ┌──────────┐    ┌──────────┐
│ 信道模型   │    │ OFDM     │    │ 噪声添加  │
│ QuaDRiGa │───→│ 调制/解调 │───→│ AWGN     │
│ DeepMIMO │    │          │    │          │
│ 3GPP CDL │    │          │    │          │
└──────────┘    └──────────┘    └──────────┘
     │                               │          │
     ▼                               ▼          ▼
  H (真实信道)                    X (导频)   Y (接收信号)
  = Ground Truth                 = 已知      = 模型输入
```

| 工具 | 特点 | 输出 |
|------|------|------|
| **QuaDRiGa** (开源, MATLAB/Octave) | 3GPP 3D/NR 信道模型兼容，SISO→mMIMO，450MHz~100GHz | 完整信道冲激响应 H(τ,t) |
| **DeepMIMO 5G NR** (开源, MATLAB) | 基于 3GPP CDL 模型 + 射线追踪场景，生成 BS-UE 信道矩阵 | H 矩阵 + 路径参数 |
| **srsRAN_matlab** (开源) | srsRAN 官方测试套件，可生成信道估计器输入输出测试向量 | 二进制测试向量 (.tar.gz) |

**路径 B：srsRAN ZMQ 模式 + 注入已知信道**

在 ZMQ 链路中间插入信道模拟模块，在真实协议栈环境下生成带标签数据：

```
┌──────────┐   ZMQ TCP    ┌──────────────┐   ZMQ TCP    ┌──────────┐
│  srsUE   │─────────────→│ 信道模拟器    │─────────────→│  gNB     │
│ (发送端)  │  tx_port     │ (Python/C++)  │  rx_port     │ (接收端)  │
└──────────┘              │              │              └──────────┘
                          │ H = 已知信道   │
                          │ N = 已知噪声   │
                          │ Y = H*X + N   │
                          └──────────────┘
                               │
                          保存三元组:
                          (X_pilot, H_true, Y_received)
```

**路径 C：纯 O-RAN 在线采集（核心矛盾）**

从运行中的 O-RAN 只能获得 (X, Y)，无法获得完美的 H：
- ✅ Y（接收信号，包含 DM-RS 位置的采样）
- ✅ X（DM-RS 导频序列，标准定义，已知）
- ❌ H（真实信道，不可直接观测，只能得到 LS/MMSE 估计 Ĥ）

**结论：在线采集数据不适合做监督学习训练集，但可作为测试集验证模型泛化能力。**

##### C. 需要保存的数据清单

| 数据项 | 形状 | 类型 | 来源 | 必须 |
|--------|------|------|------|:----:|
| 接收信号 Y | [14 sym × N_sc], complex float | 观测值 | gNB 接收缓冲区 / ZMQ 端口 | ✅ |
| 导频符号 X | [14 sym × N_sc], complex float | 已知 | 3GPP DM-RS 生成器 | ✅ |
| 真实信道 H | [14 sym × N_sc], complex float | 标签 | 信道模拟器 | ✅ (训练) |
| 导频位置掩码 mask | [14 sym × N_sc], bool | 元信息 | 3GPP DM-RS 配置 | ✅ |
| SNR / 噪声方差 σ² | 标量 | 元信息 | 信道模拟器配置 | 推荐 |
| 信道模型参数 | (CDL类型, 延迟扩展, 多普勒...) | 元信息 | 信道模拟器配置 | 推荐 |

**存储估算：**
- 一个 slot 的三元组 (X, H, Y)：14 × 624 × 2 (real+imag) × 3 × 4 bytes ≈ **200 KB**
- 10 万个 slot（训练集）：200KB × 100K = **20 GB**（HDF5 + gzip 压缩后约 5~8 GB）

##### D. 模型评价体系

| 评价层级 | 指标 | 公式 | 含义 |
|----------|------|------|------|
| **L1: 信道估计精度** | NMSE (dB) | 10·log₁₀(‖Ĥ−H‖²/‖H‖²) | 估计值 vs 真实值，越低越好。典型目标: <−15dB |
| **L2: 链路级性能** | BLER | 传输块错误率 | 用 Ĥ 做均衡+译码后的 BLER。目标: <10% @SNR=10dB |
| **L3: 系统级性能** | 吞吐量 (Mbps) | 端到端吞吐量 | 与 baseline (LS/MMSE) 对比 |
| **L4: 实时性** | 推理延迟 (ms/slot) | NPU 推理时间 | 必须 < 1 slot (0.5ms for 5G NR) |
| **L5: 泛化能力** | 跨场景 NMSE (dB) | 未见过信道上的 NMSE | 训练时未使用的信道模型上测试 |

**常见对比基线：**

```
                    NMSE (dB)
                    │
         -5         │  ■ LS (无插值)
                    │
        -10         │  ■ LS + 线性插值
                    │
        -15         │  ■ LS + Wiener滤波 (近似MMSE)
                    │  ★ AI模型 (目标)
        -20         │  ■ MMSE (理论下界, 需要完美信道统计)
                    │
        -25         │  ■ 完美CSI (不可达上界)
                    └──────────────────
                      SNR (dB)
                      0   5   10  15  20
```

##### E. 端到端实验流程

```
Phase 1: 离线数据生成 (不需要O-RAN)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  QuaDRiGa / DeepMIMO  →  生成 (X, H, Y) 三元组
  保存为 HDF5 文件集 (约 5~20 GB)
  覆盖多种场景: UMi, UMa, Indoor, Vehicular
  覆盖多种 SNR: 0~30 dB, 步长 5dB
  覆盖多种移动速度: 3km/h ~ 120km/h

Phase 2: 模型设计与训练 (不需要O-RAN)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  模型输入: Y_at_pilots [sparse, ~6% of REs are DM-RS]
  模型输出: Ĥ [dense, full channel matrix]
  架构: CNN / ResNet / HELENA风格轻量Transformer
  Loss: MSE(Ĥ, H)
  训练: PyTorch on 昇腾 340B1 (24GB RAM) 或 PC
  验证: NMSE vs SNR 曲线, 对比 LS/MMSE baseline

Phase 3: 模型导出与 NPU 部署
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  PyTorch → ONNX → CANN (昇腾) / RKNN (RK3588S)
  量化: FP32 → INT8 (精度损失 <0.5dB NMSE)
  验证: NPU 推理延迟 < 0.5ms (1 slot)

Phase 4: 集成到 srsRAN (需要O-RAN运行)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  修改 srsRAN_Project 信道估计器:
    原始: port_channel_estimator (C++)
    替换: NPU_inference_channel_estimator (C++ + NPU FFI)
  在 gNB 的 PUSCH 处理链中:
    1. 提取 DM-RS 位置的 Y (导频采样)
    2. 推送到 NPU 推理
    3. 获取 Ĥ (完整信道估计)
    4. 用 Ĥ 替代传统插值结果
    5. 后续均衡/解调使用 Ĥ

Phase 5: 端到端验证
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ZMQ 模式: gNB(NPU推理) + srsUE + Open5GS
  注入已知信道 (路径B) → 计算端到端 BLER
  对比: 传统估计器 vs AI估计器 的 BLER/吞吐量
```

---

### 赛道 2：AI 波束管理与预测（Beam Management / Beam Prediction）

**痛点：** 毫米波/高频段需要窄波束对准，传统波束扫描开销大（L1-RSRP 测量占用大量时频资源），切换滞后。

**AI 解法：**
- 输入：过去 K 个时隙的 RSRP / CSI 序列（K=10~50, 几十个浮点数）
- 模型：LSTM / Transformer / TCN
- 输出：下一时隙的最佳波束索引（Top-N 排序）
- 性能基准：相比穷举搜索，波束对齐准确率 >90% 时，开销降低 50~80%

**模型规模与 NPU 适配：**

| 指标 | 典型值 |
|------|--------|
| 参数量 | <50K（LSTM hidden=64~128） |
| 推理计算量 | <0.1 MFLOPs |
| 推理延迟 (6TOPS) | <100μs |
| 实时约束 | 1 slot = 0.5ms |
| **结论** | **RK3588S NPU 完全胜任** |

**实验策略：** 在 ZMQ 模式下，用 QuaDRiGa 信道模拟器生成多径场景数据集，训练波束预测模型，验证预测精度 vs 传统 L1-RSRP 扫描。

---

### 赛道 3：AI MAC 调度器（Intelligent MAC Scheduler）

**痛点：** 传统 PF（Proportional Fair）/ RR（Round Robin）调度器不考虑业务多样性、URLLC/eMBB 混合流量、小区间干扰协同。

**AI 解法：**
- **强化学习方案**：状态 = 用户队列长度 / 信道质量 (CQI) / 历史吞吐量，动作 = 选择哪个用户在下一个 TTI 传输 + MCS 选择
- **监督学习方案**：从离线最优解（穷举搜索 / 整数规划）中学习调度策略
- 性能基准：在 URLLC+eMBB 混合流量下，URLLC 延迟降低 30~50%，eMBB 吞吐量损失 <5%

**模型规模与 NPU 适配：**

| 指标 | RL Actor 推理 | 监督学习推理 |
|------|:------------:|:----------:|
| 参数量 | <100K (3~5层FC) | <200K |
| 推理计算量 | <0.5 MFLOPs | <1 MFLOPs |
| 推理延迟 (20TOPS) | <100μs | <200μs |
| 实时约束 | 1 TTI = 0.5ms (5G NR) | |
| RL 训练资源 | 需 16GB+ RAM (replay buffer) | N/A |
| **结论** | **推理两板均可，训练仅昇腾** | **两板均可** |

**工程挑战：** 需修改 srsRAN MAC 层，将调度决策接口暴露为 RL 环境的 action space。这是所有赛道中**对 srsRAN 源码侵入最深**的。

---

### 赛道 4：智能网络切片资源编排（Network Slicing Orchestration）

**痛点：** 5G 网络切片（eMBB / URLLC / mMTC）的静态资源分配无法适应动态流量变化，3GPP Rel-18 已将网络节能和负载均衡纳入标准。

**AI 解法：**
- 流量预测（LSTM / TCN）+ 优化求解器联合
- 预测各切片的未来流量需求 → 动态调整 PRB 分配比例
- 控制面决策周期在秒~分钟级，实时性要求低

**模型规模与 NPU 适配：**

| 指标 | 典型值 |
|------|--------|
| 参数量 | <1M |
| 推理周期 | 秒级 |
| 推理延迟 | <10ms（无实时约束） |
| **结论** | **两板均可，适合作为 Non-RT RIC rApp** |

**实验策略：** 在 srsRAN + Open5GS 上创建多个切片，注入模拟流量模式（如 Poisson + 突发），验证 AI 编排 vs 静态分配的 SLA 满足率。

---

### 赛道 5：AI 异常检测与网络节能（Anomaly Detection & Energy Saving）

**痛点：** 基站能耗占运营商 OPEX 的 20~30%。3GPP Rel-18 将网络节能列为三大 AI 用例之一。

**AI 解法：**
- **异常检测**：Autoencoder 学习正常 KPI 分布，重建误差超阈值即告警
- **网络节能**：基于流量预测的符号级关断决策（在无数据传输的 OFDM 符号上关闭 PA）
- 性能基准：异常检测 F1 > 0.95；节能可实现 15~30% 基站功耗降低

**模型规模与 NPU 适配：**

| 指标 | 异常检测 (Autoencoder) | 节能决策 (DQN) |
|------|:---------------------:|:-------------:|
| 参数量 | <50K | <100K |
| 推理延迟 | <1ms | <10ms |
| 实时约束 | 无（秒级巡检） | 秒级 |
| **结论** | **两板均可，最易出 Demo** | **两板均可** |

**为什么是 Phase 1 首选：** 不需要修改 srsRAN 源码，旁路数据采集 + 独立推理，风险最低。

---

### 赛道 6：神经接收机端到端优化（Neural Receiver / E2E Learning）

**痛点：** 传统接收机各模块（信道估计 → 均衡 → 解调 → 译码）独立优化，不是全局最优。

**AI 解法：**
- 将信道估计 + MIMO 检测 + 解调联合为端到端 NN（如 MDX 网络）
- 模型驱动：保留传统接收机结构，仅替换关键模块为可学习组件
- 数据驱动：完全端到端学习

**模型规模与 NPU 适配：**

| 指标 | 轻量 (SISO/20MHz) | 中等 (2×2 MIMO/50MHz) | 重型 (4×4 MIMO/100MHz) |
|------|:-----------------:|:--------------------:|:---------------------:|
| 推理计算量 | 0.5~1 GMACs/slot | 2~5 GMACs/slot | 10~20 GMACs/slot |
| 参数量 | 200K~500K | 500K~2M | 2M~10M |
| 推理延迟 (20TOPS) | <1ms ✅ | <3ms ✅ | <10ms ⚠️ |
| 推理延迟 (6TOPS) | <3ms ✅ | <10ms ⚠️ | <30ms ❌ |
| **结论** | **两板均可** | **仅昇腾** | **需 GPU** |

**关键论文：**
- Abdollahpour et al., "A Compute&Memory Efficient Model-Driven Neural 5G Receiver for Edge AI-assisted RAN" (arXiv:2508.12892, 2025)

**工程挑战：** 需将 NPU 推理引擎（CANN / RKNN）与 srsRAN PHY 层 C++ 代码进行 FFI 对接，实现 per-slot 的实时推理。这是**工程难度最高**但**学术价值最大**的赛道。

---

## 4.5 模型调优备忘录（以赛道 1 信道估计为例）

> 当训练效果不达标时，按以下五个维度系统排查和调优。**盲目扩大参数量往往适得其反**——信道估计的信息瓶颈在导频稀疏性（6%），而非模型容量。

### Step 0：先诊断问题类型

| 训练曲线特征 | 诊断 | 方向 |
|-------------|------|------|
| 训练 Loss 降不下去 | 欠拟合 (underfitting) | 加模型容量 / 检查数据 |
| 训练 Loss 降，验证 NMSE 差 | 过拟合 (overfitting) | 加数据 / 正则化 |
| 训练 Loss 和 验证 NMSE 都差 | 数据或结构问题 | 数据质量 / 输入表示 |
| 高 SNR 好，低 SNR 差 | 降噪能力不足 | 加正则 / 输入预处理 |
| 低 SNR 好，高 SNR 差 | 残差连接饱和 | 调整 residual scale |

### 维度 1：数据侧（最常被低估，影响最大）

| 调整 | 当前 | 建议 | 预期收益 |
|------|------|------|---------|
| 训练样本量 | 10K | 20K~30K | +1~2 dB NMSE |
| SNR 采样分布 | 均匀 [0,25]dB | Beta(0.5,1.5) 加权，低 SNR 更频繁 | 低 SNR 区 +1~3 dB |
| 信道模型多样性 | TDL-A/C/D | 加 TDL-B/E + 自定义多径 | 泛化性显著提升 |
| 多普勒范围 | 3~120 Hz | 扩展到 3~500 Hz (高速场景) | 高速场景改善 |
| DM-RS 配置随机化 | 固定 (2,11) 符号 | 单/双符号 DM-RS 随机 | 适配多种 3GPP 配置 |

### 维度 2：输入表示（信息瓶颈的根本解法，P0 优先级）

当前模型输入 94% 为零（非导频位填零），信息严重浪费。

| 输入策略 | 描述 | 输入通道数 | 预期收益 |
|----------|------|:----------:|---------|
| **当前：稀疏导频** | 非导频位填零 | 2ch | 基线 |
| **P0: LS 插值填充** | 用 LS+线性插值填充全部位置 | 2ch | +2~4 dB |
| **P1: 双通道输入** | [稀疏导频, LS插值] 拼接 | 4ch | +2~5 dB |
| **P2: 导频+LS+掩码** | [稀疏导频, LS插值, 导频掩码] | 5ch | +2~5 dB |

P0 方案的核心思想：**模型任务从"从零重建"变为"修正粗略估计"，残差连接的物理含义变为学习传统估计器的误差修正量。**

```python
# P0 实现: 输入用 LS 插值填充 (改 common.py ~20行)
H_ls_interp = ls_interpolate(Y, X, mask)     # 先做传统估计
pilot_input = channel_to_2ch(H_ls_interp)     # 用传统估计填充全部位置
```

### 维度 3：架构侧（不等同于"加参数"）

**核心洞察：** 参数量从 30K→200K 有意义，从 200K→1M 几乎无收益。瓶颈在导频密度而非模型容量。

| 优先级 | 调整 | 代码改动量 | 预期收益 | 风险 |
|:------:|------|:---------:|---------|:----:|
| **P0** | 加第 2 层 MHSA (2层 Transformer) | v3 ~15行 | +1~2 dB | 低 |
| P1 | ResBlock 在 MHSA 前后各加一层 | v3 ~20行 | +0.5~1 dB | 低 |
| P2 | d_model 64→128 | 改 1 个参数 | +0.5~1 dB | 参数量翻倍 |
| P3 | n_heads 4→8 | 改 1 个参数 | +0.2~0.5 dB | 几乎无 |
| P4 | UNet 跳跃连接 | 大改架构 | +1~2 dB | 工程量大 |

v3 当前架构短板：只有**单层 MHSA**，全局建模能力有限。建议升级为 2 层 Transformer Block：

```python
# 当前 (单层):
attn_out = self.mhsa(patches)
patches = patches + attn_out

# 改进 (2层 Transformer Block):
self.transformer = nn.ModuleList([
    TransformerBlock(d_model, n_heads) for _ in range(2)
])
for block in self.transformer:
    patches = block(patches)  # 每层: MHSA + FFN + LN + Skip
```

### 维度 4：训练策略

| 调整 | 当前 | 建议 | 为什么 |
|------|------|------|--------|
| 学习率 | 固定 5e-4 | 1e-3 warmup 5 epochs → 5e-4 | 浅层快收敛，深层精细调整 |
| Epoch 数 | 80 | 150~200 | 110K 参数需更多迭代 |
| Loss 函数 | 纯 MSE | MSE + 频域/时域平滑正则 | 信道在时频域连续，先验知识提升泛化 |
| 梯度裁剪 | 无 | clip_grad_norm_ = 1.0 | 防 MHSA 注意力分数爆炸 |
| LR 调度 | CosineAnnealing | CosineAnnealingWarmRestarts | 周期性重启，跳出局部最优 |

**频域正则化 Loss（推荐）：**

```python
def channel_estimation_loss(H_pred, H_true):
    mse = F.mse_loss(H_pred, H_true)
    freq_diff = H_pred[:, :, :, 1:] - H_pred[:, :, :, :-1]
    freq_smooth = torch.mean(freq_diff ** 2)
    time_diff = H_pred[:, :, 1:, :] - H_pred[:, :, :-1, :]
    time_smooth = torch.mean(time_diff ** 2)
    return mse + 0.01 * freq_smooth + 0.01 * time_smooth
```

物理含义：真实信道在时频域是连续平滑的（多径延迟/多普勒扩展有限），此先验帮助模型学到"合理"的信道结构，而非拟合噪声。

### 维度 5：任务重构（如果模型无论如何都不达标）

| 降级策略 | 输入 | 输出 | 难度 | 说明 |
|----------|------|------|:----:|------|
| **全信道重建** | 稀疏导频 (6%) | 完整 H | ★★★★ | 当前方案 |
| **信道插值** | LS 估计 (含噪声) | 去噪+插值的 H | **★★** | 最容易出成果 |
| **信道预测** | 历史 K slot 的 H | 未来 1 slot 的 H | ★★★ | 时序依赖 |
| **降带宽** | 25 RB (300 sc) | 完整 H | ★★ | 先验证方法论 |

**建议路径：** 如果全信道重建太难，先做"信道插值"（输入 LS 估计，输出去噪后的 H），再逐步升级到全信道重建。降带宽（52→25 RB）可快速验证方法论。

### 调优优先级总表

| 优先级 | 调整项 | 改动量 | 预期 NMSE 提升 | 风险 |
|:------:|--------|:------:|:--------------:|:----:|
| **P0** | 训练样本量 10K→20K | 改 1 个数字 | +1~2 dB | 无 |
| **P0** | 输入用 LS 插值填充 | 改 common.py ~20行 | +2~4 dB | 低 |
| P1 | 加第 2 层 MHSA | 改 v3 ~15行 | +1~2 dB | 低 |
| P1 | 频域+时域正则 Loss | 改 common.py ~10行 | +0.5~1 dB | 无 |
| P2 | 梯度裁剪 + LR warmup | 改 common.py ~5行 | 稳定性提升 | 无 |
| P2 | Epoch 80→150 | 改 1 个数字 | +0.5~1 dB | 过拟合风险 |
| P3 | SNR 加权采样 | 改 Dataset ~5行 | 低 SNR 区改善 | 高 SNR 可能略降 |
| P3 | d_model 64→128 | 改 1 个参数 | +0.5~1 dB | 参数量翻倍 |
| P4 | 降带宽先做通 (52→25 RB) | 改 config | 方法论验证 | 不直接解决原问题 |
| P5 | UNet 跳跃连接 | 大改架构 | +1~2 dB | 工程量大 |

---

## 五、双板协同架构

### 5.1 整体部署架构

```
┌─────────────────────────────────┐          ┌─────────────────────────────────┐
│       RK3588S (8核 / 16GB)      │          │     昇腾 340B1 (4核 / 24GB)     │
│                                 │          │                                 │
│  ┌───────────────────────────┐  │  ZMQ     │  ┌───────────────────────────┐  │
│  │  srsRAN gNB (CU+DU) 协议栈 │◄├─────────►│  │  srsUE (UE 模拟器)         │  │
│  │  - PHY / MAC / RLC / PDCP  │  │  /SCTP   │  │  - NR-UE 协议栈            │  │
│  │  - F1 接口 (CU-DU)        │  │          │  │                           │  │
│  └───────────────────────────┘  │          │  └───────────────────────────┘  │
│                                 │          │                                 │
│  AI 推理 co-processor:          │          │  AI 推理 co-processor:          │
│  ┌───────────────────────────┐  │          │  ┌───────────────────────────┐  │
│  │  NPU 6TOPS                 │  │          │  │  NPU 20TOPS                │  │
│  │  ├─ 波束预测 (LSTM)        │  │          │  │  ├─ 信道估计 (CNN/ResNet)  │  │
│  │  ├─ 切片流量预测 (TCN)     │  │          │  │  ├─ RL 调度器在线推理       │  │
│  │  └─ 异常检测 (Autoencoder) │  │          │  │  ├─ 异常检测 (Autoencoder) │  │
│  └───────────────────────────┘  │          │  │  └─ RL 训练 (offline)      │  │
│                                 │          │  └───────────────────────────┘  │
│  CPU 角色:                       │          │                                 │
│  ┌───────────────────────────┐  │          │  ┌───────────────────────────┐  │
│  │  gNB 协议栈 (多线程)       │  │          │  │  Open5GS 5GC (核心网)      │  │
│  │  - 8核充分并行              │  │          │  │  - AMF / SMF / UPF / UDM   │  │
│  │  - PHY+MAC 是主要负载      │  │          │  │  - 24GB 内存轻松全栈部署    │  │
│  └───────────────────────────┘  │          │  └───────────────────────────┘  │
└─────────────────────────────────┘          └─────────────────────────────────┘
```

### 5.2 资源分配策略

| 资源 | RK3588S | 昇腾 340B1 | 理由 |
|------|---------|-----------|------|
| srsRAN gNB | ✅ 主运行 | ❌ | 8核 CPU 是 gNB 多线程的刚需 |
| srsUE | ❌ | ✅ 主运行 | UE 资源消耗低，4核足够 |
| Open5GS 5GC | ❌ | ✅ 主运行 | 24GB 大内存适合核心网全栈 |
| RL 训练 | ❌ | ✅ | 24GB 内存容纳大 replay buffer |
| NPU 推理 (轻量) | ✅ | ✅ | 两板 NPU 各自独立推理 |
| NPU 推理 (重型) | ❌ | ✅ | 20TOPS 才能满足神经接收机延迟 |

### 5.3 网络拓扑

```
                    ┌──────────────┐
                    │  管理网络交换机  │
                    │  (千兆以太网)   │
                    └──┬────────┬──┘
                       │        │
              ┌────────┴──┐  ┌──┴────────┐
              │  RK3588S   │  │  昇腾340B1 │
              │ 10.x.x.1   │  │ 10.x.x.2  │
              └────────────┘  └───────────┘
                       │        │
                    ZMQ 数据通道 (TCP)
                    srsRAN IQ 采样流
                    + AI 推理 RPC
```

---

## 六、实验路线图

### Phase 0：打通基础链路

**目标：** RK3588S 跑 eNB，昇腾 340B1 跑 UE + EPC，ZMQ 模式 Ping 通

**步骤：**

1. 两块板卡分别安装 Ubuntu aarch64 + 基础依赖
2. 编译 srsRAN_4G（先验证 ARM 编译链路）
3. 编译 srsRAN_Project 5G（ZMQ 模式，需 `-DAUTO_DETECT_ISA=OFF`）
4. 双板分布式部署验证：RK3588S gNB ↔ 昇腾 UE + Open5GS EPC
5. 端到端连通性测试：Ping / iPerf

**关键命令：**

```bash
# 安装依赖（两板均执行）
sudo apt install -y cmake make gcc g++ pkg-config \
    libfftw3-dev libmbedtls-dev libsctp-dev \
    libyaml-cpp-dev libzmq3-dev

# 编译 srsRAN_4G
git clone https://github.com/srsran/srsRAN_4G.git
cd srsRAN_4G && mkdir build && cd build
cmake ../ && make -j$(nproc)

# 编译 srsRAN_Project 5G (ZMQ)
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project && mkdir build && cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON -DAUTO_DETECT_ISA=OFF
make -j$(nproc)
```

**验收标准：** UE 附着成功，Ping 延迟 <50ms（ZMQ 模式无实时约束）

---

### Phase 1：AI 异常检测 → 最快出 Demo

**目标：** 旁路采集 srsRAN KPI，NPU 推理实时异常检测

**步骤：**

1. 在 srsRAN 运行期间，周期性导出 KPI（RSRP / SNR / BLER / 吞吐量 / 队列深度）
2. 采集"正常"运行数据集（~1M 样本）
3. 训练轻量 Autoencoder（<50K params, 输入维度 = KPI 特征数）
4. 模型导出 ONNX → 转换为 CANN (昇腾) / RKNN (RK3588S) 格式
5. 部署到 NPU，实时检测异常 KPI → 触发告警
6. 注入异常（如人为丢包、干扰信号），验证检测 F1 > 0.90

**为什么先做这个：**
- 不需要修改 srsRAN 源码
- 旁路数据采集 + 独立推理，风险最低
- 可快速验证 "NPU 推理 + srsRAN" 的工程可行性

**产出物：** 可运行的 AI 异常检测 Demo + 技术报告

---

### Phase 2：信道预测 / 波束预测

**目标：** 在 srsRAN 中插入数据采集 hook，训练轻量预测模型，NPU 部署

**步骤：**

1. 在 srsRAN PHY 层插入 hook，导出 per-slot 信道估计矩阵 / RSRP 序列
2. 生成训练数据集（可用 QuaDRiGa / MATLAB 5G Toolbox 生成多径信道数据）
3. 训练 LSTM / TCN / CNN 预测模型
4. 部署到 NPU，对比预测精度 vs 传统插值方法
5. 端到端集成：预测结果反馈到 srsRAN 调度 / 波束管理模块

**关键指标：**
- 信道预测 NMSE vs MMSE baseline
- 波束预测 Top-1 / Top-3 准确率 vs 穷举搜索
- NPU 推理延迟 vs slot 时间约束

**产出物：** 一篇可投 IEEE 的 paper（AI channel/beam prediction on edge NPU）

---

### Phase 3：RL MAC 调度器

**目标：** 用强化学习替代传统 PF/RR 调度器，在混合流量下提升性能

**步骤：**

1. 修改 srsRAN MAC 层，将调度决策接口暴露为 RL 环境（OpenAI Gym 兼容）
2. 定义状态空间（队列长度 / CQI / 历史吞吐量 / 业务类型）
3. 定义动作空间（用户选择 + MCS 选择 + PRB 分配）
4. 在昇腾 340B1 上训练 PPO / DQN 调度器（24GB 内存足够大 replay buffer）
5. Actor 网络导出 → NPU 部署 → 替换 srsRAN 默认调度器
6. 对比实验：RL vs PF vs RR 在 URLLC+eMBB 混合流量下的延迟 / 吞吐量 / 公平性

**工程挑战：**
- srsRAN MAC 层源码侵入最深
- RL 训练收敛稳定性（需要 careful reward shaping）
- 在线推理与协议栈的实时交互

**产出物：** 一篇可投 ACM/IEEE 的 paper + 开源 RL-srsRAN 代码

---

### Phase 4：神经接收机端到端

**目标：** 在 NPU 上部署 5G NR 神经接收机，替代传统 PHY 接收链路

**步骤：**

1. 针对 5G NR PUSCH/PDSCH 标准，设计并训练模型驱动的神经接收机
2. 解决 NPU 推理引擎与 srsRAN PHY 层 C++ 代码的实时 FFI 交互
3. 先在 SISO / 20MHz 场景验证（两板 NPU 均可）
4. 扩展到 MIMO / 更大带宽（仅昇腾 20TOPS）
5. 端到端 BLER / 吞吐量对比

**学术价值：** "Edge NPU + 开源 srsRAN + 神经接收机"的组合目前鲜有工作，是最有学术影响力的赛道。

**产出物：** 顶会 paper（如 IEEE JSAC / TWC）+ 开源 neural-receiver-on-edge-NPU 工具链

---

## 七、各赛道综合评估

| 赛道 | 学术价值 | 工程难度 | NPU 需求 | 产出周期 | 推荐优先级 |
|------|:-------:|:-------:|:--------:|:-------:|:---------:|
| 1. 信道估计/预测 | ★★★★ | ★★★ | 6~20 TOPS | 中 | P1 |
| 2. 波束预测 | ★★★ | ★★ | 6 TOPS | 短 | P2 |
| 3. RL MAC 调度器 | ★★★★ | ★★★★★ | 6~20 TOPS | 长 | P3 |
| 4. 切片编排 | ★★★ | ★★ | 6 TOPS | 中 | P3 |
| 5. 异常检测/节能 | ★★ | ★ | 6~20 TOPS | 短 | **P0** |
| 6. 神经接收机 | ★★★★★ | ★★★★ | 20 TOPS | 长 | P4 |

---

## 八、关键风险与应对

| 风险 | 概率 | 影响 | 应对策略 |
|------|:----:|:----:|---------|
| 昇腾 340B1 Ubuntu / 工具链兼容性问题 | 中 | 高 | 先用 srsRAN_4G 验证编译链路；备选方案：纯 RK3588S 单板 |
| 4核 CPU 跑 5G gNB 启动失败 | 中 | 中 | 降低带宽/PRB 配置；gNB 部署在 RK3588S (8核) |
| NPU 推理延迟不满足 slot 级实时约束 | 低 | 高 | 量化为 INT8；模型剪枝；降低模型复杂度 |
| CANN / RKNN SDK 对自定义算子支持不足 | 中 | 中 | 退回到 CPU 推理（NEON SIMD 加速）；或使用 ONNX Runtime |
| RL 训练不收敛 | 中 | 中 | 从监督学习 warm-start；simplified reward；课程学习 |
| srsRAN 源码修改引入协议栈 Bug | 中 | 高 | 使用 Git 管理 patch；最小化侵入；先旁路后集成 |

---

## 九、参考资源

### 开源项目

| 项目 | 地址 | 用途 |
|------|------|------|
| srsRAN_Project (5G) | https://github.com/srsran/srsRAN_Project | 5G gNB / UE (ZMQ 模式) |
| srsRAN_4G | https://github.com/srsran/srsRAN_4G | 4G eNB / UE / EPC |
| Open5GS | https://github.com/open5gs/open5gs | 开源 5G 核心网 |
| build_srsran_5g_zmq | https://github.com/s5uishida/build_srsran_5g_zmq | ZMQ 模式部署参考 |
| QuaDRiGa | https://github.com/fraunhoferhhi/QuaDRiGa | 信道模拟器（生成训练数据） |

### 关键论文

| 论文 | 核心贡献 |
|------|---------|
| Abdollahpour et al., "A Compute&Memory Efficient Model-Driven Neural 5G Receiver for Edge AI-assisted RAN" (arXiv:2508.12892, 2025) | FLOPs 减少 66×，参数减少 396× 的轻量神经接收机 |
| Taksande et al., "AI/ML in 3GPP 5G Advanced - Services and Architecture" (arXiv:2512.03728, 2025) | 3GPP Rel-18/19 AI/ML 标准全景 |
| NVIDIA + SoftBank, "AI-RAN Goes Live" (2024.11) | 首个 AI-RAN 外场试验 |

### 标准

| 文档 | 内容 |
|------|------|
| 3GPP TR 37.817 | Rel-18 AI/ML for NR 研究报告 |
| 3GPP TS 38.821 | AI/ML for NR 空口规范 |
| O-RAN WG3 | Near-RT RIC 接口规范 |
| O-RAN WG6 | Cloud Architecture & Deployment |
| 3GPP SA5 AI/ML Management (2025.07 WS) | ML 模型生命周期管理框架 |
