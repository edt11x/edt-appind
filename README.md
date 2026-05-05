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
3. **sni-host's own panel window** — a small frameless always-on-top window
   with a dark background appears at the top-right corner of the primary
   monitor's work area.  It contains a black signal-bars icon for sni-host
   itself plus one slot per registered SNI app.
4. Left-click or right-click the sni-host icon for a menu showing version,
   PID, all registered items, and **Restart** / **Quit** actions.
5. When an SNI application (like Dropbox) calls `RegisterStatusNotifierItem`,
   a new icon slot is appended to the panel using the app's icon and a
   `libdbusmenu-gtk3` context menu.
6. Left-click an app icon calls `Activate`; right-click shows its dbusmenu.
7. Watches `NewIcon` / `NewStatus` / `NewTitle` signals and updates live.

> **Why not GtkStatusIcon?**  `GtkStatusIcon` requires a running X11 system
> tray (the `_NET_SYSTEM_TRAY_S0` EWMH selection).  Neither GNOME Shell nor
> default XFCE sessions provide one, so icons would silently disappear.
> sni-host's own panel window bypasses this entirely.

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

## Panel window and menu

sni-host opens a small frameless panel at the top-right corner of the primary
monitor, positioned inside the work area (respects any existing panel struts).

```
┌─────────────────────────┐
│ ▐█▌  ◉  ◉              │  ← sni-host icon + one slot per registered app
└─────────────────────────┘
  top-right of workarea
```

Right-click or left-click the **sni-host icon** (three signal bars):

```
sni-host  v1.1              ← bold header
AppIndicator / SNI host daemon
PID: 12345
─────────────────────────
Registered items: 1         ← live count
  ●  :1.234                 ← one line per registered bus name
─────────────────────────
Restart                     ← replaces process in-place (os.execv)
Quit
```

The icon tooltip shows the version and current item count.  Hovering any icon
dims it slightly to indicate it is clickable.

## Limitations

- X11 only.  Under a pure Wayland session `window.move()` is not honoured;
  the window will appear but may not be in the top-right corner.
  `gtk-layer-shell` (installed on Fedora 43) could fix this in future.
- Tested on Fedora 43 with XFCE, X11 session.
- Icon ordering follows registration order; no drag-to-reorder yet.
