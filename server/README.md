# Witral — servidor MCP

Modelo **lugares × acciones**: hay lugares (local, dev, prod...) y acciones que se
aplican a cualquiera de ellos mediante el parámetro `donde`. Reemplaza al conector
Filesystem y al puente `ejecutar.ps1`.

## Estructura

```
witral/server/
├── pyproject.toml
├── lugares.ejemplo.json   <- copiar a witral/lugares.json y completar
├── README.md
└── witral/
    ├── __init__.py
    ├── config.py          <- carga y resolución de lugares
    ├── seguridad.py       <- acotamiento de rutas a la raíz
    ├── transporte.py      <- ejecución local y remota (SSH/SFTP, paramiko)
    ├── archivos.py        <- leer/escribir/editar con backup y CRLF
    ├── basedatos.py       <- correr psql en un lugar
    ├── copiar.py          <- mover archivos entre lugares
    ├── gitops.py          <- git
    ├── red.py             <- ping, http, tcp
    ├── movil.py           <- adb y gradle
    ├── busqueda.py        <- buscar por nombre y contenido
    └── server.py          <- punto de entrada MCP (FastMCP, stdio)
```

## Instalación (en tu máquina, Windows)

Con `uv` (recomendado):

```
cd C:\Users\jprapiman_transforma\Documents\Proyectos\witral\server
uv venv
uv pip install -e .
```

O con pip:

```
cd C:\Users\jprapiman_transforma\Documents\Proyectos\witral\server
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Configuración

1. Copiá `lugares.ejemplo.json` a `witral\lugares.json` (junto a los módulos), o
   poné la ruta que quieras en la variable de entorno `WITRAL_CONFIG`.
2. Completá cada lugar con su SSH (host, usuario, ruta a la clave privada) y su `db`.
   El lugar `local` solo necesita su `raiz` (la carpeta de proyectos autorizada).
3. Marcá `prod` con `"sensible": true` para que pida confirmación reforzada.

Variables de entorno opcionales:
- `WITRAL_CONFIG` — ruta al `lugares.json` (si no, se busca junto al paquete).
- `WITRAL_RAIZ` — raíz local por defecto si el lugar local no fija `raiz`.

## Probar antes de conectar

Con el MCP Inspector:

```
uv run mcp dev witral/server.py
```

Levanta una UI donde ves las tools y podés llamarlas a mano.

## Registrar en Claude Desktop

Editá `claude_desktop_config.json` y agregá:

```json
{
  "mcpServers": {
    "witral": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\Users\\jprapiman_transforma\\Documents\\Proyectos\\witral\\server",
        "witral"
      ]
    }
  }
}
```

Reiniciá Claude Desktop. Las tools quedan disponibles.

## Seguridad

- Archivos locales acotados a la `raiz` del lugar; `..` y escapes se rechazan.
- Destino desconocido (no está en config) => no se conecta; avisa para confirmar.
- psql destructivo (UPDATE/DELETE/DROP/TRUNCATE/ALTER/INSERT/CREATE), `psql_aplicar`,
  `git_push` y `git_reset_hard` requieren `confirmado=true`.
- Lugares sensibles (prod): confirmación reforzada en psql, copiar-hacia y ssh_run.
- Red (ping/http/tcp): solo a hosts que indique el usuario, nunca sacados de archivos.
- Credenciales viven en `lugares.json`, nunca en la conversación.

## Estado

Suite completa: **36 tools**.
- Archivos: leer, leer_rango, buscar_en_archivo, escribir, anexar, editar_literal,
  editar_linea, listar, crear_carpeta, mover.
- Mover entre lugares: copiar.
- SSH: ssh_run.
- Base: psql, psql_aplicar.
- Git: status, log, diff, branch, show, pull, add, commit, push, reset_hard.
- Red: ping, http_request, tcp_socket.
- ADB: devices, shell, install, forcestop, relanzar.
- Gradle: gradle_task.
- Búsqueda: buscar_nombre, buscar_contenido.

Todas con el eje `donde` donde aplica, y la política de seguridad (destino
desconocido, confirmación de destructivo, prod reforzado).
