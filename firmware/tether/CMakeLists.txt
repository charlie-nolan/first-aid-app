# Voice tether: a rough bench firmware for trying the voice flow with the PC as the brain.
# Hold TALK, the clip goes over USB to pc_bridge.py, which runs the speech model and the
# fill-in-the-blanks form and sends back a small JSON screen (form fields or a card title).
# Same partition table as the main firmware, so the library (fonts) already in flash stays.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../components)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(vr_tether)
