"""Check maintained docs and capture current web screenshots; standard library only."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
MEDIA = Path('docs/assets/screenshots')
SHOTS = tuple(f'{screen}-{theme}-{width}.png' for screen in ('library', 'review')
              for theme in ('light', 'dark') for width in (320, 1440))
# Current reader-facing docs. Dated reports, archived plans and old specs stay historical.
HISTORY = ('docs/archive/', 'docs/release-evidence/', 'demos/', '.kiro/')
LINK = re.compile(r'\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+"[^"]*")?\s*\)|(?:src|href)=["\']([^"\']+)["\']|^\s*\[[^\]]+\]:\s*(\S+)', re.M)


def maintained(path: str) -> bool:
    return path.endswith('.md') and not path.startswith(HISTORY)


def files(root: Path) -> list[str]:
    """Include pending additions for local checks, without scanning venvs/node_modules."""
    output = subprocess.check_output(
        ['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], cwd=root,
    ).decode().split('\0')
    return sorted({name for name in output if name and (root / name).is_file()})


def source_hashes(root: Path) -> dict[str, str]:
    paths = [root / name for name in (
        'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
        'frontend/vite.config.ts', 'frontend/playwright.config.ts',
        'frontend/e2e/workspace.spec.ts',
    )]
    # Use Git's inventory so ignored local files (for example .DS_Store) cannot
    # make a manifest captured on macOS fail in a clean Linux CI checkout.
    paths.extend(root / name for name in files(root)
                 if name.startswith(('frontend/src/', 'frontend/public/'))
                 and '.test.' not in name and '/test-support/' not in name)
    return {path.relative_to(root).as_posix(): digest(path) for path in sorted(paths)}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_code(text: str, *, inline: bool = True) -> str:
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S)
    text = re.sub(r'^\s*(`{3,}|~{3,})[^\n]*\n.*?^\s*\1\s*$', '', text, flags=re.M | re.S)
    return re.sub(r'(`+)[^`\n]*?\1', '', text) if inline else text


def links(text: str) -> list[str]:
    return [next(group for group in match.groups() if group is not None).strip('<>')
            for match in LINK.finditer(without_code(text))]


def anchors(text: str) -> set[str]:
    seen: dict[str, int] = {}
    result = set(re.findall(r'(?:id|name)=["\']([^"\']+)["\']', text))
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', without_code(text, inline=False), re.M):
        heading = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', heading)
        slug = re.sub(r'[^\w\- ]', '', heading.lower()).replace(' ', '-')
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        result.add(f'{slug}-{count}' if count else slug)
    return result


def check_links(root: Path, names: list[str]) -> list[str]:
    errors = []
    indexed = set()
    for url in links((root / 'docs/README.md').read_text()):
        parsed = urlsplit(url)
        if not parsed.scheme:
            indexed.add((root / 'docs' / unquote(parsed.path)).resolve())
    for name in names:
        if not maintained(name):
            continue
        path = root / name
        if name.startswith('docs/') and name != 'docs/README.md' and path.resolve() not in indexed:
            errors.append(f'{name}: maintained page missing from docs/README.md')
        for url in links(path.read_text()):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc:
                continue
            target = (path.parent / unquote(parsed.path)).resolve() if parsed.path else path.resolve()
            if not target.is_relative_to(root.resolve()):
                errors.append(f'{name}: link escapes the repository: {url}')
                continue
            if not target.exists():
                errors.append(f'{name}: missing local link: {url}')
            elif parsed.fragment and target.suffix == '.md' and unquote(parsed.fragment) not in anchors(target.read_text()):
                errors.append(f'{name}: missing Markdown heading: {url}')
            relative = target.relative_to(root.resolve()).as_posix()
            if target.suffix.lower() in {'.png', '.jpg', '.jpeg', '.gif', '.mp4', '.webm'} and relative.startswith(HISTORY):
                errors.append(f'{name}: historical media in current docs: {url}; use current captures or link the history index')
    return errors


def check_media(root: Path) -> list[str]:
    try:
        manifest = json.loads((root / MEDIA / 'manifest.json').read_text())
        expected = manifest['sources']
        actual = source_hashes(root)
        errors = []
        if expected != actual:
            changed = sorted(name for name in expected.keys() | actual.keys() if expected.get(name) != actual.get(name))
            errors.append('Screenshots need refreshing after source changes: ' + ', '.join(changed) + '. Run make docs-screenshots.')
        if set(manifest['images']) != set(SHOTS):
            errors.append('Screenshot manifest must cover all eight documented views.')
        for name in SHOTS:
            image = root / MEDIA / name
            if not image.is_file() or manifest['images'].get(name) != digest(image):
                errors.append(f'{MEDIA / name}: missing or changed since capture; run make docs-screenshots')
        return errors
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [f'Invalid or missing screenshot manifest: {error}. Run make docs-screenshots.']


def reader_facing(name: str) -> bool:
    if name.endswith('.md') or '/tests/' in name or '/test-support/' in name or '/e2e/' in name or '.test.' in name or name.startswith('scripts/test_'):
        return False
    return name.startswith(('backend/app/', 'frontend/src/', 'frontend/public/',
                            'ios/Sources/', 'contracts/', 'training/', 'scripts/',
                            'backend/alembic/', 'backend/requirements', '.github/workflows/')) or name in {
        'Makefile', 'frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
        'frontend/vite.config.ts', 'backend/requirements.txt', 'backend/requirements-prod.txt',
        'Dockerfile', 'backend/Dockerfile', 'frontend/Dockerfile', 'frontend/nginx.conf',
        'frontend/playwright.config.ts', 'docker-compose.yml', 'docker-compose.prod.yml', '.env.example',
        '.env.production.example', 'ios/project.yml', 'ios/Package.swift',
    }


def check_impact(changed: list[str], reason: str) -> list[str]:
    if any(reader_facing(name) for name in changed):
        # A checklist, history report or agent instruction is not a reader-facing update.
        updated = [name for name in changed if maintained(name) and name not in {'AGENTS.md', '.github/pull_request_template.md'}]
        if not updated and len(reason.strip()) < 15:
            return ['Application/configuration changed without maintained docs. Update the canonical page, or explain '
                    '"Documentation impact: none: <specific reason>" in the PR body.']
    return []


def capture(root: Path) -> None:
    before = source_hashes(root)
    subprocess.run(['npm', 'run', 'build'], cwd=root / 'frontend', check=True)
    with tempfile.TemporaryDirectory(prefix='hnd-docs-') as temporary:
        subprocess.run(
            ['npm', 'exec', '--', 'playwright', 'test', 'workspace.spec.ts', '--project=chromium',
             '--grep', 'visual states reflow'], cwd=root / 'frontend', check=True,
            env={**os.environ, 'DOCS_SCREENSHOTS_DIR': temporary},
        )
        if before != source_hashes(root):
            raise RuntimeError('Frontend changed during capture; rerun make docs-screenshots.')
        images = {name: digest(Path(temporary) / name) for name in SHOTS}
        destination = root / MEDIA
        destination.mkdir(parents=True, exist_ok=True)
        for name in SHOTS:
            shutil.copyfile(Path(temporary) / name, destination / name)
        manifest = {
            'captured_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'command': 'make docs-screenshots',
            'platform': platform.platform(),
            'scope': 'Production web build; Chromium; synthetic notes; mocked API; no recognition quality claim.',
            'sources': before,
            'images': images,
        }
        (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Captured eight current web screenshots and their source manifest. Inspect changed images before committing.')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('check', 'capture'))
    parser.add_argument('--base', help='Git ref to compare with the current working tree')
    parser.add_argument('--no-docs-impact', default='', help='Specific explanation of no reader-visible change')
    args = parser.parse_args()
    if args.command == 'capture':
        capture(ROOT)
        return 0
    errors = check_links(ROOT, files(ROOT)) + check_media(ROOT)
    base = args.base
    reason = args.no_docs_impact
    if os.environ.get('GITHUB_EVENT_NAME') == 'pull_request':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        base = event['pull_request']['base']['sha']
        match = re.search(r'^Documentation impact:\s*none:\s*(.+)$', event['pull_request'].get('body') or '', re.M | re.I)
        reason = match.group(1) if match else ''
    if base:
        changed = subprocess.check_output(['git', 'diff', '--name-only', '--diff-filter=ACDMRT', base], cwd=ROOT).decode().splitlines()
        changed += subprocess.check_output(['git', 'ls-files', '--others', '--exclude-standard'], cwd=ROOT).decode().splitlines()
        # Deleted docs cannot satisfy the update requirement.
        changed = [name for name in changed if not name.endswith('.md') or (ROOT / name).is_file()]
        errors.extend(check_impact(changed, reason))
    for error in errors:
        print(error)
    if not errors:
        print('Documentation links, navigation, media and requested impact checks passed.')
    return bool(errors)


if __name__ == '__main__':
    raise SystemExit(main())
