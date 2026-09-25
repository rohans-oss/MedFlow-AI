@echo off
REM MedFlow AI - start the full stack on Windows (needs Docker Desktop running)
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo.
  echo Docker Desktop is not running. Start Docker Desktop, wait until it says "Engine running", then run this again.
  echo Download: https://www.docker.com/products/docker-desktop/
  pause
  exit /b 1
)
if not exist .env copy .env.example .env >nul
echo Building and starting MedFlow AI. First run takes a few minutes...
docker compose up --build -d
if errorlevel 1 (
  echo Something failed. Run "docker compose logs" to see why.
  pause
  exit /b 1
)
echo Waiting for the app to be ready...
:wait
timeout /t 5 /nobreak >nul
curl -s -o nul http://localhost:3000/login
if errorlevel 1 goto wait
echo.
echo MedFlow AI is running:  http://localhost:3000
echo Login: admin@sunrise.demo   Password: Demo@1234
echo Stop it with:  docker compose down
start http://localhost:3000
pause
