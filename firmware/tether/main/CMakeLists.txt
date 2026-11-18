idf_component_register(SRCS tether.c ../../main/epd.c
                       INCLUDE_DIRS . ../../main
                       PRIV_REQUIRES cart json esp_driver_spi esp_driver_gpio esp_driver_i2s esp_driver_usb_serial_jtag
                                     esp_partition spi_flash nvs_flash mbedtls esp_timer)
