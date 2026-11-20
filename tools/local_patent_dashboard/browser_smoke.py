"""Optional Chromium UI regression; all ledger/PDF data is temporary.

Install Playwright separately: python -m pip install playwright
Then: python -m playwright install chromium
Run from this directory: python browser_smoke.py
"""
import shutil
import tempfile
from contextlib import closing
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

import dashboard_config as config
from dashboard_database import connect
from dashboard_documents import upload_document, document_record, managed_path, save_analysis
from test_documents import DocumentTests, sample_pdf


def run_browser_checks(base, pdf_path, screenshots):
    with sync_playwright() as p:
     browser=p.chromium.launch(headless=True,executable_path=shutil.which('chromium') or None)
     context=browser.new_context(viewport={'width':1600,'height':1050})
     page=context.new_page();errors=[]
     page.on('pageerror',lambda e:errors.append(str(e)))
     page.on('dialog',lambda d:d.accept('1') if d.type=='prompt' else d.accept())
     page.goto(base+'/');page.locator('[data-view="ledger"]').click()
     page.locator('#ledgerTable .patent-reader-link').first.click()
     expect(page.locator('#patentTitle')).to_have_text('一种测试装置')
     expect(page.locator('#documentStatus')).to_contain_text('识别完成',timeout=90000)
     expect(page.locator('#leftPane .claim')).to_have_count(3,timeout=90000)
     expect(page.locator('#leftPane .claim.independent')).to_have_count(2)
     expect(page.locator('#leftPane .claim.dependent')).to_have_count(1)
     page.locator('#leftPane [data-reference="1"]').first.click()
     expect(page.locator('#rightPane [data-tab="drawings"]')).to_have_class('active')
     expect(page.locator('#rightPane .number-box').first).to_be_visible()
     page.locator('#rightPane img').wait_for()
     page.locator('#rightPane [data-annotate]').click()
     figure=page.locator('#rightPane [data-figure]')
     figure.click(position={'x':100,'y':120})
     expect(page.locator('#rightPane .number-box.manual').first).to_be_visible()
     page.locator('#rightPane [data-clear-annotations]').click()
     expect(page.locator('#rightPane .number-box.manual')).to_have_count(0)
     page.screenshot(path=str(screenshots / 'desktop.png'),full_page=True)
     with page.expect_popup() as event:page.locator('#popout').click()
     popup=event.value;popup.wait_for_load_state()
     expect(popup.locator('body')).to_have_class('single popout')
     page.locator('#leftPane [data-reference="2"]').first.click()
     expect(popup.locator('#rightPane .notice')).to_contain_text('(2)')
     expect(popup.locator('#rightPane .number-box').first).to_be_visible()
     page.locator('#leftPane [data-filter="dependent"]').click()
     expect(page.locator('#leftPane .claim')).to_have_count(1)
     page.locator('#leftPane [data-tab="fees"]').click();page.locator('#leftPane [data-add-fee]').click()
     page.locator('#feeType').fill('第3年年费');page.locator('#feeDue').fill('2027-01-01');page.locator('#feeAmount').fill('1500')
     page.locator('#feeForm button.primary').click()
     expect(page.locator('#leftPane .fee-item')).to_have_count(1)
     expect(page.locator('#leftPane .fee-item')).to_contain_text('1500.00')
     page.locator('#leftPane [data-edit-fee]').click();page.locator('#feeAmount').fill('1600');page.locator('#feeForm button.primary').click()
     expect(page.locator('#leftPane .fee-item')).to_contain_text('1600.00')
     page.locator('#leftPane [data-delete-fee]').click();expect(page.locator('#leftPane .fee-item')).to_have_count(0)
     page.locator('#review').click();page.locator('#claimsText').fill('1. 一种装置，包括壳体（1）。\n2. 根据权利要求1所述的装置。')
     page.locator('#reviewForm button.primary').click();expect(page.locator('#reviewDialog')).not_to_be_visible()
     page.locator('#leftPane [data-tab="claims"]').click();page.locator('#leftPane [data-filter="all"]').click()
     expect(page.locator('#leftPane .claim')).to_have_count(2)
     page.locator('#pdfFile').set_input_files(str(pdf_path))
     expect(page.locator('#documentStatus')).to_contain_text('识别完成',timeout=90000)
     expect(page.locator('#leftPane .claim')).to_have_count(3,timeout=90000)
     page.set_viewport_size({'width':390,'height':844});page.locator('#singleToggle').click()
     expect(page.locator('#rightPane')).not_to_be_visible()
     page.screenshot(path=str(screenshots / 'mobile.png'),full_page=True)
     assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), 'Mobile page overflows'
     # A legacy empty result must show the dependency failure, not success.
     record=document_record(1)
     save_analysis(managed_path(record['object_path']), {
         'status':'ready','progress':100,'pages':[{'text':'','ocr':False}],
         'claims':[],'references':[],'metadata':{},
         'warnings':['第 1 页：未找到 Tesseract OCR 引擎。']})
     page.reload()
     expect(page.locator('#documentStatus')).to_contain_text('未识别出文字')
     expect(page.locator('#leftPane')).to_contain_text('Tesseract OCR 引擎')
     expect(page.locator('#review')).not_to_be_visible()
     expect(page.locator('#retry')).to_be_visible()
     print('browser errors',errors)
     assert not errors,errors
     print('PASS: ledger navigation, claims filters, references, popup sync, fee CRUD, correction, PDF replacement and missing-OCR failure state')
     browser.close()


def main():
    fixture = DocumentTests()
    fixture.setUp()
    try:
        with closing(connect()) as connection, connection:
            connection.execute("UPDATE patents SET title='一种测试装置' WHERE id=?", (fixture.id,))
        pdf_path = config.DATA_DIR / 'sample.pdf'
        data = sample_pdf(scanned=True)
        pdf_path.write_bytes(data)
        upload_document(fixture.id, data, '测试专利.pdf')
        screenshots = Path(tempfile.gettempdir()) / 'patent-reader-ui'
        screenshots.mkdir(exist_ok=True)
        run_browser_checks(fixture.base, pdf_path, screenshots)
        print('Screenshots:', screenshots)
    finally:
        fixture.tearDown()


if __name__ == '__main__':
    main()
