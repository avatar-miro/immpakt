cmake_minimum_required(VERSION 3.16)
# App version shown in the boot log ("App version:"). Without this, ESP-IDF
# falls back to `git describe`, which advertises the ancient
# pre-m4-provisioning milestone tag. Keep in sync with FW_VERSION in
# main/defaults.h when bumping releases.
# Fallback for local builds. CI stamps the real version from the git tag with
# -DPROJECT_VER=... and -DFW_VERSION=..., so releases carry the tag's version
# without any file in the repo having to be rewritten (which would make the
# release change-gate think the firmware changed on every release).
if(NOT DEFINED PROJECT_VER)
  set(PROJECT_VER "0.0.3-dev")
endif()
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
# Names the app binary (build/immpakt.bin).
project(immpakt)
