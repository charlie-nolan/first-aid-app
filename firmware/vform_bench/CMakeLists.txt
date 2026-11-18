# vform bench: runs a parity script through the C form engine ON THE BOARD and prints every SHOW
# frame plus timings over USB, for tools/parity_check.py --frames. Default data = the committed
# made-up fixture (components/vform/test). For the real trees (personal: never commit the outputs):
#   idf.py -B build -D VFORM_PACK=/path/real.fpk -D VFORM_SCRIPT=/path/script.txt build
# Flash the app slot only (0x10000): the library partition stays as it is.
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../components/vform ${CMAKE_CURRENT_LIST_DIR}/../components/asr)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(vform_bench)
