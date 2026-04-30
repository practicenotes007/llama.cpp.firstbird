# 多线程背景知识补充

> 为阅读 `ggml.c` 所需的多线程编程知识，重要程度 ★★★★

---

## 1. 为什么 ggml 需要多线程

ggml 的核心计算（矩阵乘法、点积、逐元素运算）天然适合数据并行——同一操作应用于大量独立数据。多线程可将计算分块并行执行，在多核 CPU 上获得近线性的加速。

---

## 2. POSIX pthread

### 2.1 基本概念

pthread (POSIX Threads) 是 Unix/Linux 系统的标准线程 API：

```c
#include <pthread.h>

pthread_t thread;
int rc = pthread_create(&thread, NULL, thread_func, arg);
pthread_join(thread, NULL);
```

### 2.2 ggml 中的线程管理

ggml 创建一组工作线程，形成**线程池**：

```c
struct ggml_state {
    // ...
    int n_threads;
    pthread_t * threads;
    // ...
};
```

### 2.3 线程函数与参数

工作线程的主循环结构：

```c
void * ggml_graph_compute_thread(void * data) {
    struct ggml_compute_params * params = (struct ggml_compute_params *)data;

    while (true) {
        // 等待任务
        // 执行分配给自己的分块
        // 同步
    }
    return NULL;
}
```

---

## 3. Windows 线程 API

### 3.1 CreateThread

Windows 不支持 pthread，ggml 使用 `CreateThread` 替代：

```c
#if defined(_WIN32)
    HANDLE thread = CreateThread(NULL, 0, thread_func, arg, 0, NULL);
    WaitForSingleObject(thread, INFINITE);
    CloseHandle(thread);
#else
    pthread_t thread;
    pthread_create(&thread, NULL, thread_func, arg);
    pthread_join(thread, NULL);
#endif
```

### 3.2 ggml 的跨平台封装

```c
// 平台检测与封装
#if defined(_WIN32)
    typedef HANDLE ggml_thread_t;
    #define ggml_thread_create(t, f, a)  (*(t) = CreateThread(NULL, 0, f, a, 0, NULL))
    #define ggml_thread_join(t)         (WaitForSingleObject(t, INFINITE), CloseHandle(t))
#else
    typedef pthread_t ggml_thread_t;
    #define ggml_thread_create(t, f, a)  pthread_create(t, NULL, f, a)
    #define ggml_thread_join(t)          pthread_join(t, NULL)
#endif
```

---

## 4. 原子操作

### 4.1 为什么需要原子操作

多线程访问共享变量时，普通读/写不是原子操作，可能导致数据竞争 (data race)：

```
线程A: 读取 count=0 → 加1 → 写入 count=1
线程B: 读取 count=0 → 加1 → 写入 count=1
结果: count=1 (期望 2)
```

### 4.2 C11 stdatomic.h

C11 标准引入了原子类型和操作：

```c
#include <stdatomic.h>

atomic_int counter = ATOMIC_VAR_INIT(0);

// 原子自增并返回新值
int new_val = atomic_fetch_add(&counter, 1);

// 原子交换
int old_val = atomic_exchange(&counter, 42);

// 原子比较并交换 (CAS)
int expected = 0;
bool success = atomic_compare_exchange_strong(&counter, &expected, 1);
```

### 4.3 ggml 中的原子操作

ggml 使用原子操作实现两个关键机制：

**a) 全局状态屏障 (g_state_barrier)**

```c
static atomic_int g_state_barrier = ATOMIC_VAR_INIT(0);

void ggml_critical_section_start(void) {
    int expected = 0;
    while (!atomic_compare_exchange_strong(&g_state_barrier, &expected, 1)) {
        expected = 0;
    }
}

void ggml_critical_section_end(void) {
    atomic_store(&g_state_barrier, 0);
}
```

**b) 工作计数器**

在计算图执行时，原子计数器分配任务块给线程：

```c
atomic_int node_n;  // 当前待计算的节点索引

// 每个线程通过原子操作获取下一个任务
int my_node = atomic_fetch_add(&node_n, 1);
```

### 4.4 Windows 原子操作

```c
#if defined(_WIN32)
    // Windows 使用 Interlocked 系列函数
    LONG InterlockedExchange(volatile LONG *target, LONG value);
    LONG InterlockedIncrement(volatile LONG *target);
    LONG InterlockedCompareExchange(volatile LONG *dest, LONG exch, LONG comp);
#else
    // POSIX 使用 stdatomic.h
    atomic_int ...
#endif
```

---

## 5. 自旋锁 (Spinlock)

### 5.1 原理

自旋锁是最简单的互斥机制——线程在循环中反复尝试获取锁，不进入睡眠：

```c
void spinlock_lock(atomic_int * lock) {
    while (atomic_exchange(lock, 1) != 0) {
        // 自旋等待 (busy-wait)
        // 可选: 插入 pause/yield 指令降低功耗
    }
}

void spinlock_unlock(atomic_int * lock) {
    atomic_store(lock, 0);
}
```

### 5.2 自旋锁 vs 互斥锁

| 特性 | 自旋锁 | 互斥锁 (mutex) |
|------|--------|---------------|
| 等待方式 | 忙等待 (CPU 100%) | 线程睡眠 (CPU 0%) |
| 上下文切换 | 无 | 有 (开销大) |
| 适用场景 | 锁持有时间极短 | 锁持有时间较长 |
| 多核效果 | 好 (其他核可工作) | 单核上无优势 |

### 5.3 ggml 中的自旋锁使用

ggml 的临界区本质上就是一个自旋锁：

```c
// 保护全局状态初始化
ggml_critical_section_start();
// ... 初始化全局状态（极短时间）...
ggml_critical_section_end();
```

选择自旋锁的原因：临界区代码极短（仅几个赋值），自旋锁的忙等开销远小于互斥锁的上下文切换开销。

---

## 6. 线程同步模式

### 6.1 屏障同步 (Barrier)

ggml 使用屏障同步确保所有线程完成当前操作后再继续：

```
时间 →
线程0: [计算任务A] → 等待 ──── → [计算任务B]
线程1: [计算任务A] → 等待 ──── → [计算任务B]
线程2: [计算任务A] ──── → 等待 → [计算任务B]
                       ↑ 所有线程到齐后释放
```

### 6.2 ggml 的三阶段计算与线程分工

每个操作符的计算分为三个阶段，线程分工各不同：

| 阶段 | 任务 | 线程分工 |
|------|------|---------|
| INIT | 分配输出内存、初始化参数 | 通常仅 `ith==0` 线程执行 |
| COMPUTE | 实际并行计算 | 所有线程参与，数据分块 |
| FINALIZE | 归约、收尾 | 通常仅 `ith==0` 线程执行 |

```c
if (params->type == GGML_TASK_INIT) {
    if (params->ith != 0) return;
    // 初始化逻辑...
} else if (params->type == GGML_TASK_COMPUTE) {
    // 所有线程参与计算
    const int ith = params->ith;
    const int nth = params->nth;
    for (int i = ith; i < n; i += nth) {
        // 处理第 i 块数据
    }
} else if (params->type == GGML_TASK_FINALIZE) {
    if (params->ith != 0) return;
    // 归约逻辑...
}
```

### 6.3 矩阵乘法的并行策略

这是 ggml 多线程最典型的应用：

```
矩阵乘法 C = A × B (A: M×K, B: K×N)

行分块策略:
线程0: 计算第 0, nth, 2*nth, ... 行
线程1: 计算第 1, nth+1, 2*nth+1, ... 行
...
线程(nth-1): 计算第 nth-1, 2*nth-1, ... 行

每行 = K 个元素的点积
```

---

## 7. 工作窃取 (Work Stealing) 简介

ggml 的简单线程分配方式是**静态分块**（每线程处理固定行），在某些负载不均衡的情况下可能效率不佳。更高级的方案是工作窃取——完成自己任务的线程从其他线程"偷"任务。

ggml 使用原子计数器实现了一种简单的**动态分块**：

```c
// 每个线程通过原子操作获取下一个工作项
int my_work = atomic_fetch_add(&work_counter, 1);
if (my_work < total_work) {
    // 执行 my_work 对应的计算
}
```

这种方式天然实现了负载均衡——快的线程多处理，慢的线程少处理。

---

## 8. 关键要点总结

| 概念 | 说明 | ggml 体现 |
|------|------|----------|
| pthread / CreateThread | 跨平台线程创建 | 条件编译封装 |
| 原子操作 | 无锁同步原语 | `atomic_fetch_add`, `atomic_exchange` |
| 自旋锁 | 忙等待互斥 | `g_state_barrier` 临界区 |
| 屏障同步 | 所有线程等待到齐 | 三阶段计算的线程同步 |
| 三阶段计算 | INIT/COMPUTE/FINALIZE | `enum ggml_task_type` |
| 数据分块并行 | 行/元素级分块 | `for (i=ith; i<n; i+=nth)` |
| 动态任务分配 | 原子计数器驱动 | `atomic_fetch_add(&counter, 1)` |
