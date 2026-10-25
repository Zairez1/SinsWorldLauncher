Option Explicit
Dim shell, fso, folder, bat
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
bat = fso.BuildPath(folder, "start_launcher.bat")
shell.CurrentDirectory = folder
shell.Run Chr(34) & bat & Chr(34), 1, False
