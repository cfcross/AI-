import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import copy

from market_language import analyze, asr_context, lexicon, catalog
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

    def test_supplied_catalog_keeps_all_rows_and_separates_uncertain_names(self):
        entries = catalog()['entries']
        self.assertEqual([r['row'] for r in entries], list(range(2, 211)))
        self.assertEqual(len([e for e in entries if e['status'] == 'needs_review']), 8)
        goods = {t for c in lexicon()['categories'] for t in c['terms']}
        for entry in entries:
            self.assertTrue(set(entry['terms']) <= goods)
            self.assertNotIn('price', entry)
            self.assertNotIn('unit', entry)
        self.assertFalse({'唐山', '牛腱羊肉', '老虾', '文肠'} & goods)

    def test_contextual_new_corrections_and_names(self):
        for heard, target in [('真珠斑','珍珠斑'), ('马交鱼','马鲛鱼'), ('久节虾','九节虾'),
                              ('鸡围虾','基围虾'), ('盐局鸡','盐焗鸡'), ('红现菜','红苋菜')]:
            with self.subTest(heard=heard):
                self.assertEqual(analyze(heard+'每斤35.5元')['text'], target+'每斤35.5元')
                self.assertEqual(analyze('店名叫'+heard+'，帮我做海报')['text'], '店名叫'+heard+'，帮我做海报')
                self.assertEqual(analyze(heard)['text'], heard)

    def test_alias_hints_keep_original_and_find_source_in_both_directions(self):
        for word, target in [('菜芯','菜心'), ('福寿鱼','罗非鱼'), ('泥猛','泥猛鱼'), ('马鲛鱼丸','马鲛丸')]:
            result = analyze(word+'每斤33元')
            self.assertEqual(result['text'], word+'每斤33元')
            self.assertEqual(result['terms'][0]['canonical_hint'], target)
            self.assertTrue(result['catalog_context']['matches'])
        self.assertEqual(analyze('罗非鱼每斤33元')['catalog_context']['matches'][0]['source_name'], '福寿鱼')

    def test_variant_candidates_do_not_rewrite_spoken_order_or_supply_prices(self):
        cases = ['大闸蟹2.5两母的80元一只', '白鳝不脆65元一斤',
                 '马鲛丸纯马鲛45元一斤', '基围虾33元一条', '东星斑每斤88元']
        for raw in cases:
            result = analyze(raw)
            self.assertEqual(result['text'], raw)
            self.assertEqual(result['edits'], [])
            self.assertTrue(result['catalog_context']['matches'])
            self.assertEqual(result['catalog_context']['price_policy'], 'caller_supplied_only')
            for item in result['catalog_context']['matches']:
                self.assertNotIn('price', item)
        raw = '泥猛每斤35元，泥鳗38元，洋姜5元，芋头12元'
        result = analyze(raw)
        self.assertEqual(result['text'], raw)
        self.assertEqual(result['source_review'], [])
        self.assertEqual({t['text'] for t in result['terms']}, {'泥猛','泥鳗','洋姜','芋头'})

    def test_source_ambiguity_is_not_silently_split_or_guessed(self):
        for word in ['唐山（山西）', '牛腱羊肉', '老虾', '文肠', '雪龙', '洋姜（黄姜）', '芋头（红头）']:
            raw = word+'35元一斤'
            result = analyze(raw)
            self.assertEqual(result['text'], raw)
            self.assertEqual(result['terms'], [])
            self.assertTrue(result['source_review'])
            self.assertEqual(result['catalog_context']['matches'][0]['status'], 'needs_review')

    def test_existing_valid_goods_cannot_become_new_correction_targets(self):
        data = copy.deepcopy(lexicon())
        data['confusions'].append({'heard':'泥猛','target':'泥鳗','gate':'product'})
        with patch('market_language.lexicon', return_value=data):
            self.assertEqual(analyze('泥猛每斤35元')['text'], '泥猛每斤35元')

    def test_new_generic_venues_are_not_store_names(self):
        for word in ['鱼类档口','海鲜档口','鱼类摊位','水产市集']:
            result = analyze('帮我做一张'+word+'的海报')
            self.assertEqual(result['protected_names'], [])
            self.assertEqual(result['venues'][0]['group'], 'aquatic_sales')

    def test_new_catalog_metadata_survives_record_to_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = '东兴班每斤88元，大闸蟹2.5两母的80元一只'
            analysis = analyze(raw)
            record = root/'record.json'
            record.write_text(json.dumps({'id':'catalog-example','reason':'hangup','text':analysis['text'],
                                          'correction':analysis}),encoding='utf-8')
            job = prepare_record(record, root, root)
            request = read(job/'request.json')
            self.assertEqual(request['language_analysis']['catalog_context'], analysis['catalog_context'])
            self.assertEqual(request['transcript'], raw.replace('东兴班','东星斑'))
            self.assertEqual(request['store_name'], '')
            self.assertEqual(request['language_analysis']['catalog_context']['price_policy'], 'caller_supplied_only')


if __name__ == '__main__':
    unittest.main()
