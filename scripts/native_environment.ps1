function Merge-NativeNoProxy {
    [CmdletBinding()]
    param(
        [AllowNull()]
        [AllowEmptyString()]
        [string[]]$ExistingValues = @()
    )

    $merged = @()
    $seen = @{}
    foreach ($value in @($ExistingValues) + @('127.0.0.1', 'localhost', '::1')) {
        foreach ($entry in ([string]$value -split ',')) {
            $candidate = $entry.Trim()
            if ([string]::IsNullOrWhiteSpace($candidate) -or $seen.ContainsKey($candidate)) {
                continue
            }
            $seen[$candidate] = $true
            $merged += $candidate
        }
    }
    return ($merged -join ',')
}
