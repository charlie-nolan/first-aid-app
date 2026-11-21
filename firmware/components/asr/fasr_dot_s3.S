// fasr_dot_s3.S - int8 dot product on the ESP32-S3 vector unit (PIE): 16 multiply-adds per instruction.
// int32_t fq_dot16(const int8_t *a, const int8_t *b, int n16)
//   a, b: 16-byte aligned; n16 >= 1 blocks of 16 bytes. Exact integer sum (40-bit ACCX, low 32 bits read).
// Written for this engine; the load-while-multiply pattern follows Espressif's esp-nn
// (esp_nn_dot_s8_aligned_esp32s3, Apache License 2.0, see LICENSE-esp-nn.txt). No esp-nn code is linked.
    .text
    .align  4
    .global fq_dot16
    .type   fq_dot16, @function
fq_dot16:
    entry   a1, 32
    ee.zero.accx
    ee.vld.128.ip   q0, a2, 16
    ee.vld.128.ip   q1, a3, 16
    addi    a4, a4, -1
    beqz    a4, .Llast
    loopgtz a4, .Lloop_end
    ee.vmulas.s8.accx.ld.ip  q0, a2, 16, q0, q1
    ee.vld.128.ip   q1, a3, 16
.Lloop_end:
.Llast:
    ee.vmulas.s8.accx  q0, q1
    nop
    nop
    rur.accx_0 a2
    retw.n
    .size   fq_dot16, . - fq_dot16

// int32_t fq_dot16u(const int8_t *a, const int8_t *b, int n16)
//   a 16-byte aligned, b any alignment; reads up to 16 bytes past b + 16 * n16 (callers keep that readable).
//   Unaligned loads as in esp-nn's esp_nn_dot_s8_unaligned_esp32s3 (ee.ld.128.usar + ee.src.q.qup).
    .align  4
    .global fq_dot16u
    .type   fq_dot16u, @function
fq_dot16u:
    entry   a1, 32
    ee.zero.accx
    ee.ld.128.usar.ip   q0, a3, 16
    loopgtz a4, .Lu_end
    ee.vld.128.ip       q1, a2, 16
    ee.ld.128.usar.ip   q2, a3, 16
    ee.src.q.qup        q4, q0, q2
    ee.vmulas.s8.accx   q4, q1
.Lu_end:
    nop
    nop
    rur.accx_0 a2
    retw.n
    .size   fq_dot16u, . - fq_dot16u
