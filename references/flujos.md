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
     local y `psql_aplicar(donde="dev_porafuera", origen="local", ruta_sql=..., confirmado=True)`.
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

- `desplegar(origen="local:miapp/web/app.py", destino="dev:/srv/app/app.py", servicio="<servicio>", prueba_url="http://127.0.0.1:8000/health", confirmado=True)`

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
   devuelve la salida. En **local Windows compila como trabajo asíncrono** (con los
   fixes del sandbox puestos solos: tmpdir AF_UNIX + estrategia daemon de Kotlin):
   devuelve un id, seguirlo con `run_esperar(id)` hasta el código final (0 = BUILD
   SUCCESSFUL). Si falla, los errores de Kotlin son las líneas `e:` del out.log:
   `buscar_contenido(".witral/jobs/<id>/out.log", "^e:")` los aísla.
2. **Instalar en el POS** — `adb_install(serial, apk, donde)`.
3. **Relanzar y capturar** — `adb_relanzar(serial, paquete, donde)` y
   `adb_logcat(serial, tags=..., limpiar_antes=True, donde)` (flujo: limpiar →
   reproducir el caso → capturar).
4. **Parámetros de QA** — `datastore_get` para inspeccionar y `datastore_set`
   (confirmado=True, + relanzar) para alternar un parámetro sin UI.


## Automatizar la UI del POS (tapear por texto, esperar, guiones)

La economía correcta: la decisión ocurre del lado del dispositivo y vuelve un
veredicto. Optimiza los tokens que cuesta mirar una pantalla para decidir el paso.

**Paso a paso (interactivo):**
1. `adb_captura(serial)` para ver la pantalla, o `adb_ui(serial)` para el árbol de
   vistas (centros de tap).
2. `adb_tap_texto(serial, "Continuar")` — tapea por texto en una llamada; espera a
   que aparezca y sobrevive a que muevan el botón.
3. `adb_esperar(serial, texto="Menú Comercio")` o
   `adb_esperar(serial, patron_log="Scan C2C: code=00", tags="Scan")` — espera una
   condición en vez de adivinar un `sleep`.

**Guión de humo (correr después de cada build):**
Un `.txt` con un verbo por línea. Barato cuando pasa (una línea); ante el primer
fallo devuelve captura + textos de pantalla + logcat. Ejemplo:

```
paquete    com.ejemplo.pos
inicio                          # estado conocido: logcat 16M/limpio, force-stop + relanzar
tap        Abrir menú principal
esperar    Menú Comercio
tap        Menú Comercio
verificar  Reportes
verificar  Cierre de Turno      # verificar que EXISTE no lo tapea
no_debe    Error
atras
```

Correr con `adb_guion(serial, "ruta/humo.txt", paquete="com.ejemplo.pos")`.
Guiones largos se pausan y devuelven `seguí con desde=K`. Un `tap` a un texto de la
lista negra (cierre de turno, anulación, borrar llaves...) se niega salvo que el
guión traiga `permitir <texto>` antes.

**Pasos humanos, PIN y variables:**
- `humano <mensaje>` pausa el guión y pide ese paso al usuario (tarjeta real, PIN
  en teclado seguro); se retoma con `desde=` cuando esté hecho.
- `pin $clave+Continuar` teclea un PIN *sin valor de seguridad* (clave de menú/
  config) — SOLO si la llamada trae `confirmado=True` (un dale habilita los pin de
  la corrida). El valor va en la llamada (`valores="clave=1234"`), nunca en el
  `.txt`, y se enmascara `····` en toda salida. Ejemplo real:
  `guiones/menu_integraciones.txt` (menú → Integraciones → clave → adentro).
- Variables `$nombre` en cualquier verbo, resueltas con `valores="nombre=valor;..."`
  (`escribir $monto+Continuar`): si falta una, el guión no ejecuta nada y dice cuál.

**Aleatorios por corrida (QA con variedad):**
- En `valores`: `monto=rnd(10000,30000,500)` — un valor por corrida, compartido por
  todos los `$monto` (consistente entre tipear y verificar).
- Inline: `escribir $rnd(10000,30000,500)+Continuar`,
  `tap $rnd_opcion(10%,20%,30%)+Continuar` — cada ocurrencia rueda el suyo.
- Lo elegido queda en la traza y en la línea final (`Aleatorios: ...`); una
  expresión mal formada aborta antes de tocar el device.

**Guión de venta (rápido) y buenas prácticas de velocidad:**
Un flujo completo (monto → propina → pago) con el resultado asegurado por logcat:

```
paquete    com.ejemplo.pos.dev
limpiar_log                     # logcat -G 16M + -c, rápido (sin el force-stop de inicio)
escribir   4730+Continuar       # teclea el monto y toca Continuar (misma pantalla, 1 volcado)
tap        10%+Continuar        # dos taps encadenados
esperar_log 40  Response Message = APROBADO   # resultado determinista por logcat
tap        Imprimir copia cliente
```

- **No pongas `esperar` redundantes:** `tap`/`escribir` ya esperan a que su target
  aparezca, así que cubren la transición de pantalla sin un `esperar` aparte.
- **Encadená con `+`** las acciones de una misma pantalla: un volcado en vez de varios.
- **El resultado por `esperar_log`** (una línea estable de la app, ej. `APROBADO`) es más
  barato y determinista que esperar el voucher fugaz; requiere `limpiar_log` (o `inicio`)
  antes, para no matchear una corrida anterior.
- **`inicio` vs `limpiar_log`:** `inicio` da estado conocido pero paga el cold-start
  (~30s de force-stop + relanzar); si la app ya está abierta, `limpiar_log` alcanza.

**Lo que NO se automatiza:** tarjeta y PIN (siempre humano) y el juicio visual
("¿se ve bien?"); para eso, el verbo `captura` es un check opt-in en los pasos que
importan, no en cada paso.
