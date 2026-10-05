from unittest.mock import Mock, patch
import unittest
from universal_supplier.kami_checkpoint import write_checkpoint


class KamiCheckpointTests(unittest.TestCase):
    def test_transient_windows_reader_does_not_abort_or_discard_checkpoint(self):
        target, temporary = Mock(), Mock()
        target.with_suffix.return_value = temporary
        temporary.replace.side_effect = [PermissionError('temporary Windows lock'), None]
        with patch('universal_supplier.kami_checkpoint.time.sleep') as sleep:
            write_checkpoint(target, {'completed': ['saved']}, attempts=2)
        temporary.write_text.assert_called_once()
        self.assertEqual(temporary.replace.call_count, 2)
        target.unlink.assert_not_called()
        sleep.assert_called_once()

    def test_persistent_permission_failure_stops_closed(self):
        target, temporary = Mock(), Mock()
        target.with_suffix.return_value = temporary
        temporary.replace.side_effect = PermissionError('persistent failure')
        with patch('universal_supplier.kami_checkpoint.time.sleep'), self.assertRaises(PermissionError):
            write_checkpoint(target, {}, attempts=2)
        self.assertEqual(temporary.replace.call_count, 2)
        target.unlink.assert_not_called()
        temporary.unlink.assert_not_called()
