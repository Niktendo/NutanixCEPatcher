@echo off
cd /d "%~dp0" && ( if exist "%temp%\getadmin.vbs" del "%temp%\getadmin.vbs" ) && fsutil dirty query %systemdrive% 1>nul 2>nul || (  echo Set UAC = CreateObject^("Shell.Application"^) : UAC.ShellExecute "wt", "cmd.exe /k cd ""%~sdp0"" && %~s0 %params%", "", "runas", 1 >> "%temp%\getadmin.vbs" && "%temp%\getadmin.vbs" && exit /B )
setlocal EnableExtensions EnableDelayedExpansion

rem TBD: - Implement ISO mount, check for lager than 32GB before formatting
rem      - Upload to GH w/ instructions for compatibility

title Nutanix CE flash and patch tool
echo Welcome to the Nutanix CE flash and patch tool
echo.
timeout /t 3 /nobreak >nul

rem cls
set bulkpart=%TMP%\diskpart-%RANDOM%.txt
set excludelist=%TMP%\exclude-%RANDOM%.txt
set /p isodriveletter=Please enter the drive letter of the Nutanix image: 
set "isodriveletter=%isodriveletter%:"
if not exist "%isodriveletter%\squashfs.img" (
	echo Can't find Nutanix Installation files in the specified drive letter...
	echo.
	echo Please enter the correct drive letter...
	goto :eof
)

set /p createiso=Do you want to create an ISO [Y/N]: 
if %createiso%==Y (
	set driveletter=%TMP%\nutanix
	echo.
	goto :Copy
)

set /p driveletter=Please enter the drive letter of the USB drive: 
set "driveletter=%driveletter%:"
echo.
echo "%driveletter%" selected as target.
echo.

echo WARING: ALL DATA ON THIS DRIVE WILL BE ERASED PERMANENTLY!
echo.
pause

echo select volume %driveletter% > %bulkpart%
echo clean >> %bulkpart%
echo clean >> %bulkpart%
echo convert gpt >> %bulkpart%
echo create partition primary >> %bulkpart%
echo select partition 1 >> %bulkpart%
echo format fs=fat32 label=PHOENIX quick >> %bulkpart%
echo assign letter=%driveletter% >> %bulkpart%
diskpart /s %bulkpart%
echo.
echo USB drive "%driveletter%" has been formatted with GPT and FAT32.
echo.
timeout /t 2 /nobreak >nul

:Copy
rem cls
echo Copying image...
echo \images\svm >> %excludelist%
xcopy /E /I /H /R /Y /J "%isodriveletter%\*" %driveletter% /exclude:%excludelist%
echo Copy completed.
echo.
timeout /t 2 /nobreak >nul

rem cls
echo Splitting AOS image...
powershell.exe -ExecutionPolicy Bypass -NoProfile -Command "& { $path = Get-ChildItem '%isodriveletter%\images\svm\nutanix_installer_package*'; $chunkSize = 2147483000; $reader = [System.IO.File]::OpenRead($path); $count = 0; $buffer = New-Object Byte[] $chunkSize; $hasMore = $true; New-Item -Path '%driveletter%\images\svm\' -ItemType Directory -Force | Out-Null; while($hasMore) { $bytesRead = $reader.Read($buffer, 0, $buffer.Length); if ($bytesRead -eq 0) { break; }; $chunkFileName = '%driveletter%\images\svm\nutanix_installer_package.tar.p{0:D2}'; $chunkFileName = $chunkFileName -f $count; $output = $buffer; if ($bytesRead -ne $buffer.Length) { $hasMore = $false; $output = New-Object Byte[] $bytesRead; [System.Array]::Copy($buffer, $output, $bytesRead); }; [System.IO.File]::WriteAllBytes($chunkFileName, $output); Write-Host ('Chunk created: ' + $chunkFileName); ++$count; }; $reader.Close(); }"
echo Splitting completed.
echo.
timeout /t 2 /nobreak >nul

rem cls
echo Patching files...
copy /y "%~dp0grub.cfg" "%driveletter%\EFI\BOOT\grub.cfg"
copy /y "%~dp0isolinux.cfg" "%driveletter%\boot\isolinux\isolinux.cfg"
copy /y "%~dp0install.sh" "%driveletter%\"
copy /y "%~dp0gui.py" "%driveletter%\"
echo Patching completed.
echo.
timeout /t 2 /nobreak >nul

if %createiso%==Y (
	echo Creating ISO file...
	%~dp0oscdimg.exe -m -o -u2 -udfver102 %driveletter% %~dp0nutanix.iso
)

rem cls
echo Creation completed.
echo After first boot press Ctrl + C to exit Installer UI and type /mnt/iso/install.sh to launch the advanced CE Installer UI.
pause
goto :eof