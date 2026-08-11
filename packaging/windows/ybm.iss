; Inno Setup script for YBM's Windows installer.
;
; Built by .github/workflows/release.yml from the tree scripts/package_release.ps1
; stages, so the installer carries a prebuilt admin console and needs no Node.js
; on the target machine.
;
; Two deliberate choices:
;
;   PrivilegesRequired=lowest  - a per-user install into %LOCALAPPDATA%, with no
;     UAC prompt and no administrator account required. This is what Chrome and
;     Firefox do. It also keeps YBM's own files writable by the user it runs as,
;     which matters because the venv, config, and database live beside them.
;
;   No file associations, no services, no PATH edits - installing YBM should
;     change nothing about the machine except adding a folder and a shortcut.
;
; Build locally with:
;   iscc /DMyAppVersion=0.1.0 /DPayloadDir=..\..\dist\payload packaging\windows\ybm.iss

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#ifndef PayloadDir
  #define PayloadDir "..\..\dist\payload"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

#define MyAppName "YBM"
#define MyAppPublisher "iodriller"
#define MyAppURL "https://github.com/iodriller/YBM"
#define MyAppLauncher "YBM.bat"

[Setup]
; Never change AppId: it is how Windows recognises an upgrade rather than a
; second parallel installation.
AppId={{8F3C2A54-9D1B-4E77-A6C2-4B7F1E0D9A35}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases
DefaultDirName={localappdata}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=no
LicenseFile={#PayloadDir}\LICENSE
OutputDir={#OutputDir}
OutputBaseFilename=YBM-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Per-user install: no elevation prompt, no admin account needed.
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#MyAppName} {#MyAppVersion}
UninstallDisplayIcon={app}\YBM.bat
SetupIconFile=..\..\scripts\assets\logo.ico

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"

[Files]
; The staged payload, whole. Excludes are a safety net only:
; package_release.ps1 already leaves per-machine state behind, and neither a
; venv nor a config.yaml from a build machine should ever reach a user.
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
  Excludes: "*.pyc,__pycache__,.venv,node_modules,.agent_control,config.yaml,.env"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppLauncher}"; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppLauncher}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; First launch installs uv, creates the venv, and opens the console. It is a
; long first run, so it is offered rather than forced, and it is never silent.
Filename: "{app}\{#MyAppLauncher}"; Description: "Start {#MyAppName} now"; \
  WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; Created after install, so Inno does not track them and would otherwise leave
; the folder behind. The user's own config and data are deliberately NOT listed:
; removing the program should not silently destroy their tasks and settings.
Type: filesandordirs; Name: "{app}\backend\.venv"
Type: filesandordirs; Name: "{app}\backend\src\agent_control\__pycache__"
Type: filesandordirs; Name: "{app}\whatsapp-bridge\node_modules"

[Messages]
; The default text talks about "Setup"; this is the one screen a user reads.
WelcomeLabel2=This will install [name/ver] on your computer.%n%nYBM runs entirely on this machine. Nothing is sent anywhere until you choose a model, and high-impact capabilities stay off until you turn them on.

[Code]
// Warn before installing over a copy that is currently running. Overwriting
// files under a live backend produces a half-updated tree and a confusing
// crash later, which is much harder to diagnose than this prompt.
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  if Exec('cmd.exe', '/c tasklist /FI "IMAGENAME eq python.exe" | find /I "python.exe"',
          '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
  begin
    if ResultCode = 0 then
      Result := MsgBox('Python is running on this machine. If that is YBM, close it first ' +
                       '(right-click the tray icon, or run "ybm stop") so its files can be replaced.' + #13#10#13#10 +
                       'Continue anyway?', mbConfirmation, MB_YESNO) = IDYES;
  end;
end;
