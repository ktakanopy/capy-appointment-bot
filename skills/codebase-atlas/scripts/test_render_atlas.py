import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RENDERER = Path(__file__).with_name('render_atlas.py')


class AtlasRenderingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'repository'
        self.output = Path(self.directory.name) / 'atlas'
        (self.root / 'src').mkdir(parents=True)
        (self.root / 'src' / 'worker.py').write_text(
            'from .storage import save\n\ndef run(value):\n    return save(value.strip())\n'
            '\nraise AssertionError("Do not import application code while rendering")\n'
        )
        (self.root / 'src' / 'storage.py').write_text(
            'def save(value):\n    return {"value": value}\n'
        )
        self.views = [
            {
                'id': 'architecture-overview', 'title': 'Skyline architecture',
                'navTitle': 'Overview', 'icon': 'atlas',
                'subtitle': 'Follow an input through normalization and its returned result.',
                'columns': 2,
                'lanes': [{'label': 'Normalize and return', 'nodes': [
                    {'id': 'normalize', 'title': 'Normalize the input',
                     'detail': 'The worker removes surrounding whitespace.',
                     'why': 'Equivalent inputs should produce the same saved value.',
                     'ref': 'src/worker.py#run', 'tone': 'entry'},
                    {'id': 'result', 'title': 'Return the result',
                     'detail': 'The storage adapter returns the normalized value.',
                     'why': 'The caller can use the value returned by the adapter.',
                     'ref': 'src/storage.py#save', 'tone': 'success'},
                ]}],
                'edges': [['normalize', 'result']],
                'transcript': [{'node': 'normalize', 'speaker': 'Caller', 'text': ' sample '}],
                'transcriptContext': 'A synthetic input used to explain normalization.',
            },
            {
                'id': 'runtime', 'title': 'Normalize a record', 'navTitle': 'Record processing',
                'icon': 'runtime', 'subtitle': 'The same entry point in a focused view.',
                'columns': 1,
                'lanes': [{'label': 'Worker', 'nodes': [
                    {'id': 'run', 'title': 'Normalize the input',
                     'detail': 'The worker removes surrounding whitespace.',
                     'why': 'The adapter receives one normalized value.', 'ref': 'src/worker.py#run'},
                ]}], 'edges': [],
            },
            {
                'id': 'files-processing', 'title': 'Source reference',
                'navTitle': 'Processing modules', 'subtitle': 'Start with the worker.',
                'files': [
                    ['src/worker.py', 'Normalize input before returning the result.', ['run'], []],
                    ['src/storage.py', 'Return a value through the storage interface.', ['save'], []],
                ],
            },
        ]

    def render(self, *, success=True):
        spec = self.root / 'atlas.json'
        spec.write_text(json.dumps(self.views))
        result = subprocess.run(
            [sys.executable, str(RENDERER), '--root', str(self.root), '--out', str(self.output),
             '--web', '--project', 'Skyline', '--source-base', 'https://example.com/revision',
             str(spec)],
            cwd=self.directory.name, capture_output=True, text=True,
        )
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0)
        return result

    def data(self):
        html = (self.output / 'index.html').read_text()
        match = re.search(r'<script id="atlas-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_another_repository_renders_without_importing_its_application(self):
        self.render()
        data = self.data()
        self.assertEqual(data['project'], 'Skyline')
        self.assertEqual(data['flows'][0]['id'], 'architecture-overview')
        self.assertEqual(data['flows'][1]['navTitle'], 'Record processing')
        self.assertEqual(data['catalogs'][0]['navTitle'], 'Processing modules')
        self.assertEqual(data['sources']['src/worker.py#run']['line'], 3)
        self.assertEqual(data['imports'], [['src/worker.py', 'src/storage.py', [1]]])
        self.assertIn('Do not import application code', str(data['sourceFiles']['src/worker.py']))
        self.assertEqual(sorted(path.name for path in self.output.iterdir()), [
            'architecture-overview.svg', 'files-processing.svg', 'index.html', 'runtime.svg',
        ])
        source = self.root / 'src' / 'worker.py'
        source.write_text('\n\n' + source.read_text())
        self.render()
        self.assertEqual(self.data()['sources']['src/worker.py#run']['line'], 5)

    def test_non_python_line_references_embed_source_and_reject_stale_lines(self):
        source = 'const marker = "</script>";\nexport function run() { return marker; }\n'
        (self.root / 'src' / 'client.ts').write_text(source)
        self.views[0]['lanes'][0]['nodes'][0]['ref'] = 'src/client.ts#L2'
        self.render()
        data = self.data()
        self.assertEqual(data['sources']['src/client.ts#L2']['line'], 2)
        self.assertEqual(data['sourceFiles']['src/client.ts'], source.rstrip('\n').split('\n'))
        self.views[0]['lanes'][0]['nodes'][0]['ref'] = 'src/client.ts#L3'
        self.assertIn('Source line outside file', self.render(success=False).stderr)

    def test_source_references_cannot_embed_files_outside_the_repository(self):
        (Path(self.directory.name) / 'private.txt').write_text('not part of the repository')
        self.views[0]['lanes'][0]['nodes'][0]['ref'] = '../private.txt'
        result = self.render(success=False)
        self.assertIn('Missing source file inside repository', result.stderr)
        self.assertNotIn('not part of the repository', result.stdout)

    def test_documentation_embeds_tables_images_and_rewrites_cross_references(self):
        docs = self.root / 'docs'
        (docs / 'assets').mkdir(parents=True)
        (docs / 'assets' / 'model.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><text>Stored value</text></svg>')
        (docs / 'storage.md').write_text(
            '# Storage\n\n| Record | Purpose |\n| --- | --- |\n| Value | Normalized input |\n\n'
            '![Records](assets/model.svg)\n\n[Entry](reading.md#entry)\n\n'
            '[Source](../src/worker.py#L3)\n\n<script>alert(1)</script>\n'
        )
        (docs / 'reading.md').write_text('# Entry\n\nStart at the worker.\n')
        self.views.extend([
            {'id': 'guide-storage', 'title': 'Storage guide', 'navGroup': 'Data', 'document': 'docs/storage.md'},
            {'id': 'guide-reading', 'title': 'Reading order', 'document': 'docs/reading.md'},
        ])
        self.render()
        guide = self.data()['guides'][0]
        self.assertIn('<table>', guide['html'])
        self.assertIn('data:image/svg+xml;base64,', guide['html'])
        self.assertIn('href="#guide-reading/entry"', guide['html'])
        self.assertIn('https://example.com/revision/src/worker.py#L3', guide['html'])
        self.assertNotIn('<script', guide['html'])
        self.assertEqual(guide['navGroup'], 'Data')
        (docs / 'storage.md').write_text('![Outside](../../private.svg)\n')
        (Path(self.directory.name) / 'private.svg').write_text('<svg>private</svg>')
        self.assertIn('Documentation path outside repository', self.render(success=False).stderr)


if __name__ == '__main__':
    unittest.main()
