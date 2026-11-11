@echo off
rem Allow this PC to accept phone connections on the audiobook port.
rem Right-click this file and pick "Run as administrator".
netsh advfirewall firewall add rule name="Audiobook TTS 8800" dir=in action=allow protocol=TCP localport=8800
pause
