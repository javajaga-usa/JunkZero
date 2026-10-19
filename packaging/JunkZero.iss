; Inno Setup script for the JunkZero Windows installer (JunkZero-Setup.exe).
; Built by .github/workflows/release.yml from the PyInstaller folder build in dist\JunkZero:
;   ISCC.exe /DAppVersion=1.2.0 packaging\JunkZero.iss
; Installs for the current user only (no administrator rights needed), adds Start menu and
; optional desktop shortcuts, and its uninstaller also removes JunkZero's scheduled scan.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
; Must match TASK_NAME in app/engine/scheduler.py
#define TaskName "JunkZero Scheduled Scan"

[Setup]
AppId={{5B0E7C3A-6A57-4C1E-9F1D-4A1B2C3D4E5F}
AppName=JunkZero
AppVersion={#AppVersion}
AppVerName=JunkZero {#AppVersion}
AppPublisher=JunkZero
AppPublisherURL=https://github.com/javajaga-usa/JunkZero
AppSupportURL=https://github.com/javajaga-usa/JunkZero/issues
AppUpdatesURL=https://github.com/javajaga-usa/JunkZero/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\JunkZero
DefaultGroupName=JunkZero
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
SetupIconFile=JunkZero.ico
UninstallDisplayIcon={app}\JunkZero.exe
OutputDir=..\dist
OutputBaseFilename=JunkZero-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; Close a running JunkZero before files are replaced or removed
CloseApplications=yes

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\JunkZero\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\JunkZero"; Filename: "{app}\JunkZero.exe"
Name: "{autodesktop}\JunkZero"; Filename: "{app}\JunkZero.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\JunkZero.exe"; Description: "{cm:LaunchProgram,JunkZero}"; Flags: nowait postinstall skipifsilent

[Registry]
; JunkZero registers the junkzero: link itself (so a scheduled scan's notification can open its
; report). The installer does not create it, but removes it on uninstall.
Root: HKCU; Subkey: "Software\Classes\junkzero"; Flags: uninsdeletekey dontcreatekey

[UninstallRun]
; Scheduled scans would otherwise keep trying to start the removed JunkZero.exe.
; schtasks reports an error when no scan was scheduled; that is fine.
Filename: "{sys}\schtasks.exe"; Parameters: "/Delete /F /TN ""{#TaskName}"""; Flags: runhidden; RunOnceId: "RemoveScheduledScan"

; Your settings, history and reports in %APPDATA%\JunkZero are kept, so a reinstall picks them up.
