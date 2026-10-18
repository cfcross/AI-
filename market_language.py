"""Conservative, auditable market vocabulary correction. No network or model call."""
from functools import lru_cache
import json
from pathlib import Path
import re

LEXICON = Path(__file__).resolve().parent / 'skills/market-stall-demo/references/market-lexicon.json'


@lru_cache(maxsize=1)
def lexicon():
    return json.loads(LEXICON.read_text(encoding='utf-8'))


def asr_context(extra='', store_names=()):
    data = lexicon()
    words = list(dict.fromkeys([*store_names, *data['asr_priority']]))
    # A compact contextual vocabulary, not the whole reference book. Never add
    # known misspellings here: they are candidates only in the post-ASR stage.
    return '\n'.join(filter(None, [extra.strip(), '菜市场商品与场所词汇：' + '、'.join(words[:90])]))


def protected_spans(text, names):
    spans = []
    for name in names:
        if isinstance(name, str) and name.strip():
            spans += [{'start': m.start(), 'end': m.end(), 'text': m.group(), 'kind': 'registered_name'}
                      for m in re.finditer(re.escape(name.strip()), text)]
    # Explicit names may be arbitrary words, including food names or typos.
    pattern = r'(?:店名|档口名|摊位名|店铺名|名字|招牌|店面|店铺|我的店|我家店)(?:就)?(?:叫做|叫|是|写成|写|用|为|：|:)\s*[“「\"]?([^，。；！？、\n”」\"]{1,24})'
    for m in re.finditer(pattern, text):
        value = re.split(r'(?:我们|我家|主营|专卖|主要卖|卖|帮我|请|做一)', m.group(1))[0].rstrip()
        if value:
            spans.append({'start': m.start(1), 'end': m.start(1)+len(value), 'text': value, 'kind': 'explicit_name'})
    explicit = {span['text'] for span in spans if span['kind'] == 'explicit_name'}
    for name in explicit:
        spans += [{'start': m.start(), 'end': m.end(), 'text': name, 'kind': 'explicit_name'}
                  for m in re.finditer(re.escape(name), text)]
    # An unregistered proper-name-like prefix plus shop suffix is protected too.
    for m in re.finditer(r'[\u4e00-\u9fffA-Za-z0-9]{2,18}(?:海鲜档|海鲜店|鱼档|鱼铺|饭店|餐馆|酒楼|商行|档口|菜馆|餐厅)', text):
        spans.append({'start': m.start(), 'end': m.end(), 'text': m.group(), 'kind': 'possible_name'})
    return spans


def overlaps(start, end, spans):
    return any(start < s['end'] and end > s['start'] for s in spans)


def dish_evidence(text, start, end):
    left = re.split(r'[，。；！？\n]', text[:start])[-1][-12:]
    right = text[end:end+32]
    return bool(re.search(r'(喝|煮|炖|煲|酸菜|豆腐|鱼头)', left)
                or re.search(r'(?:每|一|两|[0-9])(?:碗|份)|(?:元|块)[^，。；！？]{0,4}(?:碗|份)', right))


def evidence(text, start, end, gate):
    # Stay within the current clause; another sentence's fish must not change soup.
    left = re.split(r'[，。；！？\n]', text[:start])[-1][-24:]
    right = re.split(r'[，。；！？\n]', text[end:])[0][:24]
    if gate == 'venue':
        # Soup/dish quantities, preparation, or serving language veto replacement.
        if re.search(r'(喝|煮|炖|煲|碗|份|酸菜|豆腐|鱼头)', left + right):
            return None
        if re.search(r'(?:每|一|两|[0-9])(?:碗|份)', text[end:end+32]):
            return None
        if re.search(r'^(?:每|一|两|三|[0-9])?(?:碗|份)|^[\d一二三四五六七八九十百]+元', right):
            return None
        if re.search(r'(?:我的|我家|我们家的|这家|这间|一家|一个)$', left) and re.search(r'(招牌|档口|摊位)', right):
            return '明确的场所修饰语与招牌/档口语境'
        return None
    if re.search(r'(卖|售|买|购|有|上新|主推|商品|食材|斤|条|只)[^，。；！？]{0,5}$', left):
        return '商品或交易位置'
    if re.match(r'(?:的)?(?:每斤|每条|每只|一斤|一条|一只|[0-9一二三四五六七八九十百]+(?:元|块))', right):
        return '商品计价位置'
    if re.search(r'(?:生成|做|画|制作).{0,8}$', left) and re.match(r'(?:的)?(?:宣传)?(?:海报|价格牌|价目表|价格表)', right):
        return '物料的商品主题位置'
    return None


def mentions(text, data, protected):
    found = []
    entries = {}
    for category in data['categories']:
        if category['id'] in ('cold_storage', 'other'):
            continue
        for term in category['terms']:
            entries.setdefault(term, []).append(category['id'])
    # Longest matches prevent extracting 鸡/鱼/肉 subparts as extra goods.
    occupied = list(protected)
    for term in sorted(entries, key=len, reverse=True):
        for m in re.finditer(re.escape(term), text):
            if not overlaps(m.start(), m.end(), occupied):
                found.append({'text': term, 'categories': entries[term], 'start': m.start(), 'end': m.end()})
                occupied.append({'start': m.start(), 'end': m.end()})
    return sorted(found, key=lambda x: x['start'])


def analyze(text, store_names=(), enabled=True):
    data = lexicon()
    protected = protected_spans(text, [*data['registered_store_names'], *store_names])
    edits, uncertain, occupied = [], [], []
    if enabled:
        for rule in data['confusions']:
            for m in re.finditer(re.escape(rule['heard']), text):
                if overlaps(m.start(), m.end(), protected + occupied):
                    continue
                if rule['gate'] == 'venue' and dish_evidence(text, m.start(), m.end()):
                    continue  # A valid dish is not an unresolved typo.
                reason = evidence(text, m.start(), m.end(), rule['gate'])
                item = {'start': m.start(), 'end': m.end(), 'original': m.group(), 'replacement': rule['target']}
                if reason:
                    edits.append({**item, 'reason': reason, 'field': rule['gate']})
                    occupied.append(item)
                else:
                    uncertain.append({**item, 'reason': '语境不足，保留原文；不是已确认的纠正'})
    corrected = text
    for edit in sorted(edits, key=lambda x: x['start'], reverse=True):
        corrected = corrected[:edit['start']] + edit['replacement'] + corrected[edit['end']:]
    protected_final = protected_spans(corrected, [*data['registered_store_names'], *store_names])
    venues = []
    for group in data['venue_groups']:
        for alias in sorted(group['aliases'], key=len, reverse=True):
            for m in re.finditer(re.escape(alias), corrected):
                if not overlaps(m.start(), m.end(), protected_final + venues):
                    venues.append({'text': alias, 'group': group['id'], 'start': m.start(), 'end': m.end()})
    # Venue equivalence classifies intent; it never rewrites "market" to "stall".
    return {'version': data['version'], 'raw_text': text, 'text': corrected, 'edits': sorted(edits, key=lambda x:x['start']),
            'uncertain': uncertain, 'protected_names': protected_final,
            'terms': mentions(corrected, data, protected_final + venues), 'venues': venues,
            'method': 'context_gated_lexicon_v1' if enabled else 'disabled'}
