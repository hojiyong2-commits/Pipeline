import re

def _consistency_listed_files(text):
    _TRUNCATION_PATTERN = re.compile(
        r'^\.\.\.$|^\.{3}\s*외\s*\d+\s*개?\s*파일|^and\s+\d+\s+more\s+files?',
        re.IGNORECASE,
    )
    if not text:
        return set(), False
    files = set()
    truncated = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not (line.startswith('- ') or line.startswith('* ')):
            continue
        body = line[2:].strip()
        if not body:
            continue
        body = re.sub(r'\*\*', '', body)
        body = body.replace('`', '')
        body = re.sub(r'\([^)]*\)', '', body).strip()
        body = re.sub(r'\s*[—–]\s*.*$', '', body).strip()
        tokens = body.split()
        token = tokens[0] if tokens else ''
        if token.endswith(':') and len(tokens) > 1:
            base = token.rstrip(':')
            if '.' in base or '/' in base or chr(92) in base:
                token = base
            else:
                token = ''
                for t in tokens[1:]:
                    t_clean = re.sub(r'\*\*', '', t).rstrip(':').strip()
                    if t_clean and ('.' in t_clean or '/' in t_clean or chr(92) in t_clean):
                        token = t_clean
                        break
        token = token.rstrip(':').rstrip(',').strip()
        if not token:
            continue
        if not ('.' in token or '/' in token or chr(92) in token):
            continue
        if _TRUNCATION_PATTERN.match(token):
            truncated = True
            continue
        files.add(token)
        print(f'  FOUND: {repr(token)} from line: {repr(line[:80])}')
    return files, truncated

with open('pr_body_6f07_final.md', encoding='utf-8') as f:
    pr_body = f.read()

print('=== Extracted file paths from PR body ===')
files, truncated = _consistency_listed_files(pr_body)
print(f'Total: {len(files)}, truncated={truncated}')
for f in sorted(files):
    print(f'  {repr(f)}')
