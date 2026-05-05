#!/usr/bin/env python3
"""
sni-host: StatusNotifierItem host for X11
Provides org.kde.StatusNotifierWatcher on DBus and renders tray icons in a
self-owned frameless GTK window positioned at the top-right of the workarea.
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

VERSION = '1.1'

log = logging.getLogger('sni-host')


def _parse_args():
    parser = argparse.ArgumentParser(
        prog='sni-host',
        description=(
            'StatusNotifierItem host for X11.\n\n'
            'Registers org.kde.StatusNotifierWatcher on the session DBus so that\n'
            'applications using the StatusNotifierItem / AppIndicator protocol\n'
            '(e.g. Dropbox) can display tray icons.\n\n'
            'sni-host shows its own icon in a small frameless panel window at the\n'
            'top-right corner of the primary monitor.  Left-click or right-click\n'
            'the icon for a menu with app info, registered items, Restart, and Quit.\n\n'
            'Intended to run as a background daemon via a systemd user service\n'
            '(see sni-host.service).'
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

ICON_SIZE = 22   # px, each icon square
PAD       = 4    # px, padding around icon row inside panel window

_HAND_CURSOR = None   # lazy-initialised Gdk.Cursor


def _hand_cursor():
    global _HAND_CURSOR
    if _HAND_CURSOR is None:
        _HAND_CURSOR = Gdk.Cursor.new_from_name(Gdk.Display.get_default(), 'pointer')
    return _HAND_CURSOR


# ---------------------------------------------------------------------------
# Icon helpers
# ---------------------------------------------------------------------------

def _make_host_pixbuf(size=ICON_SIZE) -> GdkPixbuf.Pixbuf:
    """Black background with three white rounded signal bars."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    ctx.set_source_rgb(0, 0, 0)
    ctx.rectangle(0, 0, size, size)
    ctx.fill()

    ctx.set_source_rgb(1, 1, 1)
    bar_w   = max(2, size // 8)
    gap     = max(1, size // 11)
    total_w = 3 * bar_w + 2 * gap
    x0      = (size - total_w) / 2
    bottom  = size - 2

    for i, frac in enumerate([0.35, 0.60, 0.85]):
        h  = size * frac
        bx = x0 + i * (bar_w + gap)
        by = bottom - h
        r  = bar_w / 2
        ctx.new_sub_path()
        ctx.arc(bx + r,          by + r,     r, -3.14159, -1.5708)
        ctx.arc(bx + bar_w - r,  by + r,     r, -1.5708,   0     )
        ctx.arc(bx + bar_w - r,  by + h - r, r,  0,        1.5708)
        ctx.arc(bx + r,          by + h - r, r,  1.5708,   3.14159)
        ctx.close_path()
        ctx.fill()

    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _make_fallback_pixbuf(size=ICON_SIZE) -> GdkPixbuf.Pixbuf:
    """Mid-gray filled square — used when an item provides no usable icon."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(0.45, 0.45, 0.45)
    ctx.rectangle(2, 2, size - 4, size - 4)
    ctx.fill()
    return Gdk.pixbuf_get_from_surface(surface, 0, 0, size, size)


def _load_named_icon(name: str) -> GdkPixbuf.Pixbuf | None:
    theme = Gtk.IconTheme.get_default()
    try:
        return theme.load_icon(name, ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
    except GLib.Error:
        return None


def _argb_pixmaps_to_pixbuf(pixmaps) -> GdkPixbuf.Pixbuf | None:
    """Convert SNI ARGB pixmap list [(w, h, bytes), ...] to GdkPixbuf."""
    best = None
    for (w, h, data) in pixmaps:
        w, h = int(w), int(h)
        if w <= 0 or h <= 0:
            continue
        raw = bytes(data)
        if len(raw) < w * h * 4:
            continue
        rgba = bytearray(len(raw))
        for i in range(0, len(raw), 4):
            a, r, g, b = raw[i], raw[i+1], raw[i+2], raw[i+3]
            rgba[i] = r; rgba[i+1] = g; rgba[i+2] = b; rgba[i+3] = a
        pb = GdkPixbuf.Pixbuf.new_from_data(
            bytes(rgba), GdkPixbuf.Colorspace.RGB, True, 8, w, h, w * 4)
        if best is None or w > best.get_width():
            best = pb.copy()
    return best


# ---------------------------------------------------------------------------
# Tray window  — the visible panel
# ---------------------------------------------------------------------------

_TRAY_CSS = b"""
window#sni-host-tray {
    background-color: #1c1c1c;
    border-radius: 5px;
    border: 1px solid #484848;
}
"""

class TrayWindow(Gtk.Window):
    """
    Frameless always-on-top panel at the top-right of the primary monitor's
    workarea.  Icon slots are added/removed as items register/unregister.
    """

    def __init__(self):
        super().__init__()
        self.set_title('sni-host')
        self.set_decorated(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_resizable(False)
        self.stick()
        self.set_name('sni-host-tray')

        provider = Gtk.CssProvider()
        provider.load_from_data(_TRAY_CSS)
        self.get_style_context().add_provider(
            provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self._box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self._box.set_margin_start(PAD)
        self._box.set_margin_end(PAD)
        self._box.set_margin_top(PAD)
        self._box.set_margin_bottom(PAD)
        self.add(self._box)

        self._reposition_pending = False
        self.connect('size-allocate', self._on_size_allocate)

        self.show_all()
        self._queue_reposition()

    # --- layout / positioning ---

    def _on_size_allocate(self, *_):
        self._queue_reposition()

    def _queue_reposition(self):
        if not self._reposition_pending:
            self._reposition_pending = True
            GLib.idle_add(self._reposition)

    def _reposition(self):
        self._reposition_pending = False
        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor()
        wa      = monitor.get_workarea()
        w, h    = self.get_size()
        self.move(wa.x + wa.width - w - 5, wa.y + 5)
        return False

    # --- slot management ---

    def add_slot(self, pixbuf: GdkPixbuf.Pixbuf,
                 on_press) -> tuple[Gtk.EventBox, Gtk.Image]:
        """
        Append a 22×22 icon button to the panel.
        on_press(widget, Gdk.EventButton) is called for any mouse button.
        Returns (EventBox, Image) so the caller can update the image later.
        """
        pb  = pixbuf.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
        img = Gtk.Image.new_from_pixbuf(pb)

        ebox = Gtk.EventBox()
        ebox.add(img)
        ebox.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK |
            Gdk.EventMask.ENTER_NOTIFY_MASK |
            Gdk.EventMask.LEAVE_NOTIFY_MASK)
        ebox.connect('button-press-event', on_press)
        ebox.connect('enter-notify-event',
                     lambda w, e: (img.set_opacity(0.65), False)[1])
        ebox.connect('leave-notify-event',
                     lambda w, e: (img.set_opacity(1.0),  False)[1])
        ebox.connect('realize',
                     lambda w: w.get_window().set_cursor(_hand_cursor()))

        self._box.pack_start(ebox, False, False, 0)
        self._box.show_all()
        return ebox, img

    def remove_slot(self, ebox: Gtk.EventBox):
        self._box.remove(ebox)
        self._box.show_all()


# ---------------------------------------------------------------------------
# Host icon — sni-host's own slot in the TrayWindow
# ---------------------------------------------------------------------------

class HostTrayIcon:
    """sni-host's own icon + right-click menu inside TrayWindow."""

    def __init__(self, app: 'Application', tray: TrayWindow):
        self._app  = app
        self._tray = tray
        self._ebox, self._img = tray.add_slot(_make_host_pixbuf(), self._on_press)
        self._ebox.set_tooltip_text(f'sni-host v{VERSION}')

    def refresh_tooltip(self):
        n   = self._app.item_count()
        tip = f'sni-host v{VERSION}\n{n} registered item{"s" if n != 1 else ""}'
        self._ebox.set_tooltip_text(tip)

    # --- menu ---

    def _build_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()

        def _info(text, bold=False):
            item  = Gtk.MenuItem()
            label = Gtk.Label(xalign=0)
            label.set_markup(
                f'<b>{GLib.markup_escape_text(text)}</b>' if bold else
                GLib.markup_escape_text(text))
            item.add(label)
            item.set_sensitive(False)
            return item

        menu.append(_info(f'sni-host  v{VERSION}', bold=True))
        menu.append(_info('AppIndicator / SNI host daemon'))
        menu.append(_info(f'PID: {os.getpid()}'))
        menu.append(Gtk.SeparatorMenuItem())

        names = self._app.registered_item_names()
        menu.append(_info(f'Registered items: {len(names)}', bold=True))
        for name in names:
            menu.append(_info(f'  ●  {name}'))

        menu.append(Gtk.SeparatorMenuItem())

        restart = Gtk.MenuItem(label='Restart')
        restart.connect('activate', lambda *_: self._do_restart())
        menu.append(restart)

        quit_ = Gtk.MenuItem(label='Quit')
        quit_.connect('activate', lambda *_: self._app.quit())
        menu.append(quit_)

        menu.show_all()
        return menu

    def _on_press(self, widget, event):
        if event.button in (1, 3):
            self._build_menu().popup_at_pointer(event)
        return True

    def _do_restart(self):
        log.info('Restarting…')
        os.execv(sys.executable, [sys.executable] + sys.argv)

    def destroy(self):
        self._tray.remove_slot(self._ebox)


# ---------------------------------------------------------------------------
# DBus watcher service
# ---------------------------------------------------------------------------

class StatusNotifierWatcher(dbus.service.Object):
    """Implements org.kde.StatusNotifierWatcher."""

    def __init__(self, bus, app):
        super().__init__(bus, WATCHER_OBJ_PATH)
        self._bus  = bus
        self._app  = app
        self._items: dict[str, tuple[str, str]] = {}
        self._hosts: list[str] = []
        self._host_registered  = False

    @dbus.service.method(WATCHER_IFACE, in_signature='s', out_signature='',
                         sender_keyword='sender')
    def RegisterStatusNotifierItem(self, service, sender=None):
        if service.startswith('/'):
            obj_path, bus_name = service, sender
        else:
            bus_name, obj_path = service, ITEM_DEFAULT_PATH
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

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemRegistered(self, service): pass

    @dbus.service.signal(WATCHER_IFACE, signature='s')
    def StatusNotifierItemUnregistered(self, service): pass

    @dbus.service.signal(WATCHER_IFACE)
    def StatusNotifierHostRegistered(self): pass

    @dbus.service.signal(WATCHER_IFACE)
    def StatusNotifierHostUnregistered(self): pass

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
# Per-item slot in the TrayWindow
# ---------------------------------------------------------------------------

class TrayItem:
    """One registered StatusNotifierItem rendered as a slot in TrayWindow."""

    def __init__(self, bus, bus_name: str, obj_path: str,
                 app: 'Application', tray: TrayWindow):
        self._bus      = bus
        self._bus_name = bus_name
        self._obj_path = obj_path
        self._app      = app
        self._tray     = tray
        self._menu_client = None
        self._proxy    = None
        self._props    = None

        self._ebox, self._img = tray.add_slot(_make_fallback_pixbuf(), self._on_press)
        self._ebox.set_tooltip_text(bus_name)
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
            log.warning('Could not connect to %s %s: %s',
                        self._bus_name, self._obj_path, e)

    def _get_prop(self, name, default=None):
        try:
            return self._props.Get(ITEM_IFACE, name)
        except Exception:
            return default

    def _load_icon_pixbuf(self) -> GdkPixbuf.Pixbuf | None:
        icon_name = str(self._get_prop('IconName', '') or '')
        if icon_name:
            pb = _load_named_icon(icon_name)
            if pb:
                return pb
        pixmaps = self._get_prop('IconPixmap')
        if pixmaps:
            return _argb_pixmaps_to_pixbuf(pixmaps)
        return None

    def _refresh_icon(self):
        pb = self._load_icon_pixbuf()
        if pb is None:
            pb = _make_fallback_pixbuf()
        pb = pb.scale_simple(ICON_SIZE, ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
        self._img.set_from_pixbuf(pb)

        title = str(self._get_prop('Title', '') or self._bus_name)
        self._ebox.set_tooltip_text(title)
        log.debug('%s: icon refreshed', self._bus_name)

    def _refresh_menu(self):
        menu_path = str(self._get_prop('Menu', '') or '')
        if not menu_path:
            return
        try:
            self._menu_client = DbusmenuGtk3.Client.new(self._bus_name, menu_path)
        except Exception as e:
            log.warning('Menu client failed for %s: %s', self._bus_name, e)

    def _subscribe_signals(self):
        obj = self._bus.get_object(self._bus_name, self._obj_path)
        obj.connect_to_signal('NewIcon',   self._on_new_icon,   dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewStatus', self._on_new_status, dbus_interface=ITEM_IFACE)
        obj.connect_to_signal('NewTitle',  self._on_new_title,  dbus_interface=ITEM_IFACE)

    def _on_new_icon(self, *_):
        GLib.idle_add(self._refresh_icon)

    def _on_new_status(self, status, *_):
        GLib.idle_add(self._ebox.set_visible, str(status) != 'Passive')

    def _on_new_title(self, *_):
        title = str(self._get_prop('Title', '') or self._bus_name)
        GLib.idle_add(self._ebox.set_tooltip_text, title)

    def _on_press(self, widget, event):
        if event.button == 1:
            try:
                self._proxy.Activate(0, 0)
            except Exception as e:
                log.debug('Activate failed: %s', e)
        elif event.button == 3:
            if self._menu_client:
                root = self._menu_client.get_root()
                if root:
                    menu = self._menu_client.menuitem_get_submenu(root)
                    if menu:
                        menu.popup_at_pointer(event)
                        return True
            try:
                self._proxy.ContextMenu(0, 0)
            except Exception:
                pass
        return True

    def destroy(self):
        self._tray.remove_slot(self._ebox)
        self._ebox = None


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class Application:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self._bus   = dbus.SessionBus()
        self._items: dict[str, TrayItem] = {}
        self._tray: TrayWindow | None = None
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

        self._tray      = TrayWindow()
        self._host_icon = HostTrayIcon(self, self._tray)

        self._loop = GLib.MainLoop()
        _signal.signal(_signal.SIGINT,  lambda *_: self._loop.quit())
        _signal.signal(_signal.SIGTERM, lambda *_: self._loop.quit())
        self._loop.run()

    def on_item_registered(self, bus_name: str, obj_path: str):
        if bus_name in self._items:
            return
        log.info('Item registered: %s at %s', bus_name, obj_path)
        self._items[bus_name] = TrayItem(
            self._bus, bus_name, obj_path, self, self._tray)
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
