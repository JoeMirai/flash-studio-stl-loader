#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if command -v uv >/dev/null 2>&1; then
    uv venv --python /usr/bin/python3 --system-site-packages "$project_dir/.venv"
    uv pip install --python "$project_dir/.venv/bin/python" -r "$project_dir/requirements.txt"
else
    /usr/bin/python3 -m venv --system-site-packages "$project_dir/.venv"
    "$project_dir/.venv/bin/python" -m pip install -r "$project_dir/requirements.txt"
fi
"$project_dir/.venv/bin/python" -c "import gi, cairo; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk"
"$project_dir/.venv/bin/python" "$project_dir/install_shortcut.py"
