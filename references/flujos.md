# Flujos

Recetas compuestas para los casos reales. No son tools: son secuencias de acciones
atómicas (ver `references/acciones.md`) que se arman en el momento, paso a paso,
mostrando cada uno. Adaptar al caso; no ejecutar a ciegas. Confirmar los pasos que
cruzan a un server, y reforzar la confirmación en lugares sensibles.

## Promover una migración de base de datos

Objetivo: un cambio de esquema/datos hecho en local llega y se aplica en un server.

1. **Local** — crear/editar el `.sql` de migración (`escribir` / `editar_*`,
   `donde=local`).
2. **Aplicar** — con `psql_aplicar`, que LEE el archivo y lo manda por stdin al psql
   del lugar de la base (no hace falta copiar el `.sql` primero):
   - Base local del server: `psql_aplicar(donde="<server>", ruta_sql=..., origen="<server>", confirmado=True)`.
   - Base detrás de túnel cuyo psql no ve el filesystem local: dejar el `.sql` en
     local y `psql_aplicar(donde="folil_porafuera", origen="local", ruta_sql=..., confirmado=True)`.
3. **Verificar** — `psql(donde="<server>", "\dt")` o un `SELECT` de control (lectura
   libre). Con `base="<otra>"` se apunta a otra base del mismo lugar.
4. **Repetir** en el siguiente entorno. En lugares **sensibles** (prod): mostrar el
   contenido del `.sql` y el entorno, y esperar el OK explícito antes de aplicar.

Notas:
- Si el `.sql` es grande, revisarlo antes con `leer` (con rango) / `buscar_contenido`
  en vez de cargarlo entero.
- Multi-sentencia en `psql` muestra todos los result sets. En un bloque mixto
  (SELECT + UPDATE) en lugar no sensible, `psql` corre las lecturas y pide
  confirmación solo por las escrituras.
- Nunca aplicar en un entorno sensible sin haber verificado en uno de prueba primero.

## Promover archivos web / desplegar un servicio

Camino corto con `desplegar` (copiar → restart → esperar → curl de humo en una
llamada):

- `desplegar(origen="local:folil/web/app.py", destino="folil:/srv/app/app.py", servicio="<servicio>", prueba_url="http://127.0.0.1:8000/health", confirmado=True)`

El servicio y la prueba de humo corren en el lugar de destino (la `prueba_url` puede
apuntar a localhost del server, porque el curl sale desde ahí). Si un paso falla,
`desplegar` corta y lo reporta.

A mano, si se quiere granularidad:
1. **Editar** los archivos (`editar_*` / `escribir`, `donde=local`).
2. **Copiar** — `copiar(origen="local:...", destino="<server>:...", confirmado=…)`.
3. **Reiniciar** — `servicio("restart", "<servicio>", donde="<server>", confirmado=True)`.
4. **Verificar** — `http_request(url, donde="<server>")` o `leer` de un log.

## Leer o extraer de un `.sql` (o log) grande

Sin tool dedicada: combinar acciones de archivo, sin cargar el archivo entero.

1. `buscar_contenido(archivo, "<marca de inicio>", antes=0, despues=3, donde)` → línea
   y contexto.
2. `leer(archivo, desde, hasta, donde)` para revisar ese tramo (avanzar por tramos).
   Para el final de un log: `leer(archivo, cola=N)`.
3. Para extraer a un archivo nuevo: `leer` el bloque y `escribir`/`anexar` al destino,
   por tramos si es muy grande.

## Trabajos largos (escaneos, builds, migraciones de minutos)

`run` no sirve: el cliente MCP corta las llamadas largas. Usar el buzón asíncrono:

1. `run_async(comando, donde, confirmado=True)` → devuelve un id al instante.
2. `run_esperar(id, donde)` — bloquea hasta que termine y devuelve el estado. Se topa
   en ~40s por llamada; si sigue corriendo, vuelve a llamarlo. (O `run_status(id)`
   para un vistazo puntual.)
3. `run_matar(id, donde, confirmado=True)` si hay que abortar.

La salida queda en `.witral/jobs/<id>/` del lugar y sobrevive a reinicios: un trabajo
lanzado en una conversación se puede consultar desde otra.

## Compilar y desplegar un artefacto (Java/Android)

1. **Compilar** — `gradle_build(proyecto, tarea, donde)`. En unix/remoto compila y
   devuelve la salida. En **local Windows NO compila** (sandbox): el usuario corre
   `gradlew assembleDebug` en su terminal.
2. **Instalar en el POS** — `adb_install(serial, apk, donde)`.
3. **Relanzar y capturar** — `adb_relanzar(serial, paquete, donde)` y
   `adb_logcat(serial, tags=..., limpiar_antes=True, donde)` (flujo: limpiar →
   reproducir el caso → capturar).
4. **Parámetros de QA** — `datastore_get` para inspeccionar y `datastore_set`
   (confirmado=True, + relanzar) para alternar un parámetro sin UI.
