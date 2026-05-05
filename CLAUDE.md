# sni-host — project context for Claude

## What this is

A Python daemon that provides `org.kde.StatusNotifierWatcher` on the session
DBus so that AppIndicator/SNI applications (primarily Dropbox) can show tray
icons on Fedora 43 GNOME with an X11 session, where no watcher is present by
default.

## Key decisions

- **GTK 3 / GtkStatusIcon** chosen over GTK 4 because `GtkStatusIcon` (the
  X11 system-tray embedding widget) was removed from GTK 4.  The program
  therefore depends on `gtk3`, not `gtk4`.
- **`libdbusmenu-gtk3` / `DbusmenuGtk3`** used for context menus because
  Dropbox exposes its menu via the `com.canonical.dbusmenu` protocol, not
  plain `ContextMenu()`.
- **`python3-dbus` (dbus-python)** used instead of `dasbus` or `pydbus`
  because it is already installed on Fedora 43 and has stable `sender_keyword`
  support needed for SNI registration.
- Icon pixmaps from SNI items arrive as ARGB (network byte order); converted
  to RGBA before passing to `GdkPixbuf`.
- The watcher exits with status 1 if `org.kde.StatusNotifierWatcher` is
  already owned (prevents duplicate instances).
- **sni-host's own tray icon** is drawn with `cairo` (black bg, three white
  rounded signal bars) via `Gdk.pixbuf_get_from_surface`.  Uses `HostTrayIcon`
  class with a dynamically-built `Gtk.Menu` on each right/left-click.
- **Restart** is implemented as `os.execv(sys.executable, [sys.executable] + sys.argv)`,
  replacing the process in-place while preserving all CLI arguments.

## Known issues / next steps

- `GtkStatusIcon` is deprecated since GTK 3.14 and will eventually be removed
  from GTK 3.  A future migration path is a Wayland-native panel widget using
  `gtk-layer-shell` (already installed) + a custom rendering window.
- Not tested under XWayland.  Under a pure Wayland session the X11 system tray
  embedding will not work; a layer-shell window would be needed instead.
- No support yet for `AttentionIcon` (blinking/urgent state).
- Icon ordering in the tray is determined by registration order; no way to
  reorder yet.
- The `--verbose` / `-v` flag enables `DEBUG` logging for all modules; could
  be scoped more narrowly in future.
- The host tray menu rebuilds from scratch on every open; acceptable for the
  expected item count but could be optimized with incremental updates.

## Files

```
sni-host.py       Main daemon
sni-host.service  systemd user unit
install.sh        Installs to ~/.local/bin + systemd
README.md         User-facing docs
CLAUDE.md         This file
```

## Tested environment

- Fedora 43, GNOME, X11 session
- Python 3.14
- Dropbox 2026.03.20 (nautilus-dropbox)
