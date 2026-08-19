from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from intelligence_engine import build_events, risk_taxonomy

NOW = datetime(2026, 8, 19, 8, 0, tzinfo=timezone.utc)
TAX = yaml.safe_load((ROOT / 'config' / 'signal_taxonomy.yaml').read_text(encoding='utf-8'))
TIERS = {
    'tier1_patterns': ['official'],
    'tier2_patterns': ['reuters'],
    'default_tier': 3,
    'publisher_families': {'official-family': ['official'], 'reuters-family': ['reuters']},
}


def social_event():
    return {
        'id': 's1', 'text': '当地消息称柬埔寨一中资工业园4名中国工人遭围殴，1人死亡3人重伤',
        'title': '柬埔寨中资工业园中国工人遭围殴致死伤', 'platform': 'telegram', 'author': 'local-eye',
        'source': 'telegram · local-eye', 'source_kind': 'social', 'published_at': (NOW - timedelta(hours=2)).isoformat(),
        'collected_at': NOW.isoformat(), 'relation': 'direct', 'confidence': 28, 'entities': ['中国工人', '中资工业园'],
        'country': 'Cambodia', 'reason': '直接涉及海外中国人员和中资项目。', 'url': 'https://example.test/social/1'
    }


def official_followup():
    return {
        'id': 'n1', 'title': 'Official confirms Chinese workers attacked at Cambodia industrial park, one killed',
        'snippet': 'Authorities opened an investigation after the attack.', 'source': 'Official Cambodia', 'source_kind': 'official',
        'published_at': (NOW + timedelta(hours=3)).isoformat(), 'collected_at': (NOW + timedelta(hours=3)).isoformat(),
        'relation': 'direct', 'confidence': 95, 'entities': ['中国工人', '中资工业园'], 'country': 'Cambodia',
        'reason': '直接涉及海外中国人员和中资项目。', 'url': 'https://example.test/official/1'
    }


def main():
    hits = risk_taxonomy('中资矿山工人罢工并围堵入口，项目停工', TAX)
    keys = {x['key'] for x in hits}
    assert 'overseas_projects' in keys and 'labor_social' in keys, keys

    first = build_events([], [social_event()], TIERS, now=NOW, previous_events=[], taxonomy_cfg=TAX)
    assert len(first) == 1
    e1 = first[0]
    assert e1['lifecycle'] == 'emerging', e1['lifecycle']
    assert e1['china_relevance_score'] >= 95, e1['china_relevance_score']
    assert e1['priority_score'] > e1['confidence_score'], (e1['priority_score'], e1['confidence_score'])
    assert e1['risk_category'] in {'personnel_security', 'overseas_projects'}, e1['risk_category']


    # A rebuild with no new evidence should not immediately downgrade a fresh event.
    rebuilt = build_events([], [social_event()], TIERS, now=NOW + timedelta(hours=1), previous_events=first, taxonomy_cfg=TAX)
    assert rebuilt[0]['lifecycle'] == 'emerging', rebuilt[0]['lifecycle']
    assert rebuilt[0]['id'] == e1['id']

    later = NOW + timedelta(hours=3)
    second = build_events([official_followup()], [social_event()], TIERS, now=later, previous_events=first, taxonomy_cfg=TAX)
    assert len(second) == 1
    e2 = second[0]
    assert e2['id'] == e1['id'], (e1['id'], e2['id'])
    assert e2['previous_event_matched'] is True
    assert e2['confidence_score'] > e1['confidence_score'], (e1['confidence_score'], e2['confidence_score'])
    assert e2['momentum_score'] > 50, e2['momentum_score']
    assert e2['lifecycle'] == 'escalating', e2['lifecycle']
    assert e2['change']['official_added'] is True, e2['change']
    print('v0.5.0 signal engine tests: PASS')
    print({k: e2[k] for k in ('risk_category','priority_score','confidence_score','china_relevance_score','novelty_score','momentum_score','exposure_score','lifecycle')})


if __name__ == '__main__':
    main()
