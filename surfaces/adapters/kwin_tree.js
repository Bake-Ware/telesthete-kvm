// Loaded only for the lifetime of the origin; __SERVICE__ is replaced with
// the process's private D-Bus service name before loading.
function rect(r) { return {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}; }
function publish() {
    const rows = workspace.windowList().filter(w => !w.deleted && !w.desktopWindow && !w.dock).map(w => ({
        uuid: String(w.internalId), title: w.caption, app_id: w.desktopFileName || w.resourceClass,
        parent: w.transientFor ? String(w.transientFor.internalId) : null,
        role: w.tooltip ? "tooltip" : (w.popupMenu || w.dropdownMenu ? "popup" : (w.dialog ? "dialog" : "toplevel")),
        client: rect(w.clientGeometry), frame: rect(w.frameGeometry),
        modal: w.modal, focused: w === workspace.activeWindow, minimized: w.minimized,
        decorated: !w.noBorder, urgent: w.demandsAttention,
        size_min: {w: w.minSize.width, h: w.minSize.height},
        size_max: {w: w.maxSize.width, h: w.maxSize.height}
    }));
    callDBus("__SERVICE__", "/Tree", "org.bake.SpatialTree", "Update", JSON.stringify(rows));
}
function watch(w) {
    for (const name of ["captionChanged", "frameGeometryChanged", "minimizedChanged", "demandsAttentionChanged", "activeChanged"]) {
        if (w[name]) w[name].connect(publish);
    }
}
workspace.windowList().forEach(watch);
workspace.windowAdded.connect(w => { watch(w); publish(); });
workspace.windowRemoved.connect(publish);
workspace.windowActivated.connect(publish);
publish();
