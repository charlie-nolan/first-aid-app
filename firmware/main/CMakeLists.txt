set(srcs main.c ui.c sleep_art.c epd.c buttons.c lights.c power.c voice.c voice_diy.c psram_test.c)
set(PHRASES ${CMAKE_CURRENT_LIST_DIR}/../library/phrases.txt)
set(embed "")
if(CONFIG_LIB_VOICE)
    set(embed ${PHRASES})
endif()
idf_component_register(SRCS ${srcs}
                       INCLUDE_DIRS "."
                       EMBED_TXTFILES ${embed}
                       PRIV_REQUIRES cart asr vform json esp_driver_spi esp_driver_gpio esp_driver_ledc esp_driver_i2s
                                     esp_adc esp_timer esp_partition spi_flash nvs_flash esp_driver_tsens)

# `idf.py flash` also writes the cartridge into the "library" partition.
# Default is the committed public cart; pass -D LIBRARY_BIN=<path> (build_plus.sh does) to flash
# another one, e.g. the gitignored personal library/library_personal.bin.
set(LIBRARY_BIN "" CACHE FILEPATH "cartridge to flash into the library partition (empty = library/library.bin)")
if(LIBRARY_BIN)
    set(LIB_BIN ${LIBRARY_BIN})
else()
    set(LIB_BIN ${CMAKE_CURRENT_LIST_DIR}/../library/library.bin)
endif()
message(STATUS "library partition <- ${LIB_BIN}")
if(EXISTS ${LIB_BIN})
    esptool_py_flash_to_partition(flash "library" "${LIB_BIN}")
else()
    message(WARNING "${LIB_BIN} not found: run tools/make_test_library.py")
endif()
