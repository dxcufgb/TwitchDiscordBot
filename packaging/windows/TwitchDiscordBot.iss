; TwitchDiscordBot setup wizard (Inno Setup 6).
;
; Build (Windows, Inno Setup 6 installed):
;   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" /DAppVersion=2.0.1 packaging\windows\TwitchDiscordBot.iss
; Output: dist\twitchdiscordbot-<version>-windows-setup.exe
;
; The wizard asks for the credentials and checks them. install.ps1 then does the actual work
; (Python, virtual environment, permissions, scheduled task), exactly as for the zip release.
;
; Silent install: set DISCORD_TOKEN, TWITCH_APP_ID, TWITCH_APP_SECRET (and optionally OWNER_ID) in
; the environment and run:  setup.exe /VERYSILENT /SUPPRESSMSGBOXES
; Silent uninstall: unins000.exe /VERYSILENT [/PURGE]   (/PURGE also deletes settings and data)

#ifndef AppVersion
  #error Pass the version, e.g. ISCC /DAppVersion=2.0.1 TwitchDiscordBot.iss
#endif
#define AppName "TwitchDiscordBot"

[Setup]
AppId={{B7E3F0A4-5C2D-4E8B-9F1A-6D3C2B7A8E51}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=dxcufgb
AppPublisherURL=https://github.com/dxcufgb/TwitchDiscordBot
AppSupportURL=https://github.com/dxcufgb/TwitchDiscordBot/issues
AppUpdatesURL=https://github.com/dxcufgb/TwitchDiscordBot/releases
AppComments=Posts Discord alerts when Twitch channels go live.
; Same location as the zip installer, so either can upgrade the other.
DefaultDirName={commonappdata}\{#AppName}
UsePreviousAppDir=no
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\..\dist
OutputBaseFilename=twitchdiscordbot-{#AppVersion}-windows-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
CloseApplications=no
UninstallDisplayName={#AppName}

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nThe bot runs in the background as a Windows service account, starts with Windows and posts an alert in your Discord server when the Twitch channels you track go live.%n%nPython is installed automatically if it is missing. Have your Discord bot token and Twitch application Client ID/secret ready.

[Files]
Source: "..\..\twitchbot\*.py"; DestDir: "{app}\twitchbot"; Flags: ignoreversion
Source: "..\..\Main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\start-bot.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "check-credentials.ps1"; Flags: dontcopy

[Dirs]
; Settings, data and logs survive a normal uninstall (unless the user chooses to delete them).
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\logs"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\logs"; Description: "Open the logs folder"; Flags: postinstall shellexec skipifsilent nowait unchecked
Filename: "{win}\notepad.exe"; Parameters: """{app}\README.md"""; Description: "Show the README"; Flags: postinstall skipifsilent nowait unchecked

[UninstallRun]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -InstallDir ""{app}"" -RemoveService"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\.venv"
Type: filesandordirs; Name: "{app}\twitchbot\__pycache__"
Type: files; Name: "{app}\VERSION"

[Code]
var
  CredPage: TInputQueryWizardPage;
  HadConfig: Boolean;
  CredsEntered: Boolean;
  PurgeOnUninstall: Boolean;

function SetEnvironmentVariable(lpName: String; lpValue: String): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';
function UnsetEnvironmentVariable(lpName: String; lpValue: Cardinal): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

function PowerShellExe: String;
begin
  Result := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
end;

function ConfigFile: String;
begin
  Result := ExpandConstant('{commonappdata}\{#AppName}\.env');
end;

function HasParam(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), Name) = 0 then
      Result := True;
end;

function ReadLines(const FileName: String; MaxLines: Integer): String;
var
  Lines: TArrayOfString;
  I, First: Integer;
begin
  Result := '';
  if not LoadStringsFromFile(FileName, Lines) then
    exit;
  First := GetArrayLength(Lines) - MaxLines;
  if First < 0 then
    First := 0;
  for I := First to GetArrayLength(Lines) - 1 do
    Result := Result + Lines[I] + #13#10;
end;

{ ---------------------------------------------------------------- wizard }

procedure InitializeWizard;
var
  Intro: String;
begin
  HadConfig := FileExists(ConfigFile);
  Intro := 'Discord bot token: https://discord.com/developers/applications -> your app -> Bot -> Reset Token.' + #13#10 +
    'Twitch Client ID and secret: https://dev.twitch.tv/console/apps -> your app -> Manage.' + #13#10#13#10 +
    'The values are checked with Discord and Twitch when you click Next.';
  if HadConfig then
    Intro := Intro + #13#10#13#10 + 'The bot is already configured on this computer. Leave all fields empty to keep the current credentials.';
  CredPage := CreateInputQueryPage(wpWelcome, 'Discord and Twitch credentials',
    'The bot needs a Discord bot token and a Twitch application.', Intro);
  CredPage.Add('&Discord bot token:', True);
  CredPage.Add('Twitch &Client ID:', False);
  CredPage.Add('Twitch Client &secret:', True);
  CredPage.Add('Your Discord &user ID, allows /restart (optional):', False);
end;

function CheckCredentials(const Token, ClientId, Secret: String; var Report: String): Boolean;
var
  OutFile: String;
  Code: Integer;
begin
  ExtractTemporaryFile('check-credentials.ps1');
  OutFile := ExpandConstant('{tmp}\check-result.txt');
  DeleteFile(OutFile);
  { Pass secrets through the environment, never on a command line. }
  SetEnvironmentVariable('TDB_DISCORD_TOKEN', Token);
  SetEnvironmentVariable('TDB_TWITCH_ID', ClientId);
  SetEnvironmentVariable('TDB_TWITCH_SECRET', Secret);
  try
    Result := Exec(PowerShellExe, '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{tmp}\check-credentials.ps1') + '" -OutFile "' + OutFile + '"',
      '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  finally
    UnsetEnvironmentVariable('TDB_DISCORD_TOKEN', 0);
    UnsetEnvironmentVariable('TDB_TWITCH_ID', 0);
    UnsetEnvironmentVariable('TDB_TWITCH_SECRET', 0);
  end;
  Report := ReadLines(OutFile, 10);
  if Report = '' then
    Report := 'The check could not run.' + #13#10;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Token, ClientId, Secret, Owner, Report: String;
  I: Integer;
begin
  Result := True;
  if WizardSilent or (CredPage = nil) or (CurPageID <> CredPage.ID) then
    exit;

  Token := Trim(CredPage.Values[0]);
  ClientId := Trim(CredPage.Values[1]);
  Secret := Trim(CredPage.Values[2]);
  Owner := Trim(CredPage.Values[3]);
  CredsEntered := (Token <> '') or (ClientId <> '') or (Secret <> '') or (Owner <> '');
  if HadConfig and not CredsEntered then
    exit;

  if (Token = '') or (ClientId = '') or (Secret = '') then
  begin
    MsgBox('Please enter the Discord bot token, the Twitch Client ID and the Twitch Client secret.', mbError, MB_OK);
    Result := False;
    exit;
  end;
  for I := 1 to Length(Owner) do
    if (Owner[I] < '0') or (Owner[I] > '9') then
    begin
      MsgBox('The Discord user ID may only contain digits.' + #13#10 +
        '(In Discord, turn on Developer Mode, then right-click your name -> Copy User ID.)', mbError, MB_OK);
      Result := False;
      exit;
    end;

  WizardForm.NextButton.Enabled := False;
  try
    if not CheckCredentials(Token, ClientId, Secret, Report) then
      Result := MsgBox('The credentials could not be verified:' + #13#10#13#10 + Report + #13#10 +
        'Continue anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
  finally
    WizardForm.NextButton.Enabled := True;
  end;
end;

{ ---------------------------------------------------------------- install }

function RunInstallScript(const Script, Args: String; var Code: Integer): Boolean;
var
  App, LogFile: String;
begin
  App := ExpandConstant('{app}');
  LogFile := App + '\logs\setup.log';
  { All output (including Write-Host) goes to logs\setup.log; errors thrown by the script count as failures. }
  Result := Exec(PowerShellExe, '-NoProfile -ExecutionPolicy Bypass -Command "try { & ''' + Script +
    ''' -InstallDir ''' + App + ''' ' + Args + ' *>&1 | Out-File -Append -Encoding utf8 -FilePath ''' + LogFile +
    '''; exit $LASTEXITCODE } catch { $_ | Out-File -Append -Encoding utf8 -FilePath ''' + LogFile + '''; exit 1 }"',
    App, SW_HIDE, ewWaitUntilTerminated, Code);
  if not Result then
    Code := -1;
end;

procedure StopRunningBot;
var
  Code: Integer;
begin
  { Use the new install.ps1 from the setup itself: an older installed copy may not know -StopService.
    Only stop the bot here; install.ps1 re-registers the task (and can still find an older install through it). }
  ExtractTemporaryFile('install.ps1');
  Exec(PowerShellExe, '-NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\install.ps1') +
    '" -InstallDir "' + ExpandConstant('{app}') + '" -StopService', '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure SetCredentialEnvironment(Enable: Boolean);
begin
  if Enable then
  begin
    SetEnvironmentVariable('DISCORD_TOKEN', Trim(CredPage.Values[0]));
    SetEnvironmentVariable('TWITCH_APP_ID', Trim(CredPage.Values[1]));
    SetEnvironmentVariable('TWITCH_APP_SECRET', Trim(CredPage.Values[2]));
    if Trim(CredPage.Values[3]) <> '' then
      SetEnvironmentVariable('OWNER_ID', Trim(CredPage.Values[3]));
  end else
  begin
    UnsetEnvironmentVariable('DISCORD_TOKEN', 0);
    UnsetEnvironmentVariable('TWITCH_APP_ID', 0);
    UnsetEnvironmentVariable('TWITCH_APP_SECRET', 0);
    UnsetEnvironmentVariable('OWNER_ID', 0);
  end;
end;

procedure ConfigureBot;
var
  Script: String;
  Code: Integer;
begin
  Script := ExpandConstant('{app}\install.ps1');
  SaveStringToFile(ExpandConstant('{app}\VERSION'), '{#AppVersion}' + #13#10, False);
  WizardForm.StatusLabel.Caption := 'Setting up Python and the bot service. This can take a few minutes...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  { In silent mode the credentials come from the caller's environment instead (install.ps1 -Yes reads them). }
  if CredsEntered then
    SetCredentialEnvironment(True);
  try
    if CredsEntered and HadConfig then
    begin
      { Upgrade the installation, then replace the existing credentials. }
      RunInstallScript(Script, '-Yes -SkipCheck -NoService', Code);
      if Code = 0 then
        RunInstallScript(Script, '-Reconfigure -Yes -SkipCheck', Code);
    end else
      RunInstallScript(Script, '-Yes -SkipCheck', Code);
  finally
    if CredsEntered then
      SetCredentialEnvironment(False);
    WizardForm.ProgressGauge.Style := npbstNormal;
  end;
  Log('install.ps1 exit code: ' + IntToStr(Code));
  { SuppressibleMsgBox: a plain MsgBox would block a /VERYSILENT /SUPPRESSMSGBOXES install forever. }
  if Code <> 0 then
    SuppressibleMsgBox('TwitchDiscordBot was installed, but the bot did not start correctly. Last lines of the setup log:' + #13#10#13#10 +
      ReadLines(ExpandConstant('{app}\logs\setup.log'), 12) + #13#10 +
      'Full log: ' + ExpandConstant('{app}\logs\setup.log') + #13#10 +
      'You can run this setup again to enter other credentials.', mbError, MB_OK, IDOK);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssInstall then
    StopRunningBot
  else if CurStep = ssPostInstall then
    ConfigureBot;
end;

{ ---------------------------------------------------------------- uninstall }

function InitializeUninstall: Boolean;
begin
  Result := True;
  if UninstallSilent then
    PurgeOnUninstall := HasParam('/PURGE')
  else
    PurgeOnUninstall := MsgBox('Also delete the bot''s settings (including its tokens), data and logs?' + #13#10#13#10 +
      'Choose No to keep them for a later reinstall.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and PurgeOnUninstall then
    DelTree(ExpandConstant('{app}'), True, True, True);
end;
