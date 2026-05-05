#!/usr/bin/env python3
"""
sni-host: StatusNotifierItem host for X11/GNOME
Provides org.kde.StatusNotifierWatcher on DBus and renders tray icons via GtkStatusIcon.
"""

import sys
import os
import logging
import argparse

import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Dbusmenu', '0.4')
gi.require_version('DbusmenuGtk3', '0.4')
from gi.repository import Gtk, GLib, GdkPixbuf, Dbusmenu, DbusmenuGtk3

import dbus
import dbus.service
import dbus.mainloop.glib

log = logging.getLogger('sni-host')


def _parse_args():
    parser = argparse.ArgumentParser(
        prog='sni-host',
        description=(
            'StatusNotifierItem host for X11/GNOME.\n\n'
            'Registers org.kde.StatusNotifierWatcher on the session DBus so that\n'
            'applications using the StatusNotifierItem protocol (e.g. Dropbox) can\n'
            'display tray icons in the X11 system tray.\n\n'
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


# ---------------------------------------------------------------------------
# DBus watcher service
# ---------------------------------------------------------------------------

class StatusNotifierWatcher(dbus.service.Object):
    """Implements org.kde.StatusNotifierWatcher."""

    def __init__(self, bus, app):
        super().__init__(bus, WATCHER_OBJ_PATH)
        self._bus = bus
        self._app = app
        # Map sender_bus_name -> (service, object_path)
        self._items: dict[str, tuple[str, str]] = {}
        self._hosts: list[str] = []
        self._host_registered = False

    # --- methods ---

    @dbus.service.method(WATCHER_IFACE, in_signature='s', out_signature='',
                         sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        # service may be a bus name, or an object path
        if service.startswith('/'):
            obj_path = service
            bus_name = sender
        else:
            bus_name = service
            obj_path = ITEM_DEFAULT_PATH

        log.info('RegisterStatusNotifierItem: bus=%s path=%s', bus_name, obj_path)

        if bus_name not in self._items:
            self._items[bus_name] = (bus_name, obj_path)
            service_str = bus_name  # canonical form for signals
            self.StatusNotifierItemRegistered(service_str)
            self._app.on_item_registered(bus_name, obj_path)
            # Watch for item owner disappearing
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
        items = [bn for bn in self._items]
        return {
            'RegisteredStatusNotifierItems': dbus.Array(items, signature='s'),
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
            return  # still alive or transferred
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
    """Convert SNI icon pixmap list [(w, h, data), ...] to GdkPixbuf, largest first."""
    best = None
    for (w, h, data) in pixmaps:
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            continue
        # data is dbus.Array of dbus.Byte (ARGB, big-endian 32-bit per pixel)
        raw = bytes(data)
        if len(raw) < w * h * 4:
            continue
        # Convert ARGB -> RGBA
        rgba = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            a, r, g, b = raw[i], raw[i+1], raw[i+2], raw[i+3]
            rgba[i]   = r
            rgba[i+1] = g
            rgba[i+2] = b
            rgba[i+3] = a
        pb = GdkPixbuf.Pixbuf.new_from_data(
            bytes(rgba), GdkPixbuf.Colorspace.RGB,
            True, 8, w, h, w * 4)
        if best is None or w > best.get_width():
            best = pb.copy()  # copy so raw buffer stays alive
    return best


class TrayItem:
    """Wraps one StatusNotifierItem as a GtkStatusIcon."""

    def __init__(self, bus, bus_name, obj_path, app):
        self._bus = bus
        self._bus_name = bus_name
        self._obj_path = obj_path
        self._app = app
        self._menu_client = None
        self._status_icon = Gtk.StatusIcon()
        self._status_icon.set_title(bus_name)
        self._status_icon.connect('activate', self._on_activate)
        self._status_icon.connect('popup-menu', self._on_popup_menu)
        self._status_icon.set_visible(True)

        self._proxy = None
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
        # Prefer named icon; fall back to pixmap
        icon_name = str(self._get_prop('IconName', '') or '')
        if icon_name:
            self._status_icon.set_from_icon_name(icon_name)
            log.debug('%s: icon name = %s', self._bus_name, icon_name)
            return

        pixmaps = self._get_prop('IconPixmap')
        if pixmaps:
            pb = _argb_pixmaps_to_pixbuf(pixmaps)
            if pb:
                pb = pb.scale_simple(22, 22, GdkPixbuf.InterpType.BILINEAR)
                self._status_icon.set_from_pixbuf(pb)
                log.debug('%s: set icon from pixmap', self._bus_name)
                return

        self._status_icon.set_from_icon_name('application-x-executable')

    def _refresh_menu(self):
        menu_path = str(self._get_prop('Menu', '') or '')
        if not menu_path:
            return
        try:
            self._menu_client = DbusmenuGtk3.Client.new(self._bus_name, menu_path)
            log.debug('%s: menu at %s', self._bus_name, menu_path)
        except Exception as e:
            log.warning('Could not create menu client for %s: %s', self._bus_name, e)

    def _subscribe_signals(self):
        obj = self._bus.get_object(self._bus_name, self._obj_path)
        obj.connect_to_signal('NewIcon',    self._on_new_icon,   dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewStatus',  self._on_new_status, dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewTitle',   self._on_new_title,  dbus_interface=ITEM_IFACE)

    def _on_new_icon(self, *a):
        GLib.idle_add(self._refresh_icon)

    def _on_new_status(self, status, *a):
        visible = str(status) != 'Passive'
        GLib.idle_add(self._status_icon.set_visible, visible)

    def _on_new_title(self, *a):
        title = str(self._get_prop('Title', '') or self._bus_name)
        GLib.idle_add(self._status_icon.set_title, title)

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
        # Fallback: ask the item to show its menu
        try:
            self._proxy.ContextMenu(0, 0)
        except Exception:
            pass

    def destroy(self):
        self._status_icon.set_visible(False)
        self._status_icon = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus = dbus.SessionBus()
        self._items: dict[str, TrayItem] = {}  # bus_name -> TrayItem
        self._watcher = None
        self._host_bus_name = None

    def run(self):
        # Claim the watcher name
        try:
            self._watcher_name = dbus.service.BusName(
                WATCHER_BUS_NAME, self._bus,
                allow_replacement=False,
                replace_existing=False,
                do_not_queue=True,
            )
        except dbus.exceptions.NameExistsException:
            log.error('%s is already owned. Is another host running?', WATCHER_BUS_NAME)
            sys.exit(1)

        self._watcher = StatusNotifierWatcher(self._bus, self)

        # Register ourselves as a host
        host_name = f'{HOST_BUS_PREFIX}-{os.getpid()}'
        self._host_bus_name = dbus.service.BusName(host_name, self._bus)
        self._watcher.RegisterStatusNotifierHost(host_name)
        log.info('Registered as host: %s', host_name)
        log.info('Watcher running as: %s', WATCHER_BUS_NAME)

        loop = GLib.MainLoop()
        import signal as _signal
        _signal.signal(_signal.SIGINT,  lambda *_: loop.quit())
        _signal.signal(_signal.SIGTERM, lambda *_: loop.quit())
        loop.run()

    def on_item_registered(self, bus_name: str, obj_path: str):
        if bus_name in self._items:
            return
        log.info('Creating tray icon for %s at %s', bus_name, obj_path)
        item = TrayItem(self._bus, bus_name, obj_path, self)
        self._items[bus_name] = item

    def on_item_unregistered(self, bus_name: str):
        item = self._items.pop(bus_name, None)
        if item:
            item.destroy()


if __name__ == '__main__':
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    )
    Application().run()
