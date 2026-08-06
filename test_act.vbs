On Error Resume Next
Set WshShell = WScript.CreateObject("WScript.Shell")
success = False
For i = 1 To 5
    If WshShell.AppActivate("antigravity-mobile") Then
        success = True
        Exit For
    End If
    WScript.Sleep 500
Next
WScript.Echo success
