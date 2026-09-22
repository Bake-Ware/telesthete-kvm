cmake_minimum_required(VERSION 3.16)
project(telesthete_kwin_capture C)
find_package(PkgConfig REQUIRED)
pkg_check_modules(WAYLAND REQUIRED IMPORTED_TARGET wayland-client)
find_program(WAYLAND_SCANNER wayland-scanner REQUIRED)
find_file(SCREENCAST_XML zkde-screencast-unstable-v1.xml
          PATHS /usr/share/plasma-wayland-protocols REQUIRED)
add_custom_command(OUTPUT screencast-client.h
    COMMAND ${WAYLAND_SCANNER} client-header ${SCREENCAST_XML} screencast-client.h
    DEPENDS ${SCREENCAST_XML})
add_custom_command(OUTPUT screencast-protocol.c
    COMMAND ${WAYLAND_SCANNER} private-code ${SCREENCAST_XML} screencast-protocol.c
    DEPENDS ${SCREENCAST_XML})
add_executable(telesthete-kwin-capture screencast.c screencast-client.h screencast-protocol.c)
target_include_directories(telesthete-kwin-capture PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(telesthete-kwin-capture PRIVATE PkgConfig::WAYLAND)
target_compile_options(telesthete-kwin-capture PRIVATE -Wall -Wextra -Werror)
configure_file(org.bake.TelestheteSpatialCapture.desktop.in
               org.bake.TelestheteSpatialCapture.desktop @ONLY)
