# =============================================================================
#  instalar_witral.ps1 - instalacion de Witral en Windows, de punta a punta.
#
#  Deja el repo con historial git, el entorno de Python armado y el servidor
#  registrado en Claude Desktop. Es IDEMPOTENTE: se puede correr las veces que
#  haga falta sin romper nada.
#
#  Uso tipico (desde la raiz del repo):
#      powershell -ExecutionPolicy Bypass -File .\instalar_witral.ps1
#
#  Con parametros:
#      .\instalar_witral.ps1 -Raiz 'D:\Proyectos' -Nombre 'Fulano' -Email 'f@x.cl'
#
#  Parametros:
#    -Repo    raiz del repo Witral.        Por defecto: la carpeta del script.
#    -Raiz    raiz autorizada del lugar 'local' (donde viven tus proyectos).
#             Por defecto: la carpeta que contiene al repo.
#    -Url     remoto git del proyecto.
#    -Nombre  nombre para los commits (opcional).
#    -Email   email para los commits (opcional).
#
#  Nota de estilo: este archivo es ASCII puro, con fin de linea CRLF y SIN
#  here-strings (@'...'@). Windows PowerShell 5.1 no reconoce los here-strings
#  en archivos con fin de linea LF, y falla con un error de parseo desconcertante.
# =============================================================================

[CmdletBinding()]
param(
  [string]$Repo   = $PSScriptRoot,
  [string]$Raiz   = '',
  [string]$Url    = 'https://github.com/rapiman/Witral.git',
  [string]$Nombre = '',
  [string]$Email  = ''
)

$ErrorActionPreference = 'Stop'
if (-not $Repo) { $Repo = (Get-Location).Path }
$Repo   = (Resolve-Path $Repo).Path
if (-not $Raiz) { $Raiz = Split-Path $Repo -Parent }
# Si el repo esta en la raiz de un disco, Split-Path devuelve vacio.
if (-not $Raiz) { $Raiz = $Repo }
$Server = Join-Path $Repo 'server'
$Config = Join-Path $Server 'lugares.json'

function Paso($t) { Write-Host ''; Write-Host "=== $t ===" -ForegroundColor Cyan }
function Ok($t)   { Write-Host "  OK  $t" -ForegroundColor Green }
function Aviso($t){ Write-Host "  !!  $t" -ForegroundColor Yellow }

# Busca un Python REAL. Descarta el alias del Microsoft Store: ese stub que vive
# en WindowsApps, no es Python, y escupe "no se encontro Python" por stderr.
function Buscar-Python {
  $guardar = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    foreach ($nombre in @('py', 'python', 'python3')) {
      $c = Get-Command $nombre -ErrorAction SilentlyContinue
      if (-not $c) { continue }
      $src = $c.Source
      if (-not $src) { continue }
      if ($src -like '*\WindowsApps\*') { continue }
      try { $v = & $src --version 2>&1 | Out-String } catch { continue }
      if ($v -match 'Python 3') { return @{ Cmd = $src; Ver = $v.Trim() } }
    }
  } finally { $ErrorActionPreference = $guardar }
  return $null
}

# Relee el PATH del registro (Maquina + Usuario) para ver lo recien instalado
# sin tener que abrir una consola nueva.
function Refrescar-Path {
  $m = [Environment]::GetEnvironmentVariable('Path', 'Machine')
  $u = [Environment]::GetEnvironmentVariable('Path', 'User')
  $extra = @("$env:USERPROFILE\.local\bin", "$env:LOCALAPPDATA\Microsoft\WinGet\Links")
  $env:Path = (@($m, $u) + $extra | Where-Object { $_ }) -join ';'
}

# Corre winget MOSTRANDO todo lo que dice. Nada de silenciar la salida: cuando
# falla, el motivo esta ahi.
function Winget-Instalar($id) {
  $wg = Get-Command winget -ErrorAction SilentlyContinue
  if (-not $wg) { Aviso 'winget no esta disponible en esta consola'; return $false }
  Write-Host "  -> winget install --id $id -e" -ForegroundColor DarkGray
  $guardar = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    & $wg.Source install --id $id -e --accept-package-agreements --accept-source-agreements 2>&1 |
      ForEach-Object { Write-Host "     $_" -ForegroundColor DarkGray }
    $codigo = $LASTEXITCODE
  } finally { $ErrorActionPreference = $guardar }
  Write-Host "     (winget salio con codigo $codigo)" -ForegroundColor DarkGray
  Refrescar-Path
  return ($codigo -eq 0)
}

Write-Host ''
Write-Host "  repo  : $Repo"
Write-Host "  raiz  : $Raiz"

# ---------------------------------------------------------------- 1. requisitos
Paso '1. Requisitos'
if (-not (Test-Path $Server)) { throw "No parece un repo Witral: no existe $Server" }
Refrescar-Path

$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { throw 'Falta git en el PATH. Instalalo con: winget install --id Git.Git -e' }
Ok "git    -> $($git.Source)"

# Python es opcional (uv puede bajarse el suyo), pero tenerlo no estorba.
$py = Buscar-Python
if (-not $py) {
  Aviso 'no hay Python real (el python.exe del PATH suele ser el alias del Microsoft Store)'
  Winget-Instalar 'Python.Python.3.12' | Out-Null
  $py = Buscar-Python
}
if ($py) { Ok "python -> $($py.Cmd)  ($($py.Ver))" }
else     { Aviso 'sigue sin haber Python del sistema; se intentara con uv' }

# uv es lo que realmente arma el entorno del proyecto. Tres planes.
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
  Aviso 'uv no esta instalado; plan A: winget'
  Winget-Instalar 'astral-sh.uv' | Out-Null
  $uv = Get-Command uv -ErrorAction SilentlyContinue
}
if (-not $uv) {
  Aviso 'plan B: instalador oficial de astral.sh'
  $guardar = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression } catch { Aviso "fallo: $_" }
  $ErrorActionPreference = $guardar
  Refrescar-Path
  $uv = Get-Command uv -ErrorAction SilentlyContinue
}
if (-not $uv -and $py) {
  Aviso 'plan C: pip install uv'
  $guardar = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $py.Cmd -m pip install --user --quiet uv } catch { Aviso "fallo: $_" }
  $ErrorActionPreference = $guardar
  Refrescar-Path
  $uv = Get-Command uv -ErrorAction SilentlyContinue
}
if ($uv) { Ok "uv     -> $($uv.Source)" }

if (-not $uv -and -not $py) {
  Write-Host ''
  Write-Host '  No hay ni uv ni Python, y los intentos de instalacion fallaron.' -ForegroundColor Red
  Write-Host '  Mirar arriba la salida de winget. A mano:' -ForegroundColor Red
  Write-Host '    winget install --id Python.Python.3.12 -e --scope user'
  Write-Host '    winget install --id astral-sh.uv -e --scope user'
  Write-Host '  Despues abrir una consola NUEVA y volver a correr este script.'
  throw 'Faltan requisitos'
}

# ------------------------------------------------------------ 2. repositorio git
# Si la carpeta vino de un ZIP de GitHub no tiene .git. NO se clona a un
# temporal para mover el .git despues: mover entre volumenes (C: -> D:) es
# copiar+borrar, y los objetos de git son de solo lectura => Access Denied.
# Se crea el .git EN EL LUGAR y se trae la historia con fetch.
Paso '2. Repositorio git'
Push-Location $Repo
try {
  if (Test-Path (Join-Path $Repo '.git')) {
    Ok '.git ya existe'
  } else {
    Aviso 'Carpeta sin historial (descarga ZIP): reconstruyendo el .git en el lugar'
    git init --quiet
    if ($LASTEXITCODE -ne 0) { throw 'git init fallo' }
    git remote remove origin 2>$null | Out-Null
    git remote add origin $Url
    Write-Host '  bajando la historia desde el remoto...' -ForegroundColor DarkGray
    git fetch --quiet origin main
    if ($LASTEXITCODE -ne 0) { throw 'git fetch fallo (revisar conexion o acceso al repo)' }
    # --mixed: pone HEAD e indice en origin/main y NO toca el working tree.
    # Si los archivos son los mismos del commit, git status queda limpio.
    git reset --quiet --mixed origin/main
    if ($LASTEXITCODE -ne 0) { throw 'git reset fallo' }
    # git init deja la rama en 'master': renombrarla y engancharla a origin/main.
    git branch -M main
    git branch --quiet --set-upstream-to=origin/main main | Out-Null
    Ok ('.git reconstruido en ' + (git rev-parse --short HEAD))
  }

  # Estos se aplican siempre, exista o no el .git de antes.
  # Los archivos del repo estan en LF: que git no los convierta a CRLF.
  git config core.autocrlf false
  if ($Nombre) { git config user.name  $Nombre }
  if ($Email)  { git config user.email $Email }
  # Si el script corre elevado, los archivos quedan del grupo Administradores y
  # git despues se queja de "dubious ownership" al usarlos sin elevar.
  git config --global --add safe.directory ($Repo -replace '\\', '/') 2>$null | Out-Null

  Write-Host '  --- git status ---'
  git status --short --branch
} finally { Pop-Location }

# --------------------------------------------------------------- 3. lugares.json
Paso '3. Configuracion de lugares'
if (Test-Path $Config) {
  Ok "lugares.json presente -> $Config"
} else {
  Aviso 'no hay lugares.json: creando uno minimo con solo el lugar local'
  $ident = New-Object psobject
  $lugarLocal = New-Object psobject
  $lugarLocal | Add-Member -NotePropertyName 'local' -NotePropertyValue $true
  $lugarLocal | Add-Member -NotePropertyName 'raiz'  -NotePropertyValue $Raiz
  $lugarLocal | Add-Member -NotePropertyName 'so'    -NotePropertyValue 'windows'
  if ($Nombre -and $Email) {
    $p = New-Object psobject
    $p | Add-Member -NotePropertyName 'nombre' -NotePropertyValue $Nombre
    $p | Add-Member -NotePropertyName 'email'  -NotePropertyValue $Email
    $ident | Add-Member -NotePropertyName 'personal' -NotePropertyValue $p
    $lugarLocal | Add-Member -NotePropertyName 'identidad' -NotePropertyValue 'personal'
  }
  $lugares = New-Object psobject
  $lugares | Add-Member -NotePropertyName 'local' -NotePropertyValue $lugarLocal
  $raiz = New-Object psobject
  $raiz | Add-Member -NotePropertyName 'identidades' -NotePropertyValue $ident
  $raiz | Add-Member -NotePropertyName 'lugares'     -NotePropertyValue $lugares
  $txt = $raiz | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($Config, $txt, (New-Object System.Text.UTF8Encoding($false)))
  Ok "creado -> $Config"
  Aviso 'para agregar servidores remotos, mirar server\lugares.ejemplo.json'
}

# -------------------------------------------------------------- 4. dependencias
Paso '4. Dependencias'
Push-Location $Server
try {
  $pyEnv = Join-Path $Server '.venv\Scripts\python.exe'
  if ($uv) {
    # uv resuelve tambien el interprete: si no hay Python en la maquina, lo baja.
    & $uv.Source python install 3.12
    & $uv.Source sync
    if ($LASTEXITCODE -ne 0) { throw 'uv sync fallo' }
  } else {
    if (-not (Test-Path (Join-Path $Server '.venv'))) { & $py.Cmd -m venv .venv }
    & $pyEnv -m pip install --quiet --upgrade pip
    & $pyEnv -m pip install --quiet -e .
    if ($LASTEXITCODE -ne 0) { throw 'pip install fallo' }
  }
} finally { Pop-Location }
if (-not (Test-Path $pyEnv)) { throw "No se creo el entorno en $pyEnv" }
Ok "entorno -> $pyEnv"

# ------------------------------------------------------------------ 5. arranque
# El codigo va a un .py temporal, NO por "python -c": PowerShell 5.1 se come las
# comillas dobles al pasar argumentos a un ejecutable nativo y llega destrozado.
Paso '5. Prueba de arranque'
$env:WITRAL_CONFIG = $Config
$lineas = @(
  'import sys',
  "sys.path.insert(0, r'$Server')",
  'from witral import config',
  'c = config.cargar()',
  'print("lugares:", ", ".join(c.nombres))',
  'print("identidades:", ", ".join(c.identidades) or "(ninguna)")',
  'print("raiz local:", c.resolver("local").raiz)',
  'print("error_config:", c.error_config or "(sin errores)")',
  'import witral.server as s',
  'tm = getattr(s.mcp, "_tool_manager", None)',
  'print("tools registradas:", len(getattr(tm, "_tools", [])) or "(no se pudo contar)")'
)
$tmpPy = Join-Path $env:TEMP 'witral_check.py'
[System.IO.File]::WriteAllText($tmpPy, (($lineas -join "`r`n") + "`r`n"), (New-Object System.Text.UTF8Encoding($false)))
& $pyEnv $tmpPy
$codigoPy = $LASTEXITCODE
Remove-Item $tmpPy -Force -ErrorAction SilentlyContinue
if ($codigoPy -ne 0) { throw 'El servidor no importa bien; revisar el traceback de arriba' }
Ok 'Witral importa y carga la config'

# --------------------------------------------------- 6. registro en Claude Desktop
Paso '6. Registro en Claude Desktop'
$cd    = Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
$cdDir = Split-Path $cd
if (-not (Test-Path $cdDir)) {
  Aviso "No existe $cdDir - Claude Desktop no esta instalado para este usuario. Salto este paso."
} else {
  if (Test-Path $cd) {
    $bak = "$cd.bak_" + (Get-Date -Format 'yyyyMMdd_HHmmss')
    Copy-Item $cd $bak
    Ok "respaldo -> $bak"
    $json = Get-Content $cd -Raw -Encoding UTF8 | ConvertFrom-Json
  } else {
    $json = New-Object psobject
  }
  if (-not ($json.PSObject.Properties.Name -contains 'mcpServers')) {
    $json | Add-Member -NotePropertyName 'mcpServers' -NotePropertyValue (New-Object psobject)
  }
  $entradaEnv = New-Object psobject
  $entradaEnv | Add-Member -NotePropertyName 'WITRAL_CONFIG' -NotePropertyValue $Config
  $entradaEnv | Add-Member -NotePropertyName 'WITRAL_RAIZ'   -NotePropertyValue $Raiz
  $entrada = New-Object psobject
  if ($uv) {
    $entrada | Add-Member -NotePropertyName 'command' -NotePropertyValue $uv.Source
    $entrada | Add-Member -NotePropertyName 'args'    -NotePropertyValue @('run', '--directory', $Server, 'python', '-m', 'witral.server')
  } else {
    $entradaEnv | Add-Member -NotePropertyName 'PYTHONPATH' -NotePropertyValue $Server
    $entrada | Add-Member -NotePropertyName 'command' -NotePropertyValue $pyEnv
    $entrada | Add-Member -NotePropertyName 'args'    -NotePropertyValue @('-m', 'witral.server')
  }
  $entrada | Add-Member -NotePropertyName 'env' -NotePropertyValue $entradaEnv
  if ($json.mcpServers.PSObject.Properties.Name -contains 'witral') {
    $json.mcpServers.witral = $entrada
  } else {
    $json.mcpServers | Add-Member -NotePropertyName 'witral' -NotePropertyValue $entrada
  }
  $txt = $json | ConvertTo-Json -Depth 20
  [System.IO.File]::WriteAllText($cd, $txt, (New-Object System.Text.UTF8Encoding($false)))
  Ok "registrado en $cd"
}

Paso 'LISTO'
Write-Host '  Falta el ultimo paso, a mano:'
Write-Host '   1. Clic derecho en el icono de Claude en la bandeja del sistema -> Salir'
Write-Host '      (cerrar la ventana NO basta: la app sigue viva en la bandeja).'
Write-Host '   2. Abrir Claude Desktop de nuevo y empezar una conversacion NUEVA.'
Write-Host '   3. Pedir "lugares": debe listar el lugar local con su raiz.'
Write-Host '  Si no aparece, mirar el log: %APPDATA%\Claude\logs\mcp-server-witral.log'
