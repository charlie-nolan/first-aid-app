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
