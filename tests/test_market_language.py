import json
from pathlib import Path
import tempfile
import unittest

from market_language import analyze, asr_context, lexicon
from workbuddy.protocol import create_task
from workbuddy.run import prepare_record, read


class MarketLanguageTests(unittest.TestCase):
    def test_goods_corrections_preserve_prices_and_units(self):
        cases = {
            '东兴班每斤八十八元，罗飞鱼每斤35.5元': '东星斑每斤八十八元，罗非鱼每斤35.5元',
            '帮我做一张东兴班的海报': '帮我做一张东星斑的海报',
            '我卖金仓鱼，做海报': '我卖金鲳鱼，做海报',
            '不要罗非鱼，要鲈鱼，每斤三十五元': '不要罗非鱼，要鲈鱼，每斤三十五元',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                result = analyze(raw)
                self.assertEqual(result['text'], expected)
                self.assertEqual(result['raw_text'], raw)

    def test_explicit_names_and_possible_named_shops_are_protected(self):
        for raw in ['店名叫东兴班，东兴班每斤88元', '店面叫东兴班，卖鲈鱼',
                    '我的店叫东兴班，做海报', '招牌写“东兴班”，卖鱼',
                    '东兴班海鲜档，帮我做海报', '店名叫鱼汤，给我的鱼汤做个招牌']:
            result = analyze(raw)
            # Repeated explicitly named occurrences are protected across the utterance.
            self.assertEqual(result['text'], raw)
        raw = '我卖东兴班，做海报'
        self.assertEqual(analyze(raw, ['东兴班'])['text'], raw)

    def test_soup_is_not_globally_replaced(self):
        self.assertEqual(analyze('给我的鱼汤做个招牌')['text'], '给我的鱼摊做个招牌')
        for raw in ['鱼汤十五元一碗', '帮我做一张鱼汤海报', '给我的鱼汤做张海报',
                    '给我的鱼汤做个招牌，一碗十五元', '我在鱼摊卖鱼汤',
                    '我要喝鱼汤', '豆腐鱼汤，做个招牌', '店名叫鱼汤，做个招牌']:
            self.assertEqual(analyze(raw)['text'], raw)
        self.assertEqual(analyze('鱼汤十五元一碗')['uncertain'], [])

    def test_venue_equivalence_preserves_wording_and_scale(self):
        for venue in ['海鲜市场', '鱼类集市', '鱼类市集', '鱼摊']:
            raw = '帮我做一张'+venue+'的海报'
            result = analyze(raw)
            self.assertEqual(result['text'], raw)
            self.assertIn('aquatic_sales', [v['group'] for v in result['venues']])

    def test_recognizes_dialects_and_does_not_merge_distinct_fish(self):
        raw = '濑尿虾、通菜、牛展、巴沙鱼、龙利鱼、罗非鱼、鲈鱼'
        result = analyze(raw)
        self.assertEqual(result['text'], raw)
        self.assertEqual({v['text'] for v in result['terms']}, set(raw.split('、')))
        self.assertEqual(analyze('东兴班')['text'], '东兴班')
        self.assertTrue(analyze('东兴班')['uncertain'])
        self.assertEqual(analyze('东兴班每斤88元', enabled=False)['text'], '东兴班每斤88元')

    def test_compact_asr_vocabulary_excludes_wrong_spellings(self):
        context = asr_context('已有上下文', ['测试店名'])
        self.assertIn('东星斑', context)
        self.assertIn('已有上下文', context)
        self.assertIn('测试店名', context)
        self.assertNotIn('东兴班', context)
        self.assertLess(len(context), 800)

    def test_corrected_record_reaches_workbuddy_with_original_and_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = '帮我做一张东兴班的海报'
            analysis = analyze(raw)
            record = root / 'record.json'
            record.write_text(json.dumps({'id': 'example', 'reason': 'hangup', 'text': analysis['text'],
                                          'correction': analysis}), encoding='utf-8')
            job = prepare_record(record, root, root)
            task = read(job / 'request.json')
            self.assertEqual(task['transcript'], '帮我做一张东星斑的海报')
            self.assertEqual(task['raw_transcript'], raw)
            self.assertIn('东星斑', [v['text'] for v in task['language_analysis']['terms']])
            self.assertEqual(task['store_name'], '')
            with self.assertRaises(ValueError):
                create_task('different', root / 'jobs', language_analysis=analysis)

    def test_dictionary_integrity(self):
        data = lexicon()
        self.assertEqual(len(data['categories']), 22)
        self.assertEqual(data['registered_store_names'], [])
        goods = {t for category in data['categories'] for t in category['terms']}
        venues = {t for group in data['venue_groups'] for t in group['aliases']}
        for rule in data['confusions']:
            self.assertIn(rule['target'], goods | venues)
        self.assertTrue(set(data['asr_priority']) <= goods | venues)


if __name__ == '__main__':
    unittest.main()
