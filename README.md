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
3. When an SNI application (like Dropbox) calls `RegisterStatusNotifierItem`,
   it creates a `GtkStatusIcon` in the X11 system tray using the app's icon
   and connects a `libdbusmenu-gtk3` context menu.
4. Watches for `NewIcon` / `NewStatus` / `NewTitle` signals and updates the
   tray icon live.

## Requirements

All packages are available in Fedora 43:

```
python3-gobject   (PyGObject / GI bindings)
python3-dbus      (dbus-python)
gtk3              (GtkStatusIcon)
libdbusmenu-gtk3  (context-menu rendering)
```

Install them if missing:

```bash
sudo dnf install python3-gobject python3-dbus gtk3 libdbusmenu-gtk3
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

## Limitations

- Uses `GtkStatusIcon`, which is deprecated in GTK 3 (still functional) and
  absent in GTK 4.  Works on X11; not tested under XWayland.
- Tested on Fedora 43 with GNOME and the X11 session.
- No multi-item icon ordering; icons appear in registration order.
