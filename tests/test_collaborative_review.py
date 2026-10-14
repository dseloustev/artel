"""`scripts/collaborative_review.py`: the snapshot and Markdown state a review binds to.

The collaborative review reads one pull request at recorded revisions. Its inventory
must be complete -- every page `gh api --paginate --slurp` returned, matching the PR's
own changed-file count, no duplicate path -- and every file needs a direct GitHub diff
link derived from its exact UTF-8 path, because that anchor is what a person clicks
while reviewing. A snapshot that silently lost a page or accepted a malformed row must
be refused before it becomes review input.

The inventory becomes a Markdown checklist whose header comment records the snapshot
identity and whose `## Files` section is the only approval ledger. The first unchecked
row is the current file; only a response whose `.strip().casefold()` equals `reviewed`,
for the current entry and a still-matching snapshot, checks that one row. Every other
response -- including the old numeric `1` -- changes nothing; neither do a stale
snapshot, a duplicate or wrong-ID approval, or a corrupt ledger.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import collaborative_review  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'collaborative_review.py'
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'collaborative_review'
MIXED_PR_GITHUB = FIXTURES / 'mixed-pr' / 'github'

PR_URL = 'https://github.com/example/project/pull/7'
BASE_SHA = 'a' * 40
HEAD_SHA = 'b' * 40
MERGE_BASE_SHA = 'c' * 40
ERRORS_PATH = 'lib/whois/errors.rb'
UNICODE_PATH = 'src/旧.py'
ERRORS_DIFF_URL = ('https://github.com/example/project/pull/7/files#diff-'
                   '447a111a507d8046f1ee64817cd197e9c68424b597d85d83afbbc9364f4fe41d')
UNICODE_DIFF_SUFFIX = ('#diff-'
                       '1bd4cb85f8cf517cc7e9355eef1cc009b6d00f83870b9f1722a7d49bc09799fd')


def row(filename, status='modified', additions=1, deletions=0, **extra):
    """One `repos/.../pulls/<n>/files` row as GitHub returns it."""
    entry = {
        'filename': filename,
        'status': status,
        'additions': additions,
        'deletions': deletions,
        'changes': additions + deletions,
        'sha': hashlib.sha256(filename.encode('utf-8')).hexdigest(),
        'blob_url': 'https://github.com/example/project/blob/' + filename,
        'raw_url': 'https://github.com/example/project/raw/' + filename,
        'contents_url': 'https://api.github.com/repos/example/project/contents/' + filename,
        'patch': '@@ -1 +1 @@\n-old line\n+new line',
    }
    entry.update(extra)
    return entry


def metadata(changed_files, url=PR_URL, base_sha=BASE_SHA, head_sha=HEAD_SHA):
    """`gh pr view --json url,baseRefOid,headRefOid,changedFiles` output."""
    return {'url': url, 'baseRefOid': base_sha, 'headRefOid': head_sha,
            'changedFiles': changed_files}


def snapshot_of(*paths):
    """A validated snapshot of `paths`, as the inventory verb writes it."""
    pages = [[row(path) for path in paths]]
    return collaborative_review.normalize_snapshot(metadata(len(paths)), pages, MERGE_BASE_SHA)


def ordered_entry(path, role='module', focus='review the change', **extra):
    """One `ordered` preparation entry; `extra` adds or overrides fields."""
    entry = {'path': path, 'role': role, 'focus': focus, 'ordering_note': ''}
    entry.update(extra)
    return entry


def excluded_entry(path, reason='documentation only'):
    """One `excluded` preparation entry."""
    return {'path': path, 'reason': reason}


def two_file_snapshot():
    return snapshot_of(ERRORS_PATH, UNICODE_PATH)


def two_entry_preparation():
    """The two inventory files, in snapshot order, with no exclusions."""
    return {'ordered': [ordered_entry(ERRORS_PATH, role='storage layer',
                                      focus='review the new index'),
                        ordered_entry(UNICODE_PATH, role='API', focus='check the parser')],
            'excluded': []}


class TestDiffUrl(unittest.TestCase):
    def test_link_uses_exact_utf8_filename(self):
        self.assertEqual(collaborative_review.diff_url(PR_URL, ERRORS_PATH), ERRORS_DIFF_URL)
        unicode_link = collaborative_review.diff_url(PR_URL, UNICODE_PATH)
        self.assertTrue(unicode_link.startswith(PR_URL + '/files#diff-'))
        self.assertTrue(unicode_link.endswith(UNICODE_DIFF_SUFFIX))

    def test_trailing_slash_is_the_same_pull_request(self):
        self.assertEqual(collaborative_review.diff_url(PR_URL + '/', ERRORS_PATH),
                         ERRORS_DIFF_URL)

    def test_invalid_pr_url_is_refused(self):
        for url in ('https://gitlab.com/example/project/pull/7',
                    'https://github.com/example/project',
                    'https://github.com/example/project/pull/0',
                    PR_URL + '/files'):
            with self.subTest(url=url):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.diff_url(url, 'a.py')

    def test_empty_path_is_refused(self):
        with self.assertRaises(collaborative_review.SnapshotError):
            collaborative_review.diff_url(PR_URL, '')


class TestNormalizeSnapshot(unittest.TestCase):
    def test_complete_slurped_pages(self):
        pages = [
            [row(ERRORS_PATH, status='added'), row(UNICODE_PATH, status='added')],
            [row('docs/readme.md', status='removed', additions=0, deletions=4)],
        ]
        snapshot = collaborative_review.normalize_snapshot(metadata(3), pages, MERGE_BASE_SHA)
        self.assertEqual(snapshot['pr_url'], PR_URL)
        self.assertEqual(snapshot['base_sha'], BASE_SHA)
        self.assertEqual(snapshot['head_sha'], HEAD_SHA)
        self.assertEqual(snapshot['merge_base_sha'], MERGE_BASE_SHA)
        self.assertEqual(snapshot['changed_files'], 3)
        self.assertEqual([entry['path'] for entry in snapshot['files']],
                         ['docs/readme.md', ERRORS_PATH, UNICODE_PATH])
        self.assertEqual(snapshot['pages'], pages)
        errors = snapshot['files'][1]
        self.assertEqual(set(errors), {'path', 'status', 'previous_path', 'additions',
                                       'deletions', 'blob_sha', 'diff_url'})
        self.assertEqual(errors['status'], 'added')
        self.assertEqual(errors['blob_sha'], hashlib.sha256(ERRORS_PATH.encode('utf-8')).hexdigest())
        self.assertEqual(errors['diff_url'], ERRORS_DIFF_URL)
        self.assertIsNone(errors['previous_path'])
        self.assertTrue(snapshot['files'][2]['diff_url'].endswith(UNICODE_DIFF_SUFFIX))

    def test_rename_and_deletion_links(self):
        pages = [[row('src/new_name.py', status='renamed', previous_filename='src/old_name.py'),
                  row('src/gone.py', status='removed', additions=0, deletions=5)]]
        snapshot = collaborative_review.normalize_snapshot(metadata(2), pages, MERGE_BASE_SHA)
        files = {entry['path']: entry for entry in snapshot['files']}
        self.assertEqual(files['src/new_name.py']['previous_path'], 'src/old_name.py')
        self.assertIsNone(files['src/gone.py']['previous_path'])
        # the anchor hashes the file's own path, never the rename source or a line fragment
        self.assertEqual(files['src/new_name.py']['diff_url'],
                         collaborative_review.diff_url(PR_URL, 'src/new_name.py'))
        self.assertNotEqual(files['src/new_name.py']['diff_url'],
                            collaborative_review.diff_url(PR_URL, 'src/old_name.py'))
        self.assertEqual(files['src/gone.py']['diff_url'],
                         collaborative_review.diff_url(PR_URL, 'src/gone.py'))

    def test_count_mismatch_and_duplicate_paths_block(self):
        with self.assertRaises(collaborative_review.SnapshotError) as mismatch:
            collaborative_review.normalize_snapshot(
                metadata(3), [[row('a.py'), row('b.py')]], MERGE_BASE_SHA)
        self.assertIn('changedFiles', str(mismatch.exception))
        with self.assertRaises(collaborative_review.SnapshotError) as duplicate:
            collaborative_review.normalize_snapshot(
                metadata(2), [[row('a.py')], [row('a.py', additions=9)]], MERGE_BASE_SHA)
        self.assertIn('duplicate', str(duplicate.exception))

    def test_malformed_metadata_blocks(self):
        pages = [[row('a.py')]]
        cases = [
            ('metadata is not an object', [], pages, MERGE_BASE_SHA),
            ('url missing', {'baseRefOid': BASE_SHA, 'headRefOid': HEAD_SHA,
                             'changedFiles': 1}, pages, MERGE_BASE_SHA),
            ('changedFiles missing', {'url': PR_URL, 'baseRefOid': BASE_SHA,
                                      'headRefOid': HEAD_SHA}, pages, MERGE_BASE_SHA),
            ('changedFiles is not an int', metadata('1'), pages, MERGE_BASE_SHA),
            ('changedFiles is a bool', metadata(True), pages, MERGE_BASE_SHA),
            ('changedFiles is negative', metadata(-1), [[]], MERGE_BASE_SHA),
        ]
        for label, meta, page_rows, merge_base in cases:
            with self.subTest(label):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.normalize_snapshot(meta, page_rows, merge_base)

    def test_invalid_pr_url_and_revisions_block(self):
        pages = [[row('a.py')]]
        for url in ('https://gitlab.com/example/project/pull/7',
                    'https://github.com/example/project',
                    'https://github.com/example/project/pull/0',
                    'git@github.com:example/project.git'):
            with self.subTest(url=url):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.normalize_snapshot(
                        metadata(1, url=url), pages, MERGE_BASE_SHA)
        cases = [
            ('base sha too short', metadata(1, base_sha='deadbeef'), MERGE_BASE_SHA),
            ('head sha is a ref', metadata(1, head_sha='HEAD'), MERGE_BASE_SHA),
            ('base sha uppercase', metadata(1, base_sha='A' * 40), MERGE_BASE_SHA),
            ('merge base missing', metadata(1), None),
            ('merge base too short', metadata(1), 'deadbeef'),
        ]
        for label, meta, merge_base in cases:
            with self.subTest(label):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.normalize_snapshot(meta, pages, merge_base)

    def test_malformed_rows_block(self):
        def broken(**overrides):
            entry = row('a.py')
            entry.update(overrides)
            return entry

        cases = [
            ('row is not an object', ['nope']),
            ('filename missing', [{k: v for k, v in row('a.py').items() if k != 'filename'}]),
            ('filename empty', [broken(filename='')]),
            ('filename is not a string', [broken(filename=7)]),
            ('status missing', [{k: v for k, v in row('a.py').items() if k != 'status'}]),
            ('additions negative', [broken(additions=-1)]),
            ('deletions negative', [broken(deletions=-2)]),
            ('additions is a string', [broken(additions='1')]),
            ('additions is a bool', [broken(additions=True)]),
            ('blob sha missing', [{k: v for k, v in row('a.py').items() if k != 'sha'}]),
            ('previous filename is not a string', [broken(previous_filename=5)]),
        ]
        for label, rows in cases:
            with self.subTest(label):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.normalize_snapshot(
                        metadata(len(rows)), [rows], MERGE_BASE_SHA)

    def test_digest_is_order_independent_but_changes_with_file_metadata(self):
        rows = [row('a.py', additions=1),
                row(ERRORS_PATH, additions=2),
                row(UNICODE_PATH, status='added', additions=3)]
        first = collaborative_review.normalize_snapshot(
            metadata(3), [rows[0:2], rows[2:]], MERGE_BASE_SHA)
        shuffled = collaborative_review.normalize_snapshot(
            metadata(3), [rows[2:], rows[1:2], rows[0:1]], MERGE_BASE_SHA)
        self.assertEqual(first['files'], shuffled['files'])
        self.assertEqual(first['inventory_digest'], shuffled['inventory_digest'])
        self.assertRegex(first['inventory_digest'], r'^[0-9a-f]{64}$')
        expected = hashlib.sha256(json.dumps(
            first['files'], sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        self.assertEqual(first['inventory_digest'], expected)

        altered_rows = [dict(entry) for entry in rows]
        altered_rows[1]['additions'] = 99
        altered = collaborative_review.normalize_snapshot(
            metadata(3), [altered_rows], MERGE_BASE_SHA)
        self.assertNotEqual(first['inventory_digest'], altered['inventory_digest'])

        # patches are preserved in `pages`, not hashed into the inventory identity
        repatched_rows = [dict(entry) for entry in rows]
        repatched_rows[0]['patch'] = '@@ -9 +9 @@\n-other\n+patch'
        repatched = collaborative_review.normalize_snapshot(
            metadata(3), [repatched_rows], MERGE_BASE_SHA)
        self.assertEqual(first['inventory_digest'], repatched['inventory_digest'])

    def test_empty_inventory_is_an_empty_snapshot(self):
        for pages in ([], [[]]):
            with self.subTest(pages=pages):
                snapshot = collaborative_review.normalize_snapshot(metadata(0), pages,
                                                                   MERGE_BASE_SHA)
                self.assertEqual(snapshot['files'], [])
                self.assertEqual(snapshot['changed_files'], 0)
                self.assertEqual(snapshot['pages'], pages)


class CliCase(unittest.TestCase):
    """A temporary root and the real script run as a subprocess."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write_json(self, name, payload):
        path = self.root / name
        path.write_text(json.dumps(payload), encoding='utf-8')
        return path

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True)


class TestInventoryCli(CliCase):
    def run_inventory(self, meta, pages, merge_base=MERGE_BASE_SHA, out='snapshot.json'):
        metadata_path = self.write_json('metadata.json', meta)
        pages_path = self.write_json('pages.json', pages)
        out_path = self.root / out
        proc = self.cli('inventory', '--metadata', str(metadata_path),
                        '--pages', str(pages_path), '--merge-base', merge_base,
                        '--output', str(out_path))
        return proc, out_path

    def test_inventory_writes_the_snapshot_and_answers_one_envelope(self):
        pages = [[row('a.py'), row(ERRORS_PATH, status='added')],
                 [row(UNICODE_PATH, status='added')]]
        proc, out_path = self.run_inventory(metadata(3), pages, out='run/pr-7/snapshot.json')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(proc.stdout.strip().splitlines()), 1)
        envelope = json.loads(proc.stdout)
        self.assertTrue(envelope['ok'])
        self.assertEqual(envelope['verb'], 'inventory')
        self.assertIsInstance(envelope['elapsed_ms'], int)
        snapshot = json.loads(out_path.read_text(encoding='utf-8'))
        self.assertEqual(snapshot['pr_url'], PR_URL)
        self.assertEqual(snapshot['merge_base_sha'], MERGE_BASE_SHA)
        self.assertEqual(snapshot['changed_files'], 3)
        self.assertEqual(snapshot['pages'], pages)
        self.assertEqual(snapshot['files'][1]['diff_url'], ERRORS_DIFF_URL)
        self.assertTrue(snapshot['files'][2]['diff_url'].endswith(UNICODE_DIFF_SUFFIX))
        self.assertEqual(envelope['data']['output'], str(out_path))
        self.assertEqual(envelope['data']['changed_files'], 3)
        self.assertEqual(envelope['data']['inventory_digest'], snapshot['inventory_digest'])
        # stdout stays a summary: patches and the full file list live in the artifact
        self.assertNotIn('files', envelope['data'])
        self.assertNotIn('pages', envelope['data'])

    def test_missing_or_unreadable_inputs_block_and_leave_output_untouched(self):
        out_path = self.root / 'snapshot.json'
        out_path.write_text('previous snapshot', encoding='utf-8')
        directory = self.root / 'a-directory.json'
        directory.mkdir()
        for label, metadata_arg in [('missing', self.root / 'nope.json'),
                                    ('directory', directory)]:
            with self.subTest(label):
                proc = self.cli('inventory', '--metadata', str(metadata_arg),
                                '--pages', str(self.write_json('pages.json', [[]])),
                                '--merge-base', MERGE_BASE_SHA, '--output', str(out_path))
                self.assertEqual(proc.returncode, 2, proc.stderr)
                envelope = json.loads(proc.stdout)
                self.assertFalse(envelope['ok'])
                self.assertEqual(envelope['error']['kind'], 'io_error')
                self.assertEqual(out_path.read_text(encoding='utf-8'), 'previous snapshot')

    def test_unparseable_inputs_block(self):
        pages_path = self.write_json('pages.json', [[]])
        out_path = self.root / 'snapshot.json'
        out_path.write_text('previous snapshot', encoding='utf-8')
        metadata_path = self.root / 'metadata.json'
        for label, raw in [('not JSON', b'{not json'),
                           ('not UTF-8', b'\xff\xfe\x00garbage')]:
            with self.subTest(label):
                metadata_path.write_bytes(raw)
                proc = self.cli('inventory', '--metadata', str(metadata_path),
                                '--pages', str(pages_path), '--merge-base', MERGE_BASE_SHA,
                                '--output', str(out_path))
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertEqual(json.loads(proc.stdout)['error']['kind'], 'invalid_data')
                self.assertEqual(out_path.read_text(encoding='utf-8'), 'previous snapshot')

    def test_failed_inventory_leaves_existing_output_untouched(self):
        out_path = self.root / 'snapshot.json'
        out_path.write_text('previous snapshot', encoding='utf-8')
        proc, out_path = self.run_inventory(metadata(2), [[row('a.py')]])
        self.assertEqual(proc.returncode, 2)
        envelope = json.loads(proc.stdout)
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['kind'], 'invalid_data')
        self.assertEqual(out_path.read_text(encoding='utf-8'), 'previous snapshot')

    def test_usage_errors_are_invalid_arguments(self):
        cases = [('no verb', ()), ('unknown verb', ('frobnicate',)),
                 ('missing flags', ('inventory',)), ('unknown flag', ('inventory', '--bogus'))]
        for label, args in cases:
            with self.subTest(label):
                proc = self.cli(*args)
                self.assertEqual(proc.returncode, 2, proc.stderr)
                envelope = json.loads(proc.stdout)
                self.assertFalse(envelope['ok'])
                self.assertEqual(envelope['error']['kind'], 'invalid_argument')

    def test_help_prints_usage(self):
        proc = self.cli('--help')
        self.assertEqual(proc.returncode, 0)
        self.assertIn('inventory --metadata', proc.stdout)


class TestRenderChecklist(unittest.TestCase):
    def test_render_partitions_inventory(self):
        snapshot = snapshot_of(ERRORS_PATH, UNICODE_PATH, 'docs/readme.md')
        preparation = {
            'ordered': [
                ordered_entry(ERRORS_PATH, role='storage layer', focus='review the new index',
                              id=7),
                ordered_entry(UNICODE_PATH, role='API', focus='check the parser',
                              ordering_note='introduced before its consumers', id=9),
            ],
            'excluded': [excluded_entry('docs/readme.md')],
        }
        text = collaborative_review.render_checklist(snapshot, preparation)
        status = collaborative_review.checklist_status(text)
        self.assertEqual(status['total'], 2)
        self.assertEqual(status['checked'], 0)
        self.assertEqual(status['current'], 1)
        self.assertEqual(status['snapshot'], {
            'pr_url': PR_URL, 'base_sha': BASE_SHA, 'head_sha': HEAD_SHA,
            'merge_base_sha': MERGE_BASE_SHA,
            'inventory_digest': snapshot['inventory_digest']})
        # rows are positions 1..M with the full contract, not the worker's guessed IDs
        self.assertIn('- [ ] 1. `{}` — Modified; storage layer; review the new index. '
                      '[PR diff]({})'.format(ERRORS_PATH, ERRORS_DIFF_URL), text)
        self.assertIn('- [ ] 2. `{}` — Modified; API; check the parser '
                      '(introduced before its consumers). [PR diff]({})'.format(
                          UNICODE_PATH, collaborative_review.diff_url(PR_URL, UNICODE_PATH)),
                      text)
        self.assertNotIn('- [ ] 7.', text)
        self.assertNotIn('- [ ] 9.', text)
        self.assertNotIn('- [ ] 3.', text)
        # every inventory path occurs exactly once across Files and Excluded
        for path in (ERRORS_PATH, UNICODE_PATH, 'docs/readme.md'):
            self.assertEqual(text.count(path), 1)
        self.assertIn('- `docs/readme.md` — documentation only', text)
        # the header comment is the machine-readable snapshot identity
        header = re.search(r'<!-- collaborative-review-snapshot: (\{.*\}) -->', text)
        self.assertIsNotNone(header)
        self.assertEqual(json.loads(header.group(1)), status['snapshot'])
        # missing, duplicate, and unknown preparation paths block rendering
        cases = {
            'missing path': {
                'ordered': [ordered_entry(ERRORS_PATH), ordered_entry(UNICODE_PATH)],
                'excluded': []},
            'duplicate in ordered': {
                'ordered': [ordered_entry(ERRORS_PATH), ordered_entry(ERRORS_PATH)],
                'excluded': [excluded_entry(UNICODE_PATH), excluded_entry('docs/readme.md')]},
            'duplicate across lists': {
                'ordered': [ordered_entry(ERRORS_PATH)],
                'excluded': [excluded_entry(ERRORS_PATH), excluded_entry(UNICODE_PATH),
                             excluded_entry('docs/readme.md')]},
            'unknown path': {
                'ordered': [ordered_entry(ERRORS_PATH), ordered_entry(UNICODE_PATH),
                            ordered_entry('src/not-in-this-pr.py')],
                'excluded': [excluded_entry('docs/readme.md')]},
        }
        for label, bad in cases.items():
            with self.subTest(label):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.render_checklist(snapshot, bad)

    def test_markdown_special_paths_keep_identity(self):
        paths = ['src/a`b`c.py', 'docs/``fence``.md', 'odd/name[1]|x*.py', 'bad\nname.py']
        snapshot = snapshot_of(*paths)
        preparation = {'ordered': [ordered_entry(path) for path in paths], 'excluded': []}
        text = collaborative_review.render_checklist(snapshot, preparation)
        status = collaborative_review.checklist_status(text)
        self.assertEqual(status['total'], 4)
        self.assertEqual(status['current'], 1)
        # a backtick run in the path gets a longer code-span fence
        self.assertIn('``src/a`b`c.py``', text)
        self.assertIn('```docs/``fence``.md```', text)
        # a line break in a path cannot split its row
        self.assertNotIn('bad\nname.py', text)
        self.assertIn('bad\\nname.py', text)
        # identity stays exact: the digest and the direct links hash the raw path
        self.assertEqual(status['snapshot']['inventory_digest'], snapshot['inventory_digest'])
        for entry in snapshot['files']:
            self.assertIn(entry['diff_url'], text)
        new_text, advanced = collaborative_review.advance_checklist(text, snapshot, 1, 'reviewed')
        self.assertTrue(advanced['advanced'])
        self.assertEqual(collaborative_review.checklist_status(new_text)['current'], 2)

    def test_empty_inventory_has_no_current_file(self):
        empty = collaborative_review.normalize_snapshot(metadata(0), [[]], MERGE_BASE_SHA)
        excluded_only = snapshot_of(ERRORS_PATH)
        cases = (
            ('no files', empty, {'ordered': [], 'excluded': []}),
            ('all excluded', excluded_only,
             {'ordered': [], 'excluded': [excluded_entry(ERRORS_PATH, reason='generated')]}),
        )
        for label, snapshot, preparation in cases:
            with self.subTest(label):
                text = collaborative_review.render_checklist(snapshot, preparation)
                status = collaborative_review.checklist_status(text)
                self.assertEqual(status['total'], 0)
                self.assertEqual(status['checked'], 0)
                self.assertIsNone(status['current'])
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.advance_checklist(text, snapshot, 1, 'reviewed')


class TestChecklistAdvance(unittest.TestCase):
    def setUp(self):
        self.snapshot = two_file_snapshot()
        self.text = collaborative_review.render_checklist(self.snapshot, two_entry_preparation())

    def test_only_the_reviewed_word_advances(self):
        for response in ('yes', 'next', 'reviewed looks good', '"reviewed"',
                         'why is reviewed used?', '1', 'approved', 'OK',
                         'reviewed, but the return type is still wrong',
                         'reviewed.', 'reviewed\nsecond line', ''):
            with self.subTest(response=response):
                new_text, status = collaborative_review.advance_checklist(
                    self.text, self.snapshot, 1, response)
                self.assertFalse(status['advanced'])
                self.assertEqual(new_text, self.text)
                self.assertEqual(status['checked'], 0)
                self.assertEqual(status['current'], 1)
        for response in ('reviewed', 'Reviewed', 'REVIEWED', ' reviewed\n', '\tReViewed '):
            with self.subTest(response=response):
                new_text, status = collaborative_review.advance_checklist(
                    self.text, self.snapshot, 1, response)
                self.assertTrue(status['advanced'])
                self.assertEqual(new_text, self.text.replace('- [ ] 1.', '- [x] 1.', 1))
                self.assertEqual(status['checked'], 1)
                self.assertEqual(status['current'], 2)

    def test_only_current_entry_can_advance(self):
        for entry in (2, 3):
            with self.subTest(entry=entry):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.advance_checklist(self.text, self.snapshot,
                                                           entry, 'reviewed')
        first, status = collaborative_review.advance_checklist(
            self.text, self.snapshot, 1, 'reviewed')
        self.assertTrue(status['advanced'])
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(first, self.snapshot, 1, 'reviewed')
        second, status = collaborative_review.advance_checklist(
            first, self.snapshot, 2, 'reviewed')
        self.assertTrue(status['advanced'])
        self.assertEqual(status['checked'], 2)
        self.assertIsNone(status['current'])
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(second, self.snapshot, 2, 'reviewed')

    def test_any_identity_change_rejects_approval(self):
        changes = {
            'pr_url': 'https://github.com/example/project/pull/8',
            'base_sha': 'd' * 40,
            'head_sha': 'd' * 40,
            'merge_base_sha': 'd' * 40,
            'inventory_digest': 'd' * 64,
        }
        for component, value in changes.items():
            with self.subTest(component=component):
                fresh = dict(self.snapshot)
                fresh[component] = value
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.advance_checklist(self.text, fresh, 1, 'reviewed')
        new_text, status = collaborative_review.advance_checklist(
            self.text, self.snapshot, 1, 'reviewed')
        self.assertTrue(status['advanced'])

    def test_advance_preserves_notes_and_findings(self):
        text = self.text.replace(
            '\n## Excluded',
            '\n```text\n- [ ] 8. a fenced sample, not a row\n```\n\n## Excluded', 1)
        text += ('\nA note about `lib/whois/errors.rb`.\n\n'
                 '## Findings\n\n- unresolved: the parser edge case stays open\n')
        new_text, status = collaborative_review.advance_checklist(
            text, self.snapshot, 1, 'reviewed')
        self.assertTrue(status['advanced'])
        self.assertEqual(new_text, text.replace('- [ ] 1.', '- [x] 1.', 1))
        self.assertEqual(status['total'], 2)
        self.assertIn('- [ ] 8. a fenced sample, not a row', new_text)
        self.assertIn('A note about `lib/whois/errors.rb`.', new_text)
        self.assertIn('unresolved: the parser edge case stays open', new_text)
        self.assertEqual(collaborative_review.checklist_status(new_text)['total'], 2)

    def test_stale_completed_checklist_is_rejected(self):
        first, status = collaborative_review.advance_checklist(
            self.text, self.snapshot, 1, 'reviewed')
        completed, status = collaborative_review.advance_checklist(
            first, self.snapshot, 2, 'reviewed')
        self.assertEqual(status['checked'], 2)
        self.assertIsNone(status['current'])
        stale = dict(self.snapshot)
        stale['head_sha'] = 'd' * 40
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(completed, stale, 2, 'reviewed')
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(completed, self.snapshot, 2, 'reviewed')
        self.assertEqual(completed.count('- [x]'), 2)

    def test_corrupt_ledger_is_rejected(self):
        header_line = [line for line in self.text.split('\n')
                       if 'collaborative-review-snapshot' in line][0]
        cases = {
            'duplicate id': self.text.replace('- [ ] 2.', '- [ ] 1.', 1),
            'missing id': self.text.replace('- [ ] 1.', '- [ ] 3.', 1),
            'no header': self.text.replace(header_line + '\n', '', 1),
            'broken header json': self.text.replace('{"base_sha"', '{"base_sha" broken', 1),
            'no Files section': self.text.replace('## Files', '## Changed files', 1),
        }
        for label, corrupt in cases.items():
            with self.subTest(label):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.checklist_status(corrupt)
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(cases['duplicate id'], self.snapshot,
                                                   1, 'reviewed')


class TestChecklistCli(CliCase):
    def render_files(self, snapshot, preparation=None):
        snapshot_path = self.write_json('snapshot.json', snapshot)
        preparation_path = self.write_json('preparation.json',
                                           preparation or two_entry_preparation())
        checklist = self.root / 'run' / 'review-checklist.md'
        proc = self.cli('render', '--snapshot', str(snapshot_path),
                        '--preparation', str(preparation_path), '--output', str(checklist))
        return proc, checklist, snapshot_path

    def test_render_status_advance_round_trip(self):
        snapshot = two_file_snapshot()
        proc, checklist, snapshot_path = self.render_files(snapshot)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        envelope = json.loads(proc.stdout)
        self.assertTrue(envelope['ok'])
        self.assertEqual(envelope['verb'], 'render')
        self.assertEqual(envelope['data']['output'], str(checklist))
        self.assertEqual(envelope['data']['ordered'], 2)
        self.assertEqual(envelope['data']['excluded'], 0)
        self.assertEqual(envelope['data']['inventory_digest'], snapshot['inventory_digest'])
        proc = self.cli('status', '--checklist', str(checklist))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)['data']
        self.assertEqual(data['checked'], 0)
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['current'], 1)
        self.assertEqual(data['snapshot']['pr_url'], PR_URL)
        # near-misses leave the ledger untouched: a longer reply and the old numeric `1`
        for response in ('reviewed looks good', '1'):
            with self.subTest(response=response):
                proc = self.cli('advance', '--checklist', str(checklist), '--snapshot',
                                str(snapshot_path), '--entry', '1', '--response', response)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                data = json.loads(proc.stdout)['data']
                self.assertFalse(data['advanced'])
                self.assertEqual(data['checked'], 0)
        # the case-folded word reviewed advances exactly one row, persisted on disk
        proc = self.cli('advance', '--checklist', str(checklist), '--snapshot', str(snapshot_path),
                        '--entry', '1', '--response', 'Reviewed')
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)['data']
        self.assertTrue(data['advanced'])
        self.assertEqual(data['checked'], 1)
        self.assertEqual(data['current'], 2)
        text = checklist.read_text(encoding='utf-8')
        self.assertIn('- [x] 1.', text)
        self.assertIn('- [ ] 2.', text)
        proc = self.cli('status', '--checklist', str(checklist))
        self.assertEqual(json.loads(proc.stdout)['data']['checked'], 1)
        # a corrupt ledger is refused rather than reported as progress
        checklist.write_text(text.replace('- [ ] 2.', '- [ ] 1.', 1), encoding='utf-8')
        proc = self.cli('status', '--checklist', str(checklist))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['error']['kind'], 'invalid_data')

    def test_cli_rejects_stale_and_wrong_entry(self):
        snapshot = two_file_snapshot()
        proc, checklist, snapshot_path = self.render_files(snapshot)
        original = checklist.read_bytes()
        wrong_entry = self.cli('advance', '--checklist', str(checklist), '--snapshot',
                               str(snapshot_path), '--entry', '2', '--response', 'reviewed')
        self.assertEqual(wrong_entry.returncode, 2, wrong_entry.stderr)
        self.assertEqual(json.loads(wrong_entry.stdout)['error']['kind'], 'invalid_data')
        self.assertEqual(checklist.read_bytes(), original)
        stale = dict(snapshot)
        stale['base_sha'] = 'd' * 40
        stale_path = self.write_json('stale-snapshot.json', stale)
        stale_run = self.cli('advance', '--checklist', str(checklist), '--snapshot',
                             str(stale_path), '--entry', '1', '--response', 'reviewed')
        self.assertEqual(stale_run.returncode, 2, stale_run.stderr)
        self.assertEqual(json.loads(stale_run.stdout)['error']['kind'], 'invalid_data')
        self.assertEqual(checklist.read_bytes(), original)
        bad_entry = self.cli('advance', '--checklist', str(checklist), '--snapshot',
                             str(snapshot_path), '--entry', 'zero', '--response', 'reviewed')
        self.assertEqual(bad_entry.returncode, 2)
        self.assertEqual(json.loads(bad_entry.stdout)['error']['kind'], 'invalid_argument')

    def test_persistence_failure_keeps_original(self):
        snapshot = two_file_snapshot()
        proc, checklist, snapshot_path = self.render_files(snapshot)
        original = checklist.read_bytes()
        out = io.StringIO()
        with mock.patch.object(collaborative_review.os, 'replace',
                               side_effect=OSError('disk full')):
            with contextlib.redirect_stdout(out):
                code = collaborative_review.main(['advance', '--checklist', str(checklist),
                                                  '--snapshot', str(snapshot_path),
                                                  '--entry', '1', '--response', 'reviewed'])
        self.assertEqual(code, 2)
        envelope = json.loads(out.getvalue())
        self.assertFalse(envelope['ok'])
        self.assertEqual(envelope['error']['kind'], 'io_error')
        self.assertEqual(checklist.read_bytes(), original)
        self.assertEqual(list(checklist.parent.glob('review-checklist.md.*')), [])

    def test_shortened_ledger_is_refused_without_write(self):
        """I1: a damaged ledger is invalid_data, not a completion claim or an approval."""
        snapshot = two_file_snapshot()
        proc, checklist, snapshot_path = self.render_files(snapshot)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        text = checklist.read_text(encoding='utf-8')
        corruptions = {
            'missing trailing row': '\n'.join(
                line for line in text.split('\n') if not line.startswith('- [ ] 2.')),
            'all rows removed': '\n'.join(
                line for line in text.split('\n') if not line.startswith('- [')),
            'malformed trailing row': '\n'.join(
                '- [ ] 2 missing the row body' if line.startswith('- [ ] 2.') else line
                for line in text.split('\n')),
        }
        for label, corrupt in corruptions.items():
            with self.subTest(label):
                checklist.write_text(corrupt, encoding='utf-8')
                before = checklist.read_bytes()
                status = self.cli('status', '--checklist', str(checklist))
                self.assertEqual(status.returncode, 2, status.stderr)
                self.assertEqual(json.loads(status.stdout)['error']['kind'], 'invalid_data')
                advance = self.cli('advance', '--checklist', str(checklist), '--snapshot',
                                   str(snapshot_path), '--entry', '1', '--response', 'reviewed')
                self.assertEqual(advance.returncode, 2, advance.stderr)
                self.assertEqual(json.loads(advance.stdout)['error']['kind'], 'invalid_data')
                self.assertEqual(checklist.read_bytes(), before)
        checklist.write_text(text, encoding='utf-8')


class TestChecklistCompleteness(unittest.TestCase):
    """I1: the rendered ordered count is part of the ledger's validity.

    A ledger whose `Ordered files: N` count no longer matches its parsed approval
    rows is damaged: `status` must not report a shorter checklist as progress, and
    `advance` must not check a row against it. The legitimate zero-row ledger stays
    valid.
    """

    def setUp(self):
        self.snapshot = two_file_snapshot()
        self.text = collaborative_review.render_checklist(self.snapshot, two_entry_preparation())

    @staticmethod
    def drop_rows(text, prefix):
        return '\n'.join(line for line in text.split('\n') if not line.startswith(prefix))

    def test_missing_trailing_row_is_refused(self):
        shortened = self.drop_rows(self.text, '- [ ] 2.')
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.checklist_status(shortened)
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(shortened, self.snapshot, 1, 'reviewed')

    def test_missing_trailing_row_cannot_become_a_completion(self):
        first, status = collaborative_review.advance_checklist(
            self.text, self.snapshot, 1, 'reviewed')
        self.assertTrue(status['advanced'])
        shortened = self.drop_rows(first, '- [ ] 2.')
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.checklist_status(shortened)
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(shortened, self.snapshot, 2, 'reviewed')

    def test_all_rows_removed_is_refused(self):
        emptied = self.drop_rows(self.text, '- [')
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.checklist_status(emptied)
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(emptied, self.snapshot, 1, 'reviewed')

    def test_malformed_trailing_row_is_refused(self):
        for malformed in ('- [ ] 2', '- [ ] 2.', '- [x] 2 missing the row body'):
            with self.subTest(malformed=malformed):
                corrupt = '\n'.join(
                    malformed if line.startswith('- [ ] 2.') else line
                    for line in self.text.split('\n'))
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.checklist_status(corrupt)

    def test_missing_or_mismatched_ordered_count_is_refused(self):
        count_line = 'Ordered files: 2 | Excluded: 0'
        cases = {
            'count line removed': self.text.replace(count_line + '\n', '', 1),
            'count line malformed': self.text.replace(
                count_line, 'Ordered files: many | Excluded: 0', 1),
            'count exceeds rows': self.text.replace(
                count_line, 'Ordered files: 3 | Excluded: 0', 1),
            'count below rows': self.text.replace(
                count_line, 'Ordered files: 1 | Excluded: 0', 1),
        }
        for label, corrupt in cases.items():
            with self.subTest(label):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.checklist_status(corrupt)

    def test_zero_row_checklist_stays_valid(self):
        snapshot = snapshot_of(ERRORS_PATH)
        text = collaborative_review.render_checklist(
            snapshot, {'ordered': [], 'excluded': [excluded_entry(ERRORS_PATH)]})
        self.assertIn('Ordered files: 0 | Excluded: 1', text)
        status = collaborative_review.checklist_status(text)
        self.assertEqual(status['checked'], 0)
        self.assertEqual(status['total'], 0)
        self.assertIsNone(status['current'])
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.advance_checklist(text, snapshot, 1, 'reviewed')


class TestCaptureSides(unittest.TestCase):
    """I2: the side paths a capture reads, with the rename base at `previous_path`.

    The head side is `path`; the base side is `path` except for renames, whose
    merge-base content lives at `previous_path`, and additions, which have no base
    side. A renamed entry without a recorded `previous_path` cannot be captured.
    """

    def test_rename_reads_the_base_side_at_previous_path(self):
        pages = [[row('src/domain/session_store.ts', status='renamed',
                      previous_filename='src/legacy/session store.ts')]]
        snapshot = collaborative_review.normalize_snapshot(metadata(1), pages, MERGE_BASE_SHA)
        self.assertEqual(collaborative_review.capture_sides(snapshot['files'][0]),
                         {'base': 'src/legacy/session store.ts',
                          'head': 'src/domain/session_store.ts'})

    def test_added_and_removed_files_keep_their_missing_side(self):
        pages = [[row('src/new.py', status='added'),
                  row('src/gone.py', status='removed', additions=0, deletions=3)]]
        snapshot = collaborative_review.normalize_snapshot(metadata(2), pages, MERGE_BASE_SHA)
        files = {entry['path']: entry for entry in snapshot['files']}
        self.assertEqual(collaborative_review.capture_sides(files['src/new.py']),
                         {'base': None, 'head': 'src/new.py'})
        self.assertEqual(collaborative_review.capture_sides(files['src/gone.py']),
                         {'base': 'src/gone.py', 'head': None})

    def test_modified_file_reads_the_same_path_on_both_sides(self):
        entry = snapshot_of(ERRORS_PATH)['files'][0]
        self.assertEqual(collaborative_review.capture_sides(entry),
                         {'base': ERRORS_PATH, 'head': ERRORS_PATH})

    def test_renamed_file_without_previous_path_is_refused(self):
        snapshot = collaborative_review.normalize_snapshot(
            metadata(1), [[row('src/new.py', status='renamed')]], MERGE_BASE_SHA)
        with self.assertRaises(collaborative_review.SnapshotError):
            collaborative_review.capture_sides(snapshot['files'][0])

    def test_mixed_fixture_rename_base_exists_only_at_previous_path(self):
        meta = json.loads((MIXED_PR_GITHUB / 'pr-view.json').read_text(encoding='utf-8'))
        pages = json.loads((MIXED_PR_GITHUB / 'pages.json').read_text(encoding='utf-8'))
        compare = json.loads((MIXED_PR_GITHUB / 'compare.json').read_text(encoding='utf-8'))
        snapshot = collaborative_review.normalize_snapshot(
            meta, pages, compare['merge_base_commit']['sha'])
        files = {entry['path']: entry for entry in snapshot['files']}
        sides = collaborative_review.capture_sides(files['src/domain/session_store.ts'])
        self.assertEqual(sides, {'base': 'src/legacy/session_store.ts',
                                 'head': 'src/domain/session_store.ts'})
        self.assertTrue((MIXED_PR_GITHUB / 'contents' / 'base' / sides['base']).is_file())
        self.assertFalse((MIXED_PR_GITHUB / 'contents' / 'base' / sides['head']).exists())
        self.assertTrue((MIXED_PR_GITHUB / 'contents' / 'head' / sides['head']).is_file())


class TestContentsEndpoint(unittest.TestCase):
    """I4: the contents request encodes path segments and pins the ref separately.

    Shell quoting alone leaves `#` a fragment, `?` a query separator and a literal
    `%20` a space once decoded. The endpoint percent-encodes the exact path, keeps
    `/` as the segment separator, appends the pinned 40-hex ref as its own query
    and is shell-quoted so the whole request stays one argument.
    """

    REPO = 'acme/session-kit'

    def test_special_paths_are_percent_encoded(self):
        cases = {
            'src/name#old.py': 'src/name%23old.py',
            'src/notes/why?.md': 'src/notes/why%3F.md',
            'src/util/per%20cent.py': 'src/util/per%2520cent.py',
            'src/notes/two words.md': 'src/notes/two%20words.md',
            'src/旧.py': 'src/%E6%97%A7.py',
            "src/it's.py": 'src/it%27s.py',
        }
        for path, encoded in cases.items():
            with self.subTest(path=path):
                raw = 'repos/acme/session-kit/contents/{}?ref={}'.format(
                    encoded, MERGE_BASE_SHA)
                self.assertEqual(
                    collaborative_review.contents_endpoint(self.REPO, path, MERGE_BASE_SHA),
                    shlex.quote(raw))

    def test_the_encoded_request_parses_as_one_path_and_the_pinned_query(self):
        for path, encoded in (('src/name#old.py', 'src/name%23old.py'),
                              ('src/notes/why?.md', 'src/notes/why%3F.md')):
            with self.subTest(path=path):
                endpoint = shlex.split(collaborative_review.contents_endpoint(
                    self.REPO, path, MERGE_BASE_SHA))
                self.assertEqual(len(endpoint), 1)
                parts = urllib.parse.urlsplit(endpoint[0])
                self.assertEqual(parts.path,
                                 'repos/acme/session-kit/contents/' + encoded)
                self.assertEqual(parts.query, 'ref=' + MERGE_BASE_SHA)
                self.assertEqual(parts.fragment, '')

    def test_rename_paths_and_quotes_stay_one_shell_word(self):
        for path in ('src/legacy/session store.ts', 'src/domain/session_store.ts',
                     "src/it's.py", 'src/a#b?.py'):
            with self.subTest(path=path):
                tokens = shlex.split(collaborative_review.contents_endpoint(
                    self.REPO, path, HEAD_SHA))
                self.assertEqual(len(tokens), 1)
                self.assertTrue(tokens[0].startswith('repos/acme/session-kit/contents/'))
                self.assertTrue(tokens[0].endswith('?ref=' + HEAD_SHA))

    def test_malformed_inputs_are_refused(self):
        cases = ((self.REPO, '', MERGE_BASE_SHA),
                 (self.REPO, 'a.py', 'HEAD'),
                 (self.REPO, 'a.py', None))
        for repo, path, ref in cases:
            with self.subTest(path=path, ref=ref):
                with self.assertRaises(collaborative_review.SnapshotError):
                    collaborative_review.contents_endpoint(repo, path, ref)


class TestValidateContextRequest(unittest.TestCase):
    """I3: a worker's supporting-context request is bounded and exact.

    At most eight unique repo-relative paths, each with a side (`base`, `head` or
    `both`) and a reason. A path is a literal exact path: valid `?`, `[`, `]` or
    `*` filename characters are kept as-is and are never expanded as patterns.
    Traversal, absolute paths, backslashes, empty segments, control characters
    and extra pattern-request fields are refused whole; the request never names a
    review row.
    """

    def test_exact_bounded_request_is_accepted_unchanged(self):
        request = [
            {'path': 'src/app.ts', 'sides': 'both',
             'reason': 'unchanged caller of the changed get() contract'},
            {'path': 'schemas/session.json', 'sides': 'head',
             'reason': "the generated schema's source"},
        ]
        self.assertEqual(collaborative_review.validate_context_request(request), request)

    def test_literal_special_characters_are_exact_paths_never_patterns(self):
        request = [
            {'path': 'src/why?.md', 'sides': 'head', 'reason': 'literal ? in the name'},
            {'path': 'src/odd/name[1]*.py', 'sides': 'both',
             'reason': 'literal bracket and star characters'},
            {'path': 'src/two words.md', 'sides': 'base', 'reason': 'space in the name'},
            {'path': 'src/旧/session.py', 'sides': 'both', 'reason': 'Unicode name'},
        ]
        self.assertEqual(collaborative_review.validate_context_request(request), request)

    def test_more_than_eight_paths_is_refused(self):
        request = [{'path': 'src/file{}.py'.format(index), 'sides': 'both', 'reason': 'needed'}
                   for index in range(9)]
        with self.assertRaises(collaborative_review.ContextRequestError):
            collaborative_review.validate_context_request(request)

    def test_duplicate_paths_are_refused(self):
        request = [{'path': 'src/app.ts', 'sides': 'both', 'reason': 'caller'},
                   {'path': 'src/app.ts', 'sides': 'head', 'reason': 'again'}]
        with self.assertRaises(collaborative_review.ContextRequestError):
            collaborative_review.validate_context_request(request)

    def test_traversal_absolute_and_unsafe_paths_are_refused(self):
        unsafe = ('../secrets.env', 'src/../../etc/passwd', '/etc/passwd',
                  './src/app.ts', 'src\\app.py', 'src//app.ts',
                  'src/a\x00b.py', 'src/a\nb.py', 'src/a\tb.py', '', 7)
        for path in unsafe:
            with self.subTest(path=path):
                request = [{'path': path, 'sides': 'both', 'reason': 'x'}]
                with self.assertRaises(collaborative_review.ContextRequestError):
                    collaborative_review.validate_context_request(request)

    def test_extra_pattern_or_wildcard_request_fields_are_refused(self):
        bad = (
            {'path': 'src/app.ts', 'sides': 'both', 'reason': 'x', 'glob': True},
            {'path': 'src/app.ts', 'sides': 'both', 'reason': 'x', 'pattern': 'src/**'},
            {'path': 'src/app.ts', 'sides': 'both', 'reason': 'x', 'wildcard': '*.py'},
            {'path': 'src/*.py', 'sides': 'both', 'reason': 'x', 'expand': True},
            {'glob': 'src/**/*.py', 'sides': 'both', 'reason': 'x'},
        )
        for entry in bad:
            with self.subTest(entry=entry):
                with self.assertRaises(collaborative_review.ContextRequestError):
                    collaborative_review.validate_context_request([entry])

    def test_sides_and_reason_are_validated(self):
        bad = (
            {'path': 'src/app.ts', 'reason': 'caller'},
            {'path': 'src/app.ts', 'sides': 'sideways', 'reason': 'caller'},
            {'path': 'src/app.ts', 'sides': 'both', 'reason': ''},
            {'path': 'src/app.ts', 'sides': 'both', 'reason': 'two\nlines'},
            'src/app.ts',
        )
        for entry in bad:
            with self.subTest(entry=entry):
                with self.assertRaises(collaborative_review.ContextRequestError):
                    collaborative_review.validate_context_request([entry])

    def test_a_request_must_be_a_list(self):
        for request in ('src/app.ts', {'path': 'src/app.ts'}, None):
            with self.subTest(request=request):
                with self.assertRaises(collaborative_review.ContextRequestError):
                    collaborative_review.validate_context_request(request)


REPORT_NOTE = {
    'link': 'reports/004.md',
    'assessment': 'Needs edits',
    'findings': ['`touch()` on a missing id is unpinned',
                 '`delete` has no coverage'],
}


class TestRecordReport(unittest.TestCase):
    """I5: a report's link, assessment and findings persist in the ledger.

    Recording writes under the non-approval `## Discussion and findings` section,
    never checks a box, is idempotent on resume, replaces an updated report's own
    note without duplication and survives the `reviewed` approval. The report
    reference is an actual clickable Markdown link whose relative URL target is
    percent-encoded while the raw link stays the visible label and the identity
    used to find and replace the block.
    """

    def setUp(self):
        self.snapshot = two_file_snapshot()
        self.text = collaborative_review.render_checklist(self.snapshot, two_entry_preparation())

    def files_section(self, text):
        return text.split('## Files', 1)[1].split('## Excluded', 1)[0]

    def anchors(self, text, link):
        prefix = '- Report [`{}`]('.format(link)
        return [line for line in text.split('\n') if line.startswith(prefix)]

    def test_recording_persists_the_link_assessment_and_findings(self):
        recorded, summary = collaborative_review.record_report(self.text, REPORT_NOTE)
        self.assertNotEqual(recorded, self.text)
        self.assertTrue(summary['changed'])
        # a clickable link, not only a backticked filename
        self.assertIn('- Report [`reports/004.md`](reports/004.md) — Needs edits.', recorded)
        self.assertEqual(len(self.anchors(recorded, 'reports/004.md')), 1)
        self.assertIn('`touch()` on a missing id is unpinned', recorded)
        self.assertIn('`delete` has no coverage', recorded)
        self.assertNotIn('_None yet._', recorded)
        # the note is non-approval text: no checkbox and no ## Files change
        self.assertEqual(self.files_section(recorded), self.files_section(self.text))
        self.assertEqual(recorded.count('- [x]'), 0)

    def test_recording_is_idempotent_on_resume(self):
        recorded, first = collaborative_review.record_report(self.text, REPORT_NOTE)
        self.assertTrue(first['changed'])
        again, second = collaborative_review.record_report(recorded, REPORT_NOTE)
        self.assertFalse(second['changed'])
        self.assertEqual(again, recorded)
        self.assertEqual(len(self.anchors(again, 'reports/004.md')), 1)

    def test_an_updated_report_replaces_its_note_without_duplication(self):
        recorded, _ = collaborative_review.record_report(self.text, REPORT_NOTE)
        updated_note = dict(REPORT_NOTE, findings=['one remaining gap'])
        updated, summary = collaborative_review.record_report(recorded, updated_note)
        self.assertTrue(summary['changed'])
        self.assertEqual(len(self.anchors(updated, 'reports/004.md')), 1)
        self.assertNotIn('`delete` has no coverage', updated)
        self.assertIn('one remaining gap', updated)
        # another report's note is untouched, and re-recording stays single-copy
        other, _ = collaborative_review.record_report(
            updated, dict(REPORT_NOTE, link='reports/003.md'))
        again, _ = collaborative_review.record_report(other, updated_note)
        self.assertEqual(len(self.anchors(again, 'reports/003.md')), 1)
        self.assertEqual(len(self.anchors(again, 'reports/004.md')), 1)
        self.assertIn('one remaining gap', again)

    def test_an_unresolved_finding_survives_the_reviewed_approval(self):
        recorded, _ = collaborative_review.record_report(self.text, REPORT_NOTE)
        advanced, status = collaborative_review.advance_checklist(
            recorded, self.snapshot, 1, 'reviewed')
        self.assertTrue(status['advanced'])
        self.assertEqual(advanced.count('- [x]'), 1)
        self.assertEqual(len(self.anchors(advanced, 'reports/004.md')), 1)
        self.assertIn('`touch()` on a missing id is unpinned', advanced)
        # re-presenting the reused report after the approval records nothing new
        again, summary = collaborative_review.record_report(advanced, REPORT_NOTE)
        self.assertFalse(summary['changed'])
        self.assertEqual(again, advanced)

    def test_the_link_target_is_percent_encoded_without_losing_raw_identity(self):
        cases = {
            'reports/004.md': 'reports/004.md',
            'reports/two words.md': 'reports/two%20words.md',
            'reports/why#1?.md': 'reports/why%231%3F.md',
            'reports/50%.md': 'reports/50%25.md',
            'reports/旧.md': 'reports/%E6%97%A7.md',
        }
        for link, target in cases.items():
            with self.subTest(link=link):
                recorded, summary = collaborative_review.record_report(
                    self.text, dict(REPORT_NOTE, link=link))
                self.assertTrue(summary['changed'])
                self.assertEqual(
                    self.anchors(recorded, link),
                    ['- Report [`{}`]({}) — Needs edits.'.format(link, target)])
                # the raw link is still the identity a resume re-records against
                again, second = collaborative_review.record_report(
                    recorded, dict(REPORT_NOTE, link=link))
                self.assertFalse(second['changed'])
                self.assertEqual(again, recorded)

    def test_a_special_link_keeps_its_raw_identity_when_replaced(self):
        link = 'reports/two words#?.md'
        recorded, _ = collaborative_review.record_report(
            self.text, dict(REPORT_NOTE, link=link))
        updated, summary = collaborative_review.record_report(
            recorded, dict(REPORT_NOTE, link=link, findings=['still open']))
        self.assertTrue(summary['changed'])
        self.assertEqual(len(self.anchors(updated, link)), 1)
        self.assertIn('](reports/two%20words%23%3F.md)', updated)
        self.assertIn('`{}`'.format(link), updated)
        self.assertIn('still open', updated)
        self.assertNotIn('`delete` has no coverage', updated)

    def test_malformed_notes_are_refused(self):
        bad_notes = (
            {'link': '', 'assessment': 'Needs edits', 'findings': []},
            {'link': 'reports/a\nb.md', 'assessment': 'Needs edits', 'findings': []},
            {'link': 'reports/004.md', 'assessment': 'Ship it', 'findings': []},
            {'link': 'reports/004.md', 'assessment': 'Needs edits',
             'findings': ['two\nlines']},
            {'link': 'reports/004.md', 'assessment': 'Needs edits', 'findings': [7]},
        )
        for note in bad_notes:
            with self.subTest(note=note):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.record_report(self.text, note)


class TestRecordReportFile(unittest.TestCase):
    """I5: recording reuses the atomic replacement and never checks a box.

    The file-oriented wrapper owns the report link's filesystem contract: the
    note's link is the report path relative to the checklist (`reports/004.md`),
    and the recorded hyperlink must resolve from the ledger to the existing
    report — a repo-root-relative link is canonicalized to the ledger-relative
    path, and an absolute, escaping or nonexistent destination is refused.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.run_dir = (self.root / '.artel' / 'run' / 'collaborative-review'
                        / 'acme' / 'session-kit' / 'pr-57')
        self.checklist = self.run_dir / 'review-checklist.md'
        snapshot = two_file_snapshot()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.checklist.write_text(
            collaborative_review.render_checklist(snapshot, two_entry_preparation()),
            encoding='utf-8')
        reports = self.run_dir / 'reports'
        reports.mkdir()
        (reports / '004.md').write_text('# Report 004\n', encoding='utf-8')

    def report_link(self, text):
        line = next((line for line in text.split('\n')
                     if line.startswith('- Report [')), None)
        self.assertIsNotNone(line, 'the ledger has no recorded report note')
        label = line.split('[`', 1)[1].split('`]', 1)[0]
        target = line.split('](', 1)[1].split(')', 1)[0]
        return label, target

    def chdir_root(self):
        previous = os.getcwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(self.root)

    def test_persisting_is_atomic_and_idempotent(self):
        first = collaborative_review.record_report_file(self.checklist, REPORT_NOTE)
        self.assertTrue(first['changed'])
        written = self.checklist.read_text(encoding='utf-8')
        self.assertIn('`reports/004.md`', written)
        self.assertIn('](reports/004.md)', written)
        second = collaborative_review.record_report_file(self.checklist, REPORT_NOTE)
        self.assertFalse(second['changed'])
        self.assertEqual(self.checklist.read_text(encoding='utf-8'), written)
        self.assertEqual(list(self.checklist.parent.glob('review-checklist.md.*')), [])

    def test_a_repo_root_relative_link_is_canonicalized_to_the_ledger(self):
        root_link = str(self.run_dir.relative_to(self.root) / 'reports' / '004.md')
        self.chdir_root()
        summary = collaborative_review.record_report_file(
            self.checklist, dict(REPORT_NOTE, link=root_link))
        self.assertTrue(summary['changed'])
        text = self.checklist.read_text(encoding='utf-8')
        label, target = self.report_link(text)
        self.assertEqual((label, target), ('reports/004.md', 'reports/004.md'))
        self.assertTrue((self.checklist.parent / target).resolve().is_file())
        # the canonical identity makes a re-record with either form idempotent
        again = collaborative_review.record_report_file(
            self.checklist, dict(REPORT_NOTE, link=root_link))
        self.assertFalse(again['changed'])

    def test_a_nonexistent_report_link_is_refused(self):
        with self.assertRaises(collaborative_review.ChecklistError):
            collaborative_review.record_report_file(
                self.checklist, dict(REPORT_NOTE, link='reports/999.md'))

    def test_an_absolute_or_escaping_report_link_is_refused(self):
        outside = self.run_dir.parent / 'outside.md'
        outside.write_text('# outside the run directory\n', encoding='utf-8')
        self.chdir_root()
        for link in ('/tmp/004.md', '../outside.md', 'reports/../../outside.md'):
            with self.subTest(link=link):
                with self.assertRaises(collaborative_review.ChecklistError):
                    collaborative_review.record_report_file(
                        self.checklist, dict(REPORT_NOTE, link=link))

    def test_a_nested_existing_report_link_is_kept(self):
        nested = self.run_dir / 'reports' / 'phase-1' / '004.md'
        nested.parent.mkdir()
        nested.write_text('# Report 004 (phase 1)\n', encoding='utf-8')
        summary = collaborative_review.record_report_file(
            self.checklist, dict(REPORT_NOTE, link='reports/phase-1/004.md'))
        self.assertTrue(summary['changed'])
        text = self.checklist.read_text(encoding='utf-8')
        label, target = self.report_link(text)
        self.assertEqual((label, target),
                         ('reports/phase-1/004.md', 'reports/phase-1/004.md'))
        self.assertTrue((self.checklist.parent / target).resolve().is_file())

    def test_failed_atomic_replace_keeps_the_original_bytes(self):
        original = self.checklist.read_bytes()
        with mock.patch.object(collaborative_review.os, 'replace',
                               side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                collaborative_review.record_report_file(self.checklist, REPORT_NOTE)
        self.assertEqual(self.checklist.read_bytes(), original)
        self.assertEqual(list(self.checklist.parent.glob('review-checklist.md.*')), [])


if __name__ == '__main__':
    unittest.main()
