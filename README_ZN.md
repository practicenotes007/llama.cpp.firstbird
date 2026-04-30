# llama.cpp

纯 C/C++ 实现 [Facebook LLaMA](https://github.com/facebookresearch/llama) 模型推理

**热门话题**

- Windows 运行指南：https://github.com/ggerganov/llama.cpp/issues/22

## 简介

主要目标是在 MacBook 上使用 4 位量化运行模型。

- 纯 C/C++ 实现，无外部依赖
- 苹果芯片优先支持 - 通过 Arm Neon 和 Accelerate 框架优化
- 支持 AVX2 x86 架构
- 混合 F16 / F32 精度
- 支持 4 位量化
- 在 CPU 上运行

这是一个晚上赶出来的项目 - 我不知道它是否正确运行。
请不要根据这个实现的输出来评价模型。
据我所知，它可能完全错误。本项目仅供教育目的，不会得到适当维护。
新功能可能会通过社区贡献来添加（如果有的话）。

---

以下是使用 LLaMA-7B 的典型运行示例：

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
A website can be displayed on different browsers. The browsers are basically the software that renders the website on the user's screen.
A website can also be viewed on different devices such as desktops, tablets and smartphones. Hence, to have a website displayed on a browser, the website must be hosted.
A domain name is an address of a website. It is the name of the website.
A website is an address of a website. It is a collection of web pages that are formatted with HTML. HTML is the code that defines what the website looks like and how it behaves.
The HTML code is formatted into a template or a format. Once this is done, it is displayed on the user's browser.
A website is known as a website when it is hosted

main: mem per token = 14434244 bytes
main:     load time =  1332.48 ms
main:   sample time =  1081.40 ms
main:  predict time = 31378.77 ms / 61.41 ms per token
main:    total time = 34036.74 ms
```

以下是另一个在单个 M1 Pro MacBook 上同时运行 LLaMA-7B 和 [whisper.cpp](https://github.com/ggerganov/whisper.cpp) 的演示：

https://user-images.githubusercontent.com/1991296/224442907-7693d4be-acaa-4e01-8b4f-add84093ffff.mp4

## 使用方法

以下是 LLaMA-7B 模型的步骤：

```bash
# 构建此仓库
git clone https://github.com/ggerganov/llama.cpp
cd llama.cpp
make

# 获取原始 LLaMA 模型权重并放入 ./models
ls ./models
65B 30B 13B 7B tokenizer_checklist.chk tokenizer.model

# 安装 Python 依赖
python3 -m pip install torch numpy sentencepiece

# 将 7B 模型转换为 ggml FP16 格式
python3 convert-pth-to-ggml.py models/7B/ 1

# 将模型量化为 4 位
./quantize ./models/7B/ggml-model-f16.bin ./models/7B/ggml-model-q4_0.bin 2

# 运行推理
./main -m ./models/7B/ggml-model-q4_0.bin -t 8 -n 128
```

对于更大的模型，需要额外的量化步骤。例如，对于 LLaMA-13B，转换为 FP16 格式会创建 2 个 ggml 文件，而不是一个：

```bash
ggml-model-f16.bin
ggml-model-f16.bin.1
```

需要分别对每个文件进行量化：

```bash
./quantize ./models/13B/ggml-model-f16.bin   ./models/13B/ggml-model-q4_0.bin 2
./quantize ./models/13B/ggml-model-f16.bin.1 ./models/13B/ggml-model-q4_0.bin.1 2
```

其他步骤相同。直接运行：

```bash
./main -m ./models/13B/ggml-model-q4_0.bin -t 8 -n 128
```

每个模型生成的文件数量如下：

```
7B  -> 1 个文件
13B -> 2 个文件
30B -> 4 个文件
65B -> 8 个文件
```

运行更大的模型时，请确保有足够的磁盘空间来存储所有中间文件。

## 局限性

- 不确定我的分词器是否正确。可能有几个地方存在错误：
  - https://github.com/ggerganov/llama.cpp/blob/26c084662903ddaca19bef982831bfb0856e8257/convert-pth-to-ggml.py#L79-L87
  - https://github.com/ggerganov/llama.cpp/blob/26c084662903ddaca19bef982831bfb0856e8257/utils.h#L65-L69
  一般来说，它似乎可以工作，但我认为它在 Unicode 字符支持方面可能失败。希望有人可以帮助解决这个问题
- 我还不知道量化对生成文本的质量影响有多大
- 可能的改进方向：token 采样
- Accelerate 框架目前实际上未使用，因为我发现对于 Decoder 典型的张量形状，与 ARM_NEON  intrinsics 实现相比没有优势。当然，可能我只是不知道如何正确使用它。但无论如何，你甚至可以使用 `LLAMA_NO_ACCELERATE=1 make` 来禁用它，性能将是一样的，因为当前实现没有调用任何 BLAS


---

# llama.cpp 程序模块层次图

┌─────────────────────────────────────────────────────────────────────────────┐
│                           llama.cpp 项目架构                                 │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              应用层 (Application)                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌─────────────────┐              ┌─────────────────┐                       │
│  │    main.cpp     │              │  quantize.cpp   │                       │
│  │   (推理入口)     │              │   (量化工具)     │                        │
│  └────────┬────────┘              └────────┬────────┘                       │
│           │                                  │                              │
│           │  llama_model_load()              │ llama_model_quantize()       │
│           │  llama_eval()                    │                              │
│           │  main()                          │                              │
│           ▼                                  ▼                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           模型层 (Model)                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         llama_model                                  │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │  数据结构:                                                            │   │
│  │    ├── llama_hparams        # 超参数 (n_vocab, n_embd, n_layer...)    │   │
│  │    ├── llama_layer[]        # Transformer 层数组                      │   │
│  │    ├── tok_embeddings       # 词嵌入矩阵                               │   │
│  │    ├── norm                 # 最终归一化                               │   │
│  │    ├── output               # 输出层权重                               │   │
│  │    ├── memory_k / memory_v  # KV Cache                               │   │
│  │    └── tensors              # 张量名称映射表                            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         gpt_vocab                                    │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │  数据结构:                                                            │   │
│  │    ├── token_to_id     # token → id 映射                             │   │
│  │    └── id_to_token     # id → token 映射                             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        GGML 张量运算层 (Tensor Ops)                           │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         ggml.h (接口头文件)                            │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │  核心数据结构:                                                         │   │
│  │    ├── ggml_tensor          # 张量结构 (data, shape, type, op...)     │   │
│  │    ├── ggml_context         # 计算上下文 (内存管理)                     │   │
│  │    ├── ggml_cgraph          # 计算图 (nodes, grads, leafs)            │   │
│  │    └── ggml_type            # 数据类型 (F32, F16, Q4_0, Q4_1...)      │   │
│  │                                                                      │   │
│  │  核心 API:                                                            │   │
│  │    ├── ggml_init()              # 初始化上下文                         │   │
│  │    ├── ggml_new_tensor_*()      # 创建张量                            │   │
│  │    ├── ggml_build_forward()    # 构建前向计算图                        │   │
│  │    ├── ggml_build_backward()   # 构建反向计算图                        │   │
│  │    ├── ggml_graph_compute()    # 执行计算图                            │   │
│  │    └── ggml_free()             # 释放上下文                            │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         ggml.c (具体实现)                             │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │                                                                        │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │   │
│  │  │  张量操作实现    │  │  计算图实现      │  │  自动微分实现    │       │   │
│  │  ├─────────────────┤  ├─────────────────┤  ├─────────────────┤       │   │
│  │  │ ggml_mul_mat()  │  │ ggml_build_*() │  │ ggml_compute_   │       │   │
│  │  │ ggml_add()      │  │ ggml_graph_     │  │   backward()    │       │   │
│  │  │ ggml_norm()     │  │   compute()     │  │                 │       │   │
│  │  │ ggml_softmax()  │  │                 │  │ (大部分未实现)   │       │   │
│  │  │ ggml_rope()     │  │                 │  │                 │       │   │
│  │  │ ggml_diag_*()   │  │                 │  │                 │       │   │
│  │  │ ggml_permute()  │  │                 │  │                 │       │   │
│  │  │ ggml_cpy()      │  │                 │  │                 │       │   │
│  │  │ ggml_reshape_*()│  │                 │  │                 │       │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘       │   │
│  │                                                                        │   │
│  │  ┌─────────────────┐  ┌─────────────────┐                           │   │
│  │  │  量化实现        │  │  平台适配        │                           │   │
│  │  ├─────────────────┤  ├─────────────────┤                           │   │
│  │  │ ggml_quantize_  │  │ #ifdef GGML_USE │                           │   │
│  │  │   q4_0()        │  │   _ACCELERATE   │                           │   │
│  │  │ ggml_quantize_  │  │   (Apple)       │                           │   │
│  │  │   q4_1()        │  │ #ifdef GGML_USE │                           │   │
│  │  │                 │  │   _OPENBLAS      │                           │   │
│  │  │                 │  │   (x86 AVX2)    │                           │   │
│  │  └─────────────────┘  └─────────────────┘                           │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          工具层 (Utils)                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         utils.h / utils.cpp                          │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │                                                                        │   │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐       │   │
│  │  │  参数解析        │  │  分词器          │  │  采样策略        │       │   │
│  │  ├─────────────────┤  ├─────────────────┤  ├─────────────────┤       │   │
│  │  │ gpt_params      │  │ gpt_tokenize()  │  │ gpt_sample_     │       │   │
│  │  │ gpt_params_     │  │ (GPT-2 BPE)     │  │   top_k_top_p() │       │   │
│  │  │ parse()         │  │                 │  │                 │       │   │
│  │  │                 │  │ llama_tokenize()│  │ llama_sample_   │       │   │
│  │  │                 │  │ (SentencePiece)  │  │   top_p()       │       │   │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘       │   │
│  │                                                                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         模型转换脚本 (Python)                                │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    convert-pth-to-ggml.py                             │   │
│  ├──────────────────────────────────────────────────────────────────────┤   │
│  │                                                                        │   │
│  │  PyTorch Model (.pth)  ──────►  GGML Format (.bin)                   │   │
│  │                                                                        │   │
│  │  功能:                                                                 │   │
│  │    1. 加载 PyTorch 权重                                                 │   │
│  │    2. 转换张量格式 (FP32/FP16 → Q4_0/Q4_1)                            │   │
│  │    3. 写入二进制文件                                                   │   │
│  │                                                                        │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

# 数据流图

┌──────────────────────────────────────────────────────────────────────────┐
│                              推理流程                                      │
└──────────────────────────────────────────────────────────────────────────┘

  输入文本
      │
      ▼
┌──────────────┐
│ gpt_tokenize │   # 分词
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ llama_eval   │   # Prefill 阶段
│ (n_past=0)   │   # 并行处理所有输入 tokens
└──────┬───────┘
       │
       ▼
  ┌─────────────────────────────┐
  │   Transformer Forward      │
  │   (所有 layers 并行计算)     │
  └─────────────────────────────┘
       │
       ▼
  ┌──────────────┐
  │ softmax      │   # 转换为概率
  └──────┬───────┘
       │
       ▼
  ┌──────────────────┐
  │ llama_sample_*() │   # 采样第一个 token
  └──────┬───────────┘
       │
       ▼
       ▼ ◄──────────────────┐
       │                    │
       │    ┌───────────────┴───────┐
       │    │                       │
       │    ▼                       │
       │ ┌──────────────┐          │
       │ │ llama_eval   │          │
       │ │ (n_past>0)   │          │  Decode 阶段
       │ │ 每次处理1个   │          │  循环 N 次
       │ │ 新 token     │          │
       │ └──────┬───────┘          │
       │        │                  │
       │        ▼                  │
       │ ┌──────────────────┐      │
       │ │ Transformer      │      │
       │ │ Forward          │      │
       │ │ (含 KV Cache)   │      │
       │ └──────┬───────────┘      │
       │        │                  │
       │        ▼                  │
       │ ┌──────────────────┐      │
       │ │ softmax + sample │      │
       │ └──────┬───────────┘      │
       │        │                  │
       └────────┘                  │
                                   │
                                   ▼
                            输出 token 序列