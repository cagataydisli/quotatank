Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """" & dir & "\.venv\Scripts\pythonw.exe"" """ & dir & "\launch.py""", 0, False
