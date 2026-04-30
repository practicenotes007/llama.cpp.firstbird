#include "ggml.h"
#include <stdio.h>
#include <stdlib.h>

void print_tensor_1d(struct ggml_tensor * tensor, const char * name) {
    printf("%s = [", name);
    for (int i = 0; i < tensor->ne[0]; i++) {
        printf("%.2f", ggml_get_f32_1d(tensor, i));
        if (i < tensor->ne[0] - 1) printf(", ");
    }
    printf("]\n");
}

void example_1_basic_ops(void) {
    printf("\n========================================\n");
    printf("Example 1: Basic Operations (f(x) = a*x^2 + b)\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);
    if (!ctx) {
        fprintf(stderr, "Failed to initialize GGML context\n");
        return;
    }

    struct ggml_tensor * x = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 1);
    struct ggml_tensor * a = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 1);
    struct ggml_tensor * b = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 1);

    // ggml_set_param marks tensor as trainable (adds grad field)
    // Only needed for training/autodiff, NOT for inference
    // We skip it here since many ops have unimplemented backward pass
    // ggml_set_param(ctx, x);
    // ggml_set_param(ctx, a);
    // ggml_set_param(ctx, b);

    struct ggml_tensor * x2 = ggml_mul(ctx, x, x);
    struct ggml_tensor * ax2 = ggml_mul(ctx, a, x2);
    struct ggml_tensor * f = ggml_add(ctx, ax2, b);

    struct ggml_cgraph gf = ggml_build_forward(f);

    ggml_set_f32(x, 2.0f);
    ggml_set_f32(a, 3.0f);
    ggml_set_f32(b, 4.0f);

    ggml_graph_compute(ctx, &gf);

    printf("f(x) = a*x^2 + b\n");
    printf("x = 2.0, a = 3.0, b = 4.0\n");
    printf("Expected: f(2) = 3*4 + 4 = 16\n");
    printf("Result: f(2) = %.2f\n", ggml_get_f32_1d(f, 0));

    ggml_free(ctx);
}

void example_2_matrix_mul(void) {
    printf("\n========================================\n");
    printf("Example 2: Matrix Multiplication\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    // GGML mul_mat convention: A.ne[0] == B.ne[0] (first dim must match)
    // Result: (A.ne[1], B.ne[1])
    struct ggml_tensor * A = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 2, 3);
    struct ggml_tensor * B = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 2, 4);

    float * a_data = (float *)A->data;
    float * b_data = (float *)B->data;

    a_data[0] = 1.0f; a_data[1] = 2.0f; a_data[2] = 3.0f;
    a_data[3] = 4.0f; a_data[4] = 5.0f; a_data[5] = 6.0f;

    b_data[0] = 1.0f; b_data[1] = 2.0f; b_data[2] = 3.0f; b_data[3] = 4.0f;
    b_data[4] = 5.0f; b_data[5] = 6.0f; b_data[6] = 7.0f; b_data[7] = 8.0f;

    struct ggml_tensor * C = ggml_mul_mat(ctx, A, B);

    struct ggml_cgraph gf = ggml_build_forward(C);
    ggml_graph_compute(ctx, &gf);

    printf("Matrix A (2x3):\n");
    printf("  [1, 2, 3]\n");
    printf("  [4, 5, 6]\n");

    printf("Matrix B (2x4):\n");
    printf("  [1, 2, 3, 4]\n");
    printf("  [5, 6, 7, 8]\n");

    printf("A @ B (3x4):\n");
    float * c_data = (float *)C->data;
    for (int i = 0; i < 3; i++) {
        printf("  [%.1f, %.1f, %.1f, %.1f]\n",
               c_data[i], c_data[i+3], c_data[i+6], c_data[i+9]);
    }

    ggml_free(ctx);
}

void example_3_reshape_and_permute(void) {
    printf("\n========================================\n");
    printf("Example 3: Reshape and Permute (Matrix Transpose)\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    struct ggml_tensor * X = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, 2, 3);

    float * x_data = (float *)X->data;
    for (int i = 0; i < 6; i++) x_data[i] = (float)(i + 1);

    struct ggml_tensor * X_trans = ggml_transpose(ctx, X);

    struct ggml_cgraph gf = ggml_build_forward(X_trans);
    ggml_graph_compute(ctx, &gf);

    printf("Original X (2x3):\n");
    printf("  [1, 2, 3]\n");
    printf("  [4, 5, 6]\n");

    printf("Transposed X (3x2):\n");
    float * trans_data = (float *)X_trans->data;
    for (int i = 0; i < 3; i++) {
        printf("  [%.0f, %.0f]\n", trans_data[i*2], trans_data[i*2 + 1]);
    }

    ggml_free(ctx);
}

void example_4_activation_functions(void) {
    printf("\n========================================\n");
    printf("Example 4: Activation Functions\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    struct ggml_tensor * x = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 4);

    float * x_data = (float *)x->data;
    x_data[0] = 0.0f;
    x_data[1] = 1.0f;
    x_data[2] = -1.0f;
    x_data[3] = 0.5f;

    struct ggml_tensor * relu = ggml_relu(ctx, x);
    struct ggml_tensor * gelu = ggml_gelu(ctx, x);
    struct ggml_tensor * silu = ggml_silu(ctx, x);

    struct ggml_cgraph gf = ggml_build_forward(relu);
    ggml_build_forward_expand(&gf, gelu);
    ggml_build_forward_expand(&gf, silu);
    ggml_graph_compute(ctx, &gf);

    printf("Input x:    [%.2f, %.2f, %.2f, %.2f]\n", x_data[0], x_data[1], x_data[2], x_data[3]);
    printf("ReLU(x):    [%.2f, %.2f, %.2f, %.2f]\n",
           ggml_get_f32_1d(relu, 0), ggml_get_f32_1d(relu, 1),
           ggml_get_f32_1d(relu, 2), ggml_get_f32_1d(relu, 3));
    printf("GELU(x):    [%.2f, %.2f, %.2f, %.2f]\n",
           ggml_get_f32_1d(gelu, 0), ggml_get_f32_1d(gelu, 1),
           ggml_get_f32_1d(gelu, 2), ggml_get_f32_1d(gelu, 3));
    printf("SiLU(x):    [%.2f, %.2f, %.2f, %.2f]\n",
           ggml_get_f32_1d(silu, 0), ggml_get_f32_1d(silu, 1),
           ggml_get_f32_1d(silu, 2), ggml_get_f32_1d(silu, 3));

    ggml_free(ctx);
}

void example_5_softmax(void) {
    printf("\n========================================\n");
    printf("Example 5: Softmax\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    struct ggml_tensor * x = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 4);

    float * x_data = (float *)x->data;
    x_data[0] = 1.0f;
    x_data[1] = 2.0f;
    x_data[2] = 3.0f;
    x_data[3] = 4.0f;

    struct ggml_tensor * probs = ggml_soft_max(ctx, x);

    struct ggml_cgraph gf = ggml_build_forward(probs);
    ggml_graph_compute(ctx, &gf);

    printf("Logits:     [%.2f, %.2f, %.2f, %.2f]\n",
           x_data[0], x_data[1], x_data[2], x_data[3]);
    printf("Softmax:    [%.4f, %.4f, %.4f, %.4f]\n",
           ggml_get_f32_1d(probs, 0), ggml_get_f32_1d(probs, 1),
           ggml_get_f32_1d(probs, 2), ggml_get_f32_1d(probs, 3));
    printf("(Sum = %.4f, should be ~1.0)\n",
           ggml_get_f32_1d(probs, 0) + ggml_get_f32_1d(probs, 1) +
           ggml_get_f32_1d(probs, 2) + ggml_get_f32_1d(probs, 3));

    ggml_free(ctx);
}

void example_6_rms_norm(void) {
    printf("\n========================================\n");
    printf("Example 6: RMS Normalization\n");
    printf("========================================\n");

    struct ggml_init_params params = {
        .mem_size   = 16*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    struct ggml_tensor * x = ggml_new_tensor_1d(ctx, GGML_TYPE_F32, 4);

    float * x_data = (float *)x->data;
    x_data[0] = 1.0f;
    x_data[1] = 2.0f;
    x_data[2] = 3.0f;
    x_data[3] = 4.0f;

    struct ggml_tensor * normed = ggml_norm(ctx, x);

    struct ggml_cgraph gf = ggml_build_forward(normed);
    ggml_graph_compute(ctx, &gf);

    printf("Input:  [%.2f, %.2f, %.2f, %.2f]\n",
           x_data[0], x_data[1], x_data[2], x_data[3]);
    printf("RMSNorm: [%.4f, %.4f, %.4f, %.4f]\n",
           ggml_get_f32_1d(normed, 0), ggml_get_f32_1d(normed, 1),
           ggml_get_f32_1d(normed, 2), ggml_get_f32_1d(normed, 3));

    ggml_free(ctx);
}

void example_7_feedforward_network(void) {
    printf("\n========================================\n");
    printf("Example 7: Feed-Forward Network (FFN)\n");
    printf("========================================\n");
    printf("This simulates: f(x) = Silu(x @ W1) @ W2\n");
    printf("A simplified FFN layer similar to LLaMA\n\n");

    struct ggml_init_params params = {
        .mem_size   = 64*1024*1024,
        .mem_buffer = NULL,
    };

    struct ggml_context * ctx = ggml_init(params);

    int d_model = 4;
    int d_ff = 8;
    int batch_size = 1;

    // GGML mul_mat convention: mul_mat(weight, input)
    //   weight: (d_in, d_out), input: (d_in, batch)
    //   result: (d_out, batch)
    struct ggml_tensor * x  = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, d_model, batch_size);
    struct ggml_tensor * W1 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, d_model, d_ff);
    struct ggml_tensor * W2 = ggml_new_tensor_2d(ctx, GGML_TYPE_F32, d_ff, d_model);

    float * x_data = (float *)x->data;
    float * w1_data = (float *)W1->data;
    float * w2_data = (float *)W2->data;

    for (int i = 0; i < d_model; i++) x_data[i] = 0.5f;
    for (int i = 0; i < d_model * d_ff; i++) w1_data[i] = 0.1f * (i % 5);
    for (int i = 0; i < d_ff * d_model; i++) w2_data[i] = 0.1f * (i % 3);

    // mul_mat(W1, x): W1(4,8) @ x(4,1) → h1(8,1)  [W1.ne[0]==x.ne[0]==4 ✓]
    struct ggml_tensor * h1 = ggml_mul_mat(ctx, W1, x);
    struct ggml_tensor * h2 = ggml_silu(ctx, h1);
    // mul_mat(W2, h2): W2(8,4) @ h2(8,1) → out(4,1)  [W2.ne[0]==h2.ne[0]==8 ✓]
    struct ggml_tensor * out = ggml_mul_mat(ctx, W2, h2);

    struct ggml_cgraph gf = ggml_build_forward(out);
    ggml_graph_compute(ctx, &gf);

    printf("Input x (d_model=%d):\n", d_model);
    for (int i = 0; i < d_model; i++) printf("  %.3f ", x_data[i]);
    printf("\n\n");

    printf("Output (d_model=%d):\n", d_model);
    float * out_data = (float *)out->data;
    for (int i = 0; i < d_model; i++) printf("  %.4f ", out_data[i]);
    printf("\n");

    ggml_free(ctx);
}

int main(void) {
    printf("========================================\n");
    printf("    GGML API Usage Examples\n");
    printf("========================================\n");

    example_1_basic_ops();
    example_2_matrix_mul();
    example_3_reshape_and_permute();
    example_4_activation_functions();
    example_5_softmax();
    example_6_rms_norm();
    example_7_feedforward_network();

    printf("\n========================================\n");
    printf("    All examples completed!\n");
    printf("========================================\n");

    return 0;
}
