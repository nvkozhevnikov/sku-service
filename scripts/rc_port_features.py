"""One-off scoped integration from pinned Git objects; no dirty-tree copies."""
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]

def git(*args):
    return subprocess.check_output(['git', '-C', str(ROOT), *args])

def port(ref, paths):
    base = git('merge-base', '58152e4', ref).decode().strip()
    patch = git('diff', '--ignore-space-at-eol', '--binary', base, ref, '--', *paths)
    subprocess.run(['git', '-C', str(ROOT), 'apply', '--3way', '--ignore-space-change', '-'], input=patch, check=True)

if __name__ == '__main__':
    # Retain identity/CI/auth/scheduler changes from develop and F01/F03.
    port('94deab99', ['universal_supplier', 'sterbrust_matching/product_identity.py',
                     'tests/fixtures/ltt_uss', 'tests/fixtures/ltt_uss_units',
                     'tests/fixtures/uss_semantic_tables', 'tests/fixtures/uss_terminal_batch',
                     'tests/fixtures/commercial/real', 'tests/test_ltt_uss_adapters.py',
                     'tests/test_ltt_uss_identity.py', 'tests/test_ltt_yug_page_role.py',
                     'tests/test_ltt_modx_page_role.py', 'tests/test_uss_semantic_tables.py',
                     'tests/test_uss_own_heading.py', 'tests/test_uss_terminal_batch.py',
                     'tests/test_vekprom_adapter.py', 'tests/test_qa_availability_regression.py'])
    port('c919870', ['competitor_intel', 'universal_supplier/control_plane/competitor_ui.py',
                    'universal_supplier/control_plane/static',
                    'universal_supplier/control_plane/templates',
                    'tests/test_competitor_intel.py', 'tests/test_competitor_execution.py',
                    'tests/test_competitor_worker_dispatch.py', 'tests/test_competitor_monitoring_ui.py',
                    'tests/test_competitor_news_ui.py', 'tests/test_competitor_surface_provenance.py'])
