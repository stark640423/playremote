; PlayRemote Windows 安裝程式 (Inno Setup 6)
; 編譯: iscc installer\playremote.iss   (先用 PyInstaller 產出 dist\PlayRemote\)
; 要簽章時: iscc /DSIGN "/Ssign=signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a $f" installer\playremote.iss

#define AppName "PlayRemote"
#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif
#define AppPublisher "stark640423"
#define AppURL "https://github.com/stark640423/playremote"
#define AppExe "PlayRemote.exe"

[Setup]
AppId={{8A0E2CA8-9359-4014-9B7D-DD76D5A8308D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 裝到使用者資料夾，不需要系統管理員、不跳 UAC
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=PlayRemote-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName={#AppName}
CloseApplications=yes
RestartApplications=no
#ifdef SIGN
SignTool=sign
#endif

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startup"; Description: "開機時自動啟動 {#AppName}"; GroupDescription: "其他:"
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "其他:"; Flags: unchecked

[Files]
Source: "..\dist\PlayRemote\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; 第一次安裝才複製設定檔，更新時保留使用者的設定
Source: "..\config.example.toml"; DestDir: "{app}"; DestName: "config.toml"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\config.example.toml"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\編輯設定 (config.toml)"; Filename: "notepad.exe"; Parameters: """{app}\config.toml"""
Name: "{group}\解除安裝 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "{#AppName}"; \
  ValueData: """{app}\{#AppExe}"""; Flags: uninsdeletevalue; Tasks: startup

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即啟動 {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\captures"
