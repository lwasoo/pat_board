"""Exercise independent library, PDF reading, PPUBS failures and cached text in Chrome."""
from contextlib import closing
from pathlib import Path
import shutil
from unittest.mock import patch

from playwright.sync_api import sync_playwright, expect

import dashboard_reader as reader
import dashboard_documents as documents
from dashboard_database import connect
from test_documents import DocumentTests, sample_pdf
from test_reader import RAW, NUMBER
from dashboard_ppubs import convert_document, PublicationChoices


def main():
    harness = DocumentTests()
    harness.setUp()
    output = Path(__file__).resolve().parents[2] / 'outputs/independent-reader'
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium'))
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.on('dialog', lambda dialog: dialog.accept())
            page.goto(harness.base)
            expect(page.locator('h1')).to_have_text('我的专利文献')
            page.locator('#libraryPdf').set_input_files({'name': '阅读测试.pdf', 'mimeType': 'application/pdf', 'buffer': sample_pdf()})
            expect(page.locator('#documentStatus')).to_contain_text('识别完成', timeout=60000)
            expect(page.locator('#leftPane .claim')).to_have_count(3)
            expect(page.locator('[data-tab="fees"]')).to_have_count(0)
            assert 'library=1' in page.url
            page.screenshot(path=str(output / 'pdf-reader.png'))
            page.get_by_role('link', name='返回文献库').last.click()
            expect(page.locator('.library-card')).to_have_count(1)
            page.locator('#addNumber').click()
            page.locator('#newNumber').fill(NUMBER[:-2])
            with patch.object(reader.PpubsClient, 'fetch', side_effect=ValueError('测试网络不可达，本地内容仍可阅读')):
                page.locator('#createItem').click()
                expect(page.locator('#patentTitle')).to_have_text(NUMBER[:-2])
                expect(page.locator('#onlineStatus')).to_contain_text('测试网络不可达', timeout=10000)
            def resolve(number):
                if number.endswith('A1'):
                    return convert_document(RAW, NUMBER)
                raise PublicationChoices([{'publicationNumber': NUMBER}, {'publicationNumber': 'US20250337340A9'}])
            with patch.object(reader.PpubsClient, 'fetch', side_effect=resolve), patch.object(
                    reader.PpubsClient, 'download_pdf', side_effect=ValueError('测试原文图像服务不可达')) as download:
                page.locator('#fetchText').click()
                expect(page.locator('#versionChoices')).to_be_visible(timeout=10000)
                download.assert_not_called()
                page.locator(f'[data-version="{NUMBER}"]').click()
                expect(page.locator('#pdfDownloadStatus')).to_contain_text('测试原文图像服务不可达', timeout=10000)
                download.assert_called_once_with(NUMBER)
                expect(page.locator('#onlineStatus')).to_contain_text('已缓存', timeout=10000)
                expect(page.locator('#leftPane .claim')).to_have_count(2)
                expect(page.locator('#leftPane .claim.dependent')).to_have_count(1)
                expect(page.locator('#rightPane')).to_contain_text('[0002]')
            with patch.object(reader.PpubsClient, 'fetch', side_effect=AssertionError('Cache read must be offline')):
                page.reload()
                expect(page.locator('#leftPane .claim')).to_have_count(2)
                expect(page.locator('#rightPane')).to_contain_text('[0001]')
                with page.expect_popup() as event:
                    page.locator('#popout').click()
                popup = event.value
                expect(popup.locator('#rightPane')).to_contain_text('[0002]')
                popup.close()
            page.screenshot(path=str(output / 'cached-text.png'))
            with patch.object(reader.PpubsClient, 'download_pdf', return_value=sample_pdf()):
                page.locator('#fetchPdf').click()
                expect(page.locator('#documentStatus')).to_contain_text('原 PDF 已就绪', timeout=60000)
                expect(page.locator('#rightPane [data-figure]')).to_have_count(1)
                expect(page.locator('#fetchPdf')).to_be_disabled()
                expect(page.locator('#usePdf')).to_be_disabled()
                page.locator('#rightPane [data-tab="pdf"]').click()
                expect(page.locator('#rightPane iframe')).to_be_visible()
                page.locator('#rightPane [data-tab="drawings"]').click()
                expect(page.locator('#leftPane .claim')).to_have_count(2)
                page.screenshot(path=str(output / 'downloaded-drawings.png'))

            page.reload()
            expect(page.locator('#leftPane .claim')).to_have_count(2)
            expect(page.locator('#rightPane [data-figure]')).to_have_count(1)
            page.get_by_role('link', name='返回文献库').last.click()
            numbered = convert_document({**RAW, 'descriptionHtml': '<p>[0001] A transformer (104).</p>'}, NUMBER)
            label = {'text': '104', 'source': 'ocr', 'confidence': 95, 'box': [.3, .4, .05, .02]}
            with patch.object(reader.PpubsClient, 'fetch', return_value=numbered) as fetch, patch.object(
                    reader.PpubsClient, 'download_pdf', return_value=sample_pdf()) as download, patch.object(
                    documents, 'ocr_page', return_value=('', [label])) as ocr:
                page.locator('#addNumber').click()
                page.locator('#newNumber').fill(NUMBER)
                page.locator('#createItem').click()
                expect(page.locator('#leftPane .claim')).to_have_count(2)
                expect(page.locator('#rightPane [data-figure]')).to_have_count(1, timeout=60000)
                page.reload()
                expect(page.locator('#rightPane [data-figure]')).to_have_count(1)
                ocr.assert_not_called()
                page.locator('#leftPane [data-reference="104"]').last.click()
                expect(page.locator('#rightPane .number-box')).to_have_count(1, timeout=10000)
                ocr_calls = ocr.call_count
                assert ocr_calls == 2, ocr_calls
                page.reload()
                page.locator('#leftPane [data-reference="104"]').last.click()
                expect(page.locator('#rightPane .number-box')).to_have_count(1)
                assert ocr.call_count == ocr_calls
                fetch.assert_called_once_with(NUMBER)
                download.assert_called_once_with(NUMBER)
            page.get_by_role('link', name='返回文献库').last.click()
            expect(page.locator('.library-card')).to_have_count(3)
            page.screenshot(path=str(output / 'library.png'))
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.screenshot(path=str(output / 'library-mobile.png'), full_page=True)
            with closing(connect()) as conn:
                assert conn.execute('SELECT COUNT(*) FROM patents').fetchone()[0] == 1
                assert conn.execute('SELECT COUNT(*) FROM fees').fetchone()[0] == 0
                assert conn.execute('SELECT COUNT(*) FROM reading_items').fetchone()[0] == 3
            assert not errors, errors
            browser.close()
            print('PASS: independent PDF library, no fee prerequisite, PPUBS failure, version selection, structured cached text, PDF download failure/retry, drawing panel, offline reload, popup and mobile layout')
    finally:
        reader._WORKER.submit(lambda: None).result(timeout=10)
        harness.tearDown()


if __name__ == '__main__':
    main()
