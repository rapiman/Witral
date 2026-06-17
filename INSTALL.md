# Guía de instalación

Esta guía explica cómo poner Witral a funcionar desde cero y conectarlo a un
cliente MCP (como Claude Desktop). Cubre Windows y Linux/macOS.

---

## 1. Requisitos

- **Python ≥ 3.10**
- **Git** (para las herramientas git y para clonar el repo)
- **uv** (recomendado) o **pip** para instalar dependencias
- Un **cliente MCP** que hable por stdio (p. ej. Claude Desktop)
- Opcionales según lo que vayas a usar:
  - **OpenSSH** en los servidores remotos a los que te conectes
  - **PostgreSQL** (`psql`) en los lugares donde uses la herramienta `psql`
  - **ADB** (platform-tools) si vas a manejar dispositivos Android

Verifica lo básico:

```bash
python --version
git --version
```

---

## 2. Obtener el código

```bash
git clone https://github.com/rapiman/Witral.git
cd Witral/server
```

---

## 3. Instalar dependencias

Witral depende de `mcp[cli]` y `paramiko` (este último para SSH/SFTP).

### Con uv (recomendado)

```bash
uv sync
```

### Con pip

```bash
python -m pip install -e .
```

> En Windows, si `pip` se queja de permisos, agrega `--user`, o usa un entorno
> virtual (`python -m venv .venv` y actívalo antes de instalar).

---

## 4. Crear tu configuración de lugares

Witral se configura con un archivo `lugares.json`. **Nunca lo subas a git**:
contiene credenciales y está en `.gitignore`.

Copia la plantilla y edítala:

```bash
# Windows (PowerShell)
copy lugares.ejemplo.json lugares.json

# Linux/macOS
cp lugares.ejemplo.json lugares.json
```

Edita `lugares.json` con tus datos. Estructura mínima:

```json
{
  "identidades": {
    "personal": { "nombre": "Tu Nombre", "email": "tu@correo.com" }
  },
  "lugares": {
    "local": {
      "local": true,
      "raiz": "C:\\Users\\tu_usuario\\Documents\\Proyectos",
      "so": "windows",
      "identidad": "personal"
    },
    "miservidor": {
      "ssh": {
        "host": "miservidor.ejemplo.cl",
        "usuario": "jp",
        "puerto": 22,
        "clave": "C:\\Users\\tu_usuario\\.ssh\\id_rsa"
      },
      "so": "unix"
    }
  }
}
```

Puntos clave:

- **`raiz`** (lugar local): la carpeta dentro de la cual Witral puede operar
  sobre archivos. Todo queda acotado ahí.
- **`so`**: `windows` o `unix`. Se autodetecta en el lugar local; en remotos
  asume `unix` salvo que lo declares. Un Windows remoto por SSH usa `"so": "windows"`.
- **Autenticación SSH**: preferí `clave` (ruta a la clave privada) antes que
  `password`. Si usas `password`, queda en texto plano en el archivo: protégelo.
- **`sensible: true`** en un lugar (p. ej. producción) refuerza las confirmaciones.

La ruta del archivo se toma de la variable de entorno `WITRAL_CONFIG`; si no se
define, se busca junto al paquete. Conviene fijarla explícitamente (ver paso 6).

---

## 5. Probar que arranca

Desde `server/`:

```bash
# con uv
uv run python -m witral.server

# o si instalaste con pip
python -m witral.server
```

El servidor se queda esperando por stdio (no imprime un prompt; es normal).
Si hay un error de config, lo verás en la salida. Corta con Ctrl+C.

> Witral está pensado para ser lanzado por el cliente MCP, no a mano. Este paso
> es solo para confirmar que no hay errores de instalación o de `lugares.json`.

---

## 6. Conectar a Claude Desktop

Edita el archivo de configuración de Claude Desktop:

- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`

Agrega Witral a `mcpServers`:

```json
{
  "mcpServers": {
    "witral": {
      "command": "uv",
      "args": [
        "run",
        "--directory",
        "C:\\ruta\\a\\Witral\\server",
        "python",
        "-m",
        "witral.server"
      ],
      "env": {
        "WITRAL_CONFIG": "C:\\ruta\\a\\Witral\\server\\lugares.json"
      }
    }
  }
}
```

Ajusta las rutas a tu instalación. Si no usas `uv`, reemplaza `command`/`args`
por el `python` de tu entorno:

```json
"command": "python",
"args": ["-m", "witral.server"],
```

(con el `cwd`/PATH apropiado para que encuentre el paquete).

---

## 7. Reiniciar el cliente (importante en Windows)

Cerrar la ventana de Claude Desktop **no** cierra la app: sigue viva en la
bandeja del sistema. Para que tome la configuración (o cambios en el código de
Witral):

1. Clic derecho en el icono de Claude en la bandeja del sistema → **Salir**.
2. Vuelve a abrir Claude Desktop.

Cada vez que edites el código de Witral, repite este reinicio completo.

---

## 8. Verificar

En una conversación, pide listar los lugares. Witral debería responder con tu
lugar `local` y los remotos que declaraste. Si la config tiene un error de
sintaxis, Witral arranca igual (solo con `local`) y te muestra el error con la
línea y columna a corregir.

---

## Problemas frecuentes

- **El servidor no aparece / "disconnected"**: revisa el log del cliente.
  En Claude Desktop (Windows): `%APPDATA%\Claude\logs\`, archivo
  `mcp-server-witral.log`. Ahí verás el traceback si algo falló al arrancar.
- **"Config inválida"**: hay un error de sintaxis en `lugares.json` (una coma
  de más, una llave suelta). El mensaje indica línea y columna.
- **Las herramientas git se cuelgan**: asegúrate de tener la última versión;
  versiones viejas tenían un problema con subprocesos sobre stdio (ya resuelto).
- **No conecta por SSH a un remoto**: verifica `host`, `puerto`, `usuario` y que
  la `clave` apunte a una clave privada válida. Prueba primero un `ssh` manual.
- **`psql` dice "no tiene config de base"**: ese lugar no tiene sección `db` en
  `lugares.json`. Agrégala con `base`, `usuario`, etc.
