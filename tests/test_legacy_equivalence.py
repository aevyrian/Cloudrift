import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.legacy import identical_legacy_copies


class LegacyEquivalenceTests(unittest.TestCase):
    def test_only_complete_byte_identical_ordered_copies_collapse(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, data in (('a/1.jpg', b'first'), ('a/2.mp4', b'second'),
                               ('b/1.jpg', b'first'), ('b/2.mp4', b'second')):
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
            first = {'id': 'legacy-a', 'files': ['a/1.jpg', 'a/2.mp4']}
            second = {'id': 'legacy-b', 'files': ['b/1.jpg', 'b/2.mp4']}
            self.assertEqual(identical_legacy_copies(root, [first, second]), [first])
            with patch('app.legacy.LEGACY_COMPARISON_BYTES', 1):
                self.assertEqual(identical_legacy_copies(root, [first, second]), [first, second])
            (root / 'b/2.mp4').write_bytes(b'differ')
            self.assertEqual(identical_legacy_copies(root, [first, second]), [first, second])
            (root / 'b/2.mp4').unlink()
            self.assertEqual(identical_legacy_copies(root, [first, second]), [first, second])
            self.assertEqual(identical_legacy_copies(root, [first, {**second, 'files': ['../outside']}]), [first, {**second, 'files': ['../outside']}])


if __name__ == '__main__':
    unittest.main()
