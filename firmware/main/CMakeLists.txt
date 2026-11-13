set(srcs main.c ui.c sleep_art.c epd.c buttons.c lights.c power.c voice.c psram_test.c)
set(PHRASES ${CMAKE_CURRENT_LIST_DIR}/../library/phrases.txt)
set(embed "")
if(CONFIG_LIB_VOICE)
    set(embed ${PHRASES})
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "."
                       EMBED_TXTFILES ${embed}
                       PRIV_REQUIRES cart esp_driver_spi esp_driver_gpio esp_driver_ledc esp_driver_i2s
                                     esp_adc esp_timer esp_partition spi_flash nvs_flash esp_driver_tsens)

# `idf.py flash` also writes the cartridge into the "library" partition.
set(LIB_BIN ${CMAKE_CURRENT_LIST_DIR}/../library/library.bin)
if(EXISTS ${LIB_BIN})
    esptool_py_flash_to_partition(flash "library" "${LIB_BIN}")
else()
    message(WARNING "library/library.bin not found: run tools/make_test_library.py")
endif()
