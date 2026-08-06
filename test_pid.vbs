Set WshShell = WScript.CreateObject("WScript.Shell")
Set objWMIService = GetObject("winmgmts:\\.\root\cimv2")
Set colItems = objWMIService.ExecQuery("Select * From Win32_Process Where Name LIKE '%Antigravity%' OR Name = 'Code.exe'")
For Each objItem in colItems
    WshShell.AppActivate objItem.ProcessId
Next
WScript.Echo "Done"
