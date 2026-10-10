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
;
; Automatic updates (opt-in): the unchecked "autoupdate" task asks for a GitHub token and passes -AutoUpdate to
; install.ps1. Silent: also set UPDATE_GITHUB_TOKEN and add /MERGETASKS="autoupdate". Leaving the box unchecked
; keeps the current choice; turn it off with install.cmd -DisableAutoUpdate.

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
; Unchecked tasks mean "keep the current choice", so never preselect them from an earlier run.
UsePreviousTasks=no
CloseApplications=no
UninstallDisplayName={#AppName}

[Messages]
WelcomeLabel2=This will install [name/ver] on your computer.%n%nThe bot runs in the background as a Windows service account, starts with Windows and posts an alert in your Discord server when the Twitch channels you track go live.%n%nPython is installed automatically if it is missing. Have your Discord bot token and Twitch application Client ID/secret ready.

[Tasks]
Name: "autoupdate"; Description: "Install new releases &automatically (daily; needs a read-only GitHub token)"; Flags: unchecked

[Files]
Source: "..\..\twitchbot\*.py"; DestDir: "{app}\twitchbot"; Flags: ignoreversion
Source: "..\..\Main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\start-bot.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "install.cmd"; DestDir: "{app}"; Flags: ignoreversion
Source: "auto-update.ps1"; DestDir: "{app}"; Flags: ignoreversion
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
Type: filesandordirs; Name: "{app}\twitchbot"
; Automatic updates: settings and the GitHub token (always removed, the token is a secret).
Type: filesandordirs; Name: "{app}\updater"
Type: files; Name: "{app}\VERSION"
Type: files; Name: "{app}\CHANGES.md"

[Code]
var
  CredPage: TInputQueryWizardPage;
  TokenPage: TInputQueryWizardPage;
  HadConfig: Boolean;
  HadToken: Boolean;
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

function TokenFile: String;
begin
  Result := ExpandConstant('{commonappdata}\{#AppName}\updater\github-token');
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

{ UPDATE_NOTIFY from an existing .env (python-dotenv rules, simplified); admins, the bot's default, otherwise. }
function ConfiguredUpdateNotify: String;
var
  Lines: TArrayOfString;
  I, P: Integer;
  Line, Key: String;
begin
  Result := 'admins';
  if not LoadStringsFromFile(ConfigFile, Lines) then
    exit;
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Trim(Lines[I]);
    P := Pos('=', Line);
    Key := '';
    if P > 0 then
      Key := Trim(Copy(Line, 1, P - 1));
    if Pos('export ', Lowercase(Key)) = 1 then
      Key := Trim(Copy(Key, 8, Length(Key)));
    if CompareText(Key, 'UPDATE_NOTIFY') = 0 then
    begin
      Line := Trim(Copy(Line, P + 1, Length(Line)));
      P := Pos('#', Line);
      if P > 0 then
        Line := Trim(Copy(Line, 1, P - 1));
      StringChangeEx(Line, '"', '', True);
      StringChangeEx(Line, '''', '', True);
      if Line <> '' then
        Result := Lowercase(Line);
    end;
  end;
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
    'Recommended: on the same Bot page turn on "Server Members Intent" (Privileged Gateway Intents) so update' + #13#10 +
    'notices reach server admins too; without it they go to server owners only.' + #13#10 +
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

  HadToken := FileExists(TokenFile);
  Intro := 'The repository is private, so downloading new releases needs a GitHub token. Create one at' + #13#10 +
    'https://github.com/settings/personal-access-tokens: Fine-grained token -> Resource owner dxcufgb ->' + #13#10 +
    'Only select repositories: TwitchDiscordBot -> Repository permissions: Contents = Read-only.' + #13#10#13#10 +
    'It is stored readable by Administrators and SYSTEM only, separate from the bot''s settings.';
  if HadToken then
    Intro := Intro + #13#10#13#10 + 'A token is already stored. Leave the field empty to keep it.';
  TokenPage := CreateInputQueryPage(wpSelectTasks, 'Automatic updates',
    'A daily task installs new releases of the bot.', Intro);
  TokenPage.Add('GitHub &token:', True);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (TokenPage <> nil) and (PageID = TokenPage.ID) and not WizardIsTaskSelected('autoupdate');
end;

function ValidToken(const Token: String): Boolean;
var
  I: Integer;
begin
  Result := Token <> '';
  for I := 1 to Length(Token) do
    if not (((Token[I] >= 'A') and (Token[I] <= 'Z')) or ((Token[I] >= 'a') and (Token[I] <= 'z')) or
      ((Token[I] >= '0') and (Token[I] <= '9')) or (Token[I] = '_')) then
      Result := False;
end;

{ Code: 0 = accepted, 1 = rejected or not checked, 2 = accepted with a warning (see check-credentials.ps1). }
function CheckCredentials(const Token, ClientId, Secret: String; var Report: String; var Code: Integer): Boolean;
var
  OutFile: String;
begin
  ExtractTemporaryFile('check-credentials.ps1');
  OutFile := ExpandConstant('{tmp}\check-result.txt');
  DeleteFile(OutFile);
  { Pass secrets through the environment, never on a command line. }
  SetEnvironmentVariable('TDB_DISCORD_TOKEN', Token);
  SetEnvironmentVariable('TDB_TWITCH_ID', ClientId);
  SetEnvironmentVariable('TDB_TWITCH_SECRET', Secret);
  { Only UPDATE_NOTIFY=admins (the default) asks Discord for the Server Members Intent. }
  SetEnvironmentVariable('TDB_UPDATE_NOTIFY', ConfiguredUpdateNotify);
  try
    if not Exec(PowerShellExe, '-NoProfile -ExecutionPolicy Bypass -File "' +
      ExpandConstant('{tmp}\check-credentials.ps1') + '" -OutFile "' + OutFile + '"',
      '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Code := 1;
    Result := (Code = 0) or (Code = 2);
  finally
    UnsetEnvironmentVariable('TDB_DISCORD_TOKEN', 0);
    UnsetEnvironmentVariable('TDB_TWITCH_ID', 0);
    UnsetEnvironmentVariable('TDB_TWITCH_SECRET', 0);
    UnsetEnvironmentVariable('TDB_UPDATE_NOTIFY', 0);
  end;
  Report := ReadLines(OutFile, 10);
  if Report = '' then
    Report := 'The check could not run.' + #13#10;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Token, ClientId, Secret, Owner, Report: String;
  I, Code: Integer;
begin
  Result := True;
  if WizardSilent then
    exit;
  if (TokenPage <> nil) and (CurPageID = TokenPage.ID) then
  begin
    Token := Trim(TokenPage.Values[0]);
    if (Token = '') and HadToken then
      exit;
    if not ValidToken(Token) then
    begin
      MsgBox('Please enter a GitHub token (letters, digits and _ only), or go back and clear the automatic updates box.',
        mbError, MB_OK);
      Result := False;
    end;
    exit;
  end;
  if (CredPage = nil) or (CurPageID <> CredPage.ID) then
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
    if not CheckCredentials(Token, ClientId, Secret, Report, Code) then
      Result := MsgBox('The credentials could not be verified:' + #13#10#13#10 + Report + #13#10 +
        'Continue anyway?', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES
    else if Code = 2 then
      MsgBox('The credentials work. Note:' + #13#10#13#10 + Report, mbInformation, MB_OK);
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
  Script, Extra: String;
  Code: Integer;
  TokenSet: Boolean;
begin
  Script := ExpandConstant('{app}\install.ps1');
  SaveStringToFile(ExpandConstant('{app}\VERSION'), '{#AppVersion}' + #13#10, False);
  WizardForm.StatusLabel.Caption := 'Setting up Python and the bot service. This can take a few minutes...';
  WizardForm.ProgressGauge.Style := npbstMarquee;
  { In silent mode the credentials come from the caller's environment instead (install.ps1 -Yes reads them). }
  if CredsEntered then
    SetCredentialEnvironment(True);
  { Unchecked keeps the current choice. The token goes through the environment, never the command line;
    in silent mode it comes from the caller's UPDATE_GITHUB_TOKEN. }
  Extra := '';
  TokenSet := False;
  if WizardIsTaskSelected('autoupdate') then
  begin
    Extra := ' -AutoUpdate';
    if (TokenPage <> nil) and (Trim(TokenPage.Values[0]) <> '') then
    begin
      SetEnvironmentVariable('UPDATE_GITHUB_TOKEN', Trim(TokenPage.Values[0]));
      TokenSet := True;
    end;
  end;
  try
    if CredsEntered and HadConfig then
    begin
      { Upgrade the installation, then replace the existing credentials. }
      RunInstallScript(Script, '-Yes -SkipCheck -NoService' + Extra, Code);
      if Code = 0 then
        RunInstallScript(Script, '-Reconfigure -Yes -SkipCheck', Code);
    end else
      RunInstallScript(Script, '-Yes -SkipCheck' + Extra, Code);
  finally
    if CredsEntered then
      SetCredentialEnvironment(False);
    if TokenSet then
      UnsetEnvironmentVariable('UPDATE_GITHUB_TOKEN', 0);
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
