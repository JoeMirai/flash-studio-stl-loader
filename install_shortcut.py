"""Install portable launchers for the current checkout."""
from pathlib import Path
import os
import subprocess

project = Path(__file__).resolve().parent
def desktop_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
entry = f'''[Desktop Entry]
Version=1.0
Type=Application
Name=Flash Studio STL Loader
Comment=Preview and import recent STL downloads or the latest ZIP
Exec={desktop_quote(project / 'run.sh')}
Icon=applications-graphics
Terminal=false
Categories=Graphics;Utility;
StartupNotify=true
'''
applications = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'applications'
try:
    desktop = Path(subprocess.check_output(['xdg-user-dir', 'DESKTOP'], text=True).strip())
except (OSError, subprocess.SubprocessError):
    desktop = Path.home() / 'Desktop'
for folder in [applications, desktop]:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'flash-studio-stl-loader.desktop'
    path.write_text(entry)
    path.chmod(0o755)
    if folder == desktop:
        subprocess.run(['gio', 'set', str(path), 'metadata::trusted', 'true'], check=False)
    print(f'Installed {path}')
