# llama.cpp

Inference of [Facebook's LLaMA](https://github.com/facebookresearch/llama) model in pure C/C++

**Hot topics**

- Running on Windows: https://github.com/ggerganov/llama.cpp/issues/22

## Description

The main goal is to run the model using 4-bit quantization on a MacBook.

- Plain C/C++ implementation without dependencies
- Apple silicon first-class citizen - optimized via Arm Neon and Accelerate framework
- AVX2 support for x86 architectures
- Mixed F16 / F32 precision
- 4-bit quantization support
- Runs on the CPU

This was hacked in an evening - I have no idea if it works correctly.
Please do not make conclusions about the models based on the results from this implementation.
For all I know, it can be completely wrong. This project is for educational purposes and is not going to be maintained properly.
New features will probably be added mostly through community contributions, if any.

---

Here is a typical run using LLaMA-7B:

```java
make -j && ./main -m ./models/7B/ggml-model-q4_0.bin -p "Building a website can be done in 10 simple steps:" -t 8 -n 512
I llama.cpp build info:
I UNAME_S:  Darwin
I UNAME_P:  arm
I UNAME_M:  arm64
I CFLAGS:   -I.              -O3 -DNDEBUG -std=c11   -fPIC -pthread -DGGML_USE_ACCELERATE
I CXXFLAGS: -I. -I./examples -O3 -DNDEBUG -std=c++11 -fPIC -pthread
I LDFLAGS:   -framework Accelerate
I CC:       Apple clang version 14.0.0 (clang-1400.0.29.202)
I CXX:      Apple clang version 14.0.0 (clang-1400.0.29.202)

make: Nothing to be done for `default'.
main: seed = 1678486056
llama_model_load: loading model from './models/7B/ggml-model-q4_0.bin' - please wait ...
llama_model_load: n_vocab = 32000
llama_model_load: n_ctx   = 512
llama_model_load: n_embd  = 4096
llama_model_load: n_mult  = 256
llama_model_load: n_head  = 32
llama_model_load: n_layer = 32
llama_model_load: n_rot   = 128
llama_model_load: f16     = 2
llama_model_load: n_ff    = 11008
llama_model_load: ggml ctx size = 4529.34 MB
llama_model_load: memory_size =   512.00 MB, n_mem = 16384
llama_model_load: .................................... done
llama_model_load: model size =  4017.27 MB / num tensors = 291

main: prompt: 'Building a website can be done in 10 simple steps:'
main: number of tokens in prompt = 15
     1 -> ''
  8893 -> 'Build'
   292 -> 'ing'
   263 -> ' a'
  4700 -> ' website'
   508 -> ' can'
   367 -> ' be'
  2309 -> ' done'
   297 -> ' in'
 29871 -> ' '
 29896 -> '1'
 29900 -> '0'
  2560 -> ' simple'
  6576 -> ' steps'
 29901 -> ':'

sampling parameters: temp = 0.800000, top_k = 40, top_p = 0.950000


Building a website can be done in 10 simple steps:
1) Select a domain name and web hosting plan
2) Complete a sitemap
3) List your products
4) Write product descriptions
5) Create a user account
6) Build the template
7) Start building the website
8) Advertise the website
9) Provide email support
10) Submit the website to search engines
A website is a collection of web pages that are formatted with HTML. HTML is the code that defines what the website looks like and how it behaves.
The HTML code is formatted into a template or a format. Once this is done, it is displayed on the user's browser.
The web pages are stored in a web server. The web server is also called a host. When the website is accessed, it is retrieved from the server and displayed on the user's computer.
A website is known as a website when it is hosted. This means that it is displayed on a host. The host is usually a web server.
A website can be displayed on different browsers. The browsers are basically the software that renders the website on the user's screen.
A website can also be viewed on different devices such as desktops, tablets and smartphones.
Hence, to have a website displayed on a browser, the website must be hosted.
A domain name is an address of a website. It is the name of the website.
The website is known as a website when it is hosted. This means that it is displayed on a host. The host is usually a web server.
A website can be displayed on different browsers. The browsers are basically the software that renders the website on the user’s screen.
A website can also be viewed on different devices such as desktops, tablets and smartphones. Hence, to have a website displayed on a browser, the website must be hosted.
A domain name is an address of a website. It is the name of the website.
A website is an address of a website. It is a collection of web pages that are formatted with HTML. HTML is the code that defines what the website looks like and how it behaves.
The HTML code is formatted into a template or a format. Once this is done, it is displayed on the user’s browser.
A website is known as a website when it is hosted

main: mem per token = 14434244 bytes
main:     load time =  1332.48 ms
main:   sample time =  1081.40 ms
main:  predict time = 31378.77 ms / 61.41 ms per token
main:    total time = 34036.74 ms
```

And here is another demo of running both LLaMA-7B and [whisper.cpp](https://github.com/ggerganov/whisper.cpp) on a single M1 Pro MacBook:

https://user-images.githubusercontent.com/1991296/224442907-7693d4be-acaa-4e01-8b4f-add84093ffff.mp4

## Usage

Here are the step for the LLaMA-7B model:

```bash
# build this repo
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make

# obtain the original LLaMA model weights and place them in ./models
ls ./models
65B 30B 13B 7B tokenizer_checklist.chk tokenizer.model

# install Python dependencies
python3 -m pip install torch numpy sentencepiece

# convert the 7B model to ggml FP16 format
python3 convert-pth-to-ggml.py models/7B/ 1

# quantize the model to 4-bits
./quantize ./models/7B/ggml-model-f16.bin ./models/7B/ggml-model-q4_0.bin 2

# run the inference
./main -m ./models/7B/ggml-model-q4_0.bin -t 8 -n 128
```

For the bigger models, there are a few extra quantization steps. For example, for LLaMA-13B, converting to FP16 format
will create 2 ggml files, instead of one:

```bash
ggml-model-f16.bin
ggml-model-f16.bin.1
```

You need to quantize each of them separately like this:

```bash
./quantize ./models/13B/ggml-model-f16.bin   ./models/13B/ggml-model-q4_0.bin 2
./quantize ./models/13B/ggml-model-f16.bin.1 ./models/13B/ggml-model-q4_0.bin.1 2
```

Everything else is the same. Simply run:

```bash
./main -m ./models/13B/ggml-model-q4_0.bin -t 8 -n 128
```

The number of files generated for each model is as follows:

```
7B  -> 1 file
13B -> 2 files
30B -> 4 files
65B -> 8 files
```

When running the larger models, make sure you have enough disk space to store all the intermediate files.

## Limitations

- Not sure if my tokenizer is correct. There are a few places where we might have a mistake:
  - https://github.com/ggerganov/llama.cpp/blob/26c084662903ddaca19bef982831bfb0856e8257/convert-pth-to-ggml.py#L79-L87
  - https://github.com/ggerganov/llama.cpp/blob/26c084662903ddaca19bef982831bfb0856e8257/utils.h#L65-L69
  In general, it seems to work, but I think it fails for unicode character support. Hopefully, someone can help with that
- I don't know yet how much the quantization affects the quality of the generated text
- Probably the token sampling can be improved
- The Accelerate framework is actually currently unused since I found that for tensor shapes typical for the Decoder,
  there is no benefit compared to the ARM_NEON intrinsics implementation. Of course, it's possible that I simlpy don't
  know how to utilize it properly. But in any case, you can even disable it with `LLAMA_NO_ACCELERATE=1 make` and the
  performance will be the same, since no BLAS calls are invoked by the current implementation

---

# firstbird 分支 —— RK3588 优化版

本项目是 ggerganov/llama.cpp 的早期 fork，专注于 **RK3588 (Orange Pi 5) 开发板上的 CPU-only 推理优化**。

## 场景

- 硬件: RK3588, 4x Cortex-A76 + 4x Cortex-A55, LPDDR4/4x
- 模型: 7B Q4_0 (约 3.9 GB 权重)
- 瓶颈: DDR4 带宽约 35 GB/s，decode 阶段受限严重
- 当前实测: ~1.5 tok/s，理论上限: 6-7 tok/s

详细瓶颈分析和优化方案见 `OPTIMIZATION_NOTE_RK3588_DDR_BOTTLENECK.md`。

## 基准测试框架

项目自带完整的自动化测试和基准框架 (`bench/`)，覆盖 **正确性 -> 性能基准 -> 系统指标采集 -> 对比 -> 可视化** 全流程。

### 快速开始

```bash
# 编译
make

# 正确性测试（修改内核后必须跑）
make test

# 推理基准
make bench              # 默认: 4 线程 + 64 token
make bench-ddr          # DDR 带宽校准 (STREAM + mbw)

# 对比最近结果
make bench-compare      # 默认对比最近 2 次
make bench-compare LAST=5

# 可视化
make bench-plot         # 生成系统指标图 + 对比图 + 趋势图
```

### 完整流水线（推荐）

```bash
./bench/run_all.sh -l "本次优化标签" -t 4 -n 64 -P
```

自动执行: 编译 -> 正确性测试 -> 推理基准 + perf stat -> 系统指标采集 -> DDR 校准 -> 多轮对比 -> 绘图。

所有输出写入 `bench/results/<标签>_<时间戳>/`，包括:

| 文件 | 内容 |
|------|------|
| `timing.csv` | 关键时序 (load/predict/per-token/tps) |
| `config.csv` | 测试配置 |
| `sysinfo_*.txt` | 系统快照 (CPU/内存/频率/温度) |
| `cpufreq_*.log` | CPU 频率时序 (CSV) |
| `thermal_*.log` | 温度时序 (CSV) |
| `perf_stat.log` | perf 计数器 (如启用 -P) |
| `llama_output.txt` | llama.cpp 原始输出 |
| `correctness.log` | 内核正确性测试报告 (run_all.sh) |

### 工具详解

| 命令 | 功能 |
|------|------|
| `make test` | 编译并运行 `test_kernels`，覆盖 FP16/量化/dot/mad/GELU/SiLU |
| `make bench` | 运行 `run_bench.sh`，自动采集系统指标 |
| `make bench-ddr` | 运行 `run_stream.sh`，下载 STREAM 并测带宽 |
| `make bench-compare` | 运行 `compare.sh`，并排对比多轮测试 |
| `make bench-plot` | 运行 `plot_results.py`，生成 PNG 图表 |
| `make bench-all` | 上述全流程 |
```

### 对比多个优化版本

```bash
# 假设跑了三轮测试
./bench/run_bench.sh -l baseline -t 4 -n 64
# ... 修改代码 ...
./bench/run_bench.sh -l sdot_v1  -t 4 -n 64
# ... 修改代码 ...
./bench/run_bench.sh -l prefetch_v2 -t 4 -n 64

# 对比
./bench/compare.sh --last 3

# 输出示例:
# --- 推理性能 ---
# 指标                     baseline            sdot_v1             prefetch_v2
# 加载时间 (ms)              1332.48             1332.48             1332.48
# 推理时间 (ms)             31378.77            20000.00            15000.00
# 单 token 延迟 (ms)          61.41               39.06               29.30
# 吞吐量 (tok/s)              16.28               25.60               34.13
```

### 输出示例 (系统指标图)

```bash
python3 bench/plot_results.py --last 1
```

在结果目录生成 `sys_plots.png`，包含 CPU 频率和温度随时间变化曲线。

## 正确性测试

`test_kernels` 是包含 ggml.c 全部内核的独立测试程序:

| 测试组 | 覆盖范围 |
|--------|----------|
| FP16 <-> FP32 | 全部 65536 个 bit pattern 对比 IEEE 754 参考 |
| Q4_0 量化/反量化 | 6 种数据分布，round-trip 误差 < 0.5 RMSE |
| vec_dot_q4_0 | 多种长度，SIMD vs 标量参考，误差 < 2% |
| vec_mad_q4_0 | 随机数据，max_err < 1e-4 |
| GELU 查表 | 10000 点抽样，相对误差 < 5% |
| SiLU 查表 | 10000 点抽样，相对误差 < 5% |

```bash
make test          # 简洁输出
./test_kernels --verbose  # 详细输出
./test_kernels --dot-only # 只测 vec_dot
./test_kernels --bench    # 包含微基准
```

## DDR 带宽校准

在板子上首次运行时校准实际可用带宽:

```bash
./bench/run_stream.sh -m
```

自动下载编译 STREAM，输出单线程和多线程 Triad 带宽，并估算各模型的理论上限。

## 优化笔记

`OPTIMIZATION_NOTE_RK3588_DDR_BOTTLENECK.md` 包含:

- 瓶颈定量分析 (搬运量/缓存命中率/代码根因)
- 6 个优化方案及优先级
- 推荐实施路径 (Phase 1/2/3)
- DDR 实测带宽校准方法

