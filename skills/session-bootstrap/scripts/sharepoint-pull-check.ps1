# sharepoint-pull-check.ps1
# Cross-Device-Pull-Check fuer den Google-Drive-Sharepoint.
# Zeigt: was sich seit <since> geaendert hat, Conflict-Files, offene Handoffs,
# grobe Index-Drift (INDEX.md vs. Pakete im FS). Read-only, schreibt nichts.
#
# Aufruf:
#   powershell -File sharepoint-pull-check.ps1 -Since "2026-05-24 00:00"
#   powershell -File sharepoint-pull-check.ps1            # default: letzte 3 Tage
#
# CLAUDE.md-Regeln beachtet: os-effizienter Scan, keine $_-Mangling-Anfaelligkeit
# (laeuft als -File, nicht via Bash-Tool-Inline), kanonischer Drive-Pfad.

param(
  [string]$Root = "G:\Meine Ablage\dynamic-AI\dynamic_sharepoint",
  [string]$Since = ""
)

if (-not (Test-Path $Root)) {
  Write-Output "STOP: Sharepoint-Pfad nicht gefunden: $Root"
  Write-Output "Google Drive for Desktop gemountet? Pfad korrekt?"
  exit 1
}

$sinceDate = if ($Since) { [datetime]::Parse($Since) } else { (Get-Date).AddDays(-3) }
Write-Output "=== SHAREPOINT-PULL-CHECK ==="
Write-Output "Root:  $Root"
Write-Output "Since: $($sinceDate.ToString('yyyy-MM-dd HH:mm'))"
Write-Output ""

# --- 1. Conflict-Files (Google Drive Muster + generisch) ---
$conflicts = Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.Name -match '\((1|2|3|Konflikt|conflict)\)|conflicted copy|\.gdoc-conflict' }
if ($conflicts) {
  Write-Output "!!! CONFLICT-FILES GEFUNDEN (STOP, nicht als Wahrheit lesen) !!!"
  $conflicts | ForEach-Object { "  C $($_.FullName.Substring($Root.Length+1))" }
  Write-Output ""
} else {
  Write-Output "Conflict-Files: keine."
  Write-Output ""
}

# --- 2. Geaenderte Dateien seit <since> ---
Write-Output "=== GEAENDERT SEIT $($sinceDate.ToString('yyyy-MM-dd')) ==="
$changed = Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue |
  Where-Object { $_.LastWriteTime -ge $sinceDate -and $_.Name -ne 'desktop.ini' } |
  Sort-Object LastWriteTime -Descending
if ($changed) {
  $changed | ForEach-Object {
    "  {0} | {1}" -f $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm'), $_.FullName.Substring($Root.Length+1)
  }
} else {
  Write-Output "  (nichts geaendert)"
}
Write-Output ""

# --- 3. Offene Handoffs (status:active) mit target ---
Write-Output "=== OFFENE HANDOFFS (status: active) ==="
$handoffs = Get-ChildItem -Path (Join-Path $Root '01_HANDOFFS') -Filter '*.md' -ErrorAction SilentlyContinue
$openCount = 0

# Frontmatter-Feld sicher extrahieren: Dateien ohne das Feld (z.B. INDEX.md ohne
# Frontmatter) liefern bei Select-String $null -> .Matches.Groups[1] wirft
# "Index auf NULL-Array". Guard statt Direktzugriff.
function Get-FmField([object[]]$lines, [string]$name) {
  $m = $lines | Select-String "^${name}:\s*(.+)$" | Select-Object -First 1
  if ($m) { return $m.Matches.Groups[1].Value.Trim() }
  return ""
}

foreach ($h in $handoffs) {
  $head = Get-Content $h.FullName -TotalCount 12 -ErrorAction SilentlyContinue
  $status = Get-FmField $head 'status'
  $agent  = Get-FmField $head 'agent'
  $target = Get-FmField $head 'target_agent'
  if (-not $target) { $target = '?' }
  if ($status -match 'active') {
    $openCount++
    $foreign = if ($agent -and $agent -notmatch 'claude') { "  <- VON $agent" } else { "" }
    "  [{0} -> {1}]{2}  {3}" -f $agent, $target, $foreign, $h.Name
  }
}
if ($openCount -eq 0) { Write-Output "  (keine offenen Handoffs)" }
Write-Output ""

# --- 4. Grobe Index-Drift: Pakete im FS vs. INDEX.md ---
Write-Output "=== INDEX-DRIFT (grob) ==="
$indexPath = Join-Path $Root 'INDEX.md'
if (Test-Path $indexPath) {
  $indexText = Get-Content $indexPath -Raw
  # Pakete = Ordner mit MANIFEST.md oder MANIFEST.json (rekursiv, 1. Ebene unter Kategorie)
  $pkgs = Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -in @('MANIFEST.md','MANIFEST.json') } |
    ForEach-Object { Split-Path $_.Directory -Leaf } | Sort-Object -Unique
  $missing = $pkgs | Where-Object { $indexText -notmatch [regex]::Escape($_) }
  if ($missing) {
    Write-Output "  Pakete mit MANIFEST, aber NICHT in INDEX.md erwaehnt:"
    $missing | ForEach-Object { "    ~ $_" }
  } else {
    Write-Output "  INDEX.md erwaehnt alle MANIFEST-Pakete."
  }
} else {
  Write-Output "  INDEX.md nicht gefunden."
}
Write-Output ""
Write-Output "=== ENDE PULL-CHECK ==="
