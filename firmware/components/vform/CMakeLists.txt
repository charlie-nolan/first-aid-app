# Fill-in-the-blanks form engine (C port of pipeline/voice_router/vr/intake.py + form.py).
# -ffp-contract=off: no fused multiply-add, so ranking scores round exactly like the Python's doubles.
idf_component_register(SRCS "vform.c" "vform_show.c" INCLUDE_DIRS ".")
target_compile_options(${COMPONENT_LIB} PRIVATE -ffp-contract=off -O2)
