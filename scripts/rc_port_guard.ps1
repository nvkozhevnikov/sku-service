# Pure inventory parsing and recovery decision; no start/stop/file mutations.
function Get-RcNetstatPortCount {
    param([string[]]$Lines, [int]$Port = 55449)
    $tcpRows = 0
    $portRows = 0
    foreach ($line in $Lines) {
        if ($line -notmatch '^\s*TCP\s+') { continue }
        $fields = @($line.Trim() -split '\s+')
        if ($fields.Count -ne 5 -or $fields[4] -notmatch '^\d+$' -or $fields[1] -notmatch ':\d+$') {
            throw 'Unrecognized netstat TCP row; recovery refused.'
        }
        $tcpRows++
        # Reject ANY local-port row, not only an English LISTENING label.
        # Includes IPv4/IPv6/wildcards; never mistakes a foreign port for local.
        if ($fields[1] -match (':' + $Port + '$')) { $portRows++ }
    }
    if ($tcpRows -eq 0) { throw 'Netstat inventory has no verifiable TCP rows; recovery refused.' }
    return $portRows
}

function Assert-RcRecoveryEvidence {
    param([bool]$PIDExists, [int]$PostgresCount, [int]$NetTCPCount,
          [int]$NetstatCount, [bool]$InventoriesSucceeded, [string]$ProbeResult)
    if (-not $InventoriesSucceeded -or $PostgresCount -lt 0 -or $NetTCPCount -lt 0 -or $NetstatCount -lt 0) {
        throw 'Windows inventory is unavailable; recovery refused.'
    }
    if ($PIDExists -or $PostgresCount -ne 0 -or $NetTCPCount -ne 0 -or $NetstatCount -ne 0) {
        throw 'PID/process/port inventory contradicts safe recovery; STOP.'
    }
    if ($ProbeResult -notin @('TIMEOUT', 'CONNECTION_REFUSED')) {
        throw 'TCP probe contradicts safe recovery or failed unexpectedly; STOP.'
    }
    # TIMEOUT is inconclusive, not a listener. Two successful independent OS
    # inventories plus absence of PID/postgres are the required evidence.
}
