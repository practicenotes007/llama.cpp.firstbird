# TODO - llama.cpp.firstbird 工程优化备忘

## P0: 测试体系搭建

在 AI Coding 辅助开发的前提下，单元测试是正确性的唯一保障。当前项目无任何 test suite。

- [ ] 搭建测试框架 (推荐 criterion 或 Unity —— 纯 C 项目)
- [ ] FP16 ↔ FP32 转换正确性测试
  - 遍历全部 65536 个 FP16 bit pattern，验证 `ggml_compute_fp16_to_fp32` / `ggml_compute_fp32_to_fp16` 与 IEEE 754 参考实现一致
  - 验证 `table_f32_f16` 查表结果与逐次计算结果一致
- [ ] GELU / SiLU / Exp 查表正确性测试
  - 对 `table_gelu_f16` / `table_silu_f16` / `table_exp_f16` 抽样验证
  - 与数学库 `tanh()` / `exp()` 参考值对比误差
- [ ] 量化 / 反量化正确性测试
  - Q4_0 / Q4_1 / Q5_0 / Q5_1 等各量化格式的 round-trip 测试 (FP32 → 量化 → 反量化 → FP32，误差在预期范围内)
  - 向量点积 (dot product) 量化版本 vs FP32 参考值的误差测试
- [ ] SIMD 辅助函数正确性测试
  - `bytesFromNibbles` (AVX2): 验证 4-bit 解包到 8-bit 的结果正确
  - ARM NEON 对应的 nibble 解包逻辑正确性
  - 标量回退路径正确性
- [ ] 多线程同步正确性测试
  - cache line 对齐结构体无 false sharing 验证
  - 原子操作正确性 (work stealing, barrier 等)

## P1: SIMD 优化

### 1.1 AVX2 bytesFromNibbles 优化

当前实现 (ggml.c L367-382) 使用 `andnot + slli` 位操作技巧，可替换为更清晰的 `extract + unpack` 方案:

```c
inline __m256i bytesFromNibbles(const uint8_t* rsi)
{
    __m128i tmp = _mm_loadu_si128((const __m128i*)rsi);
    __m128i lo = _mm_and_si128(tmp, _mm_set1_epi8(0x0F));
    __m128i hi = _mm_and_si128(_mm_srli_epi16(tmp, 4), _mm_set1_epi8(0x0F));
    __m256i result = _mm256_set_m128i(
        _mm_unpackhi_epi8(lo, hi),
        _mm_unpacklo_epi8(lo, hi)
    );
    return result;
}
```

- 预期收益: 延迟降低 1-2 周期 (前 5 条为 128 位操作)，逻辑更清晰
- 前提: 先完成 P0 中 bytesFromNibbles 的单元测试，确保优化后结果不变

### 1.2 ARM NEON nibble 解包封装

当前 NEON 路径的 nibble 解包逻辑散落在量化点积循环体内 (ggml.c L1326-L1333)，应抽取为辅助函数:

- [ ] 封装 `neon_bytesFromNibbles()` 辅助函数
- [ ] 封装 `neon_nibble_unpack_low()` / `neon_nibble_unpack_high()` (低/高 nibble 分别提取)
- [ ] 统一 NEON 路径的代码风格，与 AVX2 路径对齐

### 1.3 WASM SIMD nibble 解包补充

当前无 WASM SIMD 专用实现。WASM SIMD 有对应指令:

- `v128_and` → 对应 `_mm_and_si128`
- `i16x8_shr` → 对应 `_mm_srli_epi16`
- `i8x16_shuffle` → 对应 unpack 操作

- [ ] 实现 `wasm_bytesFromNibbles()`
- [ ] 在 WASM 编译路径中替换标量回退

### 1.4 AVX2 quantize_row_q4_0 优化

当前实现 (ggml.c L458-523) 存在可合并的冗余指令:

**优化 A: 合并 round + offset (确定可行)**
- 当前路径: `round_ps` → `cvtps_epi32` → `add_epi8(i, 8)` (3 条)
- 优化路径: `add_ps(v, 8.5f)` → `cvttps_epi32` (2 条, truncate 代替 round)
- 原理: 在浮点域完成偏移, 利用 `+8.5f` 的舍入偏差等效于 round+offset
- 与 NEON 路径思路对齐 (NEON 用的就是 `+8.5f` + `vcvtq_s32_f32`)
- 预期收益: 省 2 条指令

**优化 B: FMA 合并乘法与偏移 (确定可行)**
- 当前路径: `mul_ps(v, id)` + `add_ps(v, 8.5f)` (2 条, 有数据依赖)
- 优化路径: `fmadd_ps(v, id, 8.5f)` (1 条, 无中间截断, 精度更高)
- 预期收益: 省 1 条指令, 延迟从 8 周期降至 4 周期 (mul 4 + add 4 → fma 4)

**优化 C: packs + permute 替代方案 (待 benchmark)**
- 当前路径: `packs_epi32` → `packs_epi16` → `permutevar8x32` (permute 延迟 3 周期)
- 替代思路: 在 int32 阶段直接做 nibble 打包, 跳过 int16/int8 压缩
- 不确定是否更快, 需实测
- 前提: 先完成 P0 中量化 round-trip 单元测试

### 1.5 Q4_1 vec_dot_q4_1 SIMD 实现 (高优先级)

当前 `ggml_vec_dot_q4_1` (ggml.c L1567-1626) **只有标量版本**, 缺少 AVX2/NEON 实现。
对比 Q4_0 的 vec_dot 已有完整的 AVX2 + NEON 版本。

Q4_1 的 SoA 布局 (min/delta/packed 分区连续存储) 正是为 SIMD 化预留的, 但尚未兑现:

- [ ] 实现 AVX2 版本 `ggml_vec_dot_q4_1`
  - SoA 布局允许 `_mm256_load_ps(pm0+i)` 一次加载 8 个 block 的 min
  - 外层循环可按 8 个 block 为一批处理, 减少元数据加载次数
  - 内层循环 SIMD 化 nibble 解包 + 反量化 + 乘加 (参考 Q4_0 的 AVX2 路径)
- [ ] 实现 ARM NEON 版本
  - 同理利用 SoA 连续加载 `vld1q_f32(pm0+i)`
- [ ] 性能基准对比: 标量 vs SIMD, 预期加速 4-8 倍

### 1.6 Q4_0/Q4_1 内存布局统一性评估

当前 Q4_0 用 AoS、Q4_1 用 SoA, 风格不统一:

- Q4_0 AoS: `[delta][packed]` 逐 block 交织 (ggml.c L401-)
- Q4_1 SoA: `[min0,min1,...][d0,d1,...][packed0,packed1,...]` 分区连续 (ggml.c L604-)
- 原因: Q4_0 先写, 标量版本下 AoS 最直觉; Q4_1 后写, 已意识到需要 SIMD
- Q4_0 的 1 个 float (delta) 用 broadcast 即可, AoS 够用, 改 SoA 收益不大
- [ ] 评估是否将 Q4_0 也改为 SoA, 统一两种格式的访问模式
- [ ] 如果改, 需同步修改 quantize/vec_dot/dequant 全链路

### 1.7 标量回退路径优化

当前无 SIMD 时的纯 C 标量路径是兜底方案，速度最慢但必须正确:

- [ ] 确保标量路径有单元测试覆盖
- [ ] 考虑编译器 auto-vectorization 提示 (`#pragma GCC ivdep` 等)

## P2: 代码质量

- [ ] ggml.c 添加中文注释 (按需，已在逐步进行)
- [ ] 统一 SIMD 路径的辅助函数命名规范 (当前 AVX2 有 `bytesFromNibbles`，NEON 没有)
- [ ] 检查所有 `#if defined(...)` 条件编译路径，确保无遗漏平台
- [ ] Makefile 添加 test target

## P3: 领域落地探索

- [ ] 通信基站场景的端侧推理可行性分析
  - 功耗约束、延迟要求、硬件平台 (ARM/FPGA/DSP)
- [ ] ggml 在 ARM 嵌入式平台 (如 Cortex-A53/A72) 上的性能基准测试
- [ ] 量化模型 (Q4_0/Q5_0) 在基站场景的精度-性能 trade-off 评估

## 变更记录

| 日期 | 内容 |
|---|---|
| 2026-05-12 | 初始创建，基于 ggml.c 源码阅读讨论整理 |
| 2026-05-12 | 新增 P1.4: AVX2 quantize_row_q4_0 优化 (round+offset合并、FMA、packs+permute替代方案) |
| 2026-05-12 | 新增 P1.5: Q4_1 vec_dot SIMD 实现 (当前只有标量版); P1.6: Q4_0/Q4_1 内存布局统一性评估 |
