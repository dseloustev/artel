import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'hooks'))
import doc_header as dh  # noqa: E402

HEADER = ('---\ntype: prd\nticket: AW-12\nversion: 3\ntitle: A: title with a colon\n'
          'status: PRD_READY\nschema: 1\nproduced_by: artel:analyst\n---\n')


class TestSplit(unittest.TestCase):
    def test_a_header_and_its_body(self):
        block, body = dh.split(HEADER + '# PRD\n')
        self.assertTrue(block.startswith('type: prd\n'))
        self.assertTrue(block.endswith('produced_by: artel:analyst'))
        self.assertEqual(body, '# PRD\n')

    def test_no_block(self):
        self.assertEqual(dh.split('# PRD\n'), (None, '# PRD\n'))

    def test_crlf_is_no_block_as_it_is_to_kartoteka(self):
        text = HEADER.replace('\n', '\r\n') + '# PRD\r\n'
        self.assertEqual(dh.split(text), (None, text))

    def test_an_unclosed_block_is_no_block(self):
        text = '---\ntype: prd\nversion: 1\n# PRD\n'
        self.assertEqual(dh.split(text), (None, text))

    def test_a_closing_rule_at_end_of_file_is_no_block(self):
        # kartoteka's frontmatter.split needs `\n---\n`: a final `---` with no newline is text.
        text = '---\ntype: prd\nversion: 1\n---'
        self.assertEqual(dh.split(text), (None, text))

    def test_the_body_is_kept_byte_for_byte(self):
        body = '# PRD\r\n\n---\n\ntext\n'
        self.assertEqual(dh.split(HEADER + body)[1], body)


class TestFields(unittest.TestCase):
    def test_flat_fields(self):
        found = dh.fields(HEADER)
        self.assertEqual(found['type'], 'prd')
        self.assertEqual(found['version'], '3')
        self.assertEqual(found['title'], 'A: title with a colon')

    def test_none_without_a_block(self):
        self.assertIsNone(dh.fields('# PRD\n'))

    def test_comments_and_indented_lines_are_not_fields(self):
        self.assertEqual(dh.fields('---\n# a comment\n  nested: x\ntype: prd\n---\n'),
                         {'type': 'prd'})

    def test_a_repeated_key_takes_the_last_value_as_yaml_does(self):
        self.assertEqual(dh.fields('---\nversion: 1\nversion: 2\n---\n')['version'], '2')

    def test_an_empty_value(self):
        self.assertEqual(dh.fields('---\nsummary:\n---\n'), {'summary': ''})


class TestVersion(unittest.TestCase):
    def test_a_plain_decimal(self):
        self.assertEqual(dh.version(HEADER), 3)

    def test_zero(self):
        self.assertEqual(dh.version('---\nversion: 0\n---\n'), 0)

    def test_none_for_what_kartoteka_would_not_read_as_this_integer(self):
        for value in ("'3'", '"3"', '-1', '03', '3.0', '*v', '', 'three'):
            with self.subTest(value=value):
                self.assertIsNone(dh.version('---\nversion: {}\n---\n'.format(value)))

    def test_none_without_a_block_or_a_version_line(self):
        self.assertIsNone(dh.version('# PRD\nversion: 3\n'))
        self.assertIsNone(dh.version('---\ntype: prd\n---\n'))


class TestSetVersion(unittest.TestCase):
    def test_sets_the_line_and_keeps_every_other_byte(self):
        text = HEADER + '# PRD\r\nversion: 3 in the body stays\n'
        out = dh.set_version(text, 4)
        self.assertEqual(out, text.replace('version: 3\n', 'version: 4\n', 1))
        self.assertEqual(dh.version(out), 4)

    def test_every_version_line_of_the_block(self):
        self.assertEqual(dh.set_version('---\nversion: 1\nversion: 2\n---\nb', 5),
                         '---\nversion: 5\nversion: 5\n---\nb')

    def test_an_unreadable_version_value_is_replaced(self):
        self.assertEqual(dh.version(dh.set_version('---\nversion: "7"\n---\n', 8)), 8)

    def test_refuses_without_a_block_or_a_version_line(self):
        for text in ('# PRD\n', '---\ntype: prd\n---\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                dh.set_version(text, 1)


class TestStatus(unittest.TestCase):
    def test_the_header_status(self):
        self.assertEqual(dh.status(HEADER + '# PRD\n'), 'PRD_READY')

    def test_the_header_wins_over_an_old_line(self):
        self.assertEqual(dh.status(HEADER + '- **Status:** DRAFT\n'), 'PRD_READY')

    def test_old_documents_fall_back_to_their_status_line(self):
        for line in ('- Status: PRD_READY', 'Status: PRD_READY', '- **Status:** PRD_READY',
                     '**Status:** PRD_READY'):
            with self.subTest(line=line):
                self.assertEqual(dh.status('# PRD\n\n## Metadata\n\n{}\n'.format(line)),
                                 'PRD_READY')

    def test_an_empty_header_status_falls_back_too(self):
        text = '---\ntype: prd\nversion: 1\nstatus:\n---\nStatus: PLAN_DRAFTED\n'
        self.assertEqual(dh.status(text), 'PLAN_DRAFTED')

    def test_only_the_first_status_line_counts_as_grep_m1_did(self):
        self.assertIsNone(dh.status('Status: open question\nStatus: PRD_READY\n'))
        self.assertEqual(dh.status('Status: DRAFT\nStatus: PRD_READY\n'), 'DRAFT')

    def test_none_when_nothing_is_declared(self):
        self.assertIsNone(dh.status('# Research\n\nNo status here.\n'))


class TestBody(unittest.TestCase):
    def test_the_body_after_the_block(self):
        self.assertEqual(dh.body(HEADER + '**Summary**\n'), '**Summary**\n')

    def test_a_document_without_a_block_is_unchanged(self):
        for text in ('**Summary**\n', '---\nnot a header, no closing rule\n'):
            with self.subTest(text=text):
                self.assertEqual(dh.body(text), text)


if __name__ == '__main__':
    unittest.main()
