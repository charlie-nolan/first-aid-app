cmake_minimum_required(VERSION 3.16)

# ESP-IDF 5.3's component manager ignores $CONFIG{...} in idf_component.yml "if" rules, so
# main/idf_component.yml keys ESP-SR off the LIB_VOICE environment variable instead. Work
# it out here, before project(), from the build's sdkconfig (menuconfig changes) or else
# the SDKCONFIG_DEFAULTS files, last one wins.
set(_voice 0)
set(_cfgs ${SDKCONFIG_DEFAULTS})
if(SDKCONFIG AND EXISTS "${CMAKE_SOURCE_DIR}/${SDKCONFIG}")
    set(_cfgs "${CMAKE_SOURCE_DIR}/${SDKCONFIG}")
elseif(SDKCONFIG AND EXISTS "${SDKCONFIG}")
    set(_cfgs "${SDKCONFIG}")
endif()
foreach(_f ${_cfgs})
    if(NOT IS_ABSOLUTE "${_f}")
        set(_f "${CMAKE_SOURCE_DIR}/${_f}")
    endif()
    if(EXISTS "${_f}")
        file(STRINGS "${_f}" _l REGEX "^(CONFIG_LIB_VOICE=y|# CONFIG_LIB_VOICE is not set|CONFIG_LIB_VOICE=n)")
        if(_l MATCHES "CONFIG_LIB_VOICE=y")
            set(_voice 1)
        elseif(_l)
            set(_voice 0)
        endif()
    endif()
endforeach()
set(ENV{LIB_VOICE} ${_voice})
message(STATUS "Librarian voice (ESP-SR): ${_voice}")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(librarian)
