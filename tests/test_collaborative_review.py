"""`scripts/collaborative_review.py`: the validated PR snapshot a review binds to.

The collaborative review reads one pull request at recorded revisions. Its inventory
must be complete -- every page `gh api --paginate --slurp` returned, matching the PR's
own changed-file count, no duplicate path -- and every file needs a direct GitHub diff
link derived from its exact UTF-8 path, because that anchor is what a person clicks
while reviewing. A snapshot that silently lost a page or accepted a malformed row must
be refused before it becomes review input.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import collaborative_review  # noqa: E402

SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'collaborative_review.py'

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


class TestInventoryCli(unittest.TestCase):
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
        cases = [('no verb', ()), ('unknown verb', ('render',)), ('missing flags', ('inventory',)),
                 ('unknown flag', ('inventory', '--bogus'))]
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


if __name__ == '__main__':
    unittest.main()
