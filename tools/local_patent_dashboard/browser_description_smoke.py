"""Verify source-numbered paragraphs and legacy cached descriptions in Chrome."""
from pathlib import Path
import shutil

from playwright.sync_api import sync_playwright, expect

import dashboard_reader as reader
from test_documents import DocumentTests
from test_reader import RAW, NUMBER
from dashboard_ppubs import convert_document

EXAMPLE = '''[0003]现代直流-直流开关模式功率变换器需要具备高功率密度、高效率和低成本。某些串联谐振式直流-直流功率变换器，例如传统的串联谐振变换器、LLC变换器或美国专利号5,907,236中描述的谐振抽头变压器变换器，试图通过变换器初级侧功率晶体管的零电压开关操作和次级侧输出整流器的零电流开关操作来满足这些要求。
[0004]零电压和零电流开关是降低开关损耗的成熟开关技术。这使得开关频率更高、磁性元件尺寸更小、功率密度更高、成本更低成为可能。另一种减小磁性元件尺寸的方法是将转换器正常运行所需的电感器（例如谐振电感器、励磁电感器和输出滤波电感器）集成到变压器中。变压器通常是电路中体积最大、成本最高的元件。
[0005]众所周知，变压器初级绕组和次级绕组之间的漏感可以用作谐振槽电感，或者说，可以将谐振电感集成到变压器结构中。漏感的值可以通过在径向或轴向上调整初级绕组和次级绕组的间距，以及使用所谓的“磁分流器”来控制。
[0006]它还利用变压器的励磁电感来存储能量并扩展负载电流范围，同时实现零电压开关。励磁电感的值可以通过设置变压器的气隙并改变气隙尺寸来控制。这种设计方法还能将所需的励磁电感集成到变压器的磁结构中。[0007]关于这种“集成磁性”设计技术的讨论，可以在 R. Severns 和 G. Bloom 的著作《现代 DC/DC 开关模式电源转换器电路》（Van Nostrand Reinhold 公司，1985年）中找到。[0008]集成磁性结构也在美国专利号4,262,328（Bloom）、美国专利号5,619,400（Bowman）和美国专利号5,555,494（Morris）中有所描述。'''


def main():
    harness = DocumentTests()
    harness.setUp()
    try:
        item = reader.create_item({'publicationNumber': NUMBER})
        data = convert_document(RAW, NUMBER)
        data['description'] = EXAMPLE
        data['claims'] = [{'number': 11, 'kind': 'dependent', 'dependencies': [10],
                           'text': 'The transformer of claim 10. 12-13. (canceled) 14. A converter. '
                                   '15. The converter of claim 14.'}]
        # Deliberately use the old cache shape, with no new parser metadata.
        reader._save_cache(f'library:{item["id"]}', {'status': 'ready', 'data': data})
        reader._save_cache(f'pdf:library:{item["id"]}', {'status': 'failed', 'publicationNumber': NUMBER, 'error': '离线'})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=shutil.which('chromium'))
            page = browser.new_page(viewport={'width': 1500, 'height': 1050})
            page.goto(harness.base + f'/reader.html?id={item["id"]}&library=1')
            claims = page.locator('#leftPane .claim')
            expect(claims).to_have_count(5)
            expect(claims.nth(3)).to_contain_text('独立权利要求')
            page.locator('#leftPane [data-filter="canceled"]').click()
            expect(claims).to_have_count(2)
            page.locator('#leftPane [data-filter="dependent"]').click()
            expect(claims).to_have_count(2)
            expect(claims.last).to_contain_text('引用 14')
            page.locator('#leftPane [data-filter="all"]').click()
            paragraphs = page.locator('#rightPane .description-paragraph')
            expect(paragraphs).to_have_count(6)
            assert page.locator('#rightPane .paragraph-number').all_text_contents() == [f'[{i:04}]' for i in range(3, 9)]
            expect(paragraphs.nth(4)).to_contain_text('关于这种“集成磁性”设计技术的讨论')
            expect(paragraphs.nth(5)).to_contain_text('4,262,328')
            assert page.locator('#rightPane .description-paragraph[data-original="false"]').count() == 0
            assert page.evaluate("getComputedStyle(document.querySelector('.description-paragraph')).display") == 'grid'
            output = Path(__file__).resolve().parents[2] / 'outputs/independent-reader'
            output.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(output / 'numbered-description.png'))
            citation = page.evaluate("descriptionBlocks('[0003]第一段引用段落 [0004] 作为参考。\\n[0005]下一段。', true)")
            assert [p['number'] for p in citation] == ['0003', '0005']
            assert '[0004]' in citation[0]['body']
            wrapped = page.evaluate("descriptionBlocks('A wrapped line\\ncontinues here.\\n\\nSecond paragraph.', false)")
            assert len(wrapped) == 2 and wrapped[0]['body'] == 'A wrapped line continues here.'
            wide = page.evaluate("descriptionBlocks('［ ０００３ ］第一段。［0004］第二段。', true)")
            assert [p['number'] for p in wide] == ['0003', '0004']
            data['description'] = 'BACKGROUND\nFirst unnumbered paragraph.\nSecond unnumbered paragraph.'
            reader._save_cache(f'library:{item["id"]}', {'status': 'ready', 'data': data})
            page.reload()
            expect(page.locator('#rightPane .description-paragraph')).to_have_count(2)
            expect(page.locator('#rightPane .description-heading')).to_have_text('BACKGROUND')
            expect(page.locator('#rightPane')).to_contain_text('自动生成的阅读编号')
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            browser.close()
            print('PASS: six original paragraphs, adjacent markers, inline references, PDF wraps, legacy cache, full-width markers, generated reading numbers and mobile layout')
    finally:
        harness.tearDown()


if __name__ == '__main__':
    main()
