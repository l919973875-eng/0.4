from __future__ import annotations
import argparse
from pathlib import Path
import yaml


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config/social_keywords.yaml')
    ap.add_argument('--platform', choices=['xhs','dy','wb'], required=True)
    ap.add_argument('--pack', default='signal')
    ap.add_argument('--field', choices=['keywords','max_per_query'], default='keywords')
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding='utf-8')) or {}
    pack = args.pack if args.pack in (cfg.get('packs') or {}) else cfg.get('default_pack','signal')
    if args.field == 'max_per_query':
        print(int((cfg.get('max_per_query') or {}).get(pack, 8)))
        return
    values = (((cfg.get('packs') or {}).get(pack) or {}).get(args.platform) or [])
    print(','.join(str(x).strip() for x in values if str(x).strip()))


if __name__ == '__main__':
    main()
