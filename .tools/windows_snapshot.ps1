param(
    [Parameter(Mandatory=$true)][int[]]$ProcessIds,
    [Parameter(Mandatory=$true)][string]$OutputFile,
    [switch]$IncludeUiText,
    [switch]$Native32
)
$ErrorActionPreference = 'Stop'
# A 32-bit collector can enumerate the native modules of a 32-bit COM surrogate.
if ($Native32 -and [Environment]::Is64BitProcess) {
    $host32 = Join-Path $env:WINDIR 'SysWOW64\WindowsPowerShell\v1.0\powershell.exe'
    $argFile = [IO.Path]::GetTempFileName()
    try {
        @{ProcessIds=$ProcessIds; OutputFile=[IO.Path]::GetFullPath($OutputFile); IncludeUiText=[bool]$IncludeUiText} |
            ConvertTo-Json | Set-Content -LiteralPath $argFile -Encoding UTF8
        # Encoded invocation contains only properly quoted file paths, no process command lines.
        $escapedScript = $PSCommandPath.Replace("'", "''")
        $escapedArgs = $argFile.Replace("'", "''")
        $code = "`$v=Get-Content -LiteralPath '$escapedArgs' -Raw | ConvertFrom-Json; & '$escapedScript' -ProcessIds `$v.ProcessIds -OutputFile `$v.OutputFile -IncludeUiText:`$v.IncludeUiText"
        $encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($code))
        & $host32 -NoProfile -EncodedCommand $encoded
        if ($LASTEXITCODE -ne 0) { throw '32-bit snapshot failed' }
    } finally { Remove-Item -LiteralPath $argFile -Force }
    return
}
if (Test-Path -LiteralPath $OutputFile) { throw 'Snapshot output already exists' }
if ($IncludeUiText) { Add-Type -AssemblyName UIAutomationClient }
$records = @()
foreach ($processNumber in $ProcessIds) {
    $record = [ordered]@{id=$processNumber; errors=@()}
    try {
        $proc = Get-Process -Id $processNumber
        $record.path=$proc.Path; $record.title=$proc.MainWindowTitle; $record.responding=$proc.Responding
        try { $record.modules=@($proc.Modules | ForEach-Object { @{name=$_.ModuleName; path=$_.FileName} }) }
        catch { $record.errors += $_.Exception.Message }
        if ($IncludeUiText) {
            # Include owned dialogs, not just MainWindowHandle. No screenshots or unrelated apps.
            $record.ui=@()
            $desktop=[System.Windows.Automation.AutomationElement]::RootElement
            $condition=New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ProcessIdProperty,$processNumber)
            foreach ($window in $desktop.FindAll([System.Windows.Automation.TreeScope]::Children,$condition)) {
                $record.ui += @{name=$window.Current.Name; id=$window.Current.AutomationId; offscreen=$window.Current.IsOffscreen}
                foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)) {
                    $record.ui += @{name=$element.Current.Name; id=$element.Current.AutomationId; offscreen=$element.Current.IsOffscreen; bounds=$element.Current.BoundingRectangle.ToString()}
                }
            }
        }
    } catch { $record.errors += $_.Exception.Message }
    $records += $record
}
@{utc=[DateTime]::UtcNow.ToString('o'); collectorBits=([IntPtr]::Size*8); processes=$records} |
    ConvertTo-Json -Depth 9 | Set-Content -LiteralPath $OutputFile -Encoding UTF8
