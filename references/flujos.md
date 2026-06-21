# Flujos

Recetas compuestas para los casos reales. No son tools: son secuencias de acciones
atómicas (ver `references/acciones.md`) que se arman en el momento, paso a paso, mostrando
cada uno. Adaptar al caso; no ejecutar a ciegas. Confirmar los pasos que cruzan a un
server, y reforzar la confirmación en `prod`.

## Promover una migración de base de datos

Objetivo: un cambio de esquema/datos hecho en local llega y se aplica en dev y prod.

1. **Local** — crear/editar el `.sql` de migración (`escribir` / `editar_*`, `donde=local`).
2. **Mover a dev** — por la vía que corresponda:
   - Git: `commit` + `push` en local, luego `pull` en dev (`git`, `donde=dev`).
   - O directo: `copiar(local, dev)` del `.sql`.
3. **Aplicar en dev** — `psql(donde=dev, "psql -f <ruta>/migracion.sql")`. La base es
   local para dev; no se expone puerto. Mostrar qué se aplica y dónde.
4. **Verificar en dev** — `psql(donde=dev, "\dt")` o un `SELECT` de control.
5. **Promover a prod** — repetir 2–4 con `donde=prod`. **Confirmación reforzada** antes
   del `psql -f` en prod: mostrar el contenido del `.sql` y el entorno, y esperar el OK
   explícito del usuario.

Notas:
- Si el `.sql` es grande, revisarlo antes con `leer` (con rango) / `buscar_contenido` en
  vez de cargarlo entero.
- Nunca aplicar en prod sin haber verificado en dev primero.

## Promover archivos web (`/var/www/html`)

Igual al anterior, sin el paso de base.

1. **Local** — editar los archivos web (`editar_*` / `escribir`, `donde=local`).
2. **Mover** — `copiar(local, dev)` a la ruta web del lugar, **o** `pull` en dev si el
   sitio se sirve desde un repo.
3. **Verificar** — `ping(donde=dev)` al servicio, o `leer`/`http_request` para comprobar
   que el archivo quedó y responde.
4. **Promover a prod** — repetir con `donde=prod`. Copiar *hacia* prod pide confirmación
   mostrando qué archivos y a qué ruta.

## Leer o extraer de un `.sql` grande

Sin tool dedicada: combinar acciones de archivo.

1. `buscar_contenido(archivo, "<marca de inicio del bloque>", donde)` → número de línea.
2. `leer(archivo, donde, desde, hasta)` para revisar ese tramo (avanzar por tramos
   si hace falta, sin cargar el archivo entero).
3. Para extraer a un archivo nuevo: `leer` el bloque (con rango) y `escribir`/`anexar` al
   destino, por tramos si es muy grande.

Esto cubre el caso de sacar bloques (p. ej. una base concreta) de un volcado `.sql`
enorme, en local o en un server.

## Compilar y desplegar un artefacto (Java/Android)

Compuesto, para cuando aplique:

1. `gradle_task(proyecto, <tarea de build>, donde=local)` — compilar.
2. `copiar(local, <server>)` — subir el `.jar`/`.apk` resultante.
3. `ssh`/acción remota para reiniciar el servicio en el server (mostrar el comando).
4. `ping` / lectura de log en el server — verificar que levantó.

Si este flujo se repite igual muchas veces, recién ahí evaluar fijarlo; por defecto se
compone en el momento.
