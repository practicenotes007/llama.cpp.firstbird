# llama-cpp-arm 项目状态

**最后更新**：2026-06-08（实测结论修正）
**当前阶段**：瓶颈确认 — bandwidth bound，compute 优化无效
**优先级**：P1

## 关键实测结论 ⚠️

> **RK3588S 上 7B Q4_0 推理瓶颈确认为 DDR4 带宽受限（bandwidth bound），非计算受限。**
> SDOT 指令替换属于 compute 侧优化，无法提升 token 吞吐量——CPU 在 decode 阶段一直空闲等数据。
> 
> 理论天花板 ~6-7 tok/s，利用率仅 17%。正确方向是 **减少 per-token 数据搬运量**，而非加速计算。

## 当前状态
- Orange Pi 5 (RK3588S) 已到手，Armbian 运行正常 ✅
- llama.cpp 交叉编译成功 (NEON enabled) ✅
- 7B Q4_0 基准数据采集完成：1.5 tok/s ✅
- DDR 带宽瓶颈定量分析完成（431行报告）✅
- **SDOT 已实测验证：无效。瓶颈确认 bandwidth bound。** ✅
- 论文级文章 S2.1 中英双语发布 ✅

## 下一步（策略已修正）
1. ~~实施 SDOT 指令替换~~ → **已放弃**（bandwidth bound 下无效）
2. ~~NEON 内核软件预取~~ → **降级**（边际收益，非主要方向）
3. **换小模型实测**：Qwen2.5-3B Q4_K_M（~2.2GB，理论天花板 35/2.2≈16 tok/s）
4. **DDR 实测带宽校准**（STREAM benchmark — 确认 35GB/s 理论值是否属实）
5. **更激进量化对比**：Q4_K_M vs Q2_K vs IQ3，精度 vs 速度权衡
6. 写 S2.2：修正版文章 — "为什么 SDOT 不行：bandwidth bound 的工程验证"

## 阻塞项
- 散热方案需要验证（长时间跑可能降频——但对小模型影响较小）
- DDR 实测带宽未校准

## 技术资产
- 代码：`adhoc_jobs/llama.cpp.firstbird/`
- 优化笔记：`OPTIMIZATION_NOTE_RK3588_DDR_BOTTLENECK.md`（431行，分析仍有效，但 SDOT 优先级需修正）
- 部署 Skill：`rules/skills/workflow_llamacpp_arm_deployment.md`
