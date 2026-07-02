# Changelog

Todos los cambios notables de Witral se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto adhiere a [Versionado Semántico](https://semver.org/lang/es/).

## [No publicado]

### Añadido

- `http_request`: parámetros `params_json` y `donde`. `params_json` recibe los query
  params como JSON y Witral los percent-encodea en Python (urlencode, UTF-8) antes de
  armar la URL, así el texto no-ASCII (ü, ñ) llega intacto sin pelear con el locale del
  shell — resuelve la fricción de la ü rota en `curl` vía `run`. `donde` permite hacer la
  petición DESDE un lugar remoto (arma y ejecuta curl allí; el cuerpo viaja por stdin con
  `--data-binary @-`), útil para servicios que solo escuchan en localhost del server.

- `git_clone(url, destino, rama, donde)`: clona un repositorio en `destino`. El destino
  no debe existir todavía y en local se acota a la raíz autorizada (no se puede clonar
  fuera de ella); en remoto se interpreta en ese lugar. `rama` opcional clona solo esa
  rama (`--branch`). No pide confirmación (es solo descarga, no publica ni destruye),
  igual que `git_pull`/`git_fetch`. Timeout amplio (300s) por si el repo es grande.
- `editar_literal`: parámetro `verificar` (igual que `editar_linea`), corre
  verificar_sintaxis tras editar y agrega el resultado en la misma respuesta.

### Cambiado

- Backup remoto de edición: los `.bak` en lugares remotos ahora van a `~/.witral/bak/`
  del lugar (igual que la papelera remota), NUNCA al lado del archivo editado. Antes
  quedaban en el árbol de trabajo y ensuciaban el `git status` de repos remotos.
- `editar_linea` con `ancla`: el ancla ahora puede ser **solo las primeras líneas del
  rango**, no hace falta copiar todo el rango. Verifica que el inicio coincide (que ya
  protege del desfase) y el mensaje de error es más claro sobre qué se esperaba. Resuelve
  la fricción de tener que pasar el ancla completa.
- `git_show`: si `ref` es `rama:ruta` o `commit:ruta` (contiene `:`), vuelca el CONTENIDO
  de ese archivo en esa rama/commit en vez de `--stat`. Permite comparar la versión de un
  archivo entre ramas en un merge sin ir a otro clon.
- `git_commit`: parámetro `merge` — con `merge=True` y sin mensaje, sella un merge en curso
  usando el mensaje automático de git (`--no-edit`).

- `git_publicar(repo, mensaje, donde, rutas, empujar, forzar, confirmado)`: ciclo de
  commit completo en una pasada (status -> add -> diff --stat -> commit -> push),
  mostrando el diff antes del commit y parando si un paso falla. Ahorra encadenar las
  cinco tools a mano. Con `empujar=False` commitea solo local. Requiere `confirmado=True`
  cuando empuja.
- `editar_linea`: parámetro `verificar` — si es True, tras editar corre verificar_sintaxis
  sobre el archivo y agrega el resultado en la misma respuesta (ahorra una llamada al
  editar código).

- `convertir_eol(archivo, a, donde)`: convierte el fin de línea de un archivo entero a
  LF o CRLF. Para pasar archivos clonados en Windows a LF, o limpiar saltos mezclados.
- `adb_logcat(serial, tags, nivel, lineas, limpiar_antes, donde)`: captura logcat del
  dispositivo en modo dump (vuelca y sale, no streaming), con filtro por tag y nivel,
  tail de líneas y opción de limpiar el buffer antes. Cierra el ciclo de logs del POS
  sin copiar/pegar a mano.
- `datastore_get(serial, paquete, archivo, donde)`: lista las claves de un Jetpack
  DataStore (Preferences) de una app Android, con su tipo y valor decodificado. Solo
  lectura, vía run-as (app debuggable). Útil para inspeccionar parámetros del POS.
- `datastore_set(serial, paquete, archivo, clave, valor, tipo, donde, confirmado)`:
  cambia el valor de UNA clave en un Jetpack DataStore (Preferences) dejando el resto
  intacto, decodificando/recodificando el protobuf correctamente (respeta los length
  prefixes). `tipo="auto"` detecta y respeta el tipo actual de la clave. Pensado para
  alternar parámetros en QA sin UI (ej. operativa REST/RETAIL). Hace backup en /sdcard
  y `force-stop` antes de escribir (DataStore cachea en memoria); requiere
  `confirmado=True` y relanzar la app después. Nueva sección "Android DataStore" en
  `movil.py`.
- `verificar_sintaxis`: agrega perfiles JSON/YAML/TOML con validación nativa por librería
  Python (json/pyyaml/tomllib), que da línea y columna del error y funciona local y remoto.

### Cambiado

- **Fusiones de tools** (menos superficie, mejor descubrimiento):
  - `leer` absorbe a `leer_rango`: `leer(archivo, desde, hasta)` con rango opcional
    (sin rango = archivo completo).
  - `editar_linea` absorbe a `editar_anclado`: parámetro `ancla` opcional; con ancla
    verifica el contenido antes de editar, sin ancla edita directo.
  - `buscar_contenido` absorbe a `buscar_en_archivo`: el parámetro `objetivo` acepta un
    archivo o una carpeta; siempre devuelve `ruta:linea: texto`.
  - `git_remote` absorbe a `git_remote_add`: sin `nombre`/`url` lista, con ellos agrega.
- `editar_literal`: ahora normaliza saltos de línea (solo si el archivo es CRLF) antes de
  comparar, así no falla con bloques multilínea cuando el archivo tiene CRLF y el `viejo`
  viene en LF. Era la fricción nº1 de uso.

- `verificar_sintaxis(archivo, donde)`: red rápida antes de mover o compilar, en dos
  capas. **Universal** (siempre, todos los lenguajes): balance de `()[]{}`, comillas y
  comentarios sin cerrar, ignorando strings y comentarios; funciona local y remoto.
  **Nativa** (si el binario está y el lugar es local): chequeo real con `node`/`python`/
  `php`/`gcc`/`perl`/`ruby`. Reconoce kt, kts, java, c, h, cpp, js, jsx, ts, php, py,
  sql, html, xml, css, sh, rb, pl. Nuevo módulo `sintaxis.py`.
- `editar_anclado(archivo, desde, hasta, ancla, nuevo, donde)`: edición por rango que
  **verifica** que el contenido actual coincida con un ancla esperada antes de tocar el
  archivo; si no coincide, aborta y muestra esperado vs encontrado. Une la inmunidad a
  CRLF de `editar_linea` con una red contra perder la cuenta de líneas. Es el modo de
  edición más seguro.
- `editar_linea` y `editar_anclado` ahora devuelven el fragmento resultante (líneas
  editadas ± 2 de contexto), para verificar el cambio sin un `leer_rango` aparte.
- `INSTALL.md`: guía de instalación paso a paso (requisitos, config, conexión a
  Claude Desktop, problemas frecuentes), enlazada desde el README.
- `gradle_build`: compila con el `gradlew` del proyecto. En unix/remoto compila
  y devuelve la salida; en local Windows avisa que el build debe correrse en una
  terminal propia (ver más abajo).

### Corregido

- **Modos de falla: fallar rápido y claro en vez de colgarse o engañar.**
  - `psql`: agrega `-w` (nunca pedir password interactivo), `PGCONNECT_TIMEOUT=10`
    y baja el timeout total a 60s. Si la base pide password y no hay credencial,
    falla al instante en vez de colgarse minutos esperando un prompt.
  - SSH: el fallo de conexión se traduce a un mensaje según su causa (host no
    resuelve / conexión rechazada / autenticación rechazada / timeout) en vez de
    un seco "timed out". Agrega `banner_timeout` y `auth_timeout`.
  - `editar_literal`: si el bloque `viejo` no aparece pero el `nuevo` ya está
    presente, avisa que la edición probablemente ya se aplicó, en vez de un seco
    "no aparece" que induce a dudar.
- Consistencia de rutas: `adb_install` (APK) y `psql_aplicar` (`.sql`) ahora
  normalizan la ruta en local igual que las tools de archivo (acepta relativa
  contra la raíz o absoluta, acotada a la raíz). Antes fallaban con rutas
  relativas porque adb/psql las interpretaban desde su propio directorio.

### Cambiado

- Subprocesos locales: el `stdin` pasa de `DEVNULL` a un pipe vacío (`input=""`),
  para no romper el selector NIO de la JVM y mantener a git sin colgarse.
- `gradle_task` → `gradle_build`.

### Eliminado

- `run_no_sandbox` y `gradle_task`.

### Notas

- Se investigó a fondo ejecutar el build dentro del MCP en local Windows (el
  sandbox del cliente bloquea los sockets loopback que Gradle/Java necesitan).
  Se probaron flags de proceso, capas de shell y tareas programadas (`schtasks`),
  sin un resultado fiable: las tareas creables quedan "Solo interactivo" y no
  ejecutan, y las no interactivas dan *Acceso denegado*. Conclusión: en local
  Windows el build se corre en una terminal propia y witral despliega el APK.
  Detalle en las Notas técnicas del README.

## [0.2.0] - 2026-06-17

### Añadido

- **`run`**: ejecuta un comando arbitrario en cualquier lugar (local o remoto)
  con el eje `donde`. Siempre requiere `confirmado=True` y empuja hacia las tools
  tipadas. Generaliza y reemplaza a `ssh_run`.
- **Tools de sistema** que ramifican por el SO del lugar (Windows vs unix):
  - `procesos` — lista procesos (`tasklist` / `ps aux`). Solo lectura.
  - `matar_proceso` — mata por nombre/patrón (`taskkill` / `pkill`). Requiere confirmación.
  - `servicio` — status/start/stop/restart (`sc` / `systemctl`). `status` es lectura.
- **Campo `so` por lugar** (`windows` | `unix`) en la config: autodetectado para
  el lugar local, `unix` por defecto en remotos, declarable. Permite, por ejemplo,
  manejar un Windows remoto accesible por SSH con `"so": "windows"`.

### Cambiado

- `ssh_run` eliminado; su funcionalidad la cubre `run` (que además funciona en local).

## [0.1.0] - 2026-06-17

Primera versión pública. Servidor MCP con el modelo **lugares × acciones**:
cada acción opera sobre un *lugar* (local o remoto) según el parámetro `donde`.

### Añadido

**Núcleo**
- Modelo de *lugares* configurables vía `lugares.json` (local + remotos por SSH),
  con resolución por nombre y conexiones SSH cacheadas por lugar.
- Lugar `local` siempre garantizado, con raíz autorizada para operaciones de archivo.
- Política de seguridad: destinos desconocidos no se conectan a ciegas; acciones
  destructivas requieren `confirmado=True`; lugares `sensible` refuerzan la confirmación.
- Acotamiento de rutas a la raíz autorizada (`normalizar`), inmune a escapes con `..`.

**Archivos** (eje `donde`, local o remoto vía SFTP)
- Leer (`leer`, `leer_rango`), escribir (`escribir`, `anexar`), editar
  (`editar_literal`, `editar_linea`) con backup automático y preservación de fin de línea.
- Listar, crear carpetas, mover, buscar en archivo.
- Borrado a papelera recuperable (`borrar`) y vaciado definitivo (`vaciar_papelera`).
- Copia de archivos entre lugares (`copiar`) por SFTP.

**SSH y base de datos**
- `ssh_run` para comandos puntuales en lugares remotos.
- `psql` (lectura libre; sentencias destructivas requieren confirmación) y
  `psql_aplicar` para migraciones.

**Git**
- Lectura: `git_status`, `git_log`, `git_diff`, `git_branch`, `git_show`.
- Transporte: `git_pull`, `git_fetch`, `git_add`, `git_commit`, `git_push`.
- Inicialización y remotos: `git_init`, `git_remote`, `git_remote_add`.
- `git_push` configura el upstream automáticamente en el primer push y admite
  `forzar=True` (usa `--force-with-lease`).
- `git_reset_hard` (destructivo, requiere confirmación).

**Identidades git**
- Sección `identidades` en la config (nombre, email y `usuario_git` reservado).
- Campo `identidad` por lugar para fijar la identidad por defecto de sus repos.
- Tool `git_identidad` que aplica el autor del commit (`user.name`/`user.email`)
  a un repo, por defecto del lugar o forzando una identidad concreta.

**Red, móvil y búsqueda**
- Red: `ping`, `http_request`, `tcp_socket`.
- Android/ADB: `adb_devices`, `adb_shell`, `adb_install`, `adb_forcestop`, `adb_relanzar`.
- Build: `gradle_task`.
- Búsqueda: `buscar_nombre` (por nombre) y `buscar_contenido` (grep).

### Robustez

- **Subprocesos sobre transporte stdio**: los comandos locales se ejecutan con
  el `stdin` cerrado (`DEVNULL`) y `GIT_TERMINAL_PROMPT=0`, evitando cuelgues por
  competir con el canal del protocolo MCP o por prompts invisibles de git.
- **Rutas de git** resueltas con `-C <repo>` y normalizadas contra la raíz; timeout
  de 20s en operaciones git.
- **Config tolerante a fallos**: un `lugares.json` inválido ya no tumba el servidor.
  Witral arranca con el lugar `local` y reporta el error (con línea y columna) en
  las herramientas que dependen de la config.

[No publicado]: https://github.com/rapiman/Witral/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/rapiman/Witral/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/rapiman/Witral/releases/tag/v0.1.0
