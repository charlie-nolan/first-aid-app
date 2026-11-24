set(T ${CMAKE_CURRENT_LIST_DIR}/../../components/vform/test)
if(NOT VFORM_PACK)
    set(VFORM_PACK ${T}/fixture.fpk)
endif()
if(NOT VFORM_SCRIPT)
    set(VFORM_SCRIPT ${T}/fixture_script.txt)
endif()
# EMBED_FILES names the symbols after the file name: copy to fixed names first.
configure_file(${VFORM_PACK} ${CMAKE_CURRENT_BINARY_DIR}/pack.fpk COPYONLY)
configure_file(${VFORM_SCRIPT} ${CMAKE_CURRENT_BINARY_DIR}/script.txt COPYONLY)
idf_component_register(SRCS bench.c
                       PRIV_REQUIRES vform asr esp_timer esp_system
                       EMBED_FILES ${CMAKE_CURRENT_BINARY_DIR}/pack.fpk
                       EMBED_TXTFILES ${CMAKE_CURRENT_BINARY_DIR}/script.txt)
