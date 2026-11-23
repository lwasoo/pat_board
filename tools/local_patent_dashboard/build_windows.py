"""Build and validate a Windows portable bundle without including user data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from dashboard_config import APP_NAME
from dashboard_documents import ocr_command

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
APP_EXE = APP_NAME + '.exe'
MANAGED = [APP_EXE, 'start.bat', '使用说明.txt', 'build-info.json', 'ocr']


def find_runtime(explicit=None):
    if explicit:
        path = Path(explicit).expanduser().resolve()
    else:
        command = ocr_command()
        if not command:
            return None
        path = Path(command[0]).resolve()
    if path.name.lower() != 'tesseract.exe' or not path.is_file():
        raise ValueError('Tesseract 路径无效，请指定已安装的 tesseract.exe。')
    return path


def copy_runtime(executable, target):
    """Copy the engine, DLL tree and notices, excluding uninstallers and user files."""
    executable = Path(executable)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(executable, target/'tesseract.exe')
    copied = ['tesseract.exe']
    for source in executable.parent.rglob('*'):
        if source.is_symlink() or not source.is_file() or source == executable:
            continue
        relative = source.relative_to(executable.parent)
        if 'tessdata' in {part.lower() for part in relative.parts}:
            continue
        notice = re.match(r'(?i)^(?:license|copying|copyright|notice|authors)', source.name)
        notice_folder = any(part.lower() in {'licenses', 'license'} for part in relative.parts[:-1])
        if source.suffix.lower() != '.dll' and not notice and not notice_folder:
            continue
        destination = target/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative.as_posix())
    return copied


def copy_models(source, target):
    target.mkdir(parents=True, exist_ok=True)
    for language in ('chi_sim', 'eng'):
        path = source/(language+'.traineddata')
        if not path.is_file():
            raise ValueError(f'缺少 {language} 模型，请先运行 setup_ocr.py。')
        shutil.copy2(path, target/path.name)
    for source_notice in source.glob('LICENSE*'):
        shutil.copy2(source_notice, target/source_notice.name)


def write_launcher(bundle):
    # An ASCII BAT can launch Chinese paths through Windows' wide-character APIs.
    # Discover the only top-level application EXE rather than embedding its name.
    text = r'''@echo off
setlocal
cd /d "%~dp0"
set "PATENT_TESSERACT_CMD=%~dp0ocr\tesseract\tesseract.exe"
set "PATENT_TESSDATA_DIR=%~dp0ocr\tessdata"
set "PATH=%~dp0ocr\tesseract;%PATH%"
if not exist "%PATENT_TESSERACT_CMD%" goto incomplete
if not exist "%PATENT_TESSDATA_DIR%\chi_sim.traineddata" goto incomplete
if not exist "%PATENT_TESSDATA_DIR%\eng.traineddata" goto incomplete
"%PATENT_TESSERACT_CMD%" --version >nul 2>&1
if errorlevel 1 goto runtime_error
set "PATENT_APP="
set "PATENT_APP_COUNT=0"
for %%F in ("%~dp0*.exe") do (
  set "PATENT_APP=%%~fF"
  set /a PATENT_APP_COUNT+=1 >nul
)
if not "%PATENT_APP_COUNT%"=="1" goto incomplete
if not defined PATENT_APP goto incomplete
if not exist "%PATENT_APP%" goto incomplete
start "" "%PATENT_APP%"
exit /b 0
:incomplete
echo Incomplete portable bundle. Extract the entire ZIP, including the ocr folder.
pause
exit /b 1
:runtime_error
echo OCR runtime could not start. Keep all DLLs in the ocr\tesseract folder.
pause
exit /b 1
'''
    (bundle/'start.bat').write_bytes(text.replace('\n', '\r\n').encode('ascii'))
    (bundle/'使用说明.txt').write_text('''企业专利统计看板 · Windows 便携版

完整解压后直接双击本目录的看板 EXE。start.bat 只是可选的依赖检查入口。
升级时先退出旧看板，避免浏览器继续连接仍在运行的旧服务。
整个目录包含 Python 应用、Tesseract OCR 引擎、依赖 DLL 和中英文识别模型；
使用电脑无需单独安装 Python 或 Tesseract，不需要手工配置环境变量。
不要只复制单个 EXE，必须保留 ocr 文件夹。请使用 Windows 10/11 64 位。

台账和上传文件保存在本目录的 data 文件夹。移动或备份时保留整个 data 目录。
ZIP 不包含打包电脑的数据库、上传专利或通知配置。重新打包会保留既有输出目录中的 data。
升级识别规则后，在原专利页面点“重新识别”；人工校对过的识别结果会被替换。
OCR 结果与图号定位仍应对照原文核对。
''',encoding='utf-8-sig')


def validate_runtime(bundle):
    """Run from a relocated directory with the installed runtime removed from PATH."""
    executable = bundle/'ocr/tesseract/tesseract.exe'
    models = 'ocr/tessdata'
    env = os.environ.copy()
    env.pop('TESSDATA_PREFIX', None)
    system_root = env.get('SystemRoot', r'C:\Windows')
    env['PATH'] = str(executable.parent)+os.pathsep+str(Path(system_root)/'System32')+os.pathsep+system_root
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    version = subprocess.run([str(executable),'--version'],cwd=bundle,env=env,capture_output=True,timeout=30,creationflags=flags)
    output = (version.stdout+version.stderr).decode('utf-8',errors='replace')
    if version.returncode or not re.search(r'tesseract\s+v?5\.',output,re.I):
        raise ValueError('便携 OCR 引擎未通过启动验证，需要 Tesseract 5 及完整依赖 DLL。\n'+output[:1000])
    languages = subprocess.run([str(executable),'--tessdata-dir',str(models),'--list-langs'],cwd=bundle,env=env,capture_output=True,timeout=30,creationflags=flags)
    if languages.returncode or not all(re.search(rb'(?m)^'+name+rb'\r?$',languages.stdout) for name in (b'chi_sim',b'eng')):
        raise ValueError('便携 OCR 缺少可加载的中英文模型。')
    # A version check cannot detect all missing image/OCR DLL dependencies.
    import pymupdf
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=350,height=100)
        page.insert_text((20,55),'PORTABLE OCR 12345',fontsize=23)
        png = page.get_pixmap(matrix=pymupdf.Matrix(2,2),alpha=False).tobytes('png')
    for language in ('eng','chi_sim'):
        result = subprocess.run([str(executable),'stdin','stdout','--tessdata-dir',str(models),'-l',language,'--psm','6'],
            input=png,cwd=bundle,env=env,capture_output=True,timeout=60,creationflags=flags)
        if result.returncode or b'12345' not in result.stdout:
            raise ValueError(f'便携 OCR 的 {language} 实际识别验证未通过；不会发布不完整的包。')
    env.pop('PATENT_TESSERACT_CMD', None)
    env.pop('PATENT_TESSDATA_DIR', None)
    env['PATH'] = str(Path(system_root)/'System32')+os.pathsep+system_root
    application = subprocess.run([str(bundle/APP_EXE), '--check-runtime'],cwd=bundle,env=env,
        capture_output=True,timeout=90,creationflags=flags)
    if application.returncode:
        raise ValueError('打包后的看板运行依赖检查失败；请检查 PyMuPDF、DLL 及识别模型。')
    return output.splitlines()[0]


def make_archive(bundle, archive):
    # Whitelist only build-managed files; never include an existing data directory.
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as output:
        for name in MANAGED:
            root = bundle/name
            for path in sorted(root.rglob('*')) if root.is_dir() else [root]:
                if path.is_file():
                    output.write(path, Path(APP_NAME)/path.relative_to(bundle))
    with zipfile.ZipFile(archive) as output:
        if output.testzip() is not None:
            raise ValueError('便携 ZIP 校验失败。')


def publish_bundle(bundle, target, archive=None, archive_target=None):
    """Replace only managed outputs, rolling back errors and leaving data untouched."""
    target.mkdir(parents=True,exist_ok=True)
    backup = target.parent/('.portable-backup-'+uuid.uuid4().hex)
    backup.mkdir()
    operations = [(bundle/name,target/name) for name in MANAGED if (bundle/name).exists()]
    if archive is not None:
        operations.append((archive,archive_target))
    moved_old, installed = [], []
    try:
        for index,(source,destination) in enumerate(operations):
            if destination.exists():
                saved = backup/str(index)
                destination.replace(saved)
                moved_old.append((saved,destination))
            source.replace(destination)
            installed.append(destination)
    except OSError as exc:
        try:
            for destination in reversed(installed):
                if destination.is_dir(): shutil.rmtree(destination)
                else: destination.unlink(missing_ok=True)
            for saved,destination in reversed(moved_old):
                saved.replace(destination)
        except OSError as recovery:
            raise ValueError(f'无法恢复旧输出，备份保留在 {backup}；data 未修改。') from recovery
        shutil.rmtree(backup)
        raise ValueError('无法更新输出，请先退出正在运行的看板后重试；已有 data 未修改。') from exc
    try:
        shutil.rmtree(backup)
    except OSError:
        # The validated new bundle is already published. Windows antivirus or a
        # previous process may still hold the old EXE; keep its backup for later.
        print(f'新便携包已生成；旧版本备份暂时被占用，可稍后清理：{backup}')


def build(explicit=None, output=None, *, portable_zip=False):
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise ValueError('请在 64 位 Windows 上运行 build.bat，以生成 Windows 便携版。')
    executable = find_runtime(explicit)
    if not executable:
        raise ValueError('离线构建未找到本地 Tesseract 5，请用 -TesseractExe 指定路径；不会下载或安装。')
    version = subprocess.run([str(executable), '--version'], capture_output=True, timeout=30,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if version.returncode or not re.search(rb'tesseract\s+v?5\.', version.stdout+version.stderr, re.I):
        raise ValueError('打包需要可运行的 Tesseract 5，请先升级或修复 OCR 引擎。')
    from setup_ocr import main as setup_models
    setup_models(offline=True)
    target = Path(output).resolve() if output else REPO/'outputs/单机版专利看板'
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.portable-build-',dir=target.parent) as temporary:
        temporary = Path(temporary)
        bundle = temporary/'bundle'
        env = os.environ.copy()
        env['PATENT_EXTERNAL_OCR'] = '1'
        spec = next(ROOT.glob('*.spec'))
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean',
            '--distpath',str(bundle),'--workpath',str(temporary/'work'),str(spec)],cwd=REPO,env=env,check=True)
        if not (bundle/APP_EXE).is_file():
            raise ValueError('未生成看板 EXE。')
        copy_runtime(executable,bundle/'ocr/tesseract')
        copy_models(ROOT/'ocr/tessdata',bundle/'ocr/tessdata')
        if portable_zip:
            write_launcher(bundle)
        # Validate after moving to a different path, including spaces and CJK.
        relocated = temporary/'移位验证 portable'
        bundle.replace(relocated)
        validate_runtime(relocated)
        if portable_zip:
            archive_name = 'patent-dashboard-windows-x64.zip'
            archive = temporary / archive_name
            archive_target = target.parent / archive_name
            make_archive(relocated, archive)
            publish_bundle(relocated, target, archive, archive_target)
            print(f'便携 ZIP：{archive_target}')
        else:
            publish_bundle(relocated, target)
    if not portable_zip:
        for name in ('start.bat', '使用说明.txt', 'build-info.json'):
            (target/name).unlink(missing_ok=True)
    print(f'输出目录：{target}\n直接双击 EXE；保留 ocr 文件夹。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--find-tesseract',action='store_true')
    parser.add_argument('--tesseract')
    parser.add_argument('--output')
    parser.add_argument('--portable-zip', action='store_true', help='生成含启动 BAT 的分发 ZIP')
    args = parser.parse_args()
    try:
        if args.find_tesseract:
            executable = find_runtime(args.tesseract)
            if executable:
                print(executable)
                return 0
            return 3
        build(args.tesseract, args.output, portable_zip=args.portable_zip)
        return 0
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        print(f'打包失败：{exc}',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
