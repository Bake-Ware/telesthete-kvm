/* KWin PipeWire stream lifetime bridge. Protocol headers are generated at build.
 * Usage: telesthete-kwin-capture WINDOW_UUID
 * stdout: one decimal PipeWire node ID, stderr: diagnostics.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <wayland-client.h>
#include "screencast-client.h"

static struct zkde_screencast_unstable_v1 *manager;
static int done;
static int failed;

static void global(void *data, struct wl_registry *registry, uint32_t name,
                   const char *interface, uint32_t version) {
    (void)data;
    if (!strcmp(interface, "zkde_screencast_unstable_v1")) {
        /* v5 provides node IDs and avoids a dependency on the newer serial event. */
        manager = wl_registry_bind(registry, name,
                    &zkde_screencast_unstable_v1_interface, version < 5 ? version : 5);
    }
}
static void removed(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = {global, removed};
static void closed(void *data, struct zkde_screencast_stream_unstable_v1 *stream) {
    (void)data; (void)stream; done = 1;
}
static void created(void *data, struct zkde_screencast_stream_unstable_v1 *stream,
                    uint32_t node) {
    (void)data; (void)stream;
    printf("%u\n", node); fflush(stdout);
}
static void failure(void *data, struct zkde_screencast_stream_unstable_v1 *stream,
                    const char *error) {
    (void)data; (void)stream;
    fprintf(stderr, "KWin capture: %s\n", error); failed = done = 1;
}
static const struct zkde_screencast_stream_unstable_v1_listener stream_listener = {
    .closed = closed, .created = created, .failed = failure
};
int main(int argc, char **argv) {
    if (argc != 2) { fprintf(stderr, "Usage: %s WINDOW_UUID\n", argv[0]); return 2; }
    struct wl_display *display = wl_display_connect(NULL);
    if (!display) { fprintf(stderr, "Cannot connect to Wayland display\n"); return 1; }
    struct wl_registry *registry = wl_display_get_registry(display);
    wl_registry_add_listener(registry, &registry_listener, NULL);
    if (wl_display_roundtrip(display) < 0 || !manager) {
        fprintf(stderr, "KWin screencast interface unavailable to this client\n");
        wl_display_disconnect(display); return 1;
    }
    struct zkde_screencast_stream_unstable_v1 *stream =
        zkde_screencast_unstable_v1_stream_window(manager, argv[1], 1);
    zkde_screencast_stream_unstable_v1_add_listener(stream, &stream_listener, NULL);
    while (!done && wl_display_dispatch(display) >= 0) {}
    zkde_screencast_stream_unstable_v1_close(stream);
    zkde_screencast_unstable_v1_destroy(manager);
    wl_registry_destroy(registry);
    wl_display_disconnect(display);
    return failed;
}
