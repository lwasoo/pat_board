"""Small deterministic regressions for publisher headings and column ordering."""
import unittest
from dashboard_patent_text import (
    classify_page, is_drawing_sheet, order_words, parse_claims, split_sections,
    extract_metadata, jurisdiction, check_declared_counts,
)


class PatentTextTests(unittest.TestCase):
    def test_drawing_description_is_specification(self):
        text='US 10,211,630 B1\nDESCRIPTION OF DRAWINGS\nFIG. 1 shows a data center.\nDETAILED DESCRIPTION\nA transformer supplies power.'
        self.assertFalse(is_drawing_sheet(text))
        self.assertEqual(classify_page(text,'drawings'),'description')
        self.assertEqual(classify_page('The description and drawings illustrate the claims.','description'),'description')
        self.assertEqual(classify_page('Some continued paragraphs.','drawings'),'basic')

    def test_true_drawing_headers_and_cover_counts(self):
        self.assertTrue(is_drawing_sheet('U.S. Patent Feb. 19, 2019 Sheet 1 of 7 US 10,211,630 B1\nFIG. 1'))
        self.assertTrue(is_drawing_sheet('CN 119853429 A 说 明 书 附 图 1/11页\n图1'))
        self.assertEqual(classify_page('(12) United States Patent\n24 Claims, 7 Drawing Sheets'),'basic')
        self.assertEqual(classify_page('(19)国家知识产权局\n权利要求书3页 说明书13页 附图11页'),'basic')

    def test_claims_start_mid_page_and_continue(self):
        text='Continuation of the description.\nWhat is claimed is:\n1. A power system comprising:\na transformer; and\na converter.\n2. The system of claim 1, wherein the converter is mounted in a rack.'
        parts,last=split_sections(text,'description',page_number=20)
        self.assertEqual([p['section'] for p in parts],['description','claims'])
        self.assertNotIn('Continuation',parts[1]['text'])
        self.assertEqual(last,'claims')
        claims=parse_claims(parts[1]['text']+'\n3. The system of claims 1 or 2, comprising a bus.')
        self.assertEqual([c['number'] for c in claims],[1,2,3])
        self.assertEqual(claims[1]['dependencies'],[1])
        self.assertEqual(claims[2]['dependencies'],[1,2])

    def test_two_columns_are_not_interleaved(self):
        words=[]
        for row in range(20):
            for col,x in [('LEFT',80),('RIGHT',330)]:
                for j in range(5):
                    value=f'{col}{row}_{j}'
                    words.append((x+j*35,80+row*20,x+j*35+30,93+row*20,value))
        words.extend([(299,120,305,133,'5'),(255,50,349,71,'US11111111B1')])
        text,layout=order_words(words,612,792,'US','US11111111B1\nTwo column specification')
        self.assertEqual(layout,'two-column')
        self.assertLess(text.index('LEFT19_4'),text.index('RIGHT0_0'))
        self.assertNotIn('US11111111B1',text)
        self.assertNotIn('\n5\n',text)

    def test_country_uses_own_publication_before_foreign_references(self):
        self.assertEqual(jurisdiction("US10211630B1\nUnited States Patent\nForeign references: CN119853429A"), "US")

    def test_english_inid_metadata(self):
        text='US 10,211,630 B1\n(54) DATA CENTER WITH LARGE MEDIUM\nVOLTAGE DOMAIN\n(71) Applicant: Google LLC\n(21) Appl. No.: 14/040,381\n(22) Filed: Sep. 27, 2013\n(57) ABSTRACT\nA method of providing power.\n24 Claims, 7 Drawing Sheets'
        m=extract_metadata(text)
        self.assertEqual(m['title'],'DATA CENTER WITH LARGE MEDIUM VOLTAGE DOMAIN')
        self.assertEqual(m['publicationNumber'],'US10211630B1')
        self.assertEqual(m['applicationDate'],'2013-09-27')
        self.assertEqual(m['abstract'],'A method of providing power.')

    def test_cover_declarations_detect_incomplete_extraction(self):
        quality,warnings=check_declared_counts('24 Claims, 7 Drawing Sheets', {'basic':[1], 'claims':[], 'description':[], 'drawings':[2]}, 0)
        self.assertFalse(quality['passed'])
        self.assertEqual(quality['expectedClaimCount'],24)
        self.assertEqual(len(warnings),2)

    def test_footer_and_claim_continuations(self):
        claims=parse_claims('1. 一种装置。\n2. 根据权利要求1所述的装置，所述电\nCN 119853429 A 权 利 要 求 书 3/3页\n连接件用于传输信号。\n44')
        self.assertEqual(claims[-1]['text'],'根据权利要求1所述的装置，所述电连接件用于传输信号。')


if __name__=='__main__': unittest.main()
