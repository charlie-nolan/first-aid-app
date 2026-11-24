# asr bench: decodes the speech export's golden clips ON THE BOARD with the DIY int8 engine and
# prints the words, the reject and the time per stage. The model, lexicon and calibration come from
# the cart in the library partition (ASRM/LEXI/ASRC chunks), so flash a voice cart first.
#   idf.py -B build -D ASR_CLIPS=<dir of NN.audio files from golden2raw.py> build
cmake_minimum_required(VERSION 3.16)
set(EXTRA_COMPONENT_DIRS ${CMAKE_CURRENT_LIST_DIR}/../components/asr ${CMAKE_CURRENT_LIST_DIR}/../components/vform)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(asr_bench)
