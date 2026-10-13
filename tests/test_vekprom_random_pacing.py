"""Offline tests for the operator-authorized vekprom random pacing (3-7 s).

No supplier HTTP is performed: pacer sampling uses injected samplers or the
real uniform sampler against a fake sleep, and capture semantics use fake
clients only.
"""
import inspect
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

from scripts import run_vekprom_intervesp_run1 as runner
from scripts.run_vekprom_intervesp_run1 import RETRYABLE, VEKPROM_PACING_RANGE, build_pacer
from universal_supplier.full_supplier_collection import PublicPacer
from universal_supplier.http_capture import CaptureStatus, capture_public_html

DETAIL_URL = 'https://vekprom.ru/catalog/gruppy/borfreza-art-123456/'


def _response(status_code, body=b'<html></html>'):
    return SimpleNamespace(status_code=status_code, final_url=DETAIL_URL,
                           content_type='text/html', redirects=(), body=body)


class JitterRangeTests(unittest.TestCase):
    def test_range_constant_is_3_to_7(self):
        self.assertEqual(VEKPROM_PACING_RANGE, (3.0, 7.0))

    def test_delays_stay_within_bounds(self):
        drawn = iter([3.0, 7.0, 3.42, 5.18, 6.71, 4.06, 3.88, 5.63, 6.24, 4.91])
        sleeps = []
        pacer = PublicPacer.with_jitter_range(3.0, 7.0, sampler=lambda a, b: next(drawn),
                                              sleep=sleeps.append)
        self.assertEqual(pacer.before_get(), 0.0)  # first GET: no pause
        for _ in range(9):
            pacer.before_get()
        self.assertEqual(sleeps, [3.0, 7.0, 3.42, 5.18, 6.71, 4.06, 3.88, 5.63, 6.24])
        self.assertTrue(all(3.0 <= d <= 7.0 for d in sleeps))

    def test_series_varies_and_is_continuous(self):
        pacer = PublicPacer.with_jitter_range(3.0, 7.0, sleep=lambda _s: None)
        draws = [pacer.next_delay() for _ in range(200)]
        self.assertTrue(all(3.0 <= d <= 7.0 for d in draws))
        self.assertGreaterEqual(len(set(draws)), 190)  # no fixed/mechanical repeats
        self.assertTrue(any(abs(d - round(d)) > 1e-9 for d in draws))  # not rounded
        self.assertTrue(any(d < 4.0 for d in draws) and any(d > 6.0 for d in draws))
        mean = sum(draws) / len(draws)
        self.assertTrue(3.5 < mean < 6.5, mean)  # centred near 5.0

    def test_first_get_has_no_pause(self):
        sleeps = []
        pacer = PublicPacer.with_jitter_range(3.0, 7.0, sampler=lambda a, b: 5.0,
                                              sleep=sleeps.append)
        self.assertEqual(pacer.before_get(), 0.0)
        self.assertEqual(sleeps, [])

    def test_invalid_ranges_rejected(self):
        with self.assertRaisesRegex(ValueError, "min"):
            PublicPacer.with_jitter_range(7.0, 3.0)
        with self.assertRaisesRegex(ValueError, "positive"):
            PublicPacer.with_jitter_range(0.0, 5.0)
        with self.assertRaisesRegex(ValueError, "positive"):
            PublicPacer.with_jitter_range(-1.0, 3.0)


class DefaultPolicyGuardTests(unittest.TestCase):
    def test_fixed_floor_guard_preserved(self):
        with self.assertRaisesRegex(ValueError, "at least 20 seconds"):
            PublicPacer(5.0)
        with self.assertRaisesRegex(ValueError, "at least 20 seconds"):
            PublicPacer(19.999)

    def test_default_policy_still_fixed_20(self):
        sleeps = []
        pacer = PublicPacer(sleep=sleeps.append)
        pacer.before_get()
        pacer.before_get()
        pacer.before_get()
        self.assertEqual(sleeps, [20.0, 20.0])

    def test_other_suppliers_keep_default_pacing(self):
        for supplier in ('intervesp', 'partner_st', 'optimum', 'kami',
                         'beka_mak', 'beka_mak_tr'):
            pacer = build_pacer(supplier)
            self.assertEqual((pacer.pause_min_seconds, pacer.pause_max_seconds),
                             (20.0, 20.0), supplier)
            sleeps = []
            pacer.sleep = sleeps.append
            pacer.before_get()
            pacer.before_get()
            self.assertEqual(sleeps, [20.0], supplier)

    def test_vekprom_policy_wired_to_3_7(self):
        pacer = build_pacer('vekprom')
        self.assertEqual((pacer.pause_min_seconds, pacer.pause_max_seconds), (3.0, 7.0))
        self.assertIsNone(pacer.pause_seconds)


class RunnerSemanticsUnchangedTests(unittest.TestCase):
    def test_network_error_still_resume_retryable(self):
        self.assertEqual(RETRYABLE, {'NETWORK_ERROR'})

    def test_stop_file_and_atomic_checkpoint_paths_untouched(self):
        source = inspect.getsource(runner)
        self.assertIn('STOPPED_BY_FILE', source)  # STOP-file protocol intact
        self.assertIn('pending.replace(path)', source)  # atomic checkpoint write intact


class BlockingProtectionTests(unittest.TestCase):
    def _capture(self, client):
        return capture_public_html(DETAIL_URL, evidence_dir=Path('work/tmp'),
                                   client=client, timeout_seconds=20, max_attempts=1)

    def test_403_maps_to_blocked_without_retry(self):
        calls = []

        def fake_get(url, *, timeout_seconds):
            calls.append(url)
            return _response(403)

        result = self._capture(SimpleNamespace(get=fake_get))
        self.assertEqual(result.status, CaptureStatus.BLOCKED)
        self.assertEqual(len(calls), 1)

    def test_429_maps_to_blocked_without_retry(self):
        calls = []

        def fake_get(url, *, timeout_seconds):
            calls.append(url)
            return _response(429)

        result = self._capture(SimpleNamespace(get=fake_get))
        self.assertEqual(result.status, CaptureStatus.BLOCKED)
        self.assertEqual(len(calls), 1)

    def test_network_error_single_attempt_no_reprobe(self):
        calls = []

        def fake_get(url, *, timeout_seconds):
            calls.append(url)
            raise URLError('timed out')

        result = self._capture(SimpleNamespace(get=fake_get))
        self.assertEqual(result.status, CaptureStatus.NETWORK_ERROR)
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
