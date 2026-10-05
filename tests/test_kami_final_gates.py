import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import verify_kami_reconciliation as verification
from scripts import persist_kami_full_run1 as persistence
from scripts import match_kami_verified as matching


class KamiFinalGateTests(unittest.TestCase):
    def test_actual_writer_target_and_role_are_checked(self):
        from unittest.mock import MagicMock
        repo = MagicMock()
        repo.connection.execute.return_value.fetchone.return_value = (
            'universal_supplier_server', '127.0.0.1', 55449, '17.11', 'kami_app', False)
        with self.assertRaisesRegex(RuntimeError, 'Actual writer target/role mismatch'):
            persistence.runtime_preflight(repo)

    def test_matching_requires_full_live_run2_before_loading_registry(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / 'LIVE_RUN2').mkdir()
            (base / 'FULL_RUN1_CHECKPOINT.json').write_text(json.dumps({'status': 'IN_PROGRESS'}))
            (base / 'LIVE_RUN2/CHECKPOINT.json').write_text(json.dumps({'status': 'IN_PROGRESS'}))
            with patch.object(matching, 'BASE', base):
                with self.assertRaisesRegex(RuntimeError, 'Verified full RUN1/RUN2'):
                    matching.load_inputs()

    def test_no_full_verdict_while_first_collector_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'CHECKPOINT.json').write_text(json.dumps({
                'status': 'IN_PROGRESS', 'queue': ['remaining'], 'saved_category_resume_count': 0}))
            with patch.object(verification, 'SOURCE', source):
                with self.assertRaisesRegex(RuntimeError, 'Both collector passes must finish'):
                    verification.main()

    def test_no_full_verdict_before_exactly_one_coverage_resume(self):
        for count in (0, 2):
            with self.subTest(count=count), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp)
                (source / 'CHECKPOINT.json').write_text(json.dumps({
                    'status': 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED', 'queue': [],
                    'saved_category_resume_count': count}))
                with patch.object(verification, 'SOURCE', source):
                    with self.assertRaisesRegex(RuntimeError, 'Both collector passes must finish'):
                        verification.main()

    def test_unresolved_identity_collisions_block_final_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'CHECKPOINT.json').write_text(json.dumps({
                'status': 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED', 'queue': [],
                'saved_category_resume_count': 1, 'parse_errors': [], 'identity_conflicts': [{}]}))
            with patch.object(verification, 'SOURCE', source):
                with self.assertRaisesRegex(RuntimeError, 'Unresolved parser/identity'):
                    verification.main()

    def test_no_database_connection_on_unverified_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / 'FINAL_DISCOVERY_VERIFIED.json').write_text(json.dumps({
                'status': 'IN_PROGRESS', 'cards_sha256': 'not-a-sha'}))
            (source / 'CARDS.jsonl').write_text('')
            with patch.object(persistence, 'INPUT', source), patch.object(persistence.psycopg, 'connect') as connect:
                with self.assertRaisesRegex(RuntimeError, 'Verified full source manifest'):
                    persistence.main()
                connect.assert_not_called()


if __name__ == '__main__':
    unittest.main()
