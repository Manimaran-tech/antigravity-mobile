
Set WshShell = WScript.CreateObject("WScript.Shell")
WScript.Sleep 500
WshShell.Run "notepad.exe"
WScript.Sleep 1000
WshShell.SendKeys "[CRITICAL: C:/tempo]"
WScript.Sleep 500
