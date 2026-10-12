#!/usr/bin/python3
"""Desktop STL picker for Flash Studio. Linux / GTK 3 / native file transfers."""
import argparse
import concurrent.futures
import hashlib
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import subprocess
import time
import zipfile

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk

PREFIX = 'com.flashforge.orca-flashforge.InstanceCheck.Object'
CACHE = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'flash-studio-loader'
PREVIEW_TRIANGLE_LIMIT = 100_000


def downloads_folder():
    try:
        result = subprocess.run(['xdg-user-dir', 'DOWNLOAD'], capture_output=True,
                                text=True, timeout=3, check=True)
        return Path(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return Path.home() / 'Downloads'


def recent_stls(folder, hours, now=None):
    cutoff = (time.time() if now is None else now) - hours * 3600
    return sorted((p for p in folder.iterdir() if p.is_file()
                   and p.suffix.lower() == '.stl' and p.stat().st_mtime >= cutoff),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def latest_zip(folder):
    return max((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == '.zip'),
               key=lambda p: p.stat().st_mtime, default=None)


def extract_stls(archive, cache=CACHE):
    """Extract only STL members; never trust archive paths or symlinks."""
    stat = archive.stat()
    key = hashlib.sha256(f'{archive.resolve()}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()[:20]
    target = cache / 'zip' / key
    paths = []
    with zipfile.ZipFile(archive) as z:
        members = []
        seen = set()
        for item in z.infolist():
            name = PurePosixPath(item.filename.replace('\\', '/'))
            if item.is_dir() or name.suffix.lower() != '.stl':
                continue
            if name.is_absolute() or '..' in name.parts or not name.parts or ':' in name.parts[0]:
                raise ValueError('ZIP contains an unsafe STL path.')
            if name in seen:
                raise ValueError('ZIP contains duplicate STL paths.')
            seen.add(name)
            members.append((item, name))
        if len(members) > 1000 or sum(i.file_size for i, _ in members) > 1024 ** 3:
            raise ValueError('ZIP is too large (limit: 1,000 STLs / 1 GB extracted).')
        for item, name in members:
            dest = target.joinpath(*name.parts)
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Cache is private to this tool; atomic files prevent partial imports.
            tmp = dest.with_name(dest.name + '.partial')
            try:
                with z.open(item) as src, tmp.open('wb') as out:
                    shutil.copyfileobj(src, out)
                tmp.replace(dest)
            finally:
                tmp.unlink(missing_ok=True)
            paths.append(dest)
    return paths


def read_mesh(path, limit=PREVIEW_TRIANGLE_LIMIT):
    """Read binary or ASCII STL and sample large meshes for a responsive preview."""
    size = path.stat().st_size
    if size > 256 * 1024 ** 2:
        raise ValueError('Preview limit is 256 MB. This file can still be imported.')
    with path.open('rb') as f:
        head = f.read(84)
        count = struct.unpack_from('<I', head, 80)[0] if len(head) == 84 else 0
        triangles = []
        if count and size == 84 + count * 50:
            step = max(1, math.ceil(count / limit))
            for index in range(0, count, step):
                f.seek(84 + index * 50)
                values = struct.unpack('<12fH', f.read(50))
                triangles.append(tuple(tuple(values[j:j + 3]) for j in (3, 6, 9)))
        else:
            f.seek(0)
            vertices = []
            for line in f:
                bits = line.strip().split()
                if bits and bits[0].lower() == b'vertex' and len(bits) == 4:
                    vertices.append(tuple(float(v) for v in bits[1:]))
            if len(vertices) % 3:
                raise ValueError('Invalid ASCII STL: incomplete triangle.')
            step = max(1, math.ceil(len(vertices) / 3 / limit))
            triangles = [tuple(vertices[i:i + 3]) for i in range(0, len(vertices), step * 3)]
        if not triangles or not all(math.isfinite(v) for t in triangles for p in t for v in p):
            raise ValueError('STL contains no valid finite triangles.')
    low = [min(p[a] for t in triangles for p in t) for a in range(3)]
    high = [max(p[a] for t in triangles for p in t) for a in range(3)]
    span = max(high[a] - low[a] for a in range(3)) or 1
    center = [(low[a] + high[a]) / 2 for a in range(3)]
    mesh = [tuple(tuple((p[a] - center[a]) / span for a in range(3)) for p in t) for t in triangles]
    return mesh, tuple(high[a] - low[a] for a in range(3))


def bus_connection():
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def studio_instances(connection=None):
    connection = connection or bus_connection()
    names = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                                 'org.freedesktop.DBus', 'ListNames', None,
                                 GLib.VariantType.new('(as)'), Gio.DBusCallFlags.NONE,
                                 2000, None).unpack()[0]
    return sorted(n for n in names if n.startswith(PREFIX))


def import_models(paths, destination):
    connection = bus_connection()
    if destination not in studio_instances(connection):
        raise RuntimeError('Flash Studio closed. Launch it and try again.')
    for path in paths:
        if not path.is_file():
            raise ValueError(f'File no longer exists: {path.name}')
    pid = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                               'org.freedesktop.DBus', 'GetConnectionUnixProcessID',
                               GLib.Variant('(s)', (destination,)), None,
                               Gio.DBusCallFlags.NONE, 2000, None).unpack()[0]
    from native_drop import send_files
    send_files(paths, pid)


def find_launcher():
    configured = os.environ.get('FLASH_STUDIO_EXECUTABLE')
    if configured:
        return configured
    local = Path.home() / '.local/bin/flash-studio'
    if local.exists():
        return str(local)
    candidates = sorted((Path.home() / 'Applications').glob('Flash*Studio*.AppImage'))
    return str(candidates[-1]) if candidates else shutil.which('flash-studio')


class Preview(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.mesh = []
        self.note = 'Select a model to preview'
        self.yaw, self.pitch, self.zoom = -.65, .6, 1.0
        self.drag = None
        self.set_size_request(400, 360)
        self.add_events(256 | 512 | 4 | 2097152)  # press, release, motion, scroll
        self.connect('draw', self.draw_mesh)
        self.connect('button-press-event', self.press)
        self.connect('button-release-event', lambda *_: setattr(self, 'drag', None))
        self.connect('motion-notify-event', self.motion)
        self.connect('scroll-event', self.scroll)

    def press(self, _, event):
        self.drag = (event.x, event.y)

    def motion(self, _, event):
        if self.drag:
            self.yaw += (event.x - self.drag[0]) * .01
            self.pitch += (event.y - self.drag[1]) * .01
            self.drag = (event.x, event.y)
            self.queue_draw()

    def scroll(self, _, event):
        self.zoom = max(.25, min(4, self.zoom * (1.12 if int(event.direction) == 0 else .89)))
        self.queue_draw()
        return True

    def draw_mesh(self, _, cr):
        width, height = self.get_allocated_width(), self.get_allocated_height()
        cr.set_source_rgb(.09, .11, .15)
        cr.paint()
        if not self.mesh:
            cr.set_source_rgb(.8, .83, .89)
            cr.move_to(20, height / 2)
            cr.show_text(self.note[:65])
            return
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        scale = min(width, height) * .7 * self.zoom
        def rotate(p):
            x, y, z = p
            x, y = cy * x - sy * y, sy * x + cy * y
            return x, cp * z - sp * y, sp * z + cp * y
        faces = [tuple(rotate(p) for p in t) for t in self.mesh]
        faces.sort(key=lambda t: sum(p[2] for p in t))
        for t in faces:
            a, b, c = t
            u = [b[i] - a[i] for i in range(3)]
            v = [c[i] - a[i] for i in range(3)]
            normal = (u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0])
            length = math.sqrt(sum(n*n for n in normal)) or 1
            light = .35 + .65 * abs((normal[0]*.3 + normal[1]*.5 + normal[2]*.8) / length)
            cr.set_source_rgb(.2*light, .72*light, .98*light)
            for index, p in enumerate(t):
                (cr.move_to if index == 0 else cr.line_to)(width / 2 + p[0]*scale, height / 2 - p[1]*scale)
            cr.close_path()
            cr.fill()


class Window(Gtk.Window):
    def __init__(self, folder):
        super().__init__(title='Flash Studio • STL Loader')
        self.folder = folder
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self.preview_generation = 0
        self.busy = False
        self.launch_deadline = 0
        self.set_default_size(1020, 640)
        self.connect('destroy', lambda *_: Gtk.main_quit())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=18)
        self.add(box)
        top = Gtk.Box(spacing=10)
        self.state = Gtk.Label(xalign=0)
        top.pack_start(self.state, True, True, 0)
        self.instances = Gtk.ComboBoxText()
        top.pack_start(self.instances, False, False, 0)
        self.launch_button = Gtk.Button(label='Launch Flash Studio')
        self.launch_button.connect('clicked', self.launch)
        top.pack_start(self.launch_button, False, False, 0)
        box.pack_start(top, False, False, 0)
        controls = Gtk.Box(spacing=10)
        controls.pack_start(Gtk.Label(label='Downloads from the last'), False, False, 0)
        self.hours = Gtk.SpinButton.new_with_range(.1, 168, .5)
        self.hours.set_value(1)
        self.hours.set_digits(1)
        controls.pack_start(self.hours, False, False, 0)
        controls.pack_start(Gtk.Label(label='hours'), False, False, 0)
        self.recent_button = Gtk.Button(label='Load recent STLs')
        self.recent_button.connect('clicked', self.load_recent)
        controls.pack_start(self.recent_button, False, False, 0)
        self.zip_button = Gtk.Button(label='Load latest ZIP')
        self.zip_button.connect('clicked', self.load_zip)
        controls.pack_start(self.zip_button, False, False, 0)
        box.pack_start(controls, False, False, 0)
        self.source = Gtk.Label(label=str(folder), xalign=0)
        self.source.set_ellipsize(3)
        box.pack_start(self.source, False, False, 0)
        paned = Gtk.Paned()
        self.store = Gtk.ListStore(bool, str, str, str)
        tree = Gtk.TreeView(model=self.store)
        toggle = Gtk.CellRendererToggle()
        toggle.connect('toggled', self.toggle)
        tree.append_column(Gtk.TreeViewColumn('Import', toggle, active=0))
        name = Gtk.CellRendererText()
        name.set_property('ellipsize', 3)
        col = Gtk.TreeViewColumn('STL model', name, text=1)
        col.set_expand(True)
        tree.append_column(col)
        tree.append_column(Gtk.TreeViewColumn('Size', Gtk.CellRendererText(), text=2))
        tree.get_selection().connect('changed', self.preview_selected)
        scroll = Gtk.ScrolledWindow()
        scroll.add(tree)
        paned.pack1(scroll, True, False)
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.preview = Preview()
        right.pack_start(self.preview, True, True, 0)
        self.details = Gtk.Label(label='Drag to rotate • scroll to zoom', xalign=0)
        right.pack_start(self.details, False, False, 0)
        paned.pack2(right, True, False)
        paned.set_position(520)
        box.pack_start(paned, True, True, 0)
        bottom = Gtk.Box(spacing=10)
        for label, value in [('Select all', True), ('Clear selection', False)]:
            button = Gtk.Button(label=label)
            button.connect('clicked', lambda _, v=value: self.select_all(v))
            bottom.pack_start(button, False, False, 0)
        self.import_button = Gtk.Button(label='Import selected into Flash Studio')
        self.import_button.connect('clicked', self.do_import)
        bottom.pack_end(self.import_button, False, False, 0)
        box.pack_start(bottom, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.set_line_wrap(True)
        box.pack_start(self.status, False, False, 0)
        self.refresh_instances()
        GLib.timeout_add_seconds(2, self.refresh_instances)
        self.load_recent()

    def error(self, exc):
        dialog = Gtk.MessageDialog(transient_for=self, modal=True,
                                   message_type=Gtk.MessageType.ERROR,
                                   buttons=Gtk.ButtonsType.CLOSE, text=str(exc))
        dialog.run()
        dialog.destroy()

    def refresh_instances(self):
        try:
            names = studio_instances()
            selected = self.instances.get_active_id()
            self.instances.remove_all()
            for index, name in enumerate(names):
                self.instances.append(name, f'Window {index + 1}')
            self.instances.set_active_id(selected if selected in names else (names[0] if names else ''))
            self.instances.set_visible(len(names) > 1)
            self.state.set_text('Flash Studio is open' if names else 'Flash Studio is closed — launch it to import')
            self.launch_button.set_sensitive(not names and not self.launch_deadline)
            self.import_button.set_sensitive(bool(names) and not self.busy)
            if names:
                self.launch_deadline = 0
            elif self.launch_deadline and time.monotonic() > self.launch_deadline:
                self.launch_deadline = 0
                self.status.set_text('Flash Studio has not connected yet. Check its window, then try again.')
        except Exception as exc:
            self.state.set_text(f'Cannot connect to desktop session: {exc}')
            self.import_button.set_sensitive(False)
        return True

    def launch(self, *_):
        try:
            executable = find_launcher()
            if not executable:
                raise RuntimeError('Flash Studio not found. Set FLASH_STUDIO_EXECUTABLE to its AppImage path.')
            subprocess.Popen([executable], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            self.launch_deadline = time.monotonic() + 90
            self.status.set_text('Starting Flash Studio… You can choose models while it starts.')
            self.refresh_instances()
        except Exception as exc:
            self.error(exc)

    def populate(self, paths, source):
        self.preview_generation += 1
        self.store.clear()
        self.preview.mesh = []
        self.preview.note = 'Select a model to preview'
        self.preview.queue_draw()
        self.details.set_text('Drag to rotate • scroll to zoom')
        for path in paths:
            label = str(path.relative_to(self.folder)) if path.is_relative_to(self.folder) else str(path.relative_to(CACHE / 'zip').as_posix()).split('/', 1)[-1]
            self.store.append([False, label, f'{path.stat().st_size / 1024:.0f} KB', str(path)])
        self.source.set_text(source)
        self.status.set_text(f'{len(paths)} STL files found. Tick the models to import.' if paths else 'No STL files found. Try a longer time window or the latest ZIP.')

    def load_recent(self, *_):
        try:
            self.populate(recent_stls(self.folder, self.hours.get_value()),
                          f'{self.folder} • last {self.hours.get_value():g} hours')
        except Exception as exc:
            self.error(exc)

    def load_zip(self, *_):
        try:
            archive = latest_zip(self.folder)
            if not archive:
                raise ValueError('No ZIP files in Downloads.')
            self.busy = True
            self.zip_button.set_sensitive(False)
            self.recent_button.set_sensitive(False)
            self.refresh_instances()
            self.status.set_text(f'Extracting STLs from {archive.name}…')
            future = self.executor.submit(extract_stls, archive)
            def finished():
                self.busy = False
                self.zip_button.set_sensitive(True)
                self.recent_button.set_sensitive(True)
                self.refresh_instances()
                try:
                    self.populate(future.result(), f'Latest ZIP: {archive.name}')
                except Exception as exc:
                    self.error(exc)
                return False
            future.add_done_callback(lambda _: GLib.idle_add(finished))
        except Exception as exc:
            self.error(exc)

    def toggle(self, _, path):
        self.store[path][0] = not self.store[path][0]

    def select_all(self, value):
        for row in self.store:
            row[0] = value

    def preview_selected(self, selection):
        model, it = selection.get_selected()
        if it is None:
            return
        self.preview_generation += 1
        generation = self.preview_generation
        path = Path(model[it][3])
        self.preview.mesh = []
        self.preview.note = 'Rendering preview…'
        self.preview.queue_draw()
        future = self.executor.submit(read_mesh, path)
        def finished():
            if generation != self.preview_generation:
                return False
            try:
                self.preview.mesh, dimensions = future.result()
                self.preview.zoom = 1
                self.details.set_text(' × '.join(f'{d:.1f}' for d in dimensions) + ' mm • drag to rotate')
            except Exception as exc:
                self.preview.note = 'Preview unavailable'
                self.details.set_text(str(exc))
            self.preview.queue_draw()
            return False
        future.add_done_callback(lambda _: GLib.idle_add(finished))

    def do_import(self, *_):
        try:
            paths = [Path(row[3]) for row in self.store if row[0]]
            if not paths:
                raise ValueError('Tick at least one STL to import.')
            destination = self.instances.get_active_id()
            if not destination:
                raise ValueError('Launch Flash Studio first.')
            self.busy = True
            self.refresh_instances()
            self.status.set_text('Importing selected models…')
            future = self.executor.submit(import_models, paths, destination)
            def finished():
                self.busy = False
                self.refresh_instances()
                try:
                    future.result()
                    self.status.set_text(f'Imported {len(paths)} models into Flash Studio.')
                except Exception as exc:
                    self.error(exc)
                return False
            future.add_done_callback(lambda _: GLib.idle_add(finished))
        except Exception as exc:
            self.error(exc)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--downloads', type=Path, default=downloads_folder())
    parser.add_argument('--import-files', nargs='+', type=Path, help='Send files directly, without opening the picker')
    args = parser.parse_args()
    if args.import_files:
        instances = studio_instances()
        if len(instances) != 1:
            parser.error('Direct import requires exactly one running Flash Studio instance.')
        import_models(args.import_files, instances[0])
        print(f'Sent {len(args.import_files)} model(s) to Flash Studio.')
        return
    window = Window(args.downloads.expanduser())
    window.show_all()
    window.refresh_instances()
    Gtk.main()


if __name__ == '__main__':
    main()
