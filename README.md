# Witral

> *El telar mapuche que urde hilos entre máquinas.*

**Witral** es el nombre mapuche del telar vertical: cuatro maderos cruzados sobre los que la tejedora urde la lana y, hebra a hebra, levanta una pieza entera. No es solo una herramienta —es el soporte donde se teje una forma de comunicación, "el gran libro" de una cultura.

Este proyecto toma prestada esa imagen. Witral es un **servidor MCP** donde cada máquina —la tuya, un servidor de desarrollo, una terminal de pago— es una **hebra**, y cada operación —leer un archivo, correr `git`, consultar Postgres, hablar con un dispositivo Android— es un **nudo** del tejido. La trama que resulta: poder trabajar sobre muchas máquinas, locales y remotas, como si fueran una sola.

---

## Por qué nació

Witral nació de una fricción concreta del día a día desarrollando para terminales de pago PAX y sus backends: el trabajo está repartido. El código vive en la máquina local; las bases de datos y los servicios viven en servidores remotos accesibles por SSH; los dispositivos Android se conectan por ADB; y las migraciones hay que llevarlas de un lado a otro, aplicarlas con `psql`, verificar el resultado. Cada una de esas piezas habla un protocolo distinto y vive en un sitio distinto.

Hacer eso a mano significa saltar entre terminales, recordar rutas, copiar credenciales, encadenar `ssh` con `scp` con `psql`, y rezar para no equivocarse de ambiente y tocar producción por accidente.

La idea de Witral es **unificar ese tejido bajo un solo modelo mental**:

> **lugares × acciones**

Defines tus *lugares* una vez (con sus credenciales, rutas y bases, fuera de la vista del modelo), y a partir de ahí cada *acción* acepta un parámetro `donde` que dice sobre qué lugar operar. La misma acción `leer`, `git_status` o `psql` funciona en local o en remoto cambiando solo ese parámetro. El telar es el mismo; cambia la hebra.

---

## Filosofía de diseño

- **Un modelo, no un montón de comandos sueltos.** Todo es "una acción sobre un lugar". Eso hace el sistema predecible: aprendes el eje `donde` una vez y aplica a archivos, git, base de datos y red por igual.
- **El destino se resuelve primero; la sesión se reutiliza.** Las conexiones SSH se abren una vez por lugar y se cachean. Un lugar desconocido **nunca** se conecta a ciegas: se devuelve un aviso para que el usuario lo confirme y lo agregue a la config.
- **Los secretos no salen de la config.** Credenciales SSH, contraseñas y claves viven en `lugares.json` y se resuelven internamente. El modelo opera por *nombre* de lugar; nunca ve ni manipula las credenciales.
- **Las acciones peligrosas piden permiso.** Borrar, sobrescribir, `git push`, `reset --hard`, `UPDATE/DELETE/DROP` en SQL, aplicar migraciones, copiar hacia un lugar sensible: todo requiere un flag explícito `confirmado=True` que solo se pasa tras confirmar con la persona. Los lugares marcados como `sensible` (producción) refuerzan esa confirmación.
- **Acotamiento a la raíz.** En el lugar local, toda operación de archivo queda confinada a una raíz autorizada; intentar escapar con `..` o symlinks fuera de ella se rechaza.

---

## Arquitectura

Witral es un servidor [MCP](https://modelcontextprotocol.io) construido con **FastMCP**, comunicándose por transporte **stdio** (pensado para clientes como Claude Desktop). El código se organiza por responsabilidad:

| Módulo | Responsabilidad |
|--------|-----------------|
| `server.py` | Punto de entrada. Define las tools y aplica la política de seguridad (confirmaciones, destinos desconocidos). |
| `config.py` | Carga y resuelve los *lugares* desde `lugares.json`. Garantiza siempre un lugar `local`. Nunca expone secretos. |
| `transporte.py` | Cómo viaja una operación a su lugar: subprocess en local, SSH/SFTP (paramiko) en remoto. Conexiones cacheadas por lugar. |
| `seguridad.py` | Acotamiento de rutas a la raíz autorizada (`normalizar`, `dentro_de`). |
| `archivos.py` | Leer, escribir, editar (literal y por línea), listar, mover, borrar (a papelera). Backup automático y preservación de fin de línea. |
| `basedatos.py` | `psql` sobre la base local de un lugar; detección de sentencias destructivas. |
| `gitops.py` | Operaciones git sobre repos dentro de un lugar. |
| `copiar.py` | Copia de archivos entre lugares vía SFTP. |
| `red.py` | `ping`, peticiones HTTP, sockets TCP. |
| `movil.py` | ADB (dispositivos, shell, install, force-stop, relanzar) y tareas Gradle. |
| `busqueda.py` | Búsqueda por nombre de archivo y por contenido (grep) en un proyecto. |

### El eje `donde`

```
acción(args..., donde="local")     # opera en esta máquina
acción(args..., donde="prod")      # opera en el servidor "prod" vía SSH
```

En local, los comandos corren por `subprocess`; en remoto, viajan por SSH (`paramiko`) y los archivos por SFTP. El cambio es transparente para quien usa la herramienta.

---

## Catálogo de herramientas

**Lugares**
`lugares` — lista los destinos definidos (sin exponer secretos).

**Archivos** (eje `donde`)
`leer` · `leer_rango` · `escribir` · `anexar` · `editar_literal` · `editar_linea` · `listar` · `crear_carpeta` · `mover` · `borrar` (a papelera) · `vaciar_papelera` · `buscar_en_archivo`

**Entre lugares**
`copiar` — copia un archivo de un lugar a otro por SFTP.

**SSH**
`ssh_run` — comando puntual en un lugar remoto.

**Base de datos**
`psql` — consulta/sentencia sobre la base local de un lugar · `psql_aplicar` — aplica un `.sql` (migraciones).

**Git**
`git_status` · `git_log` · `git_diff` · `git_branch` · `git_show` · `git_pull` · `git_add` · `git_commit` · `git_push` · `git_reset_hard`

**Red**
`ping` · `http_request` · `tcp_socket`

**Android / ADB**
`adb_devices` · `adb_shell` · `adb_install` · `adb_forcestop` · `adb_relanzar`

**Build**
`gradle_task` — corre una tarea con el `gradlew` del proyecto.

**Búsqueda**
`buscar_nombre` (por nombre de archivo) · `buscar_contenido` (grep de contenido).

---

## Configuración

Witral se configura con un archivo `lugares.json`. La ruta se toma de la variable de entorno `WITRAL_CONFIG`, o por defecto el `lugares.json` junto al paquete.

> ⚠️ **`lugares.json` contiene credenciales y NO debe versionarse.** Está en `.gitignore`. Usa `lugares.ejemplo.json` como plantilla.

Ejemplo (`lugares.ejemplo.json`):

```json
{
  "lugares": {
    "local": {
      "local": true,
      "raiz": "C:\\Users\\tu_usuario\\Documents\\Proyectos"
    },
    "dev": {
      "ssh": {
        "host": "dev.ejemplo.cl",
        "usuario": "jp",
        "puerto": 22,
        "clave": "C:\\Users\\tu_usuario\\.ssh\\id_rsa"
      },
      "db": {
        "motor": "postgres",
        "host": "127.0.0.1",
        "puerto": 5432,
        "base": "mi-base",
        "usuario": "postgres"
      },
      "rutas": { "repo": "/home/jp/proyecto", "web": "/var/www/html" }
    },
    "prod": {
      "sensible": true,
      "ssh": { "host": "prod.ejemplo.cl", "usuario": "jp", "puerto": 22,
               "clave": "C:\\Users\\tu_usuario\\.ssh\\id_rsa" }
    }
  }
}
```

Campos por lugar:

- `local` — `true` para la máquina actual.
- `raiz` — raíz autorizada para operaciones de archivo (solo local).
- `sensible` — `true` para ambientes de producción; refuerza las confirmaciones.
- `ssh` — `host`, `usuario`, `puerto`, y autenticación por `clave` (ruta a la clave privada) o `password`.
- `db` — config para `psql` en ese lugar.
- `rutas` — rutas con nombre dentro del lugar (repo, web, etc.).

**Autenticación recomendada: por clave SSH, no por contraseña.** Si usas `password`, es responsabilidad del archivo local mantenerlo protegido.

---

## Instalación

Requiere **Python ≥ 3.10**.

```bash
cd server
# con uv (recomendado)
uv sync
# o con pip
pip install -e .
```

Dependencias: `mcp[cli]`, `paramiko`.

### Conectar a Claude Desktop

En `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "witral": {
      "command": "uv",
      "args": ["--directory", "C:\\ruta\\a\\witral\\server", "run", "witral"],
      "env": { "WITRAL_CONFIG": "C:\\ruta\\a\\tu\\lugares.json" }
    }
  }
}
```

> En Windows, recuerda que cerrar la ventana de Claude Desktop **no** cierra la app: sigue viva en la bandeja del sistema. Para recargar cambios del servidor, sal del todo desde la bandeja y reabre.

---

## Notas técnicas

- **Transporte stdio y subprocesos.** Como el servidor habla por stdio, los subprocesos locales se lanzan con el `stdin` cerrado (`DEVNULL`) para no competir por el canal del protocolo, y con `GIT_TERMINAL_PROMPT=0` para que git nunca quede esperando un prompt invisible. Sin esto, comandos que terminan en milisegundos pueden colgarse hasta el timeout.
- **Backups automáticos.** `editar_literal` y `editar_linea` guardan un `.bak` con timestamp antes de tocar el archivo.
- **Papelera.** `borrar` no elimina: mueve a `.witral/papelera/` con timestamp (recuperable). `vaciar_papelera` sí es definitivo.
- **Timeouts.** Las operaciones git cortan a los 20s para no dejar la sesión colgada.

---

## Seguridad

- Los secretos viven solo en `lugares.json`, nunca se exponen al modelo.
- Las acciones destructivas requieren `confirmado=True`.
- Los lugares `sensible` refuerzan la confirmación en cualquier ejecución.
- Las operaciones de archivo locales quedan acotadas a la raíz autorizada.
- Un lugar no declarado en la config no se conecta jamás de forma automática.

---

```
Tejido en Chile. © 2026
```
