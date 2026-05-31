import json
import sys

with open('pr364_comments.json', encoding='utf-8-sig') as f:
    data = json.load(f)
comments = data.get('comments', [])
for c in comments:
    body = c.get('body', '')
    if 'pipeline-human-acceptance-packet' in body:
        lines = body.splitlines()
        print(f'Total lines: {len(lines)}')
        print('--- Lines with file paths ---')
        for j, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('- ') and ('/' in stripped or '.' in stripped):
                safe = stripped.encode('ascii', errors='replace').decode('ascii')
                print(f'{j:3}: {safe}')
        print('--- Truncation markers ---')
        for j, line in enumerate(lines):
            stripped = line.strip()
            if '...' in stripped:
                safe = stripped.encode('ascii', errors='replace').decode('ascii')
                print(f'{j:3}: {safe}')
        break

sys.stdout.flush()
