# Changelog

Todos los cambios notables de Witral se documentan en este archivo.

El formato sigue [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/)
y el proyecto adhiere a [Versionado Semántico](https://semver.org/lang/es/).

## [No publicado]

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

[No publicado]: https://github.com/rapiman/Witral/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rapiman/Witral/releases/tag/v0.1.0
