@echo off
rem Removes the TwitchDiscordBot task and program files. Settings and data are kept unless you add -Purge.
call "%~dp0install.cmd" -Uninstall %*
