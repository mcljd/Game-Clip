; Inno Setup script: builds GAA-Clips-Setup.exe from dist\GAA Clips\.
; Installs per-user (no admin password needed - club laptops often lack it).
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6E2F3B1C-3C8B-4C51-9C1E-7A0D9E5B2A41}
AppName=GAA Clips
AppVersion={#AppVersion}
AppPublisher=Game-Clip
DefaultDirName={localappdata}\Programs\GAA Clips
DefaultGroupName=GAA Clips
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=GAA-Clips-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\GAA Clips.exe

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist\GAA Clips\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\GAA Clips"; Filename: "{app}\GAA Clips.exe"
Name: "{autodesktop}\GAA Clips"; Filename: "{app}\GAA Clips.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GAA Clips.exe"; Description: "Open GAA Clips now"; Flags: nowait postinstall skipifsilent
