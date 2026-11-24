# FASR recorder: a separate, tiny firmware for collecting training audio through the
# device's own PDM mic. It shows a sentence on the e-ink, you hold TALK and read it, and
# the clip goes over USB to `python -m fasr devrec` on the PC. It uses the same partition
# table as the main firmware, so the library (fonts) already in flash stays put.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../components)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(fasr_recorder)
