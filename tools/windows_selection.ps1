param([long]$WindowHandle,[double]$X=[double]::NaN,[double]$Y=[double]::NaN)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName WindowsBase
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$WindowHandle)
$focused=[System.Windows.Automation.AutomationElement]::FocusedElement
$result=@{text='';context='';source=$root.Current.Name;source_checked=$true;password=$false}
$candidates=[System.Collections.Generic.List[object]]::new()
function Add-SourceBranch($node) {
 $branch=[System.Collections.Generic.List[object]]::new();$belongs=$false
 for($i=0;$i -lt 18 -and $null -ne $node;$i++) {
  try {
   $branch.Add($node)
   if($node.Equals($root) -or $node.Current.NativeWindowHandle -eq $WindowHandle){$belongs=$true;break}
   $node=[System.Windows.Automation.TreeWalker]::ControlViewWalker.GetParent($node)
  } catch {break}
 }
 if($belongs){foreach($item in $branch){$candidates.Add($item)}}
}
if(-not [double]::IsNaN($X) -and -not [double]::IsNaN($Y)) {
 try {Add-SourceBranch ([System.Windows.Automation.AutomationElement]::FromPoint([System.Windows.Point]::new($X,$Y)))} catch {}
}
Add-SourceBranch $focused
$candidates.Add($root)
function Read-Selection($elements) {
 foreach($item in $elements) {
  if($null -eq $item){continue}
  try {
    if($item.Current.IsPassword){$result.password=$true;continue}
    if($item.Current.IsOffscreen){continue}
    $pattern=$item.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
    foreach($range in $pattern.GetSelection()) {
      $text=$range.GetText(2000)
      if([string]::IsNullOrWhiteSpace($text)){continue}
      # Text is authoritative. Optional provider operations must never erase
      # a valid selection (Qt/Chromium may reject Paragraph expansion/rectangles).
      $result.text=$text;$result.context=$text;$result.bounds=@()
      $result.selection_verified=$true
      try {$result.bounds=@($range.GetBoundingRectangles())} catch {}
      try {
       $rect=$item.Current.BoundingRectangle
       $result.element_bounds=@($rect.X,$rect.Y,$rect.Width,$rect.Height)
      } catch {}
      try {
       $context=$range.Clone()
       $context.ExpandToEnclosingUnit([System.Windows.Automation.TextUnit]::Paragraph)
       $result.context=$context.GetText(6000)
      } catch {}
      return $true
    }
  } catch {}
 }
 return $false
}
if(-not (Read-Selection $candidates)) {
 try {
  $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::IsTextPatternAvailableProperty,$true)
  $items=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
  $fallback=[System.Collections.Generic.List[object]]::new()
  for($i=0;$i -lt [Math]::Min(80,$items.Count);$i++){$fallback.Add($items[$i])}
  $null=Read-Selection $fallback
 } catch {}
}
$result | ConvertTo-Json -Compress
