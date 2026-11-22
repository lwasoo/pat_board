"""Download pinned official Chinese/English Tesseract models with SHA-256 checks."""
import hashlib
import shutil
import urllib.request
from pathlib import Path

MODELS = {
    # Use the higher-accuracy Chinese LSTM model; English also covers drawing digits.
    'chi_sim': ('tessdata_best', 'e12c65a915945e4c28e237a9b52bc4a8f39a0cec',
                '4fef2d1306c8e87616d4d3e4c6c67faf5d44be3342290cf8f2f0f6e3aa7e735b'),
    'eng': ('tessdata_fast', '87416418657359cb625c412a48b6e1d6d41c29bd',
            '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'),
}


def main(offline=False):
    directory = Path(__file__).resolve().parent / 'ocr' / 'tessdata'
    directory.mkdir(parents=True, exist_ok=True)
    license_path = Path(__file__).resolve().parent / 'third_party/OCR-MODELS-LICENSE.txt'
    shutil.copy2(license_path, directory / 'LICENSE-models.txt')
    for language, (repository, revision, digest) in MODELS.items():
        target = directory / (language + '.traineddata')
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            print(language + ': verified')
            continue
        if offline:
            raise ValueError(f'离线构建：本地模型缺失或校验不通过 {target}；未尝试下载。')
        url = f'https://raw.githubusercontent.com/tesseract-ocr/{repository}/{revision}/{language}.traineddata'
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(20 * 1024 * 1024)
        if hashlib.sha256(data).hexdigest() != digest:
            raise RuntimeError(language + ': checksum mismatch; model was not installed')
        temporary = target.with_suffix('.tmp')
        temporary.write_bytes(data)
        temporary.replace(target)
        print(language + ': installed and verified')


if __name__ == '__main__':
    main()
