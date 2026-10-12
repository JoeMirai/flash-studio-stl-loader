# Flash Studio STL Loader

A small Linux desktop picker for Flash Studio Desktop. Preview recent STL downloads or the STL files in your newest ZIP, tick the models you want, and import them into the running slicer.

## Features

- Detects running Flash Studio windows; offers to launch Flash Studio when closed.
- Defaults to STL downloads from the last hour; change the time window up to 168 hours.
- Loads the newest ZIP by modification time, including STL files in nested folders.
- Rotatable mesh preview, zoom, dimensions, and model selection.
- Sends native file-drop messages directly to the application. No simulated mouse movements, clicks, or keystrokes.
- Leaves ZIP originals untouched; extracted models remain in `~/.cache/flash-studio-loader/zip` so the slicer can continue reading them.

## Install

Requires Linux, Python 3, GTK 3 / PyGObject, Cairo, and X11 or XWayland. On Ubuntu/Debian, system dependencies are `python3-gi gir1.2-gtk-3.0 python3-cairo python3-venv`. Install these through your package manager if missing.

Run:

```bash
./install.sh
```

The installer creates a local virtual environment, installs `python-xlib`, and adds **Flash Studio STL Loader** to your desktop and application menu. It uses `uv` when available, otherwise Python's `venv` and `pip`.

Keep this checkout at its installed path, or rerun the installer after moving it.

## Use

1. Open the desktop shortcut. If Flash Studio is closed, press **Launch Flash Studio**.
2. Set the number of hours and press **Load recent STLs**, or press **Load latest ZIP**.
3. Click a row to preview it. Tick its **Import** checkbox; use **Select all** if desired.
4. Press **Import selected into Flash Studio**. The files go to the model workspace. Flash Studio may show its normal multiple-model grouping prompt.

When more than one Flash Studio instance is open, select the target window in the dropdown. For best results, leave Flash Studio in **Prepare** with no modal dialog open. A declined drop or timeout produces an error instead of claiming success.

Downloads uses `xdg-user-dir DOWNLOAD`, falling back to `~/Downloads`. Only the top level is scanned for recent downloads and newest ZIP selection. ZIP model discovery is recursive. “Recent” is based on file modification time.

Flash Studio launch discovery checks `~/.local/bin/flash-studio`, then `~/Applications/Flash*Studio*.AppImage`. Override it with the `FLASH_STUDIO_EXECUTABLE` environment variable (an executable path, without arguments).

```bash
./run.sh --downloads /path/to/downloads
./run.sh --import-files /path/to/model.stl
```

The direct-import option requires exactly one running instance.

## Compatibility and implementation

Tested with Flash Studio Desktop 1.7.8 on a Wayland desktop where Flash Studio runs through XWayland. D-Bus is used only to find the application and its process ID. This Flash Studio build advertises an invalid D-Bus interface name containing a hyphen; standard clients and the session broker reject imports through that interface. Import therefore uses the app's native Xdnd file-drop receiver instead. It waits for the application to request the URI list and acknowledge the drop.

This depends on Flash Studio retaining its current D-Bus instance name prefix and Xdnd support. Pure Wayland applications without an XWayland window are not supported. Preview dimensions assume millimetres, as STL does not encode units. Large previews are sampled (12,000 triangles), and files over 256 MB can be imported but are not previewed. ZIP extraction is limited to 1 GB / 1,000 STLs and rejects unsafe paths.

## Tests

```bash
.venv/bin/python -m unittest -v
```

Tests cover binary and ASCII STL parsing, invalid coordinates, download time filtering, newest ZIP selection, nested STL extraction, and archive path traversal rejection. Native imports require a live Flash Studio desktop session.

## License

MIT. This is an independent utility, not an official FlashForge product.
