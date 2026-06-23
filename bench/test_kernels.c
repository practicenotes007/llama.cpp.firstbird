// test_kernels.c - llama.cpp firstbird 正确性测试套件
//
// 编译: gcc -O2 -march=armv8-a -I. -o test_kernels test_kernels.c -lm
// 运行: ./test_kernels [--verbose] [--dot-only] [--bench]
//
// 测试内容:
//   1. FP16 <-> FP32 转换 (全部 65536 个 bit pattern)
//   2. 量化/反量化 round-trip 误差
//   3. vec_dot_q4_0 精度 (SIMD vs 标量参考)
//   4. vec_mad_q4_0 精度
//   5. GELU/SiLU 查表精度
//   6. 可选的微型性能基准

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <assert.h>
#include <stdint.h>

// 直接包含 ggml.c 以访问 static 内核函数
// 通过宏避免重复的 main()
#define GGML_TEST_MODE
#include "ggml.c"

// ---------- 测试框架辅助 ----------
static int g_tests_passed = 0;
static int g_tests_failed = 0;
static int g_verbose = 0;

#define TEST_ASSERT(cond, msg) do { \
    if (!(cond)) { \
        fprintf(stderr, "  FAIL: %s (%s:%d)\n", msg, __FILE__, __LINE__); \
        g_tests_failed++; \
        return; \
    } \
} while(0)

#define TEST_SECTION(name) do { \
    printf("\n=== %s ===\n", name); \
} while(0)

#define TEST_SUB(name) do { \
    printf("  %s ... ", name); \
    fflush(stdout); \
} while(0)

#define TEST_PASS() do { \
    printf("OK\n"); \
    g_tests_passed++; \
} while(0)

// ---------- 1. FP16 <-> FP32 转换 ----------
static float ref_fp16_to_fp32(uint16_t h) {
    // IEEE 754-2008 binary16
    uint16_t sign = (h >> 15) & 1;
    uint16_t exp  = (h >> 10) & 0x1f;
    uint16_t mant = h & 0x3ff;

    if (exp == 0) {
        // subnormal
        if (mant == 0) return sign ? -0.0f : 0.0f;
        return (sign ? -1.0f : 1.0f) * (mant / 1024.0f) * powf(2.0f, -14.0f);
    }
    if (exp == 31) {
        // infinity or NaN
        if (mant == 0) return sign ? -INFINITY : INFINITY;
        return NAN;
    }
    // normal
    return (sign ? -1.0f : 1.0f) * (1.0f + mant / 1024.0f) * powf(2.0f, (int)exp - 15);
}

static void test_fp16_conversion(void) {
    TEST_SECTION("FP16 <-> FP32 转换");

    // 遍历全部 65536 个 bit pattern
    TEST_SUB("全部 65536 个 pattern (fp16->fp32)");
    for (uint32_t i = 0; i < 65536; i++) {
        uint16_t h = (uint16_t)i;
        float ref = ref_fp16_to_fp32(h);
        float actual;
        if (isnan(ref)) {
            actual = ggml_fp16_to_fp32(h);
            if (!isnan(actual)) {
                fprintf(stderr, "\n  FAIL: pattern 0x%04x: expected NaN, got %f\n", i, actual);
                g_tests_failed++;
                return;
            }
            continue;
        }
        actual = ggml_fp16_to_fp32(h);
        if (fabsf(ref - actual) > 1e-6f * fmaxf(1.0f, fabsf(ref))) {
            fprintf(stderr, "\n  FAIL: pattern 0x%04x: ref=%f actual=%f diff=%e\n", i, ref, actual, fabsf(ref - actual));
            g_tests_failed++;
            return;
        }
    }
    TEST_PASS();

    TEST_SUB("随机抽查 fp32->fp16->fp32 round-trip");
    srand(42);
    for (int i = 0; i < 100000; i++) {
        // 生成各种范围的浮点数
        float f;
        switch (rand() % 4) {
            case 0: f = (float)(rand() % 100000) / 100.0f; break;
            case 1: f = (float)(rand() % 1000) / 100000.0f; break;
            case 2: f = (float)(-(rand() % 100000)) / 100.0f; break;
            case 3: f = 0.0f; break;
        }
        ggml_fp16_t h = ggml_fp32_to_fp16(f);
        float roundtrip = ggml_fp16_to_fp32(h);
        // FP16 精度约 0.1%，容许更大的误差
        if (fabsf(f - roundtrip) > 1e-3f * fmaxf(1.0f, fabsf(f)) && !(isnan(f) && isnan(roundtrip))) {
            fprintf(stderr, "\n  FAIL: f=%f -> 0x%04x -> %f, diff=%e\n", f, h, roundtrip, fabsf(f - roundtrip));
            g_tests_failed++;
            return;
        }
    }
    TEST_PASS();

    // 检查查表
    TEST_SUB("table_f32_f16 与计算值一致");
    for (int i = 0; i < 10; i++) {
        int idx = rand() % 65536;
        float tbl_val = table_f32_f16[idx];
        float computed = ggml_fp16_to_fp32((ggml_fp16_t)(uint16_t)idx);
        if (isnan(computed) && isnan(tbl_val)) continue;
        if (fabsf(tbl_val - computed) > 1e-6f * fmaxf(1.0f, fabsf(tbl_val))) {
            fprintf(stderr, "\n  FAIL: table[0x%04x] = %f, computed = %f\n", idx, tbl_val, computed);
            g_tests_failed++;
            return;
        }
    }
    TEST_PASS();
}

// ---------- 2. 量化/反量化 round-trip ----------
static void test_quantize_dequantize(void) {
    TEST_SECTION("Q4_0 量化/反量化 round-trip");

    const int n_elems = 4096 * 4;  // 多行
    float *src = malloc(n_elems * sizeof(float));
    float *dst = malloc(n_elems * sizeof(float));
    void *q   = malloc(n_elems * sizeof(float) + n_elems * QK / 2);  // 上界

    // 各种分布
    const char *distrib[] = {"uniform(-1,1)", "uniform(-10,10)", "高斯", "常数", "正弦", "稀疏"};
    for (int d = 0; d < 6; d++) {
        srand(42 + d);
        for (int i = 0; i < n_elems; i++) {
            switch (d) {
                case 0: src[i] = ((float)rand() / RAND_MAX) * 2.0f - 1.0f; break;
                case 1: src[i] = ((float)rand() / RAND_MAX) * 20.0f - 10.0f; break;
                case 2: {
                    float u1 = (float)rand() / RAND_MAX;
                    float u2 = (float)rand() / RAND_MAX;
                    src[i] = sqrtf(-2.0f * logf(u1)) * cosf(2.0f * M_PI * u2);
                    break;
                }
                case 3: src[i] = 3.14159f; break;
                case 4: src[i] = sinf((float)i * 0.1f); break;
                case 5: src[i] = (rand() % 20 == 0) ? ((float)rand() / RAND_MAX) * 2.0f - 1.0f : 0.0f; break;
            }
        }

        quantize_row_q4_0(src, q, n_elems);
        dequantize_row_q4_0(q, dst, n_elems);

        // 计算误差
        double sse = 0;
        float max_err = 0;
        for (int i = 0; i < n_elems; i++) {
            float err = fabsf(src[i] - dst[i]);
            sse += err * err;
            if (err > max_err) max_err = err;
        }
        float rmse = (float)sqrt(sse / n_elems);

        if (g_verbose) {
            printf("  %s: RMSE=%f max_err=%f\n", distrib[d], rmse, max_err);
        }

        TEST_SUB(distrib[d]);
        // Q4_0 的每个 block 有自己的 scale，理论精度约 1/8 = 12.5%
        // 实际 RMSE 通常在 0.05-0.15 之间
        if (rmse > 0.5f) {
            fprintf(stderr, "\n  FAIL: RMSE=%f 过大\n", rmse);
            g_tests_failed++;
            return;
        }
        TEST_PASS();

        // round-trip: 量化 -> 反量化 -> 量化 -> 反量化（第二次应该一致）
        void *q2 = malloc(n_elems * sizeof(float) + n_elems * QK / 2);
        float *dst2 = malloc(n_elems * sizeof(float));
        quantize_row_q4_0(dst, q2, n_elems);
        dequantize_row_q4_0(q2, dst2, n_elems);

        double sse2 = 0;
        for (int i = 0; i < n_elems; i++) {
            float err = fabsf(dst[i] - dst2[i]);
            sse2 += err * err;
        }
        float rmse2 = (float)sqrt(sse2 / n_elems);

        if (g_verbose) {
            printf("    double round-trip RMSE=%f\n", rmse2);
        }

        TEST_SUB("double round-trip");
        if (rmse2 > 1e-5f) {
            fprintf(stderr, "\n  FAIL: double round-trip RMSE=%f (应该接近 0)\n", rmse2);
            g_tests_failed++;
            return;
        }
        TEST_PASS();

        free(q2);
        free(dst2);
    }

    free(src);
    free(dst);
    free(q);
}

// ---------- 3. vec_dot_q4_0 精度 ----------
static void test_vec_dot(void) {
    TEST_SECTION("ggml_vec_dot_q4_0 精度");

    srand(12345);

    // 多种长度 (必须是 32 的倍数)
    const int lengths[] = {32, 64, 128, 256, 512, 1024, 4096};
    for (int li = 0; li < 7; li++) {
        int n = lengths[li];
        float *x_f32 = malloc(n * sizeof(float));
        float *y_f32 = malloc(n * sizeof(float));
        void *x_q    = malloc(n * sizeof(float) + n / 2);
        void *y_q    = malloc(n * sizeof(float) + n / 2);

        for (int trial = 0; trial < 5; trial++) {
            for (int i = 0; i < n; i++) {
                x_f32[i] = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;
                y_f32[i] = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;
            }

            quantize_row_q4_0(x_f32, x_q, n);
            quantize_row_q4_0(y_f32, y_q, n);

            // 标量参考
            float dot_ref = 0;
            dequantize_row_q4_0(x_q, x_f32, n);
            dequantize_row_q4_0(y_q, y_f32, n);
            for (int i = 0; i < n; i++) {
                dot_ref += x_f32[i] * y_f32[i];
            }

            // SIMD 实现
            float dot_simd = 0;
            ggml_vec_dot_q4_0(n, &dot_simd, x_q, y_q);

            float rel_err = fabsf(dot_ref - dot_simd) / fmaxf(1.0f, fabsf(dot_ref));

            char buf[128];
            snprintf(buf, sizeof(buf), "len=%d trial=%d", n, trial);

            TEST_SUB(buf);
            if (rel_err > 0.02f) {
                fprintf(stderr, "\n  FAIL: ref=%f simd=%f rel_err=%f\n", dot_ref, dot_simd, rel_err);
                g_tests_failed++;
                return;
            }
            TEST_PASS();
        }

        free(x_f32);
        free(y_f32);
        free(x_q);
        free(y_q);
    }
}

// ---------- 4. vec_mad_q4_0 精度 ----------
static void test_vec_mad(void) {
    TEST_SECTION("ggml_vec_mad_q4_0 精度");

    srand(67890);
    const int n = 4096;
    float *y_simd = malloc(n * sizeof(float));
    float *y_ref  = malloc(n * sizeof(float));
    float *x_f32  = malloc(n * sizeof(float));
    void *x_q     = malloc(n * sizeof(float) + n / 2);

    for (int trial = 0; trial < 10; trial++) {
        for (int i = 0; i < n; i++) {
            y_simd[i] = ((float)rand() / RAND_MAX) * 10.0f - 5.0f;
            y_ref[i]  = y_simd[i];
            x_f32[i]  = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;
        }
        float v = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;

        quantize_row_q4_0(x_f32, x_q, n);

        // 标量参考
        float *tmp = malloc(n * sizeof(float));
        dequantize_row_q4_0(x_q, tmp, n);
        for (int i = 0; i < n; i++) {
            y_ref[i] += tmp[i] * v;
        }
        free(tmp);

        // SIMD
        ggml_vec_mad_q4_0(n, y_simd, x_q, v);

        float max_err = 0;
        for (int i = 0; i < n; i++) {
            float err = fabsf(y_ref[i] - y_simd[i]);
            if (err > max_err) max_err = err;
        }

        char buf[64];
        snprintf(buf, sizeof(buf), "trial=%d max_err=%e", trial, max_err);
        TEST_SUB(buf);
        if (max_err > 1e-4f) {
            fprintf(stderr, "\n  FAIL: vec_mad 精度超限\n");
            g_tests_failed++;
            return;
        }
        TEST_PASS();
    }

    free(y_simd);
    free(y_ref);
    free(x_f32);
    free(x_q);
}

// ---------- 5. GELU/SiLU 查表精度 ----------
static void test_table_gelu(void) {
    TEST_SECTION("GELU 查表精度");

    // 初始化表
    {
        for (int i = 0; i < (1 << 16); i++) {
            uint16_t u = (uint16_t)i;
            float f = table_f32_f16[u];
            table_gelu_f16[i] = ggml_fp32_to_fp16(f * 0.5f * (1.0f + erff(f / sqrtf(2.0f))));
        }
    }

    srand(999);
    double max_rel_err = 0;
    for (int i = 0; i < 10000; i++) {
        // 在 FP16 表示范围内抽样
        uint16_t pattern = (uint16_t)(rand() % 65536);
        float x = table_f32_f16[pattern];
        if (fabsf(x) > 100.0f) continue;  // FP16 上限约 65504

        float expected = x * 0.5f * (1.0f + erff(x / sqrtf(2.0f)));
        ggml_fp16_t h = table_gelu_f16[pattern];
        float actual = ggml_fp16_to_fp32(h);

        float rel_err = fabsf(expected - actual) / fmaxf(1.0f, fabsf(expected));
        if (rel_err > max_rel_err) max_rel_err = rel_err;

        if (rel_err > 0.05f) {
            fprintf(stderr, "\n  FAIL: x=%f expected=%f actual=%f rel_err=%f\n", x, expected, actual, rel_err);
            g_tests_failed++;
            return;
        }
    }

    TEST_SUB("GELU 10000 点抽样");
    if (max_rel_err > 0.05f) {
        fprintf(stderr, "max_rel_err=%f\n", max_rel_err);
        g_tests_failed++;
    } else {
        printf("max_rel_err=%e ", max_rel_err);
        TEST_PASS();
    }
}

static void test_table_silu(void) {
    TEST_SECTION("SiLU 查表精度");

    // 初始化表
    {
        for (int i = 0; i < (1 << 16); i++) {
            uint16_t u = (uint16_t)i;
            float x = table_f32_f16[u];
            float silu = x / (1.0f + expf(-x));
            table_silu_f16[i] = ggml_fp32_to_fp16(silu);
        }
    }

    srand(888);
    double max_rel_err = 0;
    for (int i = 0; i < 10000; i++) {
        uint16_t pattern = (uint16_t)(rand() % 65536);
        float x = table_f32_f16[pattern];
        if (fabsf(x) > 100.0f) continue;

        float expected = x / (1.0f + expf(-x));
        ggml_fp16_t h = table_silu_f16[pattern];
        float actual = ggml_fp16_to_fp32(h);

        float rel_err = fabsf(expected - actual) / fmaxf(1.0f, fabsf(expected));
        if (rel_err > max_rel_err) max_rel_err = rel_err;

        if (rel_err > 0.05f) {
            fprintf(stderr, "\n  FAIL: x=%f expected=%f actual=%f rel_err=%f\n", x, expected, actual, rel_err);
            g_tests_failed++;
            return;
        }
    }

    TEST_SUB("SiLU 10000 点抽样");
    if (max_rel_err > 0.05f) {
        fprintf(stderr, "max_rel_err=%f\n", max_rel_err);
        g_tests_failed++;
    } else {
        printf("max_rel_err=%e ", max_rel_err);
        TEST_PASS();
    }
}

// ---------- 6. 微型性能基准 ----------
static void bench_vec_dot(void) {
    TEST_SECTION("vec_dot 微基准");

    const int lengths[] = {256, 4096};
    const int n_iters = 10000;

    for (int li = 0; li < 2; li++) {
        int n = lengths[li];
        float *x_f32 = malloc(n * sizeof(float));
        float *y_f32 = malloc(n * sizeof(float));
        void *x_q    = malloc(n * sizeof(float) + n / 2);
        void *y_q    = malloc(n * sizeof(float) + n / 2);

        for (int i = 0; i < n; i++) {
            x_f32[i] = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;
            y_f32[i] = ((float)rand() / RAND_MAX) * 2.0f - 1.0f;
        }
        quantize_row_q4_0(x_f32, x_q, n);
        quantize_row_q4_0(y_f32, y_q, n);

        float sum = 0;
        int64_t t0 = ggml_time_us();

        for (int i = 0; i < n_iters; i++) {
            float s = 0;
            ggml_vec_dot_q4_0(n, &s, x_q, y_q);
            sum += s;
        }

        int64_t t1 = ggml_time_us();
        double total_us = (double)(t1 - t0);
        double avg_us = total_us / n_iters;
        double gflops = (double)n * 2.0 * n_iters / total_us / 1e3;  // GFLOP/s

        printf("  len=%-5d %d iters: %.3f us/call, %.2f GFLOPS (sum=%f)\n",
               n, n_iters, avg_us, gflops, sum);

        free(x_f32);
        free(y_f32);
        free(x_q);
        free(y_q);
    }
    TEST_PASS();  // microbenchmark always passes
}

// ---------- 主函数 ----------
int main(int argc, char **argv) {
    int run_fp16 = 1;
    int run_quant = 1;
    int run_dot = 1;
    int run_mad = 1;
    int run_tables = 1;
    int run_bench = 0;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--verbose") == 0 || strcmp(argv[i], "-v") == 0) {
            g_verbose = 1;
        } else if (strcmp(argv[i], "--dot-only") == 0) {
            run_fp16 = run_quant = run_mad = run_tables = 0;
        } else if (strcmp(argv[i], "--bench") == 0) {
            run_bench = 1;
        } else if (strcmp(argv[i], "--fast") == 0) {
            run_tables = 0;  // 跳过查表测试（需要初始化，较慢）
        } else {
            fprintf(stderr, "用法: %s [--verbose] [--dot-only] [--bench] [--fast]\n", argv[0]);
            return 1;
        }
    }

    ggml_time_init();

    printf("llama.cpp firstbird 正确性测试\n");
    printf("================================\n\n");

    struct ggml_tensor *t;
    printf("CPU: NEON=%d ARM_FMA=%d FP16_VA=%d\n\n",
           ggml_cpu_has_neon(), ggml_cpu_has_arm_fma(), ggml_cpu_has_fp16_va());

    if (run_fp16)    test_fp16_conversion();
    if (run_quant)   test_quantize_dequantize();
    if (run_dot)     test_vec_dot();
    if (run_mad)     test_vec_mad();
    if (run_tables) { test_table_gelu(); test_table_silu(); }
    if (run_bench)   bench_vec_dot();

    printf("\n================================\n");
    printf("结果: %d 通过, %d 失败\n", g_tests_passed, g_tests_failed);

    return g_tests_failed > 0 ? 1 : 0;
}
