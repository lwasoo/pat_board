"""Export editable sources with a relocatable Windows build environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

from build_windows import REPO, ROOT, copy_models, copy_runtime, find_runtime


def build_source_kit(tesseract):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('源码依赖包需要在 Windows x64 上生成。')
    if sys.prefix != sys.base_prefix:
        raise ValueError('请用完整 Python 安装生成依赖包，不能复制虚拟环境。')
    engine = find_runtime(tesseract)
    if not engine:
        raise ValueError('未找到 OCR 引擎。')
    outputs = REPO / 'outputs'
    outputs.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.source-kit-', dir=outputs) as temporary:
        kit = Path(temporary) / '中文离线源码 kit'
        kit.mkdir()
        tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=REPO)
        for filename in tracked.decode('utf-8').split('\0'):
            if not filename:
                continue
            source = REPO / filename
            if source.is_symlink():
                raise ValueError(f'源码包不支持符号链接：{filename}')
            destination = kit / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        # Copy the full installation, including stdlib, Python DLL, headers,
        # Tcl/Tk and installed build packages. A venv retains absolute paths.
        python_home = kit / '.build-python'
        shutil.copytree(sys.base_prefix, python_home,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'Scripts'))
        app = kit / ROOT.relative_to(REPO)
        copy_runtime(engine, app / 'ocr/tesseract')
        copy_models(ROOT / 'ocr/tessdata', app / 'ocr/tessdata')
        (kit / '离线编译说明.txt').write_text(
            '此目录包含完整源码和 Windows x64 编译依赖。\n'
            '修改 tools/local_patent_dashboard 内的 Python、HTML、JS、CSS 后，双击根目录 build.bat。\n'
            '输出程序位于 outputs/单机版专利看板。\n'
            'Python、PyInstaller、PyMuPDF、OCR 引擎、DLL 和模型已附带，无需安装。\n'
            '换电脑时复制整个目录，包括 .build-python 和 ocr。\n'
            '已有台账及上传文件请保留在运行程序旁的 data 中。\n', encoding='utf-8-sig')
        env = os.environ.copy()
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env.pop('PATENT_TESSERACT_CMD', None)
        env.pop('PATENT_TESSDATA_DIR', None)
        env['PYTHONNOUSERSITE'] = '1'
        system_root = Path(env.get('SystemRoot', r'C:\Windows'))
        env['PATH'] = str(system_root / 'System32')
        # Exercise the source package with downloads disabled and with only
        # Windows system directories in PATH, using the bundled Python.
        for proxy in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
            env[proxy] = 'http://127.0.0.1:9'
        env['PIP_NO_INDEX'] = '1'
        python = python_home / 'python.exe'
        subprocess.run([str(python), '-c',
                        'import sys, pathlib, PyInstaller, pymupdf; '
                        'assert pathlib.Path(sys.prefix).resolve() == pathlib.Path(sys.executable).resolve().parent'],
                       cwd=kit, env=env, check=True)
        powershell = system_root / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.run([str(powershell), '-NoProfile', '-ExecutionPolicy', 'Bypass',
                        '-File', str(kit / 'build.ps1'), '-NoPause'],
                       cwd=kit, env=env, check=True)
        # The artifact delivers source + dependencies. Compilation above only
        # verifies that a recipient can build it on a clean computer.
        shutil.rmtree(kit / 'outputs')
        archive = outputs / 'patent-dashboard-source-offline-windows-x64.zip'
        temporary_zip = Path(temporary) / archive.name
        with zipfile.ZipFile(temporary_zip, 'w', zipfile.ZIP_DEFLATED) as output:
            for path in sorted(kit.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    output.write(path, Path('patent-dashboard-source') / path.relative_to(kit))
        with zipfile.ZipFile(temporary_zip) as output:
            if output.testzip():
                raise ValueError('源码依赖包 ZIP 校验失败。')
        temporary_zip.replace(archive)
        print(f'源码及离线编译依赖：{archive}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tesseract', required=True)
    args = parser.parse_args()
    try:
        build_source_kit(args.tesseract)
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'源码依赖包生成失败：{exc}', file=sys.stderr)
        raise SystemExit(1)
