# Acciones

Catálogo de las acciones (tools) de Witral por área. Casi todas aceptan `donde`
(`local` por defecto, o un lugar definido en config).

> **Referencia canónica:** `WITRAL_PARA_CLAUDE.md` (raíz de `Proyectos\`) es el
> documento único y detallado. Este archivo es el índice rápido; si algo difiere,
> manda `WITRAL_PARA_CLAUDE.md`. El número exacto de tools se ve con `tool_search`,
> no acá.

## Índice
- El eje `donde` y los lugares
- Archivos
- Edición y verificación de sintaxis
- Búsqueda
- Copiar y desplegar entre lugares
- Base de datos (psql en un lugar)
- Git
- Ejecución y sistema (run, async, procesos, servicio)
- Red
- ADB / DataStore
- Gradle

---

## El eje `donde` y los lugares

- `donde="local"` → esta máquina (Windows). Valor por defecto.
- `donde="<lugar>"` → un servidor remoto por SSH (la sesión se abre una vez y se
  reutiliza). Ver los lugares con `lugares`.
- Un `donde` que no es un lugar conocido = destino nuevo → se aborta y se pide
  confirmación (nunca conexión a ciegas).

El **SO del lugar** (`windows`/`unix`) decide la sintaxis de las tools de sistema,
no si es local o remoto. Rutas relativas se resuelven contra la raíz del lugar
(en local, `Proyectos\`); absolutas se aceptan si caen dentro de esa raíz.

---

## Archivos

- `listar(ruta, donde)` — contenido de un directorio.
- `leer(archivo, desde, hasta, cola, donde)` — sin rango: archivo completo (chicos;
  con autodefensa para grandes). Con `desde`/`hasta`: ese tramo, numerado. Con
  `cola=N`: las últimas N líneas (logs; en remoto usa tail).
- `escribir(archivo, contenido, donde)` — crea o SOBRESCRIBE entero (solo texto).
- `subir_b64(archivo, contenido_b64, donde, anexar_trozo)` — escribe BYTES de base64:
  el puente para traer binarios/contenido grande desde afuera. `anexar_trozo=True`
  sube por partes.
- `anexar(archivo, contenido, donde)` — agrega al final sin reescribir todo.
- `convertir_eol(archivo, a, donde)` — pasa el archivo entero a `lf` o `crlf`
  (reescribe todo; para editar contenido no se usa, las tools de edición ya
  preservan el EOL).
- `crear_carpeta(ruta, donde)` · `mover(origen, destino, donde)` (dentro de un
  mismo lugar).
- `borrar(ruta, donde, confirmado)` — NO elimina: mueve a `.witral/papelera/`
  (recuperable). `vaciar_papelera(donde, confirmado)` — eliminación DEFINITIVA.

**Garantías de edición:** validación en dos fases (si algo falla, no escribe nada),
**backup automático** con rotación (`.witral/bak/`: 12 por archivo / 30 días), y
preservación del EOL original.

---

## Edición y verificación de sintaxis

- `editar_literal(archivo, viejo, nuevo, verificar, donde)` — reemplaza una
  ocurrencia EXACTA y ÚNICA. Falla si no aparece o aparece más de una vez. Inmune a
  CRLF. Ubica por CONTENIDO.
- `editar_linea(archivo, desde, hasta, nuevo, ancla, verificar, donde)` — reemplaza
  el rango `[desde, hasta]`. Ubica por POSICIÓN. Para UNA línea, omitir `hasta`
  (toma `desde`). El parámetro **`ancla`** (muy recomendado) edita solo si el
  contenido esperado coincide; si no, aborta sin tocar el archivo. Devuelve el
  fragmento resultante (±2 líneas). `verificar=True` corre `verificar_sintaxis` en
  el mismo viaje.
- `verificar_sintaxis(archivo, donde)` — dos capas: **universal** (balance de
  `()[]{}`, comillas/comentarios; todos los lenguajes, local y remoto) y **nativa**
  (JSON/YAML/TOML por librería Python local y remoto; y por binario en local:
  `node --check`, `py_compile`, `php -l`, `gcc -fsyntax-only`, `perl -c`, `ruby -c`).

Elegir: texto corto y único a la vista → `editar_literal`. Bloque por rango →
`editar_linea` con `ancla` (y `verificar=True` si es código).

---

## Búsqueda

- `buscar_nombre(proyecto, patron, donde)` — por NOMBRE de archivo (regex).
- `buscar_contenido(objetivo, patron, incluir, antes, despues, donde)` — grep de
  contenido (regex) en un ARCHIVO o CARPETA. Si es carpeta, recorre recursivo con
  `incluir` (globs; por defecto `*.kt *.java *.xml *.kts *.gradle`). `antes`/`despues`
  agregan líneas de CONTEXTO (como -B/-A de grep): el match llega con su entorno sin
  un `leer` posterior. Excluye `build`/`.gradle`/`.git`/`.witral`/`node_modules`/
  entornos Python (`.venv`, `__pycache__`, ...). Salida: `ruta:linea: texto`.

---

## Copiar y desplegar entre lugares

- `copiar(origen, destino, ...)` — copia un archivo entre lugares (SFTP). Forma
  COMPACTA: `origen="local:folil/web/app.py"`, `destino="wedwed:/srv/app/app.py"`
  (el prefijo es el lugar solo si es conocido; así `C:\...` y `/srv/...` no se
  confunden). También la forma explícita (origen_ruta/origen_lugar/destino_lugar/
  destino_ruta). Hacia un lugar sensible pide `confirmado=True`.
- `desplegar(origen, destino, servicio, prueba_url, espera, confirmado)` — copiar →
  reiniciar servicio → esperar → curl de humo, en UNA pasada. origen/destino en
  forma compacta; el servicio y la prueba corren en el lugar de DESTINO. Requiere
  `confirmado=True`; corta y reporta si un paso falla.

---

## Base de datos (psql en un lugar)

- `psql(donde, comando, base, confirmado)` — corre psql en el lugar (la base es
  local allí). El SQL viaja por **stdin**: con varias sentencias se muestran TODOS
  los result sets. Lectura libre; las escrituras piden `confirmado=True`. En lugar
  NO sensible con bloque MIXTO (SELECT + UPDATE), corre las lecturas y pide
  confirmación solo por las escrituras. `base` apunta a otra base del mismo lugar.
- `psql_aplicar(donde, ruta_sql, origen, base, confirmado)` — aplica un `.sql`
  (migraciones). Witral LEE el archivo (desde `origen`, por defecto el mismo `donde`)
  y lo manda por stdin al psql de la BASE: sirve para bases detrás de túnel cuyo psql
  no ve el filesystem local (ej. `origen="local"`, `donde="folil_porafuera"`).
  Siempre pide `confirmado=True`.

---

## Git

Sobre repos dentro de un lugar; soporta `donde`.

- **Lectura:** `git_status` (estado actual — primer paso, no inferir desde log),
  `git_log`, `git_diff`, `git_branch`, `git_show`.
- **Transporte:** `git_pull`, `git_fetch`, `git_add` → `git_commit` → `git_push`.
- **Atajo (recomendado):** `git_publicar(repo, mensaje, donde, rutas, excluir,
  empujar, forzar, confirmado)` hace status → add → diff → commit → push en UNA
  pasada, mostrando el diff antes del commit; lista los untracked NUEVOS y acepta
  `excluir` (pathspec) para dejar polizones afuera. `empujar=False` = commit local;
  al empujar pide `confirmado=True`.
- **Setup/identidad:** `git_clone(url, destino, rama, donde)`, `git_init`,
  `git_remote(repo, nombre, url)` (sin nombre/url lista los remotos), `git_identidad`.
- **Destructivo:** `git_reset_hard` (confirmación reforzada).

---

## Ejecución y sistema

- `run(comando, donde, confirmado, max_salida)` — comando arbitrario. Escotilla
  general; **siempre** `confirmado=True`. cwd = raíz del lugar. SOLO para comandos
  CORTOS (<~45s): el cliente MCP corta las llamadas largas.
- `run_async(comando, donde, confirmado)` — lanza un comando LARGO detached y
  devuelve un id. Estado en `.witral/jobs/<id>/`, sobrevive a reinicios.
- `run_status(id, donde, lineas)` — corriendo/terminado + código + últimas líneas
  (sin id: lista los trabajos). Lectura libre.
- `run_esperar(id, hasta_segundos, lineas, donde)` — BLOQUEA del lado de Witral
  hasta que el trabajo termine y devuelve su estado; reemplaza el polling con
  `sleep`. Se topa en ~40s por llamada (corte del cliente) y pide re-llamar si sigue.
- `run_matar(id, donde, confirmado)` — mata el árbol completo del trabajo.
- `procesos(donde, filtro)` — lista procesos (`tasklist`/`ps`). Solo lectura.
- `matar_proceso(patron, donde, confirmado)` — `taskkill`/`pkill`.
- `servicio(accion, nombre, donde, confirmado)` — status/start/stop/restart
  (`sc`/`systemctl`). `status` es lectura; el resto pide confirmación.

La sintaxis la decide el campo `so` del lugar (`windows`/`unix`), no si es local o
remoto.

---

## Red

- `ping(host, cuenta, donde)` · `tcp_socket(host, puerto, enviar, ...)`.
- `http_request(url, metodo, cuerpo, headers_json, params_json, donde, a_archivo,
  max_salida)` — petición HTTP desde un lugar. `params_json`: query params como JSON,
  percent-encodeados por Witral en Python (UTF-8) — la forma correcta de pasar
  no-ASCII (ü, ñ) en la URL, nunca curl por `run`. `donde`: la petición sale de ese
  lugar (curl remoto), para servicios en localhost del server. `a_archivo`: guarda
  el cuerpo en el lugar y devuelve solo status + tamaño + ruta (respuestas grandes).

---

## ADB / DataStore

Dos coordenadas: `donde` (qué máquina corre `adb`) y `serial` (qué dispositivo).

- `adb_devices(donde)`, `adb_shell(serial, comando, donde)`,
  `adb_install(serial, apk, donde)`, `adb_forcestop`, `adb_relanzar`.
- `adb_logcat(serial, tags, nivel, lineas, limpiar_antes, donde)` — logcat en modo
  dump (vuelca y sale), con filtro por tag/nivel, tail y opción de limpiar el buffer
  antes (flujo: limpiar → reproducir en el POS → capturar).
- `datastore_get(serial, paquete, archivo, donde)` — lista las claves (tipo y valor).
  Solo lectura.
- `datastore_set(serial, paquete, archivo, clave, valor, tipo, donde, confirmado)` —
  cambia UNA clave dejando el resto intacto. `tipo="auto"` respeta el tipo actual.
  Backup en `/sdcard` y force-stop antes de escribir; requiere `confirmado=True` y
  relanzar la app después. Requiere app debuggable (run-as).

---

## Gradle

- `gradle_build(proyecto, tarea, donde)` — compila con el `gradlew` del proyecto.
  En unix/remoto compila y devuelve la salida. En **local Windows NO compila** (el
  sandbox del cliente bloquea el loopback que Gradle/Java necesitan): el usuario
  compila en su terminal y Witral despliega el APK con `adb_install`.
