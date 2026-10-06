"""Copy installed dependency notices and cache official license documents."""
from pathlib import Path
import importlib.metadata as metadata
import json
import shutil
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'licenses'


def collect(download=False):
    OUTPUT.mkdir(parents=True, exist_ok=True)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    shutil.copy2(python_license, OUTPUT / 'CPython-LICENSE.txt')
    packages = ('Panda3D', 'simpy', 'openpyxl', 'et_xmlfile', 'psutil', 'pyinstaller',
                'PySide6', 'shiboken6')
    records = []
    for name in packages:
        distribution = metadata.distribution(name)
        notices = []
        for entry in distribution.files or []:
            base = Path(str(entry)).name.upper()
            if '.dist-info' not in str(entry) or not base.startswith(('LICENSE', 'LICENCE', 'COPYING', 'AUTHORS')):
                continue
            source = distribution.locate_file(entry)
            target = OUTPUT / (name + '-' + Path(str(entry)).name)
            shutil.copy2(source, target)
            notices.append(target.name)
        records.append({'name': name, 'version': distribution.version,
                        'license': distribution.metadata.get('License'),
                        'files': notices, 'source_links': distribution.metadata.get_all('Project-URL')})
    if download:
        documents = {
            'LGPL-3.0.txt': 'https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/LGPL-3.0-only.txt',
            'GPL-3.0.txt': 'https://raw.githubusercontent.com/qt/qtbase/dev/LICENSES/GPL-3.0-only.txt',
            'NVIDIA-Cg-license.pdf': 'https://developer.download.nvidia.com/cg/Cg_2.2/license.pdf',
            'FreeType-FTL.txt': 'https://raw.githubusercontent.com/freetype/freetype/master/docs/FTL.TXT',
            'zlib-LICENSE.txt': 'https://raw.githubusercontent.com/madler/zlib/master/LICENSE',
            'libpng-LICENSE.txt': 'https://raw.githubusercontent.com/pnggroup/libpng/master/LICENSE',
            'OpenSSL-1.0-LICENSE.txt': 'https://raw.githubusercontent.com/openssl/openssl/OpenSSL_1_0_2-stable/LICENSE',
            'HarfBuzz-COPYING.txt': 'https://raw.githubusercontent.com/harfbuzz/harfbuzz/main/COPYING',
            'libjpeg-turbo-LICENSE.md': 'https://raw.githubusercontent.com/libjpeg-turbo/libjpeg-turbo/main/LICENSE.md',
            'Qt-upstream-third-party-notices.html': 'https://doc.qt.io/qt-6/licenses-used-in-qt.html',
            'Panda3D-upstream-third-party-notices.html': 'https://docs.panda3d.org/1.10/python/distribution/thirdparty-licenses',
        }
        retrieved = []
        for name, url in documents.items():
            if (OUTPUT / name).is_file() and (OUTPUT / name).stat().st_size:
                retrieved.append({'file': name, 'source': url})
                continue
            print(f'Downloading {name}', flush=True)
            with urllib.request.urlopen(url, timeout=25) as response:
                content = response.read(2_000_001)
            if len(content) > 2_000_000:
                raise ValueError('License document unexpectedly large')
            (OUTPUT / name).write_bytes(content)
            retrieved.append({'file': name, 'source': url})
        (OUTPUT / 'upstream_documents.json').write_text(json.dumps(retrieved, indent=2), encoding='utf-8')
    (OUTPUT / 'installed_dependencies.json').write_text(json.dumps(records, indent=2), encoding='utf-8')
    print(f'Copied dependency notices for {len(records)} components')


if __name__ == '__main__':
    collect('--download' in sys.argv)
