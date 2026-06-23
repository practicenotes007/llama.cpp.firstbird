# llama-cpp-arm 工作流看板

**最后更新**：2026-06-08（策略修正）
**内部工作流数**：2 条并行（模型对比测试 / 内容输出）

---

## 双线并行（已修正）

| # | 工作流 | 状态 | 上次产出 | 下一步 |
|---|--------|------|---------|--------|
| 1 | **模型对比测试** | 策略修正 | 7B Q4_0 基线 + SDOT 无效确认 | Qwen2.5-3B Q4_K_M 实测 |
| 2 | **内容输出** | 第1/5篇 | S2.1 发布 | 写 S2.2（bandwidth bound 验证） |

## 策略修正说明

原优化路线基于"compute + bandwidth 双重优化"假设，把 SDOT 列为 P0。
实测确认瓶颈是 **纯 bandwidth bound**，SDOT 无效。策略修正为：

```
原路线（错误）：SDOT → 预取 → 流水线 → 调度 → 1.5→3.0 tok/s
修正路线（正确）：换小模型 → 激进量化 → 1.5→10+ tok/s（3B 模型实测预期）
```

## 新的优化 Roadmap

| Phase | 内容 | 预期效果 | 状态 |
|-------|------|---------|------|
| Phase 1 | Qwen2.5-3B Q4_K_M 实测 | 预期 8-15 tok/s | ⬜ 待执行 |
| Phase 2 | DDR 实测带宽校准（STREAM） | 修正理论天花板数值 | ⬜ |
| Phase 3 | Q2_K / IQ3 量化对比 | 精度 vs 速度权衡 | ⬜ |
| Phase 4 | 文章 S2.2：bandwidth bound 工程验证 | — | ⬜ |

## 阻塞项

| 阻塞 | 解决方案 |
|------|---------|
| Qwen2.5-3B 模型未下载量化 | 从 HuggingFace 拉 GGUF |

## 技术资产位置

- 代码：`adhoc_jobs/llama.cpp.firstbird/`
- 优化笔记：`OPTIMIZATION_NOTE_RK3588_DDR_BOTTLENECK.md`（分析仍有效，SDOT 优先级需修正）
- 文章 S2.1：`content/rk3588-edge-inference-cross-compile-bandwidth.md`

---

*详细状态见同目录 `STATUS.md`*
