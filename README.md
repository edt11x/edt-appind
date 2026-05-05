# edt-appind

StatusNotifierItem host daemon for Fedora/GNOME on X11.

Provides `org.kde.StatusNotifierWatcher` on the session DBus so that
applications using the StatusNotifierItem (SNI) / AppIndicator protocol —
most notably Dropbox — can display tray icons in the X11 system tray.

Without this (or a GNOME Shell extension), Dropbox shows:
> *"Dropbox requires App indicator support to display the Dropbox tray icon.
> Dropbox will continue to run in the background."*

## How it works

1. Registers the well-known DBus name `org.kde.StatusNotifierWatcher`.
2. Registers itself as `org.kde.StatusNotifierHost-<pid>`.
3. **sni-host's own tray icon** — a black icon with three white signal bars
   appears in the system tray.  Left-click or right-click opens a menu showing
   version, PID, all registered items, and **Restart** / **Quit** actions.
4. When an SNI application (like Dropbox) calls `RegisterStatusNotifierItem`,
   it creates a `GtkStatusIcon` in the X11 system tray using the app's icon
   and connects a `libdbusmenu-gtk3` context menu.
5. Watches for `NewIcon` / `NewStatus` / `NewTitle` signals and updates the
   tray icon live.

## Requirements

All packages are available in Fedora 43:

```
python3-gobject   (PyGObject / GI bindings)
python3-dbus      (dbus-python)
gtk3              (GtkStatusIcon)
libdbusmenu-gtk3  (context-menu rendering)
python3-cairo     (host icon drawing)
```

Install them if missing:

```bash
sudo dnf install python3-gobject python3-dbus gtk3 libdbusmenu-gtk3 python3-cairo
```

## Installation

```bash
bash install.sh
```

This copies `sni-host.py` to `~/.local/bin/`, installs `sni-host.service`
as a systemd user unit, and enables + starts it immediately.

After installation restart Dropbox (or any SNI app) to pick up the watcher:

```bash
dropbox stop && dropbox start
```

## Manual / one-shot run

```bash
./sni-host.py          # normal
./sni-host.py -v       # verbose / debug logging
./sni-host.py --help   # usage
```

## Project files

| File | Purpose |
|---|---|
| `sni-host.py` | Main daemon (StatusNotifierWatcher DBus service + GTK tray host) |
| `sni-host.service` | systemd user service unit |
| `install.sh` | Installs the above and enables the service |

## sni-host tray menu

Right-click (or left-click) the black signal-bars icon to open the menu:

```
sni-host  v1.0              ← bold header
AppIndicator / SNI host daemon
PID: 12345
─────────────────────────
Registered items: 1         ← live count
  ●  :1.234                 ← one line per registered bus name
─────────────────────────
Restart                     ← replaces process in-place (os.execv)
Quit
```

The tooltip on the icon shows the version and current item count.

## Limitations

- Uses `GtkStatusIcon`, which is deprecated in GTK 3 (still functional) and
  absent in GTK 4.  Works on X11; not tested under XWayland.
- Tested on Fedora 43 with GNOME and the X11 session.
- No multi-item icon ordering; icons appear in registration order.
