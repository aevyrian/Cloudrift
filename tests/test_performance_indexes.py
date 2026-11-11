import tempfile
import unittest
from pathlib import Path

from app.store import Store


class PerformanceIndexTests(unittest.TestCase):
    def test_author_and_reverse_alias_queries_use_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            with store.connect() as db:
                author_plan = [row[3] for row in db.execute(
                    'EXPLAIN QUERY PLAN SELECT COUNT(*) FROM works WHERE 1=1 AND (author_id=? OR author=?)',
                    ('same', 'same'))]
                alias_plan = [row[3] for row in db.execute(
                    'EXPLAIN QUERY PLAN SELECT 1 FROM work_aliases WHERE work_id=? AND id!=? LIMIT 1',
                    ('legacy-test', '7431234567890123456'))]
            self.assertTrue(any('work_author ' in row for row in author_plan), author_plan)
            self.assertTrue(any('work_author_name' in row for row in author_plan), author_plan)
            self.assertTrue(any('work_alias_target' in row for row in alias_plan), alias_plan)

    def test_existing_database_gains_indexes_without_changing_results(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            with store.connect() as db:
                db.execute('DROP INDEX IF EXISTS work_author_name')
                db.execute('DROP INDEX IF EXISTS work_alias_target')
            store.save_work({'id': 'legacy-test', 'nickname': 'same', 'uid': 'one'},
                            ['2025-12-01 12.30.00-视频-example.mp4'])
            store.save_work({'id': '123', 'nickname': 'other', 'uid': 'same'}, ['video.mp4'])
            store.save_work({'id': '456', 'nickname': 'other', 'uid': 'another'}, ['other.mp4'])
            store.link_work('7431234567890123456', 'legacy-test')
            before_filter = store.library(author='same')
            before_alias = store.work('7431234567890123456')
            self.assertEqual(before_filter['total'], 2)
            self.assertEqual(before_alias['id'], 'legacy-test')

            for _ in range(2):
                store = Store(Path(directory))
                self.assertEqual(store.library(author='same'), before_filter)
                self.assertEqual(store.work('7431234567890123456'), before_alias)
                with store.connect() as db:
                    work_indexes = {row[1] for row in db.execute('PRAGMA index_list(works)')}
                    alias_indexes = {row[1] for row in db.execute('PRAGMA index_list(work_aliases)')}
                self.assertIn('work_author_name', work_indexes)
                self.assertIn('work_alias_target', alias_indexes)
