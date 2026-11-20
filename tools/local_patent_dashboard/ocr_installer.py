"""Fetch/verify the pinned Windows OCR installer without weakening TLS or hashes."""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

VERSION = '5.4.0.20240606'
FILENAME = f'tesseract-ocr-w64-setup-{VERSION}.exe'
# Microsoft WinGet manifest for UB-Mannheim.TesseractOCR 5.4.0.20240606.
SHA256 = 'c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9'
URLS = (
    f'https://github.com/UB-Mannheim/tesseract/releases/download/v{VERSION}/{FILENAME}',
    f'https://digi.bib.uni-mannheim.de/tesseract/{FILENAME}',
)
MAX_BYTES = 256 * 1024 * 1024


def verify_installer(path):
    path = Path(path)
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != SHA256:
        raise ValueError(f'OCR 安装包 SHA-256 不匹配，未执行。需要固定版本 {VERSION}，期望 {SHA256}。')
    return path.resolve()


def ensure_installer(target):
    target = Path(target)
    if target.is_file():
        try:
            return verify_installer(target)
        except ValueError:
            print('本地安装包校验不匹配，尝试重新下载官方文件。',file=sys.stderr)
    target.parent.mkdir(parents=True,exist_ok=True)
    for url in URLS:
        temporary = None
        try:
            print(f'下载官方 OCR 安装包：{url}',flush=True)
            with tempfile.NamedTemporaryFile(prefix='.tesseract-',suffix='.part',dir=target.parent,delete=False) as output:
                temporary = Path(output.name)
                total = 0
                # Inherit supported proxy/CA settings; certificate verification stays on.
                with urllib.request.urlopen(url,timeout=120) as response:
                    while chunk := response.read(1024*1024):
                        total += len(chunk)
                        if total > MAX_BYTES:
                            raise ValueError('安装包超过预期大小，下载已停止。')
                        output.write(chunk)
            verify_installer(temporary)
            temporary.replace(target)
            return target.resolve()
        except (OSError, urllib.error.URLError, ValueError) as exc:
            print(f'该官方地址下载或校验失败：{exc}',file=sys.stderr)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    raise ValueError('官方安装包下载失败。可用浏览器或另一台联网电脑下载固定版本安装包，再执行 build.bat -OcrInstaller "安装包完整路径"。安装包仍会校验 SHA-256。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    options.add_argument('--download',type=Path)
    options.add_argument('--verify',type=Path)
    args = parser.parse_args()
    try:
        path = ensure_installer(args.download) if args.download else verify_installer(args.verify)
        print(f'OCR 安装包校验通过：{path}')
        return 0
    except (OSError,ValueError) as exc:
        print(str(exc),file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
