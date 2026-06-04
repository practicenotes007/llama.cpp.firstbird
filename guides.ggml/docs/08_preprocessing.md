# 编译预处理背景知识补充

> 为阅读 `ggml.c` 所需的 C 预处理知识，重要程度 ★★★

---

## 1. C 预处理器概述

### 1.1 什么是预处理器

C 预处理器 (preprocessor) 在编译前对源代码进行文本替换和处理。所有以 `#` 开头的指令都是预处理指令，在编译阶段之前执行。

```
源代码 → [预处理器] → 展开后的代码 → [编译器] → 目标文件
```

ggml.c 大量使用预处理来实现跨平台支持和代码生成，理解预处理是阅读 ggml.c 的前提。

---

## 2. 宏定义 (#define)

### 2.1 对象宏 (Object-like Macro)

简单的文本替换：

```c
#define GGML_MAX_DIMS      4
#define GGML_MAX_NODES     4096
#define GGML_MAX_CONTEXTS  64
#define GGML_MAX_NAME      32
#define GGML_MAX_PARAMS    64
#define QK                 32     // 量化块大小
#define CACHE_LINE_SIZE    64

// 使用时预处理器直接替换
int dims = GGML_MAX_DIMS;  // → int dims = 4;
```

### 2.2 函数宏 (Function-like Macro)

带参数的文本替换：

```c
#define UNUSED(x)       (void)(x)
#define SWAP(x, y)      do { typeof(x) _tmp = (x); (x) = (y); (y) = _tmp; } while (0)
#define GGML_ASSERT(x)  do { if (!(x)) { fprintf(stderr, "Assertion failed: %s\n", #x); abort(); } } while(0)
#define MIN(a, b)       ((a) < (b) ? (a) : (b))
#define MAX(a, b)       ((a) > (b) ? (a) : (b))

// 使用
int m = MIN(3, 5);  // → int m = ((3) < (5) ? (3) : (5));
```

### 2.3 宏的安全写法

```c
// ❌ 危险：参数未被括号包围
#define SQUARE(x) x * x
SQUARE(1+2)  // → 1+2 * 1+2 = 1+2+2 = 5 (而非 9)

// ✅ 安全：每个参数和整体都加括号
#define SQUARE(x) ((x) * (x))
SQUARE(1+2)  // → ((1+2) * (1+2)) = 9

// ✅ 安全：多语句宏使用 do-while(0)
#define SWAP(x, y) do { typeof(x) _tmp = (x); (x) = (y); (y) = _tmp; } while(0)
// do-while(0) 使宏在 if/else 中表现正确
```

### 2.4 ggml 中的典型宏模式

**调试输出宏**：
```c
#ifdef GGML_DEBUG
#define GGML_PRINT_DEBUG(...) fprintf(stderr, __VA_ARGS__)
#else
#define GGML_PRINT_DEBUG(...)  // 非调试模式：空操作
#endif
```

**性能测量宏**：
```c
#define GGML_PERF(...)  do { \
    const int64_t t0 = ggml_time_us(); \
    __VA_ARGS__; \
    const int64_t t1 = ggml_time_us(); \
    fprintf(stderr, "%s: %.2f ms\n", #__VA_ARGS__, (t1 - t0) / 1000.0); \
} while(0)
```

---

## 3. 条件编译 (#if / #ifdef / #ifndef)

### 3.1 基本语法

```c
#if defined(CONDITION)
    // CONDITION 为真时编译这段代码
#elif defined(OTHER)
    // OTHER 为真时编译这段代码
#else
    // 其他情况编译这段代码
#endif
```

`#ifdef X` 等价于 `#if defined(X)`
`#ifndef X` 等价于 `#if !defined(X)`

### 3.2 平台检测

ggml 大量使用条件编译检测目标平台和可用特性：

```c
// 操作系统检测
#if defined(_WIN32)
    // Windows 特有代码
#elif defined(__APPLE__)
    // macOS 特有代码
#else
    // Linux/POSIX 代码
#endif

// 架构检测
#if defined(__ARM_NEON)
    // ARM NEON 代码
#elif defined(__wasm_simd128__)
    // WASM SIMD 代码
#elif defined(__SSE3__)
    // x86 SSE3 代码
#else
    // 标量 fallback 代码
#endif

// 特性检测
#if defined(__FMA__)
    // 使用 FMA 融合乘加指令
#endif

#if defined(__AVX2__)
    // 使用 AVX2 256-bit 向量
#endif
```

### 3.3 编译器预定义宏

常用编译器会预定义一些宏，可用于条件编译：

| 宏 | 含义 |
|----|------|
| `_WIN32` | Windows 平台 (32/64位) |
| `_WIN64` | Windows 64位 |
| `__linux__` | Linux 平台 |
| `__APPLE__` | macOS/iOS |
| `__ARM_NEON` | ARM NEON 可用 |
| `__ARM_FEATURE_FMA` | ARM FMA 可用 |
| `__SSE3__` | x86 SSE3 可用 |
| `__AVX2__` | x86 AVX2 可用 |
| `__FMA__` | x86 FMA3 可用 |
| `__wasm_simd128__` | WASM SIMD 可用 |
| `__POWER9_VECTOR__` | Power9 SIMD 可用 |

### 3.4 ggml 中的嵌套条件编译

ggml 的 SIMD 抽象层使用了复杂的嵌套条件编译：

```c
#if defined(__ARM_NEON)
    #if defined(__ARM_FEATURE_FMA)
        #define GGML_F32_VEC_FMA(a, b, c) vmlaq_f32(a, b, c)
    #else
        #define GGML_F32_VEC_FMA(a, b, c) vaddq_f32(a, vmulq_f32(b, c))
    #endif
#elif defined(__wasm_simd128__)
    #define GGML_F32_VEC_FMA(a, b, c) wasm_f32x4_add(a, wasm_f32x4_mul(b, c))
#elif defined(__SSE3__)
    #if defined(__FMA__)
        #define GGML_F32_VEC_FMA(a, b, c) _mm_fmadd_ps(b, c, a)
    #else
        #define GGML_F32_VEC_FMA(a, b, c) _mm_add_ps(a, _mm_mul_ps(b, c))
    #endif
#endif
```

---

## 4. 平台检测与条件编译的 ggml 模式

### 4.1 原子操作的平台封装

```c
#if defined(_WIN32)
    typedef volatile LONG ggml_atomic_int;
    #define ggml_atomic_fetch_add(obj, arg)     InterlockedExchangeAdd(obj, arg)
    #define ggml_atomic_store(obj, desired)     InterlockedExchange(obj, desired)
    #define ggml_atomic_load(obj)               (*(obj))
#else
    typedef atomic_int ggml_atomic_int;
    #define ggml_atomic_fetch_add(obj, arg)     atomic_fetch_add(obj, arg)
    #define ggml_atomic_store(obj, desired)     atomic_store(obj, desired)
    #define ggml_atomic_load(obj)               atomic_load(obj)
#endif
```

### 4.2 线程 API 的平台封装

```c
#if defined(_WIN32)
    typedef HANDLE ggml_thread_t;
    #define ggml_thread_create(t, f, a)  (*(t) = CreateThread(NULL, 0, (LPTHREAD_START_ROUTINE)(f), (a), 0, NULL))
    #define ggml_thread_join(t)          (WaitForSingleObject(t, INFINITE), CloseHandle(t))
#else
    typedef pthread_t ggml_thread_t;
    #define ggml_thread_create(t, f, a)  pthread_create(t, NULL, f, a)
    #define ggml_thread_join(t)          pthread_join(t, NULL)
#endif
```

### 4.3 FP16 转换的平台选择

```c
#if defined(__ARM_NEON)
    // ARM NEON: 硬件直接转换
    #define ggml_fp16_to_fp32(x) vaddvq_f32(vcvt_f32_f16(vld1_f16(&(x))))
    #define ggml_fp32_to_fp16(x) /* ... NEON intrinsic ... */
#elif defined(__F16C__)
    // x86 F16C: 硬件指令
    #define ggml_fp16_to_fp32(x) _mm_cvtss_f32(_mm_cvtph_ps(_mm_cvtsi32_si128(x)))
    #define ggml_fp32_to_fp16(x) _mm_extract_epi16(_mm_cvtps_ph(_mm_set_ss(x), 0), 0)
#else
    // 软件实现: 位操作转换
    float ggml_compute_fp16_to_fp32(ggml_fp16_t h);
    ggml_fp16_t ggml_compute_fp32_to_fp16(float f);
#endif
```

---

## 5. `#` 和 `##` 运算符

### 5.1 字符串化运算符 `#`

将宏参数转为字符串字面量：

```c
#define STRINGIFY(x) #x
#define TOSTRING(x)  STRINGIFY(x)

STRINGIFY(hello)  // → "hello"
TOSTRING(42)      // → "42"
```

ggml 中的用法：
```c
#define GGML_ASSERT(x) do { \
    if (!(x)) { \
        fprintf(stderr, "GGML_ASSERT: %s:%d: %s\n", __FILE__, __LINE__, #x); \
        exit(1); \
    } \
} while(0)
// #x 将断言条件转为字符串显示
```

### 5.2 标记粘贴运算符 `##`

将两个标记连接为一个：

```c
#define CONCAT(a, b) a##b

CONCAT(foo, bar)  // → foobar

// ggml 中的典型用法：生成函数名
#define GGML_COMPUTE_FORWARD_IMPL(type) \
    void ggml_compute_forward_##type(struct ggml_tensor * tensor) { ... }

GGML_COMPUTE_FORWARD_IMPL(add_f32)   // → ggml_compute_forward_add_f32
GGML_COMPUTE_FORWARD_IMPL(mul_f32)   // → ggml_compute_forward_mul_f32
```

---

## 6. 预定义宏

### 6.1 编译器预定义的实用宏

| 宏 | 含义 | 用途 |
|----|------|------|
| `__FILE__` | 当前源文件名 | 调试输出 |
| `__LINE__` | 当前行号 | 断言/调试 |
| `__func__` | 当前函数名 | 调试输出 |
| `__DATE__` | 编译日期 | 版本信息 |
| `__TIME__` | 编译时间 | 版本信息 |

### 6.2 ggml 中的使用

```c
#define GGML_PRINT_DEBUG_5(...) do { \
    if (ggml_debug > 4) { \
        fprintf(stderr, "%s:%d: ", __FILE__, __LINE__); \
        fprintf(stderr, __VA_ARGS__); \
        fprintf(stderr, "\n"); \
    } \
} while(0)
```

---

## 7. 条件编译的组织模式

### 7.1 ggml 的 "层次化条件编译" 模式

ggml.c 的条件编译遵循从外到内的层次：

```
第1层: 操作系统 (Windows vs POSIX)
  → 选择线程 API、原子操作、时间函数

第2层: 指令集架构 (ARM vs x86 vs WASM vs PowerPC)
  → 选择 SIMD intrinsics

第3层: 特性可用性 (FMA、F16C、FP16 算术)
  → 选择特定指令的优化路径
```

### 7.2 代码组织原则

```
// 第1步: 先检测平台并定义抽象宏
#if defined(__ARM_NEON)
    #define GGML_F32x4 float32x4_t
    // ...
#elif defined(__SSE3__)
    #define GGML_F32x4 __m128
    // ...
#endif

// 第2步: 核心算法代码只使用抽象宏，不出现平台特定代码
void ggml_vec_dot_f32(...) {
    GGML_F32x4 vx = GGML_F32_VEC_LOAD(x);  // 平台无关
    // ...
}
```

这种模式使得核心算法代码保持平台无关性，所有平台差异都封装在宏定义层。

---

## 8. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| 对象宏 | 简单文本替换 | `GGML_MAX_DIMS`, `QK`, `CACHE_LINE_SIZE` |
| 函数宏 | 带参数的文本替换 | `MIN`, `MAX`, `UNUSED`, `GGML_ASSERT` |
| 条件编译 | 按条件选择性编译 | 平台检测、SIMD 选择 |
| 平台检测 | 编译器预定义宏 | `__ARM_NEON`, `__SSE3__`, `_WIN32` |
| `#` 运算符 | 参数转字符串 | 断言宏中的条件显示 |
| `##` 运算符 | 标记连接 | 函数名生成 |
| 层次化条件编译 | 平台→架构→特性 | ggml 的跨平台设计模式 |
| 抽象宏层 | 核心代码平台无关 | `GGML_F32_VEC_*` 宏族 |
