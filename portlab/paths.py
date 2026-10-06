from pathlib import Path
import sys
import os


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))


def asset_path() -> Path:
    return resource_root() / 'assets'


def data_path() -> Path:
    return resource_root() / 'data'


def user_root() -> Path:
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else resource_root()
    path = Path(os.environ.get('PORTLAB_USER_DIR', str(base / 'projects')))
    path.mkdir(parents=True, exist_ok=True)
    return path
