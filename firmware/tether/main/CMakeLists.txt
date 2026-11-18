set(embed "")
if(CONFIG_TETHER_BOARD_BRAIN)
    # The form pack (tools/formpack.py in components/vform). Real trees are personal: build with
    #   idf.py -B build-brain -D TETHER_FPK=/path/real.fpk ... ; the default is the made-up fixture.
    if(NOT TETHER_FPK)
        set(TETHER_FPK ${CMAKE_CURRENT_LIST_DIR}/../../components/vform/test/fixture.fpk)
    endif()
    configure_file(${TETHER_FPK} ${CMAKE_CURRENT_BINARY_DIR}/form.fpk COPYONLY)
    set(embed ${CMAKE_CURRENT_BINARY_DIR}/form.fpk)
endif()
idf_component_register(SRCS tether.c ../../main/epd.c
                       INCLUDE_DIRS . ../../main
                       EMBED_FILES ${embed}
                       PRIV_REQUIRES cart json vform esp_driver_spi esp_driver_gpio esp_driver_i2s esp_driver_usb_serial_jtag
                                     esp_partition spi_flash nvs_flash mbedtls esp_timer)
