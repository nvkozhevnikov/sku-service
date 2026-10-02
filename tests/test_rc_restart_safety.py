"""Offline restart-policy tests. Never executes the PostgreSQL launcher."""
from pathlib import Path
import shutil
import subprocess
import base64

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/start_local_rc_postgres.ps1'


def test_restart_is_ascii_and_windows_powershell_51_parseable():
    text = SCRIPT.read_text(encoding='ascii')
    assert 'param([switch]$RecoverStalePid)' in text
    powershell = shutil.which('powershell.exe')
    if not powershell:
        pytest.skip('Windows PowerShell 5.1 is unavailable')
    # Parsing only: no launcher statements are executed.
    command = "$t=$null; $e=$null; [System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$t,[ref]$e) | Out-Null; if ($e.Count) { $e | Out-String | Write-Output; exit 1 }; $PSVersionTable.PSVersion.ToString()"
    escaped = str(SCRIPT).replace("'", "''")
    command = command.replace('$args[0]', "'" + escaped + "'")
    result = subprocess.run([powershell, '-NoProfile', '-Command', command], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().startswith('5.1.')


def test_identity_does_not_parse_localized_labels():
    text = SCRIPT.read_text(encoding='ascii')
    assert '[BitConverter]::ToUInt64($controlBytes, 0).ToString()' in text
    assert '$controlExit -ne 0 -or $actualId -cne $expectedId' in text
    assert 'Select-String' not in text
    assert '7691270601420084116' in text


def test_recovery_is_opt_in_preserves_lock_and_checks_twice():
    text = SCRIPT.read_text(encoding='ascii')
    assert 'if (-not $RecoverStalePid)' in text
    assert 'Get-CimInstance Win32_Process' in text
    assert 'Get-NetTCPConnection -State Listen -ErrorAction Stop' in text
    assert 'Get-RcNetstatPortCount -Lines $netstat' in text
    assert 'Assert-RcRecoveryEvidence' in text
    checks = [i for i, line in enumerate(text.splitlines()) if line.strip() == 'Assert-NoRcPostmaster']
    assert len(checks) == 2
    assert text.index('WriteAllBytes($savedPid, $pidBytes)') < text.index('Move-Item -LiteralPath $pidPath')
    assert text.index('metadata.json') < text.index('Move-Item -LiteralPath $pidPath')
    assert 'postmaster.pid.original' in text
    assert 'Remove-Item' not in text
    assert 'initdb.exe' not in text and 'pg_resetwal' not in text
    assert text.count("'pg_ctl.exe') start") == 1


def test_after_start_identity_port_and_readonly_counts_are_required():
    text = SCRIPT.read_text(encoding='ascii')
    assert 'BEGIN READ ONLY;' in text
    assert 'pg_is_in_recovery(),system_identifier,current_setting(\'port\')' in text
    assert 'universal_supplier_server|rc_admin|17.11|f|$expectedId|55449' in text
    assert "'supplier_http_captures',count(*)" in text
    assert "'offer_commercial_observations',count(*)" in text


@pytest.mark.parametrize('encoding', ['utf-8', 'cp1251'])
@pytest.mark.parametrize('wrong_path', [False, True])
def test_pid_decoder_accepts_only_lossless_exact_approved_path(encoding, wrong_path):
    powershell = shutil.which('powershell.exe')
    if not powershell:
        pytest.skip('Windows PowerShell 5.1 is unavailable')
    approved = 'D:/documents/personal/ChatGPT/Стербруст/work/universal_supplier_rc_pg17_data'
    recorded = approved.replace('universal_supplier_rc', 'other_rc') if wrong_path else approved
    payload = base64.b64encode(f'1276\n{recorded}\n1790763488\n55449\n\n127.0.0.1\n\nready   \n'.encode(encoding)).decode()
    helper = str(ROOT / 'scripts/rc_pid_file.ps1').replace("'", "''")
    command = (f"$ErrorActionPreference='Stop'; . '{helper}'; "
               f"$r=ConvertFrom-RcPidBytes -Bytes ([Convert]::FromBase64String('{payload}')) "
               f"-ApprovedDataDirectory '{approved}'; Write-Output $r.Encoding; "
               "if ($r.Lines[3] -ne '55449' -or $r.Lines[7] -ne 'ready   ') { exit 3 }")
    encoded = base64.b64encode(command.encode('utf-16le')).decode()
    result = subprocess.run([powershell, '-NoProfile', '-EncodedCommand', encoded], capture_output=True)
    if wrong_path:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert ('windows-1251' if encoding == 'cp1251' else 'utf-8').encode() in result.stdout


@pytest.mark.parametrize('pid,postgres,nettcp,netstat,ok,probe,allowed', [
    (False, 0, 0, 0, True, 'TIMEOUT', True),
    (False, 0, 0, 0, True, 'CONNECTION_REFUSED', True),
    (False, 0, 0, 0, True, 'CONNECTED', False),
    (False, 0, 0, 0, True, 'ERROR', False),
    (True, 0, 0, 0, True, 'TIMEOUT', False),
    (False, 1, 0, 0, True, 'TIMEOUT', False),
    (False, 0, 1, 0, True, 'TIMEOUT', False),
    (False, 0, 0, 1, True, 'TIMEOUT', False),
    (False, 0, 0, 0, False, 'TIMEOUT', False),
])
def test_actual_powershell_port_decision(pid, postgres, nettcp, netstat, ok, probe, allowed):
    powershell = shutil.which('powershell.exe')
    if not powershell:
        pytest.skip('Windows PowerShell 5.1 is unavailable')
    helper = str(ROOT / 'scripts/rc_port_guard.ps1').replace("'", "''")
    command = (f"$ErrorActionPreference='Stop'; . '{helper}'; "
               f"Assert-RcRecoveryEvidence -PIDExists ${str(pid).lower()} -PostgresCount {postgres} "
               f"-NetTCPCount {nettcp} -NetstatCount {netstat} -InventoriesSucceeded ${str(ok).lower()} -ProbeResult '{probe}'")
    result = subprocess.run([powershell, '-NoProfile', '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode()], capture_output=True)
    assert (result.returncode == 0) == allowed, result.stderr


@pytest.mark.parametrize('rows,count', [
    (['TCP 0.0.0.0:135 0.0.0.0:0 LISTENING 1112'], 0),
    (['TCP [::]:55449 [::]:0 LISTENING 1234'], 1),
    (['TCP 127.0.0.1:55449 0.0.0.0:0 LOCALIZED_STATE 1234'], 1),
    (['TCP 127.0.0.1:49152 127.0.0.1:55449 SYN_SENT 1234'], 0),
    (['TCP malformed'], None),
    ([], None),
])
def test_actual_powershell_netstat_parser(rows, count):
    powershell = shutil.which('powershell.exe')
    if not powershell:
        pytest.skip('Windows PowerShell 5.1 is unavailable')
    helper = str(ROOT / 'scripts/rc_port_guard.ps1').replace("'", "''")
    quoted = ','.join("'" + row + "'" for row in rows)
    command = f"$ErrorActionPreference='Stop'; . '{helper}'; Get-RcNetstatPortCount -Lines @({quoted}) -Port 55449"
    result = subprocess.run([powershell, '-NoProfile', '-EncodedCommand', base64.b64encode(command.encode('utf-16le')).decode()], capture_output=True)
    if count is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == str(count).encode()
