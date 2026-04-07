; PhoneBridge — Inno Setup Installer Script
; Compile with Inno Setup 6+ (https://jrsoftware.org/isinfo.php)
;
; This creates PhoneBridge_Setup.exe which:
;   - Installs PhoneBridge.exe to Program Files
;   - Creates desktop shortcut
;   - Creates Start Menu entry
;   - Creates uninstall entry in Control Panel
;   - Registers "Start with Windows" option

#define MyAppName "PhoneBridge"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "PhoneBridge"
#define MyAppURL "https://github.com/ysachin26/PhoneBridge"
#define MyAppExeName "PhoneBridge.exe"

[Setup]
AppId={{B5F4E321-7A2C-4D8B-9E6F-1A3B5C7D9E0F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
; Output setup installer
OutputDir=installer
OutputBaseFilename=PhoneBridge_Setup
; Compression
Compression=lzma2/ultra64
SolidCompression=yes
; Require admin for Program Files install
PrivilegesRequired=admin
; Modern look
WizardStyle=modern
; Minimum Windows version (Windows 10)
MinVersion=10.0

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupicon"; Description: "Start PhoneBridge when Windows starts"

[Files]
; Main executable
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
; Desktop
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
; Start with Windows (optional, based on task selection)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#MyAppName}"; ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; Tasks: startupicon

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Kill the process before uninstalling
Filename: "taskkill"; Parameters: "/F /IM {#MyAppExeName}"; Flags: runhidden

[UninstallDelete]
; Clean up config directory
Type: filesandordirs; Name: "{localappdata}\PhoneBridge"
