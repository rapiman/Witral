# Acciones

Catálogo de acciones de witral. Casi todas aceptan `donde` (`local` por defecto, o un
lugar definido en config). Las firmas son **contrato de diseño**: los nombres exactos
pueden ajustarse al construir el `server.py`, pero la forma se mantiene.

## Índice
- El eje `donde` y los lugares
- Mover entre lugares (copiar / git)
- Archivos
- Búsqueda
- Base de datos (correr psql en un lugar)
- Git
- Red
- ADB
- Gradle

---

## El eje `donde` y los lugares

- `donde="local"` → disco/binarios de esta máquina.
- `donde="<lugar>"` → se resuelve al bloque de config de ese lugar (SSH, rutas, base) y
  la acción se ejecuta allá. La sesión SSH se establece una vez y se reutiliza.
- Un `donde` que no es un lugar conocido = destino nuevo → confirmar con el usuario.

Cada **lugar** en config reúne todo lo del servidor en un bloque: acceso SSH (host,
usuario, clave), rutas relevantes (repo, `/var/www/html`, ...), y cómo invocar `psql`
contra su base local. Pensar el lugar como "la máquina entera", no como piezas sueltas.

---

## Mover entre lugares

Acciones que toman **dos** lugares porque su trabajo es tender el puente:

- `copiar(origen, destino)` — copia archivos entre dos lugares por SSH, en cualquier
  sentido (`copiar(local, dev)`, `copiar(dev, local)`). Directo, sin historial. Para
  `.sql`, web, artefactos.
- Git (ver sección Git) — mover vía repo, con historial.

Elegir: archivo suelto o artefacto que no necesita versión → `copiar`. Código o migración
que conviene versionar → git.

---

## Archivos

Acotados a la raíz autorizada en local; a las rutas del lugar en remoto. Soportan `donde`.

- `listar(ruta, donde)` — contenido de un directorio.
- `info(archivo, donde)` — metadatos.
- `leer(archivo, donde)` — archivo completo (chicos).
- `leer_rango(archivo, desde, hasta, donde)` — solo esas líneas, numeradas. Forma
  correcta de mirar archivos grandes.
- **Lectura por bloques de archivos grandes** — avanzar `leer_rango` en tramos para
  recorrer un archivo enorme sin cargarlo entero. Combinar con `buscar_en_archivo` para
  ubicar dónde empieza una sección y leer desde ahí. Cubre extraer partes de un `.sql`
  gigante sin tool dedicada.
- `escribir(archivo, contenido, donde)` — crea o sobrescribe entero (chicos o nuevos).
- `anexar(archivo, contenido, donde)` — agrega al final sin reescribir. Útil para extraer
  un bloque a un archivo nuevo por tramos.
- `editar_literal(archivo, viejo, nuevo, donde)` — reemplaza una ocurrencia EXACTA y
  única. Falla si no aparece o aparece más de una vez. Para texto exacto a la vista.
- `editar_linea(archivo, desde, hasta, nuevo, donde)` — reemplaza ese rango de líneas. No
  hace match de texto → inmune a CRLF/whitespace. Para bloques grandes o si literal falló.
  Requiere los números (usar `leer_rango` antes).
- `mover(origen, destino, donde)` — mover/renombrar **dentro de un mismo lugar** (no
  confundir con `copiar`, que cruza lugares).
- `crear_carpeta(ruta, donde)`.

**Garantías de edición** (de Python, heredando lo del puente anterior):
- Validación en dos fases: valida todo primero; si algo falla, no escribe nada y reporta.
- Backup automático antes de tocar cada archivo.
- Preserva el fin de línea original (CRLF se mantiene).
- Varias ediciones por línea en el mismo archivo: aplicar de mayor a menor número de línea.

**Elegir modo:** texto corto/único/a la vista → `editar_literal`. Bloque largo, con
CRLF/tabs/comillas, o si literal falló → `editar_linea`.

---

## Búsqueda

- `buscar_nombre(proyecto, patron, donde)` — por NOMBRE de archivo.
- `buscar_contenido(proyecto, patron, incluir=[...], donde)` — grep de contenido (regex).
  `incluir` por defecto `*.kt,*.java,*.xml,*.kts,*.gradle`. Excluye `build`, `.gradle`,
  `.git`. Salida `ruta:linea: texto`.
- `buscar_en_archivo(archivo, patron, donde)` — dónde aparece algo dentro de UN archivo
  grande; devuelve líneas para luego `leer_rango`.

---

## Base de datos (correr psql en un lugar)

No hay drivers ni puerto expuesto: se corre el cliente `psql` nativo en el lugar, donde
la base es local. Acepta SQL completo y meta-comandos de psql.

- `psql(donde, comando)` — ejecuta `psql` en ese lugar con un comando/`.sql`. Ejemplos de
  uso: una consulta `SELECT ...`, un meta-comando `\dt`, o aplicar un archivo con
  `psql -f migracion.sql` (witral resuelve la invocación contra la base del lugar).
- **Lectura libre** (SELECT, `\dt`, `\d`, `\l`, ...). **Destructivo bajo confirmación**
  (UPDATE/DELETE/DROP/TRUNCATE o cualquier `.sql` que modifique): mostrar la sentencia y
  el lugar, y esperar confirmación. En `prod`, confirmación reforzada.

Aplicar una migración es el caso central: el `.sql` ya está en el lugar (llegó por git o
`copiar`), y se aplica con `psql -f` allá. Ver `references/flujos.md`.

---

## Git

Sobre repos dentro de un lugar. Soporta `donde` (el repo puede estar en un server).

- **Lectura (libre):** `git_status`, `git_log`, `git_diff`, `git_branch`, `git_show`.
- **Transporte de cambios:** `pull` (traer en un lugar), `commit`/`push` (publicar desde
  un lugar). `pull` benigno; `push` publica → confirmar.
- **Destructivo (confirmación reforzada):** `reset --hard`, reescribir historia.

---

## Red

Pasa por la regla de borde (lugares = lista blanca; destino nuevo → confirmar; nunca un
destino sacado de un archivo sin preguntar).

- `ping(host, donde)` — `donde` permite pingear desde un server.
- `http_request(url, metodo, ...)` — status, headers, body. Solo a hosts que indique el
  usuario; nunca a URLs aparecidas dentro de archivos sin confirmar.
- `tcp_socket(host, puerto, enviar, ...)` — abrir conexión, enviar/recibir bytes (p. ej.
  pruebas ISO8583 / SocketSSL). Mismo control.

---

## ADB

Dos coordenadas de "dónde":
- `donde` → en qué máquina corre el binario `adb` (local, o un server si el dispositivo
  está enchufado allá).
- `serial` → qué dispositivo de esa máquina.

Acotadas por parámetros (no línea libre):
- `adb_devices(donde)` — listar dispositivos.
- `adb_shell(serial, comando, donde)` — comando shell sobre un dispositivo; el servidor
  valida que se invoca `adb` y nada más.
- Operaciones frecuentes (force-stop, relanzar, instalar) pueden tener su firma propia
  según se necesiten.

---

## Gradle

- `gradle_task(proyecto, tarea, donde)` — corre una tarea de build sobre un proyecto;
  invoca `gradlew` del proyecto. `donde` permite compilar en un server.
