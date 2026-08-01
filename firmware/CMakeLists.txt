cmake_minimum_required(VERSION 3.16)
# App version shown in the boot log ("App version:"). Without this, ESP-IDF
# falls back to `git describe`, which advertises the ancient
# pre-m4-provisioning milestone tag. Keep in sync with FW_VERSION in
# main/defaults.h when bumping releases.
set(PROJECT_VER "0.0.1")
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Names the app binary (build/immpakt.bin).
project(immpakt)
