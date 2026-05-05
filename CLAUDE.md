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
- **No `GtkStatusIcon`**: the original design used `GtkStatusIcon` but it
  requires `_NET_SYSTEM_TRAY_S0` to be owned by a running tray.  Neither GNOME
  Shell nor default XFCE provides one, so icons silently vanished.  Replaced
  with a self-owned frameless `Gtk.Window` (`TrayWindow`) that positions itself
  at the top-right of the primary monitor workarea via `Gdk.Monitor.get_workarea()`.
- **`TrayWindow`** is a frameless, always-on-top, skip-taskbar window with a
  dark CSS background and an orange border (`#c07820`) for visibility.  Each
  icon is a `Gtk.EventBox` + `Gtk.Image` with opacity-based hover feedback.
  `size-allocate` → `idle_add` keeps it right-aligned as slots are added/removed.
- **Qubes OS quirk**: `Gdk.WindowTypeHint.DOCK` must be set before the window
  is realised; `gdkwin.set_skip_taskbar_hint(True)` is called again in
  `_on_map_event` on the mapped `GdkWindow` because qubes-gui-agent does not
  forward window-type hints set pre-map to dom0, but it does forward WM-state
  changes on a mapped window.  A `GLib.timeout_add(500, _reposition)` fires
  500 ms after map to override dom0 XFWM4's smart-placement (which ignores the
  initial `move()`).  A `configure-event` handler logs the actual position the
  WM assigns.  The panel uses mouse-pointer monitor detection so it lands on
  the active monitor in multi-monitor setups.  Minimum panel width is 80 px
  (`_PANEL_MIN_W`) to prevent the window from becoming an invisible sliver.
- **`HostTrayIcon`** owns the first slot (signal-bars icon); `TrayItem` owns
  subsequent slots (one per registered SNI app).
- **`popup_at_pointer(event)`** is used for all menus (replaces the old
  `StatusIcon.position_menu` function which only works with GtkStatusIcon).
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
- Window positioning uses `Gdk.Monitor.get_workarea()` (respects panel struts)
  and `Gtk.Window.move()` — the latter is ignored on Wayland compositors.
  Future fix: use `gtk-layer-shell` for Wayland support.
- Tested desktop: XFCE, X11, Fedora 43 inside Qubes OS VM.  Confirmed
  `_NET_SYSTEM_TRAY_S0` was absent, which is why `GtkStatusIcon` was silent.
- XFCE does not reliably activate `graphical-session.target`.  The service
  uses `PassEnvironment=DISPLAY XAUTHORITY` to forward display vars.  The
  XDG autostart `.desktop` file is installed alongside the service as the more
  reliable autostart mechanism for XFCE.  If both fire, the second instance
  exits immediately on the `NameExistsException` guard.
- **Qubes window positioning**: dom0 XFWM4 applies smart-placement overriding
  the initial `move()` call.  The 500 ms delayed `_reposition` call overrides
  this.  The panel should appear at the top-right of the active monitor.  If
  it doesn't, run `sni-host.py -v` and look for the "Panel placed by WM at"
  log line to find the actual coordinates.  Try increasing the timeout if
  500 ms is not enough for your Qubes dom0 to settle.

## Files

```
sni-host.py       Main daemon
sni-host.service  systemd user unit (PassEnvironment=DISPLAY XAUTHORITY)
sni-host.desktop  XDG autostart entry — reliable trigger for XFCE sessions
install.sh        Installs all three + enables service
README.md         User-facing docs
CLAUDE.md         This file
```

## Tested environment

- Fedora 43, GNOME, X11 session
- Python 3.14
- Dropbox 2026.03.20 (nautilus-dropbox)
