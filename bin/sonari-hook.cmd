@echo off
rem Windows launcher for the Sonari plugin hook.
rem
rem Lets hooks/hooks.json's "${CLAUDE_PLUGIN_ROOT}/bin/sonari-hook <Event>" resolve
rem to this .cmd on Windows (via PATHEXT) instead of the extensionless shebang
rem script, which cmd/PowerShell cannot execute directly. Runs the Python hook
rem entrypoint windowless (pythonw = no console flash on every hook). The hook
rem payload arrives on stdin; sonari-hook self-bootstraps sys.path, so no
rem PYTHONPATH is needed. A hook must never fail loudly, so swallow errors.
pythonw "%~dp0sonari-hook" %* 2>nul
exit /b 0
