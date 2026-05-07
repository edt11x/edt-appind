# edt-appind

StatusNotifierItem host daemon and notification capture for Fedora/GNOME on X11.

Provides `org.kde.StatusNotifierWatcher` on the session DBus so that
applications using the StatusNotifierItem (SNI) / AppIndicator protocol —
most notably Dropbox — can display tray icons in the X11 system tray.

Also claims `org.freedesktop.Notifications` to capture desktop notifications
in a dismissible panel.

Without this (or a GNOME Shell extension), Dropbox shows:
> *"Dropbox requires App indicator support to display the Dropbox tray icon.
> Dropbox will continue to run in the background."*

## How it works

1. Registers the well-known DBus name `org.kde.StatusNotifierWatcher`.
2. Registers itself as `org.kde.StatusNotifierHost-<pid>`.
3. **Tray panel** — a small frameless always-on-top window appears at the
   top-right corner of the primary monitor's work area.  It contains a
   signal-bars icon for sni-host itself plus one slot per registered SNI app.
4. **Left-click** the sni-host icon to toggle the notification panel.
   **Right-click** for a menu showing version, PID, registered items, Restart, Quit.
5. **Notification panel** — a second frameless window appears directly below
   the tray when a notification arrives.  Each row shows the app name, summary,
   and body with a ✕ dismiss button.  A "Clear all" button in the header
   dismisses everything at once.
6. When an SNI application (like Dropbox) calls `RegisterStatusNotifierItem`,
   a new icon slot is appended to the tray using the app's icon and a
   `libdbusmenu-gtk3` context menu.
7. Left-click an app icon calls `Activate`; right-click shows its dbusmenu.
8. Watches `NewIcon` / `NewStatus` / `NewTitle` signals and updates live.

> **Why not GtkStatusIcon?**  `GtkStatusIcon` requires a running X11 system
> tray (the `_NET_SYSTEM_TRAY_S0` EWMH selection).  Neither GNOME Shell nor
> default XFCE sessions provide one, so icons would silently disappear.
> sni-host's own panel window bypasses this entirely.

## Requirements

All packages are available in Fedora 43:

```
python3-gobject   (PyGObject / GI bindings)
python3-dbus      (dbus-python)
gtk3              (GTK 3 widgets)
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

This installs three things:

| Destination | Purpose |
|---|---|
| `~/.local/bin/sni-host.py` | The executable |
| `~/.config/systemd/user/sni-host.service` | systemd user unit (enabled immediately) |
| `~/.config/autostart/sni-host.desktop` | XDG autostart fallback for XFCE and other non-systemd sessions |

Both the systemd service and the XDG autostart entry are installed.  If both
fire at login, the second launch exits immediately because
`org.kde.StatusNotifierWatcher` is already owned — no harm done.

After installation restart Dropbox (or any SNI app) to pick up the watcher:

```bash
dropbox stop && dropbox start
```

### Will the panel window appear when running as a service?

Yes — `DISPLAY` and `DBUS_SESSION_BUS_ADDRESS` are present in the systemd
user environment.  The service uses `PassEnvironment=DISPLAY XAUTHORITY` to
forward display credentials explicitly.

> **XFCE note**: XFCE does not reliably activate `graphical-session.target`,
> so the systemd service may start before the display is ready on some
> machines.  The XDG autostart entry (`sni-host.desktop`) is the more
> reliable trigger for XFCE sessions.

## Updating an existing installation

If sni-host is already installed, copy the updated script and restart the service:

```bash
install -Dm755 sni-host.py ~/.local/bin/sni-host.py
systemctl --user restart sni-host.service
```

## Manual / one-shot run

```bash
./sni-host.py          # normal
./sni-host.py -v       # verbose / debug logging
./sni-host.py --help   # usage
```

Send a test notification:

```bash
notify-send "Test" "This is a test notification body"
```

## Project files

| File | Purpose |
|---|---|
| `sni-host.py` | Main daemon (SNI watcher + notification daemon + GTK panels) |
| `sni-host.service` | systemd user service unit |
| `sni-host.desktop` | XDG autostart entry (reliable fallback for XFCE) |
| `install.sh` | Installs all of the above |

## Tray panel

sni-host opens a small frameless panel at the top-right corner of the primary
monitor, positioned inside the work area (respects any existing panel struts).

```
┌─────────────────────────┐
│ ▐█▌  ◉  ◉              │  ← sni-host icon + one slot per registered app
└─────────────────────────┘
  top-right of workarea
```

- **Left-click** the sni-host icon (signal bars) → toggle notification panel
- **Right-click** the sni-host icon → menu

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

Hovering any icon dims it slightly to indicate it is clickable.

## Notification panel

When sni-host claims `org.freedesktop.Notifications` (i.e. no other
notification daemon is running), incoming notifications appear in a second
frameless panel directly below the tray:

```
┌──────────────────────────────────────┐
│ Notifications (2)        [Clear all] │
├──────────────────────────────────────┤
│ [icon] AppName                    [✕]│
│        Summary text                  │
│        Body text that may wrap…      │
├──────────────────────────────────────┤
│ [icon] AnotherApp                 [✕]│
│        Another summary               │
└──────────────────────────────────────┘
```

- Notifications accumulate until dismissed (✕) or cleared (Clear all).
- `expire_timeout > 0` auto-dismisses after the specified milliseconds.
- `NotificationClosed` signals are sent back to callers on dismiss.
- If another daemon (dunst, notify-osd, GNOME) already owns the bus name,
  a warning is logged and the notification panel is simply not created.

To verify sni-host owns the notification bus:

```bash
dbus-send --session --print-reply --dest=org.freedesktop.DBus \
  /org/freedesktop/DBus org.freedesktop.DBus.GetNameOwner \
  string:org.freedesktop.Notifications
```

## Limitations

- X11 only.  Under a pure Wayland session `window.move()` is not honoured;
  the window will appear but may not be in the top-right corner.
  `gtk-layer-shell` (installed on Fedora 43) could fix this in future.
- Tested on Fedora 43 with XFCE, X11 session inside Qubes OS.
- Icon ordering follows registration order; no drag-to-reorder yet.
- No support for notification actions (buttons); only dismiss is implemented.
