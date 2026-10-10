@echo off
setlocal
cd /d "%~dp0"
set LOG=build_log.txt

echo Inicio: %date% %time% > %LOG%
set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY where python >nul 2>&1 && set PY=python
if not defined PY goto semPython
%PY% --version >nul 2>&1 || goto semPython

echo Python encontrado:
%PY% --version
%PY% --version >> %LOG% 2>&1

echo [1/4] Criando ambiente...
%PY% -m venv --clear .venv >> %LOG% 2>&1 || goto erro
call .venv\Scripts\activate.bat
python -c "import struct; raise SystemExit(struct.calcsize('P') != 8)" >> %LOG% 2>&1 || goto arquitetura
echo [2/4] Instalando bibliotecas...
python -m pip install --upgrade pip >> %LOG% 2>&1
python -m pip install -r requirements_app.txt >> %LOG% 2>&1 || goto erro
python -c "import fastapi, pydantic_core._pydantic_core, uvicorn, webview" >> %LOG% 2>&1 || goto erro
echo [3/4] Baixando os graficos para uso offline...
python -c "import urllib.request as u; u.urlretrieve('https://cdn.plot.ly/plotly-2.35.2.min.js','static/plotly.min.js')" >> %LOG% 2>&1 || goto erro
echo [4/4] Gerando o executavel unico (uma unica instancia/arquivo)...
pyinstaller --noconfirm --clean UnimedVitoriaTreinamentos.spec >> %LOG% 2>&1 || goto erro
echo Preparando pacote para enviar...
powershell -NoProfile -Command "$package='dist\Pacote Unimed Vitoria Windows x64'; if (Test-Path -LiteralPath $package) { Remove-Item -LiteralPath $package -Recurse -Force }; New-Item -ItemType Directory -Path $package | Out-Null; Copy-Item -LiteralPath 'dist\UnimedVitoriaTreinamentos.exe' -Destination $package; Copy-Item -LiteralPath 'LEIA-ME.txt' -Destination $package; Compress-Archive -Force -Path ($package + '\*') -DestinationPath 'dist\UnimedVitoriaTreinamentos-Windows-x64.zip'" >> %LOG% 2>&1 || goto erro

echo.
echo PRONTO!
echo Executavel unico: dist\UnimedVitoriaTreinamentos.exe
echo Pacote para enviar: dist\UnimedVitoriaTreinamentos-Windows-x64.zip
goto fim

:arquitetura
echo.
echo O build precisa de Python 64 bits para criar o pacote Windows x64.
goto fim

:semPython
echo.
echo PYTHON NAO ENCONTRADO.
echo Instale Python 3.12+ de 64 bits e marque "Add python.exe to PATH".
goto fim

:erro
echo.
echo ERRO. Os detalhes foram gravados em build_log.txt nesta pasta.

:fim
echo.
pause
