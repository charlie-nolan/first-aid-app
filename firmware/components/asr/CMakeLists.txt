# Speech engines (Kconfig "Speech recognizer engine"). DIY int8 = fasr_frontend + fasr_q8 + fasr_decode.
set(srcs "asr.c" "fasr_frontend.c" "fasr_q8.c" "fasr_decode.c")
if(IDF_TARGET STREQUAL "esp32s3")
    list(APPEND srcs "fasr_dot_s3.S")   # vector dot product (pattern from esp-nn, Apache 2.0: LICENSE-esp-nn.txt)
endif()
idf_component_register(SRCS ${srcs} INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_timer heap)
target_compile_options(${COMPONENT_LIB} PRIVATE -O2)
if(CONFIG_ASR_FE_FLOAT)
    target_compile_definitions(${COMPONENT_LIB} PRIVATE FE_FAST)   # float32 FFT (E1): not bit-exact with numpy
endif()
target_compile_options(${COMPONENT_LIB} PRIVATE -ffp-contract=off)
