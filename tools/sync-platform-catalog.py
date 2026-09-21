"""Copy a reviewed bench-api operation catalog to all independently shipped SDKs.

Usage: python3 tools/sync-platform-catalog.py ../bench-api/internal/headless/operations.json
Run each SDK's tests and inspect the generated diff before committing.
"""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
catalog = json.loads(Path(sys.argv[1]).read_text())
assert catalog['version'] == 1
ids = [op['id'] for op in catalog['operations']]
assert len(ids) == len(set(ids))
serialized = json.dumps(catalog, indent=2) + '\n'
for target in ['python/src/bench_sdk/operations.json', 'go/operations.json', 'rust/src/operations.json']:
    (root / target).write_text(serialized)
(root / 'src/operations.ts').write_text(
    '// Generated from bench-api internal/headless/operations.json. Do not edit.\n'
    + 'export const operationCatalog = ' + serialized.strip() + ' as const;\n'
    + 'export type OperationId = typeof operationCatalog.operations[number]["id"];\n'
)
