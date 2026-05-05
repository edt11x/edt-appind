#!/usr/bin/env python3
"""
sni-host: StatusNotifierItem host for X11/GNOME
Provides org.kde.StatusNotifierWatcher on DBus and renders tray icons via GtkStatusIcon.
"""

import sys
import os
import logging
import argparse
import signal as _signal

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('Dbusmenu', '0.4')
gi.require_version('DbusmenuGtk3', '0.4')
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Dbusmenu, DbusmenuGtk3
import cairo

import dbus
import dbus.service
import dbus.mainloop.glib

VERSION = '1.0'

log = logging.getLogger('sni-host')


def _parse_args():
    parser = argparse.ArgumentParser(
        prog='sni-host',
        description=(
            'StatusNotifierItem host for X11/GNOME.\n\n'
            'Registers org.kde.StatusNotifierWatcher on the session DBus so that\n'
            'applications using the StatusNotifierItem protocol (e.g. Dropbox) can\n'
            'display tray icons in the X11 system tray.\n\n'
            'sni-host itself appears in the tray with a black icon and a right-click\n'
            'menu showing registered items, a Restart option, and a Quit option.\n\n'
            'Intended to run as a background daemon, typically via a systemd user\n'
            'service (see sni-host.service).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '-v', '--verbose', '--debug',
        action='store_true',
        dest='verbose',
        help='enable debug logging',
    )
    return parser.parse_args()


WATCHER_BUS_NAME  = 'org.kde.StatusNotifierWatcher'
WATCHER_OBJ_PATH  = '/StatusNotifierWatcher'
WATCHER_IFACE     = 'org.kde.StatusNotifierWatcher'

ITEM_IFACE        = 'org.kde.StatusNotifierItem'
ITEM_DEFAULT_PATH = '/StatusNotifierItem'

HOST_BUS_PREFIX   = 'org.kde.StatusNotifierHost'

PROPS_IFACE       = 'org.freedesktop.DBus.Properties'

ICON_SIZE         = 22  # pixels


# ---------------------------------------------------------------------------
# Host icon drawing
# ---------------------------------------------------------------------------

def _make_host_pixbuf(size=ICON_SIZE) -> GdkPixbuf.Pixbuf:
    """Draw the sni-host tray icon: black background, three white signal bars."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Black background
    ctx.set_source_rgb(0, 0, 0)
    ctx.rectangle(0, 0, size, size)
    ctx.fill()

    # Three white vertical signal bars, bottom-aligned, rounded caps
    ctx.set_source_rgb(1, 1, 1)
    bar_w   = max(2, size // 8)
    gap     = max(1, size // 11)
    total_w = 3 * bar_w + 2 * gap
    x0      = (size - total_w) / 2
    bottom  = size - 2
    fracs   = [0.35, 0.60, 0.85]  # bar heights as fraction of icon size

    for i, frac in enumerate(fracs):
        h  = size * frac
        bx = x0 + i * (bar_w + gap)
        by = bottom - h
        r  = bar_w / 2
        # Rounded rectangle
        ctx.new_sub_path()
        ctx.arc(bx + r,          by + r,     r, -3.14159, -1.5708)
        ctx.arc(bx + bar_w - r,  by + r,     r, -1.5708,  0)
        ctx.arc(bx + bar_w - r,  by + h - r, r,  0,       1.5708)
        ctx.arc(bx + r,          by + h - r, r,  1.5708,  3.14159)
        ctx.close_path()
        ctx.fill()

    pb = Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)
    return pb


# ---------------------------------------------------------------------------
# Host tray icon (sni-host's own icon in the system tray)
# ---------------------------------------------------------------------------

class HostTrayIcon:
    """sni-host's own GtkStatusIcon — black signal-bars icon, right-click menu."""

    def __init__(self, app: 'Application'):
        self._app = app
        self._pixbuf = _make_host_pixbuf()

        self._icon = Gtk.StatusIcon()
        self._icon.set_from_pixbuf(self._pixbuf)
        self._icon.set_title('sni-host')
        self._icon.set_tooltip_text(f'sni-host v{VERSION} — AppIndicator host')
        self._icon.connect('popup-menu', self._on_popup_menu)
        self._icon.connect('activate',   self._on_activate)
        self._icon.set_visible(True)

    def refresh_tooltip(self):
        n = self._app.item_count()
        tip = f'sni-host v{VERSION}\n{n} registered item{"s" if n != 1 else ""}'
        self._icon.set_tooltip_text(tip)

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        def _label(text, bold=False, sensitive=False):
            item = Gtk.MenuItem()
            label = Gtk.Label(xalign=0)
            if bold:
                label.set_markup(f'<b>{GLib.markup_escape_text(text)}</b>')
            else:
                label.set_text(text)
            item.add(label)
            item.set_sensitive(sensitive)
            return item

        menu.append(_label(f'sni-host  v{VERSION}', bold=True))
        menu.append(_label('AppIndicator / SNI host daemon'))
        menu.append(_label(f'PID: {os.getpid()}'))
        menu.append(Gtk.SeparatorMenuItem())

        items = self._app.registered_item_names()
        count_item = _label(
            f'Registered items: {len(items)}',
            bold=True,
        )
        menu.append(count_item)

        for name in items:
            row = _label(f'  ●  {name}')  # ● indented
            menu.append(row)

        menu.append(Gtk.SeparatorMenuItem())

        restart_item = Gtk.MenuItem(label='Restart')
        restart_item.connect('activate', self._on_restart)
        menu.append(restart_item)

        quit_item = Gtk.MenuItem(label='Quit')
        quit_item.connect('activate', self._on_quit)
        menu.append(quit_item)

        menu.show_all()
        return menu

    def _on_activate(self, icon):
        # Left-click also opens the menu
        menu = self._build_menu()
        menu.popup(None, None,
                   Gtk.StatusIcon.position_menu,
                   icon, 1, Gtk.get_current_event_time())

    def _on_popup_menu(self, icon, button, time):
        menu = self._build_menu()
        menu.popup(None, None,
                   Gtk.StatusIcon.position_menu,
                   icon, button, time)

    def _on_restart(self, _item):
        log.info('Restarting…')
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def _on_quit(self, _item):
        log.info('Quit requested from tray menu')
        self._app.quit()

    def destroy(self):
        self._icon.set_visible(False)


# ---------------------------------------------------------------------------
# DBus watcher service
# ---------------------------------------------------------------------------

class StatusNotifierWatcher(dbus.service.Object):
    """Implements org.kde.StatusNotifierWatcher."""

    def __init__(self, bus, app):
        super().__init__(bus, WATCHER_OBJ_PATH)
        self._bus = bus
        self._app = app
        self._items: dict[str, tuple[str, str]] = {}
        self._hosts: list[str] = []
        self._host_registered = False

    # --- methods ---

    @dbus.service.method(WATCHER_IFACE, in_signature='s', out_signature='',
                         sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        if service.startswith('/'):
            obj_path = service
            bus_name = sender
        else:
            bus_name = service
            obj_path = ITEM_DEFAULT_PATH

        log.info('RegisterStatusNotifierItem: bus=%s path=%s', bus_name, obj_path)

        if bus_name not in self._items:
            self._items[bus_name] = (bus_name, obj_path)
            self.StatusNotifierItemRegistered(bus_name)
            self._app.on_item_registered(bus_name, obj_path)
            self._bus.watch_name_owner(bus_name, self._on_name_owner_changed)

    @dbus.service.method(WATCHER_IFACE, in_signature='s', out_signature='',
                         sender_keyword='sender')
    def RegisterStatusNotifierHost(self, service, sender=None):
        log.info('RegisterStatusNotifierHost: %s', service)
        if service not in self._hosts:
            self._hosts.append(service)
        if not self._host_registered:
            self._host_registered = True
            self.StatusNotifierHostRegistered()

    # --- properties ---

    @dbus.service.method(PROPS_IFACE, in_signature='ss', out_signature='v')
    def Get(self, interface, prop):
        return self._get_all(interface).get(prop, dbus.String(''))

    @dbus.service.method(PROPS_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        return self._get_all(interface)

    def _get_all(self, interface):
        if interface != WATCHER_IFACE:
            return {}
        return {
            'RegisteredStatusNotifierItems': dbus.Array(list(self._items), signature='s'),
            'IsStatusNotifierHostRegistered': dbus.Boolean(self._host_registered),
            'ProtocolVersion': dbus.Int32(0),
        }

    # --- signals ---

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemRegistered(self, service):
        pass

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemUnregistered(self, service):
        pass

    @dbus.service.signal(WATCHER_IFACE)
    def StatusNotifierHostRegistered(self):
        pass

    @dbus.service.signal(WATCHER_IFACE)
    def StatusNotifierHostUnregistered(self):
        pass

    # --- internal ---

    def _on_name_owner_changed(self, new_owner):
        if new_owner:
            return
        gone = [bn for bn in self._items if not self._bus.name_has_owner(bn)]
        for bn in gone:
            log.info('Item disappeared: %s', bn)
            del self._items[bn]
            self.StatusNotifierItemUnregistered(bn)
            self._app.on_item_unregistered(bn)


# ---------------------------------------------------------------------------
# Per-item tray icon
# ---------------------------------------------------------------------------

def _argb_pixmaps_to_pixbuf(pixmaps):
    """Convert SNI ARGB pixmap list [(w, h, bytes), ...] to GdkPixbuf."""
    best = None
    for (w, h, data) in pixmaps:
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            continue
        raw = bytes(data)
        if len(raw) < w * h * 4:
            continue
        # ARGB (network order) -> RGBA
        rgba = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            a, r, g, b = raw[i], raw[i+1], raw[i+2], raw[i+3]
            rgba[i] = r; rgba[i+1] = g; rgba[i+2] = b; rgba[i+3] = a
        pb = GdkPixbuf.Pixbuf.new_from_data(
            bytes(rgba), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
        if best is None or w > best.get_width():
            best = pb.copy()
    return best


class TrayItem:
    """Wraps one registered StatusNotifierItem as a GtkStatusIcon."""

    def __init__(self, bus, bus_name, obj_path, app):
        self._bus      = bus
        self._bus_name = bus_name
        self._obj_path = obj_path
        self._app      = app
        self._menu_client = None
        self._proxy    = None
        self._props    = None

        self._icon = Gtk.StatusIcon()
        self._icon.set_title(bus_name)
        self._icon.connect('activate',   self._on_activate)
        self._icon.connect('popup-menu', self._on_popup_menu)
        self._icon.set_visible(True)

        self._connect()

    def _connect(self):
        try:
            obj = self._bus.get_object(self._bus_name, self._obj_path)
            self._proxy = dbus.Interface(obj, ITEM_IFACE)
            self._props = dbus.Interface(obj, PROPS_IFACE)
            self._refresh_icon()
            self._refresh_menu()
            self._subscribe_signals()
        except Exception as e:
            log.warning('Could not connect to %s %s: %s', self._bus_name, self._obj_path, e)

    def _get_prop(self, name, default=None):
        try:
            return self._props.Get(ITEM_IFACE, name)
        except Exception:
            return default

    def _refresh_icon(self):
        icon_name = str(self._get_prop('IconName', '') or '')
        if icon_name:
            self._icon.set_from_icon_name(icon_name)
            return
        pixmaps = self._get_prop('IconPixmap')
        if pixmaps:
            pb = _argb_pixmaps_to_pixbuf(pixmaps)
            if pb:
                pb = pb.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
                self._icon.set_from_pixbuf(pb)
                return
        self._icon.set_from_icon_name('application-x-executable')

    def _refresh_menu(self):
        menu_path = str(self._get_prop('Menu', '') or '')
        if not menu_path:
            return
        try:
            self._menu_client = DbusmenuGtk3.Client.new(self._bus_name, menu_path)
        except Exception as e:
            log.warning('Could not create menu client for %s: %s', self._bus_name, e)

    def _subscribe_signals(self):
        obj = self._bus.get_object(self._bus_name, self._obj_path)
        obj.connect_to_signal('NewIcon',   self._on_new_icon,   dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewStatus', self._on_new_status, dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewTitle',  self._on_new_title,  dbus_interface=ITEM_IFACE)

    def _on_new_icon(self, *_):
        GLib.idle_add(self._refresh_icon)

    def _on_new_status(self, status, *_):
        GLib.idle_add(self._icon.set_visible, str(status) != 'Passive')

    def _on_new_title(self, *_):
        title = str(self._get_prop('Title', '') or self._bus_name)
        GLib.idle_add(self._icon.set_title, title)

    def _on_activate(self, icon):
        try:
            self._proxy.Activate(0, 0)
        except Exception as e:
            log.debug('Activate failed: %s', e)

    def _on_popup_menu(self, icon, button, time):
        if self._menu_client:
            root = self._menu_client.get_root()
            if root:
                menu = self._menu_client.menuitem_get_submenu(root)
                if menu:
                    menu.popup(None, None,
                               Gtk.StatusIcon.position_menu,
                               icon, button, time)
                    return
        try:
            self._proxy.ContextMenu(0, 0)
        except Exception:
            pass

    def destroy(self):
        self._icon.set_visible(False)
        self._icon = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus           = dbus.SessionBus()
        self._items: dict[str, TrayItem] = {}
        self._host_icon: HostTrayIcon | None = None
        self._loop: GLib.MainLoop | None = None

    def item_count(self) -> int:
        return len(self._items)

    def registered_item_names(self) -> list[str]:
        return list(self._items.keys())

    def quit(self):
        if self._loop:
            self._loop.quit()

    def run(self):
        try:
            self._watcher_bus_name = dbus.service.BusName(
                WATCHER_BUS_NAME, self._bus,
                allow_replacement=False,
                replace_existing=False,
                do_not_queue=True,
            )
        except dbus.exceptions.NameExistsException:
            log.error('%s is already owned — is another host running?', WATCHER_BUS_NAME)
            sys.exit(1)

        self._watcher = StatusNotifierWatcher(self._bus, self)

        host_name = f'{HOST_BUS_PREFIX}-{os.getpid()}'
        self._host_dbus_name = dbus.service.BusName(host_name, self._bus)
        self._watcher.RegisterStatusNotifierHost(host_name)
        log.info('Watcher:  %s', WATCHER_BUS_NAME)
        log.info('Host:     %s', host_name)

        self._host_icon = HostTrayIcon(self)

        self._loop = GLib.MainLoop()
        _signal.signal(_signal.SIGINT,  lambda *_: self._loop.quit())
        _signal.signal(_signal.SIGTERM, lambda *_: self._loop.quit())
        self._loop.run()

    def on_item_registered(self, bus_name: str, obj_path: str):
        if bus_name in self._items:
            return
        log.info('Item registered: %s at %s', bus_name, obj_path)
        self._items[bus_name] = TrayItem(self._bus, bus_name, obj_path, self)
        if self._host_icon:
            self._host_icon.refresh_tooltip()

    def on_item_unregistered(self, bus_name: str):
        item = self._items.pop(bus_name, None)
        if item:
            item.destroy()
        if self._host_icon:
            self._host_icon.refresh_tooltip()


if __name__ == '__main__':
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    Application().run()
