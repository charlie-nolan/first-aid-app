# Speech engines (Kconfig "Speech recognizer engine"). DIY int8 = fasr_frontend + fasr_q8 + fasr_decode.
idf_component_register(SRCS "asr.c" "fasr_frontend.c" "fasr_q8.c" "fasr_decode.c" INCLUDE_DIRS "."
                       PRIV_REQUIRES esp_timer)
target_compile_options(${COMPONENT_LIB} PRIVATE -O2)
