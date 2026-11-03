#!/usr/bin/env python3
"""Verify original modules and explicitly recorded, pre-existing local adaptations."""
import argparse
import hashlib
import json
from pathlib import Path


def verify(root, original):
    manifest = json.loads((root/'config/reuse-manifest.json').read_text())
    adaptations = json.loads((root/'snapshot/reuse-reconciliation-20261103.json').read_text())['adaptations']
    if set(adaptations)-set(manifest['files']):
        raise ValueError('Unknown source adaptation')
    results = []
    for name, expected in manifest['files'].items():
        local = hashlib.sha256((root/name).read_bytes()).hexdigest()
        source = hashlib.sha256((original/name).read_bytes()).hexdigest()
        adapted = adaptations.get(name)
        results.append(dict(path=name, copiedSourceUnchanged=local == expected,
                            recordedAdaptation=adapted,
                            localSourceMatches=local == (adapted['sha256'] if adapted else expected),
                            velaSourceUnchanged=source == expected))
    return dict(files=results, allMatched=all(row['localSourceMatches'] and row['velaSourceUnchanged'] for row in results))


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', type=Path, default=Path('/root/Vera'))
    parser.add_argument('--output', type=Path, default=root/'snapshot/source-preservation.json')
    args = parser.parse_args()
    result = verify(root, args.original)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(dict(allMatched=result['allMatched'], modules=len(result['files']),
                          adaptations=sum(bool(row['recordedAdaptation']) for row in result['files']))))
    return 0 if result['allMatched'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
