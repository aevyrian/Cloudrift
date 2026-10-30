import tempfile
import unittest
from pathlib import Path

from app.store import Store


WORK = '7431234567890123456'


def item(cid, root='', reply_to='', attachments=None):
    return {'id': cid, 'root_id': root, 'reply_to_id': reply_to, 'reply_to_user': '',
            'nickname': '名字', 'text': '<安全文本>', 'created_at': 10, 'likes': 1,
            'reply_count': 0, 'attachments': attachments or []}


class CommentStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_unsaved_is_read_only_and_identity_only_uses_trusted_aliases(self):
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'unsaved')
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM comment_jobs').fetchone()[0], 0)
        self.assertEqual(self.store.comment_work_id(WORK), WORK)
        self.assertIsNone(self.store.comment_work_id('legacy-one'))
        with self.store.connect() as db:
            db.execute('INSERT INTO work_aliases VALUES (?,?)', (WORK, 'legacy-one'))
        self.assertEqual(self.store.comment_work_id('legacy-one'), WORK)
        with self.store.connect() as db:
            db.execute('INSERT INTO work_aliases VALUES (?,?)', ('7431234567890123457', 'legacy-one'))
        self.assertIsNone(self.store.comment_work_id('legacy-one'))

    def test_page_checkpoint_atomic_and_local_pagination_hides_sources(self):
        job = self.store.comment_queue(WORK, 'save')
        token = job['token']
        attachment = {'kind': 'image', 'source_url': 'https://example.invalid/private', 'status': 'pending', 'error': ''}
        page = [item('111', attachments=[attachment]), item('222', '111', 'missing')]
        checkpoint = {'cursor': 9, 'root_done': False, 'replies': []}
        self.assertTrue(self.store.comment_commit_page(WORK, token, page, checkpoint, None, 0.1))
        self.assertEqual(self.store.comment_job(WORK)['checkpoint'], checkpoint)
        root = self.store.comment_page(WORK)['items'][0]
        self.assertEqual(root['reply_count'], 1)
        self.assertNotIn('source_url', root['attachments'][0])
        self.assertTrue(self.store.comment_page(WORK, '111')['items'][0]['reply_target_missing'])
        self.assertEqual(self.store.comment_snapshot(WORK)['items'][0]['attachments'][0]['source_url'], attachment['source_url'])

    def test_cancel_requeue_stale_result_does_not_cross_operation(self):
        old = self.store.comment_queue(WORK, 'save')['token']
        self.store.comment_cancel(WORK)
        new = self.store.comment_queue(WORK, 'continue')['token']
        self.assertNotEqual(old, new)
        self.assertFalse(self.store.comment_commit_page(WORK, old, [item('111')], {'cursor': 1, 'root_done': False, 'replies': []}, None, 0))
        self.assertEqual(self.store.comment_state(WORK)['total'], 0)
        self.assertEqual(self.store.comment_state(WORK)['operation'], 'queued')

    def test_changed_attachment_is_not_misrepresented_as_already_saved(self):
        token = self.store.comment_queue(WORK, 'save')['token']
        self.store.comment_claim()
        attachment = {'kind': 'image', 'source_url': 'https://example.invalid/a', 'status': 'pending', 'error': ''}
        checkpoint = {'cursor': 0, 'root_done': False, 'replies': []}
        self.store.comment_commit_page(WORK, token, [item('111', attachments=[attachment])], checkpoint, None, 0)
        asset = self.store.comment_next_asset(WORK)
        self.store.comment_commit_asset(WORK, token, asset['id'], {'relative_path': 'comment-assets/a.png', 'sha256': 'a' * 64, 'bytes': 1}, 0)
        self.store.comment_commit_page(WORK, token, [item('111', attachments=[attachment])], checkpoint, None, 0)
        self.assertEqual(self.store.comment_asset(asset['id'])['status'], 'saved')
        replacement = {**attachment, 'source_url': 'https://example.invalid/b'}
        self.store.comment_commit_page(WORK, token, [item('111', attachments=[replacement])], checkpoint, None, 0)
        current = self.store.comment_asset(asset['id'])
        self.assertEqual((current['status'], current['relative_path'], current['refresh_pending']),
                         ('saved', 'comment-assets/a.png', 1))

    def test_upgrade_preserves_old_pending_reference(self):
        token = self.store.comment_queue(WORK, 'save')['token']
        self.store.comment_claim()
        attachment = {'kind': 'image', 'source_url': 'https://example.invalid/a', 'status': 'pending', 'error': ''}
        self.store.comment_commit_page(WORK, token, [item('111', attachments=[attachment])],
                                       {'cursor': 0, 'root_done': False, 'replies': []}, None, 0)
        asset = self.store.comment_next_asset(WORK)
        self.store.comment_commit_asset(WORK, token, asset['id'], {'relative_path': 'comment-assets/a.png', 'sha256': 'a' * 64, 'bytes': 1}, 0)
        # Simulate the previously released database while a replacement was in flight.
        with self.store.connect() as db:
            db.execute("UPDATE comment_assets SET status='pending' WHERE id=?", (asset['id'],))
            db.execute('ALTER TABLE comment_assets DROP COLUMN refresh_pending')
        restored = Store(self.store.root).comment_asset(asset['id'])
        self.assertEqual((restored['status'], restored['relative_path'], restored['refresh_pending']),
                         ('saved', 'comment-assets/a.png', 1))


if __name__ == '__main__':
    unittest.main()
