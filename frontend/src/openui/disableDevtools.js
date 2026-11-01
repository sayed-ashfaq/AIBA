// In development, @openuidev/react-lang auto-mounts an inspector widget by fetching a script from
// cdn.jsdelivr.net, and adds a floating button to every page. Its bootstrap skips itself when this
// global is already set, so setting it first keeps third-party code off the page. Production builds
// never include the widget, so this only changes `npm run dev`.
//
// Must be imported before anything that imports @openuidev/react-lang. If a future release renames
// the symbol, the only effect is that the widget comes back.
globalThis[Symbol.for("openui.devtools.autoMount")] = true;
