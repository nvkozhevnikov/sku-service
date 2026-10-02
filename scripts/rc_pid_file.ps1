# Strict decoding only; does not read, move or edit any files or start processes.
function ConvertFrom-RcPidBytes {
    param([byte[]]$Bytes, [string]$ApprovedDataDirectory)
    $encodings = @(
        [Text.UTF8Encoding]::new($false, $true),
        [Text.Encoding]::GetEncoding(1251, [Text.EncoderFallback]::ExceptionFallback, [Text.DecoderFallback]::ExceptionFallback)
    )
    foreach ($encoding in $encodings) {
        try { $text = $encoding.GetString($Bytes) }
        catch [Text.DecoderFallbackException] { continue }
        $lines = @($text -split "`r?`n")
        if ($lines.Count -lt 6) { continue }
        # CP1251 is accepted ONLY when the decoded path equals the pinned path.
        # No replacement characters, guessed aliases or directory migrations.
        if ($lines[1].Replace('/', '\').TrimEnd('\') -ine $ApprovedDataDirectory.Replace('/', '\').TrimEnd('\')) { continue }
        $roundTrip = $encoding.GetBytes($text)
        if ([Convert]::ToBase64String($roundTrip) -cne [Convert]::ToBase64String($Bytes)) { continue }
        return [PSCustomObject]@{ Lines = $lines; Encoding = $encoding.WebName }
    }
    throw 'PID file cannot be decoded to the exact approved RC directory (strict UTF-8 or CP1251).'
}
