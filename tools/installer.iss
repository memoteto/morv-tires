; Windows installer for Morv Tires. Build: ISCC /DAppVersion=0.1.0 tools\installer.iss
; The shop's data lives in LocalAppData, never under {app}, so install, upgrade and
; uninstall can replace the program folder without touching a single invoice.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
; A fixed id is what makes the next version replace this one instead of installing beside it
AppId={{8E3C1C2A-7D46-4C1B-9E5A-2F0B6D41A7C3}
AppName=Morv Tires
AppVersion={#AppVersion}
AppPublisher=Morv
DefaultDirName={autopf}\MorvTires
DefaultGroupName=Morv Tires
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=MorvTires-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
ArchitecturesAllowed=x64compatible
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
; An upgrade must not fail because the program is open
CloseApplications=yes
RestartApplications=no
UninstallDisplayName=Morv Tires {#AppVersion}
UninstallDisplayIcon={app}\MorvTires.exe
SetupIconFile=..\morv\ui\assets\app_icon.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\MorvTires\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Morv Tires"; Filename: "{app}\MorvTires.exe"
Name: "{group}\{cm:UninstallProgram,Morv Tires}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Morv Tires"; Filename: "{app}\MorvTires.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\MorvTires.exe"; Description: "{cm:LaunchProgram,Morv Tires}"; Flags: nowait postinstall skipifsilent

; No [UninstallDelete]: removing the program must never remove the shop's data or backups.
