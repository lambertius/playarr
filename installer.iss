; Playarr Installer — Inno Setup Script
; Builds a standard Windows installer from the PyInstaller dist output.
;
; Prerequisites:
;   1. Run `python build_installer.py` first to create dist\Playarr\
;   2. Run `python -c "..."` icon generation (or build_installer.py does it)
;   3. Compile with: "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
;
; Output: Output\PlayarrSetup.exe

#define MyAppName "Playarr"
#define MyAppVersion "1.9.11"
#define MyAppPublisher "Playarr Contributors"
#define MyAppURL "https://github.com/lambertius/playarr"
#define MyAppExeName "Playarr.exe"

[Setup]
AppId={{E7A3F1B2-4C5D-4E6F-8A9B-0C1D2E3F4A5B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=LICENSE
OutputDir=Output
OutputBaseFilename=PlayarrSetup
SetupIconFile=playarr.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
PrivilegesRequired=admin
CloseApplications=yes
UsePreviousAppDir=yes
UsePreviousGroup=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupentry"; Description: "Start Playarr when Windows starts"; GroupDescription: "Startup:"

[InstallDelete]
; Clean stale files from previous version before installing new ones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Main executable
Source: "dist\Playarr\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

; All supporting files and folders from the PyInstaller output
Source: "dist\Playarr\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

; Icon file
Source: "playarr.ico"; DestDir: "{app}"; Flags: ignoreversion

; Bundled ffmpeg + ffprobe
Source: "tools\ffmpeg.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "tools\ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
; Start Menu
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\playarr.ico"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"

; Desktop (optional)
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\playarr.ico"; Tasks: desktopicon

; Windows Startup (optional)
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--delay 10 --headless"; Tasks: startupentry

[Run]
; Launch after install
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Clean up any runtime files created in the install directory
Type: filesandordirs; Name: "{app}\__pycache__"
Type: files; Name: "{app}\playarr.ico"

[Code]
// Kill running Playarr before install/uninstall
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM Playarr.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

function InitializeUninstall(): Boolean;
var
  ResultCode: Integer;
begin
  Exec('taskkill', '/F /IM Playarr.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Result := True;
end;

// Check for ffmpeg after install (should always pass now that we bundle it)
procedure CurStepChanged(CurStep: TSetupStep);
var
  FFmpegPath: String;
begin
  if CurStep = ssPostInstall then
  begin
    FFmpegPath := ExpandConstant('{app}\ffmpeg.exe');
    if not FileExists(FFmpegPath) then
    begin
      MsgBox('Warning: ffmpeg.exe was not found in the install directory.' + #13#10 + #13#10 +
             'Playarr requires ffmpeg to process videos.' + #13#10 +
             'Please install ffmpeg and add it to your system PATH:' + #13#10 +
             'https://ffmpeg.org/download.html',
             mbInformation, MB_OK);
    end;
  end;
end;
