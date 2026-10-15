"""Regression checks for documentation drift prevention; no application dependencies."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import docs


class DocumentationChecks(unittest.TestCase):
    def test_local_links_and_anchors_ignore_examples_and_external_urls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs').mkdir()
            (root / 'docs/README.md').write_text('[Guide](guide.md)\n')
            (root / 'docs/guide.md').write_text(
                '# Guide\n## Save `text`\n## Save `text`\n'
                '[Home](README.md) [Section](#save-text-1) '
                '[External](https://example.com/anything)\n'
                '```md\n[Example](missing.md)\n```\n'
                '`[Inline example](missing.md)`\n'
            )
            self.assertEqual(docs.check_links(root, ['docs/guide.md']), [])
            (root / 'docs/guide.md').write_text('[Missing](missing.md)\n[Bad](#absent)\n<img src="missing.png">')
            errors = docs.check_links(root, ['docs/guide.md'])
            self.assertEqual(len(errors), 3)
            self.assertTrue(any('missing Markdown heading' in error for error in errors))

    def test_unindexed_pages_and_old_media_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'docs').mkdir()
            (root / 'demos').mkdir()
            (root / 'demos/old.png').write_bytes(b'old')
            (root / 'docs/README.md').write_text('# Docs\n')
            (root / 'docs/guide.md').write_text('![Old](../demos/old.png)\n')
            errors = docs.check_links(root, ['docs/guide.md'])
            self.assertTrue(any('missing from docs/README.md' in error for error in errors))
            self.assertTrue(any('historical media' in error for error in errors))
            self.assertEqual(docs.check_links(root, ['demos/old.md']), [])

    def test_media_detects_source_and_image_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            media = root / docs.MEDIA
            media.mkdir(parents=True)
            for name in docs.SHOTS:
                (media / name).write_bytes(b'fixture')
            manifest = {'sources': {'frontend/src/main.ts': 'original'},
                        'images': {name: docs.digest(media / name) for name in docs.SHOTS}}
            (media / 'manifest.json').write_text(json.dumps(manifest))
            with patch.object(docs, 'source_hashes', return_value=manifest['sources']):
                self.assertEqual(docs.check_media(root), [])
                (media / docs.SHOTS[0]).write_bytes(b'tampered')
                self.assertTrue(any(docs.SHOTS[0] in error for error in docs.check_media(root)))
            with patch.object(docs, 'source_hashes', return_value={'frontend/src/main.ts': 'new'}):
                self.assertTrue(any('source changes' in error for error in docs.check_media(root)))

    def test_manifest_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertTrue(docs.check_media(Path(temporary)))

    def test_source_inventory_excludes_ignored_machine_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ['frontend/index.html', 'frontend/package.json', 'frontend/package-lock.json',
                     'frontend/vite.config.ts', 'frontend/playwright.config.ts',
                     'frontend/e2e/workspace.spec.ts', 'frontend/src/main.ts']
            for name in names + ['frontend/src/.DS_Store']:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture')
            with patch.object(docs, 'files', return_value=names):
                hashes = docs.source_hashes(root)
            self.assertIn('frontend/src/main.ts', hashes)
            self.assertNotIn('frontend/src/.DS_Store', hashes)

    def test_impact_requires_current_docs_or_a_specific_reason(self):
        change = ['backend/app/services/jobs.py']
        self.assertTrue(docs.check_impact(change, ''))
        self.assertTrue(docs.check_impact(change + ['docs/archive/old.md'], ''))
        self.assertTrue(docs.check_impact(change + ['AGENTS.md'], ''))
        self.assertEqual(docs.check_impact(change + ['docs/guides/using-the-app.md'], ''), [])
        self.assertEqual(docs.check_impact(change, 'Internal refactor; public behavior and configuration are unchanged.'), [])
        self.assertEqual(docs.check_impact(['backend/tests/test_jobs.py', 'frontend/src/main.test.ts', 'scripts/test_docs.py'], ''), [])
        self.assertTrue(docs.reader_facing('frontend/Dockerfile'))


if __name__ == '__main__':
    unittest.main()
