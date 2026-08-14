Option Explicit

Dim shell, files, root, standalone, source, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

root = files.GetParentFolderName(WScript.ScriptFullName)
standalone = files.BuildPath(files.BuildPath(root, "dist"), "GitPulse.exe")
source = files.BuildPath(root, "GitPulse.pyw")

If files.FileExists(standalone) Then
    shell.Run Quote(standalone), 0, False
    WScript.Quit 0
End If

If Not files.FileExists(source) Then
    MsgBox "GitPulse.pyw is missing. Extract the complete GitPulse folder and try again.", 16, "GitPulse — Startup problem"
    WScript.Quit 1
End If

If shell.Run("cmd.exe /d /c where pyw.exe >nul 2>nul", 0, True) = 0 Then
    command = "pyw.exe -3 " & Quote(source)
    shell.Run command, 0, False
    WScript.Quit 0
End If

If shell.Run("cmd.exe /d /c where pythonw.exe >nul 2>nul", 0, True) = 0 Then
    command = "pythonw.exe " & Quote(source)
    shell.Run command, 0, False
    WScript.Quit 0
End If

MsgBox "GitPulse needs Python 3.10 or newer. Install Python from python.org, then try again.", 16, "GitPulse — Startup problem"
WScript.Quit 1

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
