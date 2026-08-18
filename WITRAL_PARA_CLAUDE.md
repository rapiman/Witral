# WITRAL PARA CLAUDE

Documento unico de referencia para trabajar con **Witral**, el servidor MCP propio que
opera sobre la raiz de proyectos del lugar `local` (ver `lugares.json`) y sobre los
servidores remotos configurados.

> ESTE ARCHIVO VIVE EN UN REPO PUBLICO. No poner aca credenciales, IPs internas,
> hostnames privados ni detalles de defectos de apps de clientes. Lo especifico de
> una maquina o de un cliente va en `lugares.json` (gitignoreado) o en el canal del
> proyecto que corresponda.

Witral reemplaza al viejo puente `ejecutar.ps1`: ya no hace falta escribir `orden.json` ni
pedirle al usuario que corra un script. Claude llama las tools de Witral directamente.

> NOTA DE VIGENCIA: si en algun momento Witral deja de estar disponible o el entorno cambia,
> Claude debe AVISAR al usuario antes de asumir que estas tools existen. Mientras Witral
> responda, este archivo se considera vigente.

---

## 1. LA IDEA CENTRAL: lugares x acciones

Witral se organiza en **lugares** y **acciones**. Un *lugar* es una maquina con su propio
acceso, sus rutas y (opcional) su base de datos. Una *accion* se aplica a cualquier lugar
pasando el parametro `donde`.

- `donde="local"` -> la maquina del usuario (Windows). Es el valor por defecto.
- `donde="wedwed"` (u otro nombre configurado) -> un servidor remoto, vía SSH.

La gracia: la misma accion funciona igual en local y en remoto. `leer(archivo, donde="local")`
y `leer(archivo, donde="wedwed")` hacen lo mismo en distinta maquina. SSH es solo el transporte;
no hay que pensar en el. Para ver los lugares configurados, usar `lugares`.

**Rutas:** relativas se resuelven contra la raiz del lugar (en local, `Proyectos\`); absolutas
se aceptan si caen dentro de la raiz. Esto aplica de forma consistente a las tools de archivo,
a `adb_install` y a `psql_aplicar`.

**El SO del lugar** (`windows`/`unix`) decide la sintaxis de las tools de sistema, no si es
local o remoto. Un Windows remoto por SSH usaria `taskkill`; un Linux local usaria `pkill`.

---

## 2. CATALOGO DE TOOLS

### Archivos (eje `donde`)
- `leer(archivo, donde, desde, hasta, cola)` — sin parametros de rango: archivo completo
  (chicos; con AUTODEFENSA: si es grande devuelve el comienzo + totales + como seguir, no
  lo vuelca entero). Con `desde`/`hasta`: solo ese rango de lineas, numerado (forma
  correcta de mirar archivos grandes, y de ubicar numeros antes de editar por linea).
  Con `cola=N`: las ultimas N lineas (logs, resultados; en remoto usa tail).
- `escribir(archivo, contenido, donde)` — crea o SOBRESCRIBE el archivo entero. Para nuevos o
  chicos, SOLO TEXTO.
- `subir_b64(archivo, contenido_b64, donde, anexar_trozo)` — escribe BYTES decodificados
  de base64: el puente para traer binarios o contenido grande desde afuera (p. ej. el
  sandbox de analisis de Claude). Con `anexar_trozo=True` se sube por trozos.
- `anexar(archivo, contenido, donde)` — agrega texto al final sin reescribir todo.
- `convertir_eol(archivo, a, donde)` — convierte el fin de linea del archivo entero a `lf` o
  `crlf`. Para pasar archivos clonados en Windows (CRLF) a LF, o limpiar saltos mezclados.
  Reescribe todo el archivo (en git aparece como muchas lineas cambiadas). Para editar contenido
  NO se usa: las tools de edicion ya preservan el EOL.
- `listar(ruta, donde)` — contenido de un directorio.
- `crear_carpeta(ruta, donde)`.
- `mover(origen, destino, donde)` — mover/renombrar DENTRO de un mismo lugar.
- `borrar(ruta, donde, confirmado)` — NO elimina: mueve a `.witral/papelera/` con timestamp
  (recuperable). Requiere `confirmado=True`.
- `vaciar_papelera(donde, confirmado)` — eliminacion DEFINITIVA de la papelera.

### Edicion: dos modos
- `editar_literal(archivo, viejo, nuevo, donde)` — reemplaza una ocurrencia EXACTA y unica.
  Falla si no aparece o aparece mas de una vez. Inmune a CRLF (normaliza antes de comparar).
  Para texto corto que Claude ya tiene a la vista. Ubica por CONTENIDO (que texto).
- `editar_linea(archivo, desde, hasta, nuevo, ancla, linea, donde)` — reemplaza ese rango de
  lineas. Inmune a CRLF/whitespace. Ubica por POSICION (que lineas). Para UNA sola linea:
  pasar `linea=N` (alias comodo de desde=hasta=N) u omitir `hasta`. El parametro `ancla` (muy
  recomendado) es la red de seguridad: si lo pasas con el contenido que ESPERAS en esas lineas,
  edita solo si coincide; si no, ABORTA sin tocar el archivo y muestra esperado vs encontrado.
  Sin `ancla`, edita directo confiando en los numeros. **Pasa `ancla` siempre que puedas** —
  evita editar el lugar equivocado si se perdio la cuenta de lineas. El parametro `verificar=True`
  corre verificar_sintaxis tras editar y agrega el resultado en la misma respuesta (util al editar
  codigo: ahorra una llamada aparte).

Elegir: texto corto y unico a la vista -> `editar_literal`. Bloque por rango -> `editar_linea`
con `ancla` (y `verificar=True` si es codigo).

Garantias de edicion: validacion en dos fases (si algo falla, no escribe nada), **backup
automatico** con timestamp antes de tocar cada archivo — en local a `<raiz>/.witral/bak/`,
en remoto a `~/.witral/bak/` del lugar (nunca al lado del archivo: no ensucia repos), preserva el fin de
linea original (CRLF se mantiene). `editar_linea` devuelve el fragmento resultante (lineas
editadas +-2) para verificar en el acto sin un `leer` con rango aparte.

### Verificar sintaxis
- `verificar_sintaxis(archivo, donde)` — red rapida antes de mover o compilar. Dos capas:
  - **Universal** (siempre, todos los lenguajes): balance de `()[]{}`, comillas y comentarios
    sin cerrar, ignorando strings y comentarios. Atrapa el error de edicion mas comun. Local y remoto.
  - **Nativa**: para JSON/YAML/TOML valida con la libreria de Python (json, pyyaml, tomllib) y
    da linea/col del error — funciona local y remoto. Para lenguajes con binario instalado y en
    local, chequeo real (`node --check`, `py_compile`, `php -l`, `gcc -fsyntax-only`, `perl -c`).
  - Reconoce: kt, kts, java, c, h, cpp, js, jsx, ts, php, py, sql, html, xml, css, sh, rb, pl,
    json, yaml, yml, toml.
  - No reemplaza al compilador. Para Kotlin (sin verificador nativo posible) da solo la universal,
    que igual detecta el error de balance tipico.

### Busqueda
- `buscar_nombre(objetivo, patron, donde)` — busca por NOMBRE de archivo bajo `objetivo`: un
  proyecto o cualquier carpeta del lugar. Es REGEX (ej. `\.apk$`), pero si el patron no compila
  como regex y parece un glob (ej. `*.apk`) se interpreta como glob automaticamente, sin dar
  error. **El parametro se llama `objetivo`, igual que en `buscar_contenido`** (ronda 15): antes
  esta tool lo llamaba `proyecto` y la de al lado `objetivo`, dos nombres para el mismo
  concepto. `proyecto` se sigue aceptando como alias.
- `buscar_contenido(objetivo, patron, incluir, antes, despues, max_resultados, donde)` — grep
  de contenido (regex) en un ARCHIVO o una CARPETA/proyecto. Si `objetivo` es archivo, busca
  solo ahi (reemplaza al viejo `buscar_en_archivo`); si es carpeta, recorre recursivo con
  `incluir` (globs, ej. `*.kt`, excluye `build`/`.gradle`/`.git`/entornos). `antes`/`despues`:
  lineas de CONTEXTO antes/despues de cada match (como -B/-A de grep) — el match llega con su
  entorno sin un `leer` posterior; grupos separados con `--`, contexto con `-`. `max_resultados`
  (por defecto 200): tope de coincidencias; al alcanzarlo corta y avisa, para no devolver un
  muro (0 = sin tope). Salida sin contexto: `ruta:linea: texto`.

### Git (sobre repos dentro de un lugar; soporta `donde`)
Lectura: `git_status` (estado actual — primer paso antes de commitear, no inferir desde log),
`git_log`, `git_diff` (que cambio, la verificacion antes de sellar), `git_branch`, `git_show`.
Transporte: `git_pull` (traer), `git_add` -> `git_commit` -> `git_push` (publicar). `push` hace
`--set-upstream` solo si falta y acepta `forzar` (`--force-with-lease`).
**Atajo (recomendado):** `git_publicar(repo, mensaje, donde, ...)` hace status -> add -> diff
-> commit -> push EN UNA PASADA, mostrando el diff antes del commit. Reemplaza encadenar las
cinco a mano (ahorra llamadas). `empujar=False` para commit solo local; requiere `confirmado=True`
al empujar. Al agregar todo, los NUEVOS (untracked) se listan en la confirmacion y en la
salida; `excluir` (pathspec `:(exclude)`, separado por espacios) deja archivos sueltos
fuera del add — evita polizones del working tree.
Setup/identidad: `git_clone(url, destino, rama, donde)` (clona un repo; 'destino' no debe existir
aun y en local se acota a la raiz; 'rama' opcional con --branch; no pide confirmacion, es solo
descarga), `git_init`, `git_remote(repo, nombre, url)` (sin nombre/url lista los remotos;
con ellos agrega uno), `git_identidad(repo, identidad)` (fija el autor de los commits segun la
identidad nombrada en config; no toca remoto ni credenciales).
Destructivo: `git_reset_hard` (confirmacion reforzada).

**Flujo de commit:** lo normal es `git_publicar(repo, mensaje, confirmado=True)` en una sola
llamada. Si se quiere a mano: `git_status` -> `git_add` -> `git_diff` -> `git_commit` -> `git_push`.

### Ejecucion y sistema (segun el SO del lugar)
- `run(comando, donde, confirmado, max_salida, shell, segundos)` — comando arbitrario en un
  lugar. Escotilla general; pide `confirmado=True` SALVO que sea claramente de SOLO LECTURA
  en un lugar no sensible (allowlist: `git status/log/diff/show/branch`, `ls`, `dir`, `cat`,
  `findstr`, `grep`, `head`, `tail`, etc., encadenados solo con `&&`/`;`, sin redirecciones/
  pipes/background) — ahi corre sin confirmacion. El cwd es la RAIZ del lugar (rutas
  relativas predecibles). `max_salida` trunca la salida con aviso. Preferir las tools
  tipadas cuando existan.
  **`segundos`** (por defecto y tope 45, ronda 14): TOPE PROPIO de Witral, puesto por debajo
  del corte del cliente MCP (~60s). Al pasarse devuelve codigo 124 con el aviso de saltar a
  `run_async`, en vez de que la llamada muera sin salida ni diagnostico. Para trabajos
  largos, `run_async` desde el principio.
  **`shell`** (ronda 14): ahora **`"auto"` por defecto** — corre en cmd salvo que detecte una
  construccion con la que cmd pelea (alternacion `\|` de findstr, `%%` de los for, comillas
  dobles anidadas o escapadas, comillas simples como agrupador), y ahi envuelve en PowerShell
  avisandolo en la salida. Ademas, si cmd falla por SU sintaxis o por comando no reconocido
  Y el comando es de solo lectura, reintenta solo en PowerShell (repetir una lectura es
  inocuo). Con `&&` o `||` NUNCA desvia: PowerShell 5.1 no los soporta. `"powershell"`
  fuerza, `"cmd"` desactiva el desvio. Ya no hace falta caer a mano al PowerShell.
- `run_async(comando, donde, confirmado)` — lanza un comando LARGO en segundo plano
  (detached) y devuelve un id al instante. `run_status(id, donde, lineas)` — estado +
  codigo + ultimas lineas de out/err (sin id: lista los trabajos del lugar; lectura
  libre). `run_matar(id, donde, confirmado)` — mata el arbol completo del trabajo.
  Estado en disco en `.witral/jobs/<id>/` del lugar; sobrevive a reinicios.
- `run_esperar(id, hasta_segundos, lineas, donde)` — BLOQUEA del lado de Witral hasta que
  el trabajo termine y devuelve su estado final: reemplaza el polling manual con `sleep` +
  `run_status`. Vuelve al instante cuando el trabajo termina; como el cliente MCP corta las
  llamadas largas, cada llamada se topa en ~40s y, si sigue corriendo, pide volver a llamar
  `run_esperar(id)`. Lectura libre. **NO es el paso por defecto tras lanzar un trabajo**:
  lo natural es DEVOLVER EL CONTROL — seguir con otras tareas de la conversacion (editar,
  revisar, responder al usuario) y consultar despues con `run_status(id)`, que vuelve al
  instante sin bloquear. Encadenar `run_esperar` tras `run_esperar` solo cuando ya no queda
  nada util que hacer y solo falta el resultado.
- `procesos(donde, filtro)` — lista procesos (`tasklist`/`ps`). Solo lectura.
- `matar_proceso(patron, donde, confirmado)` — `taskkill`/`pkill`.
- `servicio(accion, nombre, donde, confirmado)` — status/start/stop/restart (`sc`/`systemctl`).
  `status` es lectura; el resto pide confirmacion.

### Copiar y desplegar entre lugares
- `copiar(origen, destino, ...)` — copia un archivo entre lugares (SFTP). Forma COMPACTA
  (recomendada): `origen="local:miapp/web/app.py"`, `destino="wedwed:/srv/app/app.py"` (el
  prefijo antes del `:` es el lugar, solo si es un lugar conocido; asi `C:\...` y `/srv/...`
  no se confunden). Tambien acepta la forma explicita (origen_ruta / origen_lugar /
  destino_lugar / destino_ruta). Hacia un lugar sensible pide `confirmado=True`.
- `desplegar(origen, destino, servicio, prueba_url, espera, confirmado)` — el patron mas
  repetido (copiar -> restart -> esperar -> curl de humo) en UNA llamada. origen/destino en
  forma compacta `lugar:ruta`; el servicio y la prueba de humo corren en el lugar de DESTINO.
  Requiere `confirmado=True` (escribe en server + reinicia servicio); corta y reporta si un
  paso falla.

### SQLite (un archivo, no un servidor)
- `sqlite(archivo, comando, donde, confirmado, maximo)` (ronda 15) — consulta un `.db`. Witral
  ya corre en Python, asi que usa el modulo `sqlite3` de la stdlib: no hace falta el cliente
  externo ni escribir `python -c "import sqlite3..."` a mano. Es el motor que aparece en el
  mundo Android: la base de una app, o un `.db` traido con `adb_pull(..., paquete=...)`.
  Sin `comando`, lista las tablas. Las LECTURAS abren el archivo en modo **solo-lectura**
  (URI `mode=ro`), asi una consulta no puede modificarlo ni por error; lo que escribe pide
  `confirmado=True`.

### Base de datos (cliente nativo del motor, en un lugar)
El MOTOR es un eje mas, igual que `donde`: sale de la config del lugar (`db.motor`) y
decide que cliente nativo se invoca. Soportados: **postgres** (`psql`) y **sqlserver**
(`sqlcmd`). `oracle` esta reservado (`sqlplus`) pero aun NO implementado: da error claro.
- `sql(donde, comando, base)` — consulta/sentencia sobre la base del lugar, con el
  cliente que corresponda al motor. Con varias sentencias en una llamada se muestran
  TODOS los result sets. `base` apunta a otra base del mismo lugar (override del -d).
  En lugar NO sensible con bloque MIXTO (SELECT + UPDATE), sin `confirmado` corre las
  LECTURAS de inmediato y pide confirmacion solo por las ESCRITURAS (se acabo el doble viaje
  por un SELECT escondido). Ante caida de conexion (WinError 10054, communication link
  failure), reintenta una vez SOLO las lecturas.
- `psql(...)` — alias historico de `sql`, comportamiento identico. Para codigo nuevo,
  preferir `sql`.
- Destructivo = UPDATE/DELETE/DROP/TRUNCATE/ALTER/INSERT/CREATE/GRANT/REVOKE y, para
  T-SQL, tambien MERGE/EXEC/EXECUTE/BACKUP/RESTORE. Un `EXEC` inofensivo pide
  confirmacion de mas: sale mas barato que escribir de menos.
- Una base SQL Server alcanzable por RED (VPN/LAN) se modela como un lugar **local** con
  bloque `db` apuntando al host: el cliente corre en esta maquina. No hace falta SSH.
- ATENCION sqlcmd: **ignora la codepage de entrada cuando el SQL va por stdin** y destroza el
  no-ASCII (una `ñ` entra rota y queda rota EN LA BASE). Por eso en local Windows Witral
  deja el SQL en un temporal UTF-8 con BOM bajo `<raiz>\.witral\tmp\`, lo pasa con `-i` y
  lo borra despues. No "arreglar" esto volviendo a stdin.
- `psql_aplicar(donde, ruta_sql, origen, base, confirmado)` — aplica un `.sql`
  (migraciones). Witral LEE el archivo (desde `origen`, por defecto el mismo `donde`) y
  manda el contenido por stdin al psql del lugar de la BASE: sirve para bases detras de
  tunel cuyo psql no ve el filesystem local (ej. `origen="local"`, `donde="dev_porafuera"`).
  Requiere que el lugar tenga config `db`.

### Puerto serie (generico)
- `serial_puertos(donde)` — lista los COM/tty visibles con su descripcion. Solo lectura.
- `serial_enviar(puerto, texto, baud, bits, paridad, stop, framing, ack, timeout, reintentos, hexa, donde)`
  — envia por el puerto y espera respuesta. **El encuadre es un PARAMETRO, no algo cableado:**
  `framing="crudo"` habla con cualquier equipo por serial (balanza, impresora, placa);
  `framing="stx_etx_crc16arc"` habla el encuadre tipico de los POS integrados de varios adquirentes y la
  herramienta **calcula el CRC al enviar y lo valida al recibir** — el que llama nunca ve un CRC.
  Se llama `arc` porque "CRC-16" a secas es ambiguo (ARC, MODBUS, CCITT y XMODEM diferen).
  Con `ack=True` acusa recibo con ACK y manda NAK si el CRC vino mal; **el POS espera ese ACK**,
  sin el reenvia la respuesta. Reenvia ante NAK hasta `reintentos` y corta ante EOT.
  `hexa=True` agrega el volcado hex de la trama. Tope 40s por llamada. Requiere `pyserial`.
  Pruebas del framing sin hardware: `witral/server/pruebas_puerto_serial.py`.

### Red
- `ping(host, donde)` · `tcp_socket(host, puerto, donde)`.
- `http_request(url, metodo, cuerpo, headers_json, params_json, donde, a_archivo,
  max_salida)` — peticion HTTP desde un lugar. `params_json`: query params como JSON;
  Witral los percent-encodea en Python (UTF-8), asi el no-ASCII (u con dieresis, enie)
  llega intacto sin pelear con el shell — **usar SIEMPRE esto para no-ASCII en la URL,
  nunca curl via `run`**. `donde`: la peticion sale de ese lugar (curl remoto); sirve
  para servicios que solo escuchan en localhost del server. `a_archivo`: guarda el cuerpo
  de la respuesta en esa ruta del lugar y devuelve solo status + tamano + ruta — **usar
  para respuestas grandes** (JSON de APIs, dumps): inline atascan el transporte MCP.
  Despues procesar el archivo con `leer`/`buscar_contenido`/`run`. `max_salida` acota el
  cuerpo inline (trunca con aviso). **`auth`** (ronda 14): autorizacion por NOMBRE de
  credencial, sin que el token pase por la conversacion — `"bearer:<cred>"`,
  `"token:<cred>"` (GitHub clasico), `"basic:<cred>"` (usa el usuario guardado en la
  credencial) o `"header:<Nombre>:<cred>"`. El valor se enmascara (····) en toda salida.

### Secretos (Credential Manager de Windows)
- `secreto(nombre, filtro)` — credenciales guardadas, POR NOMBRE y **sin exponer el valor
  nunca**. Sin `nombre`: lista las genericas disponibles. Con `nombre`: si existe, con que
  usuario, cuantos caracteres mide y cuando se escribio. Solo lectura, sin confirmacion.
- El valor se usa pasandolo por nombre a otra tool: hoy `http_request(..., auth="bearer:X")`.
  Eso reemplaza el patron de escribir un script de PowerShell descartable para leer un token
  del Credential Manager y probarlo (scripts que despues hay que acordarse de borrar).
- Guardar una credencial es cosa del usuario, una vez, en su terminal:
  `cmdkey /generic:<nombre> /user:<usuario> /pass`. Alternativa sin Windows: variable de
  entorno `WITRAL_SECRETO_<NOMBRE>`.


### Android / ADB
- `adb_devices(donde)` — lista dispositivos.
- `adb_shell(serial, comando, donde)` — comando en el dispositivo.
- `adb_install(apk, serial, donde, permitir_downgrade)` — instala un APK (acepta ruta relativa
  o absoluta). Encabeza la respuesta con `Dispositivo: <modelo> (serial <serial>)` — util cuando
  el POS cambia de serial entre pruebas y eso explica params/estado inesperados.
  `permitir_downgrade=True` (ronda 15) agrega `-d` para instalar sobre una build con
  versionCode MAYOR sin desinstalar ni perder datos. Los fallos vuelven TRADUCIDOS:
  `INSTALL_FAILED_VERSION_DOWNGRADE` explica que la comparacion es por versionCode y no por
  versionName, `UPDATE_INCOMPATIBLE` que la firma es otra, mas falta de espacio y equipo sin
  autorizar. El mensaje crudo de adb no dice ninguna de esas causas.
- `adb_estado_app(serial, paquete, donde)` (ronda 15) — que build esta instalada: versionName,
  versionCode, minSdk/targetSdk, firstInstallTime, lastUpdateTime, instalador, ruta del APK y
  si es debuggable. Es la pregunta natural DESPUES de cada install; a mano son varios
  `dumpsys package | grep` seguidos.
- `adb_pull(serial, remoto, destino, paquete, donde)` · `adb_push(serial, origen, remoto, donde)`
  (ronda 15) — traer y llevar archivos. Con `paquete`, el pull usa `run-as` para alcanzar el
  sandbox privado de una app DEBUGGABLE (donde `adb pull` directo no llega) y los bytes viajan
  CRUDOS por `exec-out`: un `.db` llega intacto en vez de destrozado por la decodificacion de
  texto. Despues se mira con `sqlite`.
- `adb_forcestop(serial, paquete, donde)` · `adb_relanzar(serial, paquete, donde)`.
- `adb_logcat(serial, tags, nivel, lineas, limpiar_antes, donde)` — captura logcat en modo dump
  (vuelca y sale, no streaming). `tags` filtra por tag separado por comas (ej.
  `NavMenuOperacion,Anulacion`); `nivel` minimo V/D/I/W/E; `lineas` para tail. `limpiar_antes`
  limpia el buffer para capturar solo lo nuevo (flujo: limpiar -> reproducir en el POS -> capturar).
- `adb_captura(serial, donde)` — captura la pantalla y devuelve la IMAGEN (PNG) directamente,
  en UNA llamada (sin screencap -> pull -> stage). Usa `exec-out screencap -p`.
- `adb_ui(serial, solo_clickeables, donde)` — `uiautomator dump` PARSEADO: por cada elemento
  con texto/content-desc/clickable, da el CENTRO (x,y) para tapear POR TEXTO en vez de por
  pixel, si es clickeable, su clase y resource-id. Si un boton se mueve, no te equivocas de
  coordenada. `solo_clickeables=True` filtra a los tapeables. Despues del centro, tapear con
  `adb_shell "input tap x y"`. (Para tapear en una sola llamada, ver `adb_tap_texto`.)
- `adb_tap_texto(serial, texto, timeout, parcial, confirmado, donde)` — busca el texto/desc,
  saca el centro y TAPEA, en UNA llamada (reemplaza volcado -> leer -> elegir -> tapear).
  ESPERA a que aparezca (no tapea al vacío) y sobrevive a que muevan el botón. Los textos de
  la LISTA NEGRA del POS (cierre de turno, anulación, borrar llaves, reversa, devolución) se
   niegan salvo `confirmado=True`. Acepta cadenas `+` (ver `adb_escribir`).
- `adb_escribir(serial, texto, donde)` — teclea una cifra en el teclado en pantalla con UN
  volcado (mucho más rápido que un tap por dígito). Tanto `adb_escribir` como `adb_tap_texto`
  aceptan SECUENCIAS con `+` sobre la MISMA pantalla: `escribir 4730+Continuar` (teclea y
  continúa), `tap 10%+Continuar` (dos taps). Cada segmento se teclea si es dígitos, o se tapea
  si es texto — un solo volcado para toda la cadena.
- `adb_esperar(serial, texto | patron_log, timeout, tags, donde)` — espera una CONDICIÓN en
  vez de un `sleep` adivinado. `texto`: hasta que aparezca en la UI. `patron_log` (regex):
  hasta que una línea de logcat matchee — determinista y barato para aserciones de tags
  estables (ej. `Scan C2C: code=00`). Tope 40s por llamada.
- `adb_guion(serial, archivo, paquete, desde, origen, donde)` — corre un GUIÓN de UI del lado
  del dispositivo y devuelve UNA línea si pasó; ante el primer fallo junta captura + textos de
  pantalla + logcat (barato cuando pasa, caro solo cuando falla). Verbos: `inicio` (estado
   conocido: logcat 16M/limpio + force-stop + relanzar), `limpiar_log` (igual pero SIN
   force-stop, rápido), `tap`, `escribir`, `permitir`, `esperar`,
  `esperar_log`, `verificar`, `no_debe`, `atras`, `captura`, `humano <mensaje>` (pausa y
  pide ese paso al usuario: tarjeta real, PIN fisico) y `pin` (teclea un PIN SIN valor de
  seguridad — clave de menu/config — SOLO con confirmado=True en la llamada). Guiones
  largos se pausan y devuelven `seguí con desde=K` (el cliente MCP corta las llamadas
  largas). VARIABLES `$nombre` resueltas con `valores="nombre=valor;..."`: el valor vive
  solo en la llamada (nunca en el .txt; en los `pin` se enmascara ···· en toda salida) y
  parametriza guiones (`escribir $monto+Continuar`). ALEATORIOS por corrida:
  `monto=rnd(10000,30000,500)` en valores (un valor compartido) o `$rnd(min,max,paso)` /
  `$rnd_opcion(10%,20%,30%)` inline (uno por ocurrencia); lo elegido queda en la traza y
  en la linea final. Ejemplo real: `guiones/menu_integraciones.txt` (menu -> Integraciones
  -> `pin $clave+Continuar`). Robustez: doble volcado contra pantallas en transición,
  match por texto/desc (los IDs en Compose vienen vacíos), y lista negra activa en los `tap`.

### DataStore (Jetpack Preferences de una app del dispositivo)
Las prefs de una app viven en `files/datastore/<nombre>.preferences_pb` (formato protobuf).
Estas tools lo decodifican/recodifican respetando los length prefixes. Vía `run-as`: requieren
app **debuggable** (en release no hay acceso).
- `datastore_get(serial, paquete, archivo, donde)` — lista las claves con su tipo y valor.
  Solo lectura. `archivo` con o sin `.preferences_pb` (ej. `indicators_data`). Util para
  inspeccionar parametros del POS.
- `datastore_set(serial, paquete, archivo, clave, valor, tipo, donde, confirmado)` — cambia UNA
  clave dejando el resto intacto. `tipo="auto"` detecta y respeta el tipo actual de la clave
  (string/int/long/bool/float/double); para crear una clave nueva hay que indicar tipo explicito.
  Pensado para alternar parametros en QA sin UI (ej. `operativa` REST/RETAIL). Hace backup en
  `/sdcard` y `force-stop` antes de escribir (DataStore cachea en memoria); requiere
  `confirmado=True` y **relanzar la app** (`adb_relanzar`) despues para que cargue el cambio.

### Build
- `gradle_build(proyecto, tarea, donde)` — compila con el `gradlew` del proyecto. En unix/remoto
  compila sincrono y devuelve la salida. **En local Windows compila como TRABAJO asincrono**:
  devuelve el id de inmediato; por mientras se puede seguir con otras tareas y mirar con
  `run_status(id)`, o `run_esperar(id)` si no hay nada mas; codigo 0 = BUILD SUCCESSFUL.
  (El fix del sandbox de la JVM ya NO vive aca sino en el transporte — ronda 15 — asi que
  `run` y `run_async` lo tienen igual: ver seccion 4.)
- `gradle_errores(job_id, donde, maximo)` (ronda 15) — los errores de compilacion del build en
  UNA llamada: las lineas `e:` de los logs del job, deduplicadas y en orden. Mira err.log Y
  out.log, porque **Gradle emite los errores de Kotlin por stderr**: estan en `err.log`, no en
  `out.log` (el mensaje anterior mandaba al log equivocado). Si no hay lineas `e:`, cae al
  bloque `FAILURE:` / "What went wrong" de Gradle, que es lo que explica los fallos que no son
  de compilacion.

---

## 3. QUE TOOL USAR PARA CADA COSA (resumen)

| Necesidad                                    | Tool                                  |
|----------------------------------------------|---------------------------------------|
| Crear archivo nuevo / reescribir chico       | `escribir`                            |
| Leer un rango de un archivo grande           | `leer` con `desde`/`hasta`            |
| Editar por texto exacto y unico              | `editar_literal`                      |
| Editar por rango (con red de seguridad)      | `editar_linea` con `ancla`            |
| Convertir saltos de linea LF/CRLF            | `convertir_eol`                       |
| Revisar sintaxis antes de mover/compilar     | `verificar_sintaxis`                  |
| Buscar contenido (archivo o proyecto)        | `buscar_contenido`                    |
| Buscar contenido con contexto (-B/-A)        | `buscar_contenido` con `antes`/`despues` |
| Buscar por nombre de archivo                 | `buscar_nombre`                       |
| Ver estado del repo                          | `git_status` (no `git_log`)           |
| Revisar cambios antes del commit             | `git_diff`                            |
| Cerrar el ciclo git (en una pasada)          | `git_publicar` (confirmado=True)      |
| Editar codigo y verificar de una             | `editar_linea` con `verificar=True`   |
| Instalar/relanzar app en el POS              | `adb_install` / `adb_relanzar`        |
| Capturar logs del POS filtrados             | `adb_logcat`                          |
| Ver la pantalla del POS (imagen)            | `adb_captura`                         |
| Tapear por texto (una llamada)              | `adb_tap_texto`                       |
| Esperar una condicion (UI o logcat)         | `adb_esperar`                         |
| Correr un guion de humo del POS             | `adb_guion`                           |
| Ver el arbol de vistas crudo                | `adb_ui`                              |
| Ver parametros (DataStore) del POS           | `datastore_get`                       |
| Cambiar un parametro del POS en QA           | `datastore_set` (confirmado=True)     |
| Ver si un trabajo termino (sin bloquear)     | `run_status(id)`                      |
| Esperar un trabajo cuando no queda mas nada  | `run_esperar(id)`                     |
| Desplegar (copiar+restart+humo) en una pasada| `desplegar` (confirmado=True)         |
| Copiar entre lugares (forma compacta)        | `copiar(origen="l:ruta", destino=…)`  |
| Ver/resolver conflictos de merge             | `git_conflictos` / `git_resolver`     |
| Compilar Android en local Windows            | `gradle_build` -> `run_esperar(id)`   |
| Consultar una base (cualquier motor)         | `sql(donde, comando)`                 |
| Comando arbitrario (ultimo recurso)          | `run` (siempre confirmado)            |
| Ver por que fallo un build                   | `gradle_errores(job_id)`              |
| Saber que build esta instalada en el POS     | `adb_estado_app(serial, paquete)`     |
| Instalar sobre una version mas nueva         | `adb_install(..., permitir_downgrade=True)` |
| Traer la base de una app del dispositivo     | `adb_pull(..., paquete=...)` -> `sqlite` |
| Mirar un archivo .db                         | `sqlite(archivo, "SELECT ...")`       |
| Comando que pelea con cmd (comillas, findstr)| `run` tal cual (`shell="auto"` ya desvia) |
| Usar un token sin exponerlo                  | `http_request(auth="bearer:<cred>")`  |
| Ver que credenciales hay guardadas           | `secreto()` (nunca muestra el valor)  |
| Leer un archivo grande sin pelear con el puente | stagearlo al sandbox (ver receta)  |

---

## 4. LIMITACIONES CONOCIDAS

- **Build de Gradle en local Windows: RESUELTO (ronda 10).** El famoso `Unable to
  establish loopback connection` NO era el loopback TCP: era el socket **AF_UNIX**
  que los pipes NIO de Java (JDK 16+) crean en el TMP, donde el sandbox del cliente
  MCP lo rompe con EINVAL (diagnostico: TCP loopback de Java funciona, AF_UNIX en
  Proyectos funciona, AF_UNIX en TMP falla). Fix: `JAVA_TOOL_OPTIONS` con
  `-Djdk.net.unixdomain.tmpdir=<raiz>\.witral\tmpjava`. **Desde la ronda 15 ese fix vive
  en el TRANSPORTE** (`transporte.entorno_jvm`), no en `gradle_build`: lo aplican por igual
  `run`, `run_async` y los trabajos. Antes estaba solo en `gradle_build`, y por eso el mismo
  `gradlew --stop` moria por `run` con "Unable to establish loopback connection" y funcionaba
  por `gradle_build` — una diferencia entre dos tools del mismo servidor que no habia forma
  de adivinar la primera vez. Segundo obstaculo (tambien resuelto): hay proyectos que fijan
  `kotlin.compiler.execution.strategy=in-process` en gradle.properties, que con
  metaspace 512m muere con OOM; `gradle_build` agrega
  `-Pkotlin.compiler.execution.strategy=daemon` (el daemon de Kotlin usa TCP
  loopback, que funciona bajo el sandbox). VERIFICADO EN VIVO: assembleDevDebug
  de un proyecto multi-modulo => BUILD SUCCESSFUL en 5m, 1258 tareas, APK generado
  (app-dev-debug.apk) — el ciclo editar -> compilar -> adb_install -> guion de
  humo queda entero dentro de Witral.
- **No hay verificacion real de tipos en Kotlin/Java.** `verificar_sintaxis` en `.kt` solo hace
  la capa universal (balance de llaves): NO detecta un tipo mal, un import faltante ni un `when`
  no exhaustivo. Un `kotlinc` de un solo archivo NO ayuda: sin el classpath del proyecto reporta
  "unresolved reference" en cada clase propia (falsos positivos). Compilacion real: el usuario en
  su terminal, o `gradle_build` en un lugar unix/remoto. **Mitigacion practica** para un cambio
  que toca varios puntos (ej. volver `portType` nullable): antes y despues, `buscar_contenido` del
  simbolo en el proyecto para enumerar TODAS las usos y no dejarse ninguno — no es un compilador,
  pero atrapa el "me falto un punto de uso".
- **Escapes `\uXXXX` en el input (Kotlin/Java/JSON).** La capa JSON/MCP DESESCAPA las secuencias
  unicode del argumento antes de que lleguen a Witral (Witral no desescapa nada). Si el archivo
   tiene una secuencia de escape unicode en el fuente (p. ej. los seis caracteres
   backslash-u-0-0-B-7, que Kotlin compila a un caracter no-ASCII) y vos anclas/buscas con
   ese caracter, no matchea: en el archivo estan los seis chars del escape, no el caracter.
   Anclar en texto ASCII adyacente, o editar por numero de linea sin incluir el escape.
  Las tools de edicion ahora detectan este caso y lo avisan en el error.
- **Tools nuevas solo aparecen en conversaciones nuevas.** Tras agregar o modificar tools, el
  usuario reinicia Claude Desktop (salir desde la bandeja, no solo cerrar la ventana) y conviene
  abrir conversacion nueva. Si una tool da "not loaded", llamar `tool_search` antes de usarla.
- **El `ssh.exe` de Windows NO funciona bajo el sandbox del cliente MCP.**
  `C:\Windows\System32\OpenSSH\ssh.exe` via `run` devuelve codigo 255 y CERO salida —
  ni siquiera `ssh -V`, ni redirigiendo stderr a un archivo. No es la red: `tcp_socket`
  al mismo host:22 conecta perfecto. **Usar el ssh que trae Git**:
  `C:\Program Files\Git\usr\bin\ssh.exe` (OpenSSH 9.7p1) funciona bien. Para no pelear
  con las comillas del espacio en "Program Files", usar la ruta corta 8.3
  `C:/PROGRA~1/Git/usr/bin/ssh.exe`. Se fija por repo con
  `core.sshCommand "C:/PROGRA~1/Git/usr/bin/ssh.exe -i <llave> -o IdentitiesOnly=yes"`.
  Descubierto en la instalacion de finoli (2026-08-07); aplica a cualquier maquina Windows.
- **`ssh-keygen` no se puede correr por `run`** (cmd sin TTY): devuelve codigo 0 y no genera
  nada. Se le pide al usuario en SU PowerShell, en forma interactiva (Enter dos veces para
  passphrase vacia). Atencion: `-N ""` desde PowerShell tampoco sirve — el argumento vacio se
  pierde antes de llegar al ejecutable nativo.
- **Comandos multilinea en `run`**: no funcionan. Encadenar con `&` en UNA sola linea, o
  usar `shell="powershell"`.

---

## 5. RECETARIO: que NO se puede, alternativas y uso correcto

Reglas practicas destiladas del uso real. Leer antes de improvisar.

**Trabajos largos (mas de ~45-60s).**
- NO SE PUEDE: correrlos con `run` — el cliente MCP corta la llamada (~60s) aunque el
  timeout interno sea mayor. Tampoco improvisar detach con `nohup … &` via run: a veces
  vuelve con -1 sin arrancar.
- ALTERNATIVA: `run_async` -> id -> polling con `run_status(id)` cada 20-60s ->
  `run_matar(id)` si hay que abortar. La salida queda en `.witral/jobs/<id>/` del lugar
  y sobrevive a reinicios: un trabajo lanzado en una conversacion se puede consultar
  desde otra.
- REGLA (pedido del usuario, 2026-08-05): tras lanzar un trabajo largo, lo NATURAL es
  devolver el control y hacer otras cosas por mientras (mas ediciones, revisar logs,
  responder); recien al necesitar el resultado, `run_status(id)` (no bloquea). Reservar
  `run_esperar(id)` para cuando no queda otra tarea util y solo falta que termine.
- ATENCION en Windows: `timeout /t` NO sirve dentro de un job (no soporta stdin redirigido);
  para esperas usar `powershell -NoProfile -Command "Start-Sleep N"`.

**Traer datos DESDE el sandbox de analisis de Claude a un lugar.**
- NO SE PUEDE: puente directo. El sandbox corre del lado de Claude y Witral en la maquina
  del usuario; no se ven entre si — todo pasa por el chat si o si.
- ALTERNATIVA: texto chico -> `escribir`. Binarios o contenido grande -> `subir_b64`
  (base64; con `anexar_trozo=True` en trozos de ~100-200 KB de base64 por llamada).
  Entre lugares -> `copiar` (SFTP). Del lugar hacia el chat -> `leer` con rango o `cola`.

**Archivos grandes (logs, TSV de resultados, dumps).**
- NO CONVIENE: `leer` sin rango (la autodefensa lo corta igual, pero gasta contexto).
- ALTERNATIVA: `leer cola=N` para el final; `leer desde/hasta` por tramos;
  `buscar_contenido` (regex, acepta archivo o carpeta) como grep; o procesar EN el lugar
  (`run` con awk/python) y traer solo el resumen.
- MEJOR TODAVIA cuando Claude corre EN LA NUBE (Cowork) y el archivo esta en local: no
  leerlo por rangos sobre el puente, sino **stagearlo de una** al sandbox de Claude
  (`device_stage_files` del puente de dispositivos) y ahi leerlo/grepearlo con las tools
  del sandbox, que no dependen del puente. Requiere que la carpeta este conectada
  (`device_request_folder_access` sobre la raiz de Proyectos, una vez por sesion). Un
  archivo de miles de lineas pasa a ser UNA transferencia en vez de N llamadas, y cada
  llamada de menos es una oportunidad de menos de que el puente se caiga en el medio.

**Respuestas HTTP grandes (APIs con JSON de cientos de KB).**
- NO SE PUEDE: traerlas inline — atascan el transporte MCP (timeout de 4 min).
- ALTERNATIVA: `http_request … a_archivo="ruta"` (guarda en el lugar, devuelve status +
  tamano + ruta) y despues `leer`/`buscar_contenido`/`run` sobre el archivo.

**No-ASCII (n con tilde, u con dieresis) en URLs o comandos.**
- NO SE PUEDE: armar la URL a mano o usar curl via `run` — el locale del shell la rompe.
- ALTERNATIVA: `http_request` con `params_json` (Witral percent-encodea en Python).

**SQL y migraciones.**
- Multi-sentencia en `psql` funciona (stdin, muestra todos los result sets).
- Migracion con el .sql local contra base detras de tunel:
  `psql_aplicar(donde="dev_porafuera", origen="local", ruta_sql=..., confirmado=True)`.
- Otra base del mismo lugar: parametro `base` (no tocar lugares.json).
- NO usar psycopg boilerplate: `psql_aplicar` ya lee el archivo y lo manda por stdin.

**Git.**
- Ciclo normal: `git_publicar` (pipeline con diff visible; lista los untracked NUEVOS y
  acepta `excluir` para dejar polizones afuera).
- Si el cliente cuelga el pipeline (paso intermedio largo o cliente inestable): cadena
  manual `git_add` -> `git_diff --cached --stat` -> `git_commit` -> `git_push`, que son
  llamadas cortas y confiables. SIEMPRE verificar con `git_status` antes de reintentar:
  la operacion pudo completarse aunque la respuesta se perdiera.

**Compilar Android en local Windows.**
- SE PUEDE (desde ronda 10): `gradle_build(proyecto, tarea)` lanza el build como
  trabajo asincrono con el fix del sandbox puesto (JAVA_TOOL_OPTIONS redirige el
  socket AF_UNIX de los pipes NIO fuera del TMP). Seguirlo con `run_esperar(id)`;
  codigo 0 = BUILD SUCCESSFUL; errores de Kotlin = lineas `e:` del out.log del job
  (`buscar_contenido` sobre `.witral/jobs/<id>/out.log` con patron `^e:`).
- Despues del build: `adb_install` + logcat/datastore/relanzar, como siempre.

**Consultar Sonar por archivo (SonarCloud).**
- Tool nativa (desde ronda 11): `sonar_archivo(ruta)` — issues abiertos de ESE archivo,
  formateados (L<linea> [SEVERIDAD] regla: mensaje), ordenados por severidad. Sin `ruta`:
  resumen del proyecto por severidad. `nuevos=True`: solo codigo nuevo. `proyecto` por
  `proyecto` sale de WITRAL_SONAR_PROYECTO o de systemProp.sonar.projectKey (nunca
  cableado en el codigo). Solo lectura, sin confirmacion, UNA llamada. El token
  se lee de ~/.gradle/gradle.properties (systemProp.sonar.token, el mismo de gradlew sonar).
- **`rama` (desde 2026-08-11): la rama de SonarCloud a consultar.** SIN ESE PARAMETRO SE
  RESPONDE POR LA RAMA POR DEFECTO del proyecto, que casi nunca es la que uno acaba de
  analizar. Si el analisis se subio con `gradle_build(proyecto, "sonar
  -Dsonar.branch.name=<rama>")`, hay que pasar la MISMA rama aca. La salida ahora SIEMPRE
  dice a que rama corresponden los numeros, incluso cuando no se pidio ninguna: no alcanza
  con poder pedir la rama correcta, hay que poder darse cuenta de que se leyo la equivocada.
  Motivo: paso en vivo — se analizo una rama de release dos veces, se consulto la tool, y las
  dos veces devolvio los mismos numeros de la rama principal. Lo unico que delato el
  problema fue que el total no se moviera despues de corregir dos issues.
- USO NATURAL: consultar el archivo ANTES de tocarlo (foto de sus issues) y re-consultar
  despues del proximo analisis. Para el usuario en terminal existe el equivalente
  `herramientas\sonar_archivo.ps1 <ruta>` (mismo comportamiento, -Nuevos, -Proyecto).
- ATENCION: SonarCloud refleja el ULTIMO analisis subido, no el working tree. Tras editar, los
  hallazgos nuevos y las lineas corridas recien aparecen al correr
  `gradle_build(proyecto, "sonar")` de nuevo (~5 min). Flujo: consultar antes de tocar ->
  editar -> compilar -> sonar -> re-consultar.

**Windows REMOTO por SSH.**
- NO SOPORTADO: la ejecucion remota asume shell POSIX (falla con mensaje claro).

**Si una llamada MCP cuelga 4 minutos sin respuesta.**
- Primero verificar con una tool LIVIANA de lectura (`lugares`, `git_status`) si el
  servidor sigue vivo y si la operacion en realidad se completo. NUNCA reintentar a
  ciegas una accion con efectos (commit, push, migracion) sin mirar el estado antes.
- Preferir llamadas cortas y de un solo paso cuando el cliente ande inestable.

**"The device is not connected to the bridge" (Claude en la nube, Cowork).**
- QUE ES: el puente entre el sandbox de Claude y el Claude Desktop del usuario, NO Witral.
  Witral esta corriendo del otro lado sin enterarse; el corte esta en el transporte. Por eso
  no hay reintento que Witral pueda hacer: cuando el puente se cae, la llamada ni siquiera
  llega. Reconecta solo, en general en segundos.
- QUE SI ES DE WITRAL, y esta cubierto: el estado de los trabajos vive en disco
  (`.witral/jobs/<id>/`), asi que un `run_async` lanzado antes del corte sigue corriendo y se
  consulta despues con `run_status(id)`, incluso desde otra conversacion. Los backups de
  cada edicion tambien estan en disco. Nada de lo que estaba en vuelo se pierde por el corte.
- COMO OPERAR: llamadas CORTAS y de un solo paso (cada llamada es una exposicion); despues de
  un corte, verificar con una tool liviana ANTES de reintentar algo con efectos; para lo
  largo, `run_async` en vez de `run` (el trabajo sobrevive al corte, la llamada no); para
  leer mucho, stagear al sandbox (ver "Archivos grandes") en vez de N lecturas por rango.
- El tope de 45s de `run` (ronda 14) juega para el mismo lado: un comando que se pasa vuelve
  como timeout limpio y accionable, no como una llamada que se muere sin decir nada.

**Siempre.**
- `run`/`run_async` piden `confirmado=True`: proponer el comando y esperar la confirmacion explicita.
- El cwd de `run`/`run_async` es la raiz del lugar: usar rutas relativas a ella.
- Editar codigo con `editar_literal`/`editar_linea` + `verificar=True` (chequeo de
  sintaxis en el mismo viaje); los backups quedan en `.witral/bak/`.

---

## 6. NOTAS DE OPERACION

- **Cambios de codigo de Witral requieren reinicio completo** de Claude Desktop para cargarse.
- **Backups con rotacion automatica**: `.witral/bak/` ya se poda solo (se conservan los 12
  mas nuevos de cada archivo y se borran los de mas de 30 dias, local y remoto). Los backups
  ESPEJAN la ruta relativa del archivo (`.witral/bak/<ruta>/<nombre>.<ts>.bak`), asi dos
  archivos con el mismo nombre en modulos distintos no colisionan. La
  **papelera** (`.witral/papelera/`) sigue creciendo con el uso: vaciarla de vez en cuando
  con `vaciar_papelera`.
- **Confirmaciones**: las acciones destructivas o de proposito general (`borrar`, `vaciar_papelera`,
  `run`, `matar_proceso`, `servicio` no-status, `git_push`, `psql_aplicar`) piden `confirmado=True`.
- **Filesystem como respaldo**: mientras Witral siga en desarrollo y autoeditandose, conviene
  mantener el conector Filesystem disponible como red de seguridad, por si un bug impide que
  Witral arranque y haya que editar su codigo desde afuera.
- **Repo**: el codigo de Witral vive en `Proyectos\witral\` y se publica en
  `https://github.com/rapiman/Witral`.

---

## 7. ESTADO Y PENDIENTES (para retomar desde otra conversacion)

### Ultima sesion (2026-08-18, ronda 15: bugs y fricciones de dos jornadas Android/POS)

Tres bugs, cuatro fricciones y una pasada de idioma. Validado con
`server/pruebas_ronda15.py` (45 aserciones, todas OK) y con `import witral.server` limpio.

1. **`run_esperar` ya no puede afirmar tres cosas incompatibles.** Reportaba a la vez "sin
   codigo y proceso no encontrado", `BUILD SUCCESSFUL` y "sigue CORRIENDO". Eran tres
   fuentes separadas: el estado miraba solo si existia el archivo `codigo`, el log se
   volcaba crudo, y el pie de "volver a llamar" se decidia por reloj. Ahora hay UNA funcion
   (`trabajos._diagnostico_local`) que decide entre `no_existe | corriendo | terminado |
   terminado_sin_codigo` mirando las tres entradas —archivo `codigo`, proceso vivo, cierre
   del log— y todo el texto se deriva de ella: si el estado es terminal, el pie de "volver a
   llamar" es inalcanzable. Cuando el proceso ya no esta y el log cierra en `BUILD
   SUCCESSFUL`/`BUILD FAILED`, se informa el codigo INFERIDO en vez de declarar "abortado".
   **Causa raiz encontrada**: en batch, invocar otro `.bat`/`.cmd` sin `call` TRANSFIERE el
   control y el script que llama nunca retoma — con el comando inline, `gradlew.bat`
   terminaba el wrapper y la linea que escribe `codigo` no llegaba a correr. El comando
   ahora va en su propio `.cmd` invocado con `call`.
2. **El fix del sandbox de la JVM se mudo al transporte** (ver seccion 4).
3. **Los errores de Kotlin estan en `err.log`, no en `out.log`** (Gradle los emite por
   stderr). Corregido el mensaje y, mejor, agregada `gradle_errores(job_id)` para no
   depender de recordarlo.
4. `adb_install(..., permitir_downgrade=True)` y traduccion de los fallos de install;
   `adb_estado_app`; `adb_pull`/`adb_push` (con `run-as` y bytes crudos); tool `sqlite`.
5. `buscar_nombre` recibe `objetivo`, igual que `buscar_contenido` (`proyecto` sigue como
   alias), y la descripcion de `run` dice explicitamente que en Windows el shell es cmd y no
   bash: no hay heredocs (`<<EOF` muere con "no se esperaba << en este momento"), y la
   alternativa es `shell="powershell"` con here-string o escribir el archivo aparte.
6. **Idioma**: pasada de voseo a español neutro sobre las descripciones de tools y la
   documentacion (96 reemplazos, script `server/neutralizar_idioma.py`, reejecutable). Las
   entradas viejas del CHANGELOG quedan como historia.

PENDIENTE: reinicio completo de Claude Desktop + conversacion nueva (hay tools NUEVAS:
`gradle_errores`, `adb_estado_app`, `adb_pull`, `adb_push`, `sqlite`, y `secreto` de la
ronda 14). Despues, verificacion en vivo y commit.

### Sesion anterior (2026-08-18, ronda 14: feedback de una sesion Android/POS)

Feedback de una sesion larga (git + gradle + adb + datastore + edicion). Lo que brillo
quedo como esta: `datastore_get/set`, `adb_captura` devolviendo la imagen, `adb_ui` con
coordenadas parseadas, `run_esperar`, `git_publicar`, el backup de cada `editar_literal` y
el eje `donde`. Lo que rozo, atendido asi:

1. **`run` con tope propio (`segundos`, por defecto y tope 45).** Antes `run` esperaba 120s
   internos mientras el cliente MCP cortaba a los ~60: el resultado era una llamada muerta.
   Ahora Witral corta primero y devuelve codigo 124 con el aviso de saltar a `run_async`.
2. **`shell="auto"` por defecto.** Detecta las construcciones con las que cmd pelea
   (alternacion `\|` de findstr, `%%`, comillas anidadas/escapadas, comillas simples como
   agrupador) y envuelve en PowerShell avisandolo; ademas reintenta en PowerShell si cmd
   fallo por SU sintaxis o por comando no reconocido y el comando era de solo lectura. Con
   `&&`/`||` nunca desvia (PowerShell 5.1 no los soporta) y `%VAR%` tampoco cuenta como
   pelea (ahi cmd es lo que se quiso). Ya no hace falta pasar `shell="powershell"` a mano.
3. **Tool `secreto` + `http_request(auth=...)`.** Lee el Credential Manager de Windows por
   ctypes (`CredReadW`/`CredEnumerateW`, sin dependencias) y NUNCA devuelve el valor: solo
   metadatos. El valor se usa por nombre en `auth="bearer:<cred>"` / `token:` / `basic:` /
   `header:<Nombre>:<cred>`, y se enmascara en toda salida. Cierra el hueco que llevaba a
   escribir scripts de PowerShell descartables para leer un token y probarlo.
4. **Puente inestable y archivos grandes**: no es codigo, es receta (seccion 5). El corte
   es del puente Cowork↔Desktop, no de Witral; lo que Witral aporta ya esta (estado de
   trabajos en disco, backups). La receta nueva es stagear el archivo grande al sandbox en
   vez de leerlo por rangos sobre un puente que se cae.

Validacion: `witral/server/pruebas_ronda14.py` (logica pura de `_motivo_powershell`,
`_envolver_shell`, `_huele_a_fallo_de_cmd`, parsing de `auth` y enmascarado, mas una
verificacion en vivo de que la estructura ctypes del Credential Manager responde).
PENDIENTE reinicio completo de Claude Desktop + conversacion nueva (hay una tool NUEVA,
`secreto`, y firmas cambiadas en `run`/`run_async`/`http_request`).

ATENCION: la copia `Proyectos\WITRAL_PARA_CLAUDE.md` (fuera del repo) quedo congelada en la
ronda 11. La version vigente es esta, la del repo.

### Sesion anterior (2026-08-11, ronda 13: motor sqlserver + lugares SAAM/EAM)

Witral pasa de "solo postgres" a "el motor es un eje". Detalle completo en el
CHANGELOG (ronda 13). Resumen operativo:

- Tool nueva `sql(donde, comando, confirmado, base)`; `psql` sigue como alias.
- Motor `sqlserver` via `sqlcmd` (ya venia instalado en finoli con las ODBC 170
  Tools). No hay cliente Oracle en la maquina y `oracle` NO esta implementado.
- Lugares nuevos, todos locales (el cliente corre aca, la base esta detras de la
  VPN): `saam` -> INFORDES (desarrollo), `saam_qas` -> INFORQAS y `saam_rep` ->
  INFORREP, estos dos marcados `sensible` para que toda escritura pida
  confirmacion. Mismo servidor SQL Server 2017.
- Contexto del trabajo: Infor EAM (hoy Hexagon EAM), el sistema de mantenimiento.
  El esquema `dbo` tiene 1.901 tablas, 1.239 con prefijo `R5`. Las ordenes de
  trabajo NO estan en una tabla `R5WORKORDERS`: viven en `R5EVENTS` (herencia de
  Datastream 7i).
- VERIFICADO contra la base real corriendo `basedatos.py` con el venv: lectura
  multi-sentencia, acentos intactos, error de SQL que corta el lote con codigo 1,
  heuristica de destructivo, override de `base`, y limpieza del temporal.
- PENDIENTE: reinicio completo de Claude Desktop + conversacion nueva para que
  aparezca la tool `sql`.

### Sesion anterior (2026-08-07, ronda 12: instalacion en la maquina finoli)

Witral instalado y andando en una SEGUNDA maquina (`finoli`, usuario Windows
`insan`), con el repo en `D:\Proyectos\witral` y la raiz del lugar local en
`D:\Proyectos`. Nada del codigo de Witral cambio; lo que se hizo fue instalacion,
y de ahi salieron varios hallazgos que ya quedaron en la seccion 4.

ESTADO: MCP cargando en Claude Desktop (67 tools), `lugares` responde los 8
lugares, git sincronizado con `origin/main` y push por SSH funcionando.

1. **`instalar_witral.ps1`** (nuevo, commit `305c6fb`, en la raiz del repo) —
   instalador idempotente de punta a punta: instala Python y uv si faltan (tres
   planes de respaldo, refresca el PATH desde el registro), reconstruye el `.git`
   si la carpeta vino de un ZIP, arma el venv, prueba que el servidor importe y
   cargue la config, y registra el MCP en `claude_desktop_config.json` con
   respaldo previo. Parametros `-Repo -Raiz -Url -Nombre -Email` con defaults
   derivados de `$PSScriptRoot`. `INSTALL.md` gana la seccion "0. Atajo".
2. **NO CORRER EL INSTALADOR ELEVADO.** La leccion mas cara. Corrido como
   administrador (por winget), todo lo que crea —`.git`, `.venv`— queda del grupo
   Administradores, y Claude Desktop corre SIN elevar: `git config` falla con
   "could not write config file .git/config: Permission denied" y el indice de git
   queda corrupto (los 29 archivos como modificados, con `git diff` VACIO y el SHA
   del working tree identico al del indice). Arreglo:
   `icacls "<repo>" /grant "<usuario>:(OI)(CI)F" /T /C /Q` elevado una vez, y
   despues reconstruir el indice —ni `update-index --refresh` ni `--really-refresh`
   alcanzan—: borrar `.git\index` y `git reset --mixed HEAD`.
   Recomendacion: winget aparte y elevado; el instalador, sin elevar.
3. **Convenciones para cualquier `.ps1`**: ASCII puro, CRLF y SIN here-strings.
   PowerShell 5.1 NO reconoce `@'...'@` en archivos con fin de linea LF y tira un
   error de parseo que senala lineas sin relacion con el problema real.
4. **`python -c "..."` por `run` llega destrozado**: PowerShell 5.1 se come las
   comillas dobles al pasar argumentos a un ejecutable nativo. Escribir un `.py`
   temporal con `WriteAllText` y ejecutar el archivo.
5. **El `python.exe` del PATH suele ser el alias del Microsoft Store**: un stub en
   `WindowsApps` que no es Python y escupe "no se encontro Python" por stderr (con
   `$ErrorActionPreference='Stop'` eso aborta el script). Filtrar por ruta y validar
   que la salida diga `Python 3`. Igual no es bloqueante: `uv` se baja su propio
   interprete con `uv python install 3.12`.
6. **`core.autocrlf` global en `true`** en esta maquina; el repo esta en LF, asi que
   necesita `core.autocrlf false` LOCAL en cada repo.
7. **Reconstruir un `.git` perdido sin mover nada**: NO clonar a `%TEMP%` y mover el
   `.git` (entre volumenes C: -> D: es copiar+borrar, y los objetos de git son de
   solo lectura => Access Denied). En su lugar, en la carpeta misma:
   `git init` -> `remote add origin` -> `fetch origin main` ->
   `reset --mixed origin/main` (no toca el working tree) -> `git branch -M main`
   (git init deja la rama en `master` y el `--set-upstream-to` falla en silencio).

PENDIENTE en finoli: copiar a la maquina las llaves .pem que referencia lugares.json;
traer los proyectos (`D:\Proyectos` solo tiene `Arduino`, `Videos` y `witral` — faltan
`guiones\`, `herramientas\` y el resto); verificar que `adb` este en el PATH.

### Ultima sesion (2026-08-05 pm, ronda 11: tool sonar_archivo)

1. **`sonar_archivo(ruta, proyecto, nuevos)`** — consulta SonarCloud por archivo (o resumen
   del proyecto sin ruta) en UNA llamada, salida compacta ya formateada. Logica en
   `red.py` (sonar_issues + _token_sonar), tool en `server.py`. Token desde el
   gradle.properties del usuario (nunca en el repo). Motivacion: ahorrarse el ciclo
   http_request -> a_archivo -> parseo por run (con confirmacion) cada vez que se quiere
   saber que tiene un archivo antes de tocarlo. Validado en vivo ejecutando red.sonar_issues
   con el python del venv (archivo real: 6 issues; proyecto: 615, 0 criticos).
   PENDIENTE reinicio de Claude Desktop + conversacion nueva para que la tool aparezca.
2. Contexto del dia: quedo `systemProp.sonar.token` en el
   gradle.properties del usuario, el permiso Execute Analysis otorgado en SonarCloud, y
   la rama de trabajo con 0 criticos/0 blockers (el detalle, en el canal del
   proyecto). Script gemelo para el usuario: `herramientas\sonar_archivo.ps1`.

### Sesion anterior (2026-08-05, ronda 10: feedback de la sesion Kotlin/merge)

A partir del feedback de una sesion de ~40 ediciones + 11 conflictos de merge.
Implementado y validado (verificar_sintaxis nativa OK en cada archivo + 21
tests de logica pura en sandbox: parser de conflictos con LF/CRLF/diff3/sin
cierre/marcadores anidados, ancla de bordes, roundtrip PowerShell). PENDIENTE
reinicio + conversacion nueva (hay tools NUEVAS). Cambios:

1. **`git_conflictos(repo, archivo?)`** — sin archivo lista los archivos en
   conflicto (diff-filter=U); con archivo muestra los hunks numerados con
   vista previa de ours/theirs (lados largos resumidos: primeras 4 + conteo +
   ultimas 2). Soporta diff3 (bloque base descartado).
2. **`git_resolver(repo, archivo, lado, hunk)`** — elige "ours"/"theirs"/
   "ambos" por hunk (o hunk=0: todos), con backup y EOL preservado. Cierra el
   pedido nº2 del feedback (los 11 conflictos a mano).
3. **Ancla de bordes en `editar_linea`**: una linea "..." en el ancla valida
   COMIENZO y FINAL del rango (ej. ancla="<<<<<<< HEAD\n...\n=======") — el
   pedido nº3 (borrar 178 lineas validando 2). HALLAZGO: el ancla PARCIAL de
   comienzo (menos lineas que el rango) YA EXISTIA pero no estaba documentado
   ni en la tool ni aca; ahora si.
4. **`verificar_sintaxis` honesto**: cuando no hay verificador nativo (.kt),
   el mensaje ahora enumera que NO detecta (referencias sin resolver, imports,
   tipos, campos borrados, suspend) y recuerda la mitigacion buscar_contenido.
   Era el punto de "confianza falsa" del feedback.
5. **`run`/`run_async` con `shell="powershell"`** (solo Windows): el comando
   viaja en base64 UTF-16LE via -EncodedCommand — cero peleas de escapado con
   cmd (findstr sin alternacion, %%, comillas, head/tr). Se escribe PowerShell
   normal y llega intacto.
6. **Verbo `humano <mensaje>` en `adb_guion`**: pausa el guion y pide ese paso
   al humano (tarjeta real, PIN fisico); se retoma con desde=siguiente. Era
   "idea casi gratis" de la ronda 8; el feedback lo daba por existente.
7. **Verbo `pin` + variables `$nombre`** (pedido del usuario): hay PIN sin
   valor de seguridad (menu/config del POS) que la maquina SI puede tipear,
   con una confirmacion explicita. `pin $clave` en el guion + `adb_guion(..., confirmado=True,
   valores="clave=1234")`: un confirmado habilita los pin de la corrida; sin
   el, el guion PAUSA en ese paso (reintentar con confirmado, o tipear a mano
   y desde=N+1). El valor vive SOLO en la llamada de la conversacion — ni en
   el .txt, ni en disco, ni en el repo — y se enmascara (····) en traza y
   fallos. Las variables tambien sirven para parametrizar guiones (`escribir
   $monto+Continuar`): si falta una, no se ejecuta nada y el error dice cual.
   Validado con 9 tests de logica pura (expansion, regex con $ intacta,
   deteccion de faltantes, enmascaramiento). El PIN de TARJETA en teclado
   seguro sigue siendo `humano`, siempre.
8. **Aleatorios por corrida** (pedido del usuario, QA con variedad): en
   'valores', `monto=rnd(10000,30000,500)` resuelve UN valor por corrida que
   todos los `$monto` comparten (consistencia entre pasos); inline en el
   guion, `$rnd(min,max,paso)` y `$rnd_opcion(10%,20%,30%)` ruedan un valor
   por ocurrencia (comillas opcionales, acepta espacios). Validacion previa:
   una expresion mal formada aborta ANTES de tocar el device. Lo elegido
   queda registrado en la traza y en la linea final del GUION OK
   ("Aleatorios: ..."); si una corrida con rnd se pausa, conviene reanudar
   fijando en 'valores' los ya resueltos. 18 tests de logica pura (rango,
   alineacion al paso, extremos alcanzables, invalidos, integracion con
   cadenas `+` y con `pin`).

RESUELTO (pedido nº1, compilar) — verificacion en vivo post-reinicio:
- git_conflictos/git_resolver probados contra un archivo real de 2 hunks (uno
  de 12 lineas resumido en la vista): listar, resolver hunk 2 theirs, resto
  "ambos", contenido final correcto, backups hechos.
- pin: sin confirmado PAUSA (valor ya enmascarado ····); con confirmado tipeo
  $1.500 en el teclado del POS, lo verifico en pantalla y lo borro — 4/4, el
  valor jamas aparecio en ninguna salida.
- rnd: montos y propinas aleatorios tipeados correctamente en ventas reales
  (las ventas no cerraron por WiFi caida del POS — "Network is not Available"
  en Nebula/MQTT —, no por Witral; la deteccion de variable faltante y el
  registro de aleatorios funcionaron).
- COMPILAR: el bloqueo historico de Gradle era el socket AF_UNIX de los pipes
  NIO de Java (JDK 16+) en el TMP (EINVAL bajo el sandbox; TCP y AF_UNIX en
  Proyectos funcionan — diagnostico con T.java minimo). Fix verificado:
  `-Djdk.net.unixdomain.tmpdir` via JAVA_TOOL_OPTIONS => `gradlew help`
  BUILD SUCCESSFUL dentro del sandbox, y despues el APK COMPLETO:
  assembleDevDebug de un proyecto multi-modulo en 5m (1258 tareas). Obstaculo 2: el
  proyecto fija kotlin in-process (OOM de metaspace con 512m); fix:
  `-Pkotlin.compiler.execution.strategy=daemon` (TCP loopback, funciona).
  `gradle_build` local Windows quedo cableado como job asincrono con ambos
  fixes (carga con el proximo reinicio).

### Ultima sesion (2026-08-04, ronda 9: tiempos de los guiones)

Revision de donde se va el tiempo en los guiones del POS. Implementado y
validado con verificar_sintaxis (nativa OK); PENDIENTE reinicio para cargar
(los .txt de guiones NO necesitan reinicio, se leen del disco en cada corrida):

1. **`esperar_log` bloqueante** (la mejora grande): primero un barrido inmediato
   del buffer (no pierde eventos ya logueados) y despues `logcat -e <regex> -m 1`
   BLOQUEANTE del lado del device — devuelve la linea en cuanto aparece
   (latencia de deteccion ~0, un round-trip), en vez del poll de 0.6s volcando
   2000 lineas por vuelta. La linea candidata se re-verifica con la regex de
   Python; fallback al poll clasico si el logcat no soporta -e/-m.
2. **Cadenas `+` en UN round-trip adb**: todos los segmentos del plan (taps y
   tecleos) encadenados en un solo shell del device, con `sleep 0.2` entre
   segmentos. Antes: un viaje adb por segmento.
3. **Polls de UI mas frecuentes**: sleep entre volcados 0.4s -> 0.2s en
   tap/cadena/esperar (el volcado de uiautomator ~1s sigue dominando).
4. **`inicio` sin doble espera**: sleep post-relanzar 1.5s -> 0.5s (el `esperar`
   del guion ya pollea la carga).
5. **Guiones**: `venta_dividir` colapsa esperar+escribir+tap en
   `escribir 4730+Dividir Cuentas` (la cadena exige todos los tokens en un
   volcado: es gate y accion a la vez, ~2 pasos menos); `venta_calculadora`
   suma `Pagar` a la cadena de la calculadora (un volcado menos).

Estimado por guion: 2-5s menos (lo irreducible sigue siendo el host bancario
~5-8s y el volcado de uiautomator ~1s). Nota: el fix atomico de
`_ejecutar_cadena` que la ronda 8 daba como "NO commiteado" ya estaba
commiteado y en main.

HALLAZGO EN VIVO (primer reinicio, commit `3e1f16f`): el esperar_log
bloqueante daba FALSO POSITIVO — adbd LOGUEA la linea de comando de cada
`adb shell` (tag ADB_SERVICES/adbd), asi que el `logcat -e <patron>` se
matcheaba A SI MISMO al instante (el guion dio por APROBADA una venta que
seguia en el PIN; los guiones igual pasaban porque el tap final tiene su
propia espera). Fix: el patron viaja en base64 y se decodifica en el shell
del device (`logcat -e \"$(echo B64 | base64 -d)\" -m 1`), y toda linea de
adbd se descarta (`_es_eco_adb`) en TODOS los matcheos de Python (barrido,
bloqueante y fallback) — tambien blinda contra ecos viejos de greps
manuales. Verificado contra el POS real: base64/$()/-e/-m funcionan.
MORALEJA generalizable: cualquier matcheo de logcat cuyo patron viaje en la
linea de comando de adb shell se auto-matchea via el eco de adbd.

VERIFICADO EN VIVO (segundo reinicio, los 4 guiones verdes): la captura
final de venta_dividir ahora muestra APROBADA (00) — el esperar_log espera
de verdad. Tiempos de pared (incluyen puente MCP; el host banco vario entre
5 y 25s entre corridas, asi que comparar con cuidado): venta_rapida 27s y
venta_calculadora 38s en UNA llamada; venta_dividir ~69s con una pausa
(host de la cuenta 1: 14s); venta_propina10 ~93s con dos pausas (cold start
~30s + host lento ~25s esa corrida). Sin pendientes de esta ronda.

### Ultima sesion (2026-08-04, ronda 8: guiones de venta en vivo + velocidad)

Se manejo un POS Android real de punta a punta. Guiones que
FUNCIONAN, guardados en `Proyectos\guiones\`:
- `venta_rapida.txt` — sin inicio, resultado por logcat. El mas rapido (5 pasos,
  una llamada). Para el dia a dia con la app abierta.
- `venta_propina10.txt` — con `inicio` (reset + resultado por logcat). Para
  despues de un build.
- `venta_calculadora.txt` — flujo desde la calculadora: computa el monto
  (`1500+Botón sumar+3230+Botón igual`), Pagar, propina, resultado.

Velocidad (todo implementado y en vivo): `adb_escribir`, cadenas `+`, UN volcado
por tap/esperar (antes doble), volcado de 1 round-trip adb, verbo `limpiar_log`,
`esperar_log` robusto (grep del buffer reciente), y sacar los `esperar`
redundantes. Lo unico irreducible es la latencia del host bancario (~5-8s).

PENDIENTE:
- ~~Bug `_ejecutar_cadena` (ejecucion parcial)~~ RESUELTO: el codigo en main ya
  valida que TODOS los tokens esten en el volcado antes de ejecutar nada (el
  "NO commiteado" de esta nota quedo viejo; verificado en ronda 9).
- **Guion de division de cuentas:** a medias. La calculadora tiene "Dividir
  Cuentas" (ademas de "Pagar"); falta explorar ese sub-flujo desde estado limpio.
- Ideas casi gratis: verbo `humano "<msg>"` (handoff de tarjeta+PIN en build
  real), guion de humo de los 5 menus (Menu principal: Operaciones/Comercio/
  Soporte/Integracion/Preferencias).

HALLAZGOS DE LA APP BAJO PRUEBA (no de Witral): la corrida dejo dos defectos de
la app cliente — un boton que queda inerte tras cierto camino de navegacion, y
crashes nativos al arrancar detectados por el monkey. El detalle va al dev por
el canal del proyecto, NO en este archivo: esto es un repo publico. Lo que
importa aca es la leccion de Witral: los guiones de UI sirven tambien como
deteccion de defectos de la app, no solo como regresion propia.

### Ultima sesion (2026-08-03, ronda 7: guiones de UI del POS)

Reorientacion del feedback: la variable a optimizar son TOKENS DE DECISION (lo
que cuesta mirar una pantalla para decidir el paso), no llamadas. La logica debe
correr del lado del dispositivo y devolver un veredicto. Tres tools + un modulo
nuevo (`guion.py`), validado con py_compile (el sandbox estaba caido para los
tests de logica pura; la logica replica patrones ya validados). PENDIENTE
reinicio y prueba en vivo. Cambios:

1. **`adb_tap_texto(serial, texto, ...)`** — tap por texto/desc en una llamada,
   con espera y lista negra del POS (confirmado=True para lo peligroso).
2. **`adb_esperar(serial, texto | patron_log, ...)`** — espera una condicion UI
   o una linea de logcat (regex, tags); mata el `sleep` adivinado.
3. **`adb_guion(serial, archivo, ...)`** — runner de guiones del lado del
   dispositivo; barato cuando pasa (una linea), captura+textos+logcat cuando
   falla. Verbos: inicio/tap/permitir/esperar/esperar_log/verificar/no_debe/
   atras/captura. Presupuesto por llamada con reanudacion (`desde=K`).
   Robustez incorporada: doble volcado (transiciones), match por texto/desc
   (IDs vacios en Compose), estado inicial forzado, logcat 16M/limpio en
   `inicio`, lista negra en los tap.

PENDIENTE / ideas del feedback aun no hechas: (a) snapshot del set de textos por
pantalla y diff entre corridas (barato, detecta rotulos que desaparecen; NO
cachea lo visual como el dibujo de la tarjeta); (b) verbo `captura` ya existe
como check visual opt-in. Tarjeta y PIN: siempre humano. El usuario ofrece
escribir el primer guion de humo (recorrer los cinco menus) cuando esto cargue.

### Ultima sesion (2026-08-03, ronda 6: automatizacion de UI del POS)

Dos tools nuevas pedidas para cerrar el loop de QA en el POS sin tapear por pixel.
Implementado y validado (py_compile + parseo de un dump uiautomator de ejemplo;
firma de `Image` confirmada contra el venv). PENDIENTE reinicio para cargar.

1. **`adb_captura(serial, donde)`** — screenshot directo como IMAGEN (PNG) en una
   llamada (exec-out screencap -p; local a memoria, remoto por temporal + SFTP).
2. **`adb_ui(serial, solo_clickeables, donde)`** — `uiautomator dump` parseado a
   texto/desc/clickable + CENTRO (x,y) para tapear por texto; luego
   `adb_shell "input tap x y"`.

### Ultima sesion (2026-07-30, ronda 5: fricciones de la sesion Kotlin/POS)

Feedback de una sesion de varias decenas de llamadas. Implementado y validado
(py_compile de cada archivo + tests de logica pura: glob/regex, tope de
resultados, allowlist read-only, pista de escape unicode, alias `linea`, espejo
de rutas de backup). PENDIENTE reinicio para cargar. Cambios:

1. **`buscar_contenido`**: `max_resultados` (tope 200 por defecto) con corte y
   aviso — se acabo el muro de matches.
2. **`buscar_nombre`**: si el patron no compila como regex y parece glob
   (`*.apk`), se interpreta como glob (antes: "nothing to repeat").
3. **`editar_linea`**: parametro `linea=N` (alias de desde=hasta=N) — no mas
   error de schema al llamar con `linea`.
4. **`run`**: allowlist de solo-lectura (git status/log/diff/show, ls, dir,
   findstr...) sin confirmacion, en lugares no sensibles, sin redirecciones/
   pipes/background.
5. **Backups espejan la ruta relativa** en `.witral/bak` (no mas colision por
   nombre entre modulos).
6. **`adb_install`** encabeza con modelo + serial del dispositivo.
7. **Pista de desescape `\uXXXX`**: las tools de edicion detectan cuando el
   ancla llega con el caracter y el archivo tiene el escape literal, y lo avisan.

NO implementado (con fundamento): verificacion real de Kotlin. Un `kotlinc` de
un solo archivo da falsos positivos sin el classpath del proyecto; el gradle
local Windows sigue bloqueado por el sandbox. Camino real: build en la terminal
del usuario o `gradle_build` remoto; mitigacion: `buscar_contenido` del simbolo
para enumerar usos antes/despues de un cambio transversal (ver seccion 4).

### Ultima sesion (2026-07-13, ronda 4: fricciones de la sesion de dos dias)

A partir del feedback de la sesion de dos dias (25 commits / ~40 migraciones,
sin perdida de datos). Implementado y validado (py_compile de cada archivo
tocado + tests de logica pura del splitter SQL, el parser `lugar:ruta`, el
armado de contexto de grep, la rotacion de backups y el lazo de `run_esperar`).
PENDIENTE reinicio para cargar y verificacion en vivo. Cambios:

1. **`run_esperar(id, hasta_segundos, lineas, donde)`** — bloqueo del lado
   servidor, adios al polling con `sleep 44`. Se topa en ~40s por llamada
   (limite del cliente) y pide re-llamar si sigue.
2. **`desplegar(origen, destino, servicio, prueba_url, espera, confirmado)`** —
   copiar -> restart -> esperar -> curl de humo en una pasada.
3. **`buscar_contenido`**: `antes`/`despues` (contexto -B/-A).
4. **`copiar`**: forma compacta `origen="local:ruta"`, `destino="wedwed:/ruta"`.
5. **`psql`**: bloque mixto en lugar no sensible corre lecturas y confirma solo
   escrituras; reintento de conexion solo para lecturas.
6. **Transporte remoto**: reintento ante conexion SSH cacheada muerta (WinError
   10054) SOLO si el comando no llego a correr (no duplica escrituras).
7. **`editar_linea`**: `hasta` opcional (una sola linea). **Rotacion de
   backups** en `.witral/bak` (12 por archivo / 30 dias).

PENDIENTE de la sesion (idea de futuro no implementada, esperar propuesta
concreta): "comandos guardados por proyecto" (`witral suite` que ya sepa cwd,
venv y timeout de un proyecto) — encaja con la seccion "Idea en carpeta".

### Ultima sesion (2026-07-09, ronda 3: buzon asincrono y puentes)

A partir del feedback de la sesion de las 873 requests. Implementado y probado en
contenedor (16/16 nuevas + 32/32 de regresion); PENDIENTE reinicio para cargar:

1. **Buzon asincrono** (`trabajos.py` nuevo): `run_async` -> id, `run_status(id)`,
   `run_matar(id)`. Estado en disco (`.witral/jobs/<id>/`), sobrevive reinicios. Era el
   freno nº1 (timeout del cliente ~60s con trabajos de 10 min).
2. **`subir_b64`**: puente sandbox->lugar para binarios/contenido grande, con
   `anexar_trozo` para subir por partes.
3. **`leer`**: `cola=N` y autodefensa con archivos grandes (freno nº4).
4. Nueva seccion 5 "RECETARIO" en este documento: que NO se puede y alternativas.

VERIFICACION EN VIVO (post-reinicio, Windows local): ciclo completo OK — lanzar,
CORRIENDO con pid, TERMINADO con codigo, salida UTF-8 capturada (echo/dir), matar con
arbol completo, y listado. HALLAZGO corregido en el acto: DETACHED_PROCESS dejaba mudas
a las console-apps (ping sin salida, powershell sin Write-Output); A/B confirmo que
solo CREATE_NO_WINDOW captura todo. El fix esta commiteado y carga en el PROXIMO
reinicio; hasta entonces, en jobs locales los comandos de cmd capturan bien pero
ping/powershell pueden salir vacios. Ademas: el remoto de witral paso a SSH con la
llave id_ed25519_rapiman (el HTTPS quedo sin token y el blindaje anti-prompt fallo
al instante con mensaje claro, como debia).

### Ultima sesion (julio 2026, ronda 2)

Gran ronda de fixes a partir de los dos feedbacks de uso intensivo. Implementado,
probado en contenedor (32/32 unit tests + prueba real con Postgres del incidente
libro/book) y PUBLICADO (commit `9f3c3f4`). Falta solo el reinicio completo de
Claude Desktop para que cargue, y la verificacion en vivo:

1. **psql por stdin**: multi-sentencia muestra TODOS los result sets (era la friccion
   nº1: la consulta doble oculto un SELECT y se duplicaron tablas). Nuevo parametro
   `base` en `psql` y `psql_aplicar`.
2. **psql_aplicar desacoplado**: nuevo parametro `origen` — Witral lee el .sql (de
   cualquier lugar) y lo manda por stdin al psql del lugar de la base. Sirve para el
   caso dev_porafuera (tunel sin filesystem) sin boilerplate psycopg.
3. **stdin remoto con EOF garantizado** (`shutdown_write` siempre): elimina los cuelgues
   de 4 minutos con comandos remotos que leen stdin. Timeout remoto ahora devuelve 124.
4. **Respuestas grandes**: `http_request` gano `a_archivo` (guarda el cuerpo en el lugar,
   devuelve status+tamano+ruta) y `max_salida`; `run` gano `max_salida`; truncado global
   en `_fmt` (40k) con aviso explicito.
5. **Mojibake resuelto**: salida de subprocesos locales decodificada UTF-8 primero (con
   fallback OEM); psql con `PGCLIENTENCODING=UTF8`. Adios "MigraciÃ³n".
6. **Blindaje git**: `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=never` — falla al
   instante en vez de colgarse esperando credenciales.
7. **git_publicar sin polizones**: lista los NUEVOS (untracked) en la confirmacion y en
   la salida; nuevo parametro `excluir` (pathspec `:(exclude)`).
8. **run con cwd fijo** en la raiz del lugar (rutas relativas predecibles).
9. **Host keys TOFU** en `~/.witral/known_hosts` (reemplaza AutoAddPolicy; cambio de
   clave => error con aviso de posible MITM). `look_for_keys` solo sin clave/password.
10. **Config fail-soft por lugar**: un lugar roto ya no borra a los demas.
11. **Guard Windows remoto**: falla con mensaje claro (shell POSIX asumido).

NOTA: durante esa sesion el cliente Desktop colgaba llamadas pesadas (run local con
python, Filesystem copy) 4 min sin devolver; las tools livianas de Witral respondian
bien. Por eso la verificacion se hizo en el contenedor de Claude, no con run local.

### Pendientes

1. **Menor**: `lugares.json` duplicado en `server/` y `server/witral/` (ambos
   gitignoreados; el de `server/` parece residuo — confirmar con el usuario y borrar
   uno para evitar divergencia).
2. **Verificado en vivo tras reinicio** (esta parte YA ESTA HECHA): psql multi-sentencia
   muestra todos los result sets contra wedwed y dev_porafuera; UTF-8 intacto en psql
   remoto ("Migración ñü") y en salida de git local (tildes del CHANGELOG bien); el SQL
   por stdin remoto no cuelga (EOF ok); TOFU activo (claves guardadas en la primera
   conexion post-reinicio); firmas nuevas (base/origen/excluir/a_archivo/max_salida)
   registradas. Falta probar en uso real: una migracion con
   `psql_aplicar(donde="dev_porafuera", origen="local")` y ver un commit con tildes
   en GitHub.
3. Cuelgues de 4 min del cliente con `run` local + python: CAUSA ENCONTRADA Y CORREGIDA
   (commit `9c12e1a`, carga en el PROXIMO reinicio). subprocess.run tras el timeout mataba
   solo al hijo directo (cmd.exe con shell=True); el nieto vivo sujetaba los pipes y el
   drenaje interno colgaba para siempre sin devolver ni el 124. Ahora Popen + matanza del
   arbol (taskkill /T /F en Windows, killpg en unix). Reproducido y verificado en
   contenedor: el escenario del nieto huerfano devuelve 124 en ~timeout. El mojibake del
   mensaje de commit quedo ademas verificado en vivo: git_publicar mostro "árbol" y
   "código" perfectos en la salida del commit `9c12e1a`.

### Idea en carpeta

- **Ejecucion asincrona ("buzon")**: comandos largos estilo lanzar/consultar/matar.
  El intento con `schtasks` se revirtio (el sandbox del cliente MCP bloquea loopback y
  no ejecuta tareas de forma fiable). Camino prometedor: proceso watcher que el usuario
  lanza desde SU terminal (fuera del sandbox) y que ejecuta comandos que Witral deja
  en un archivo. Retomar solo con propuesta concreta del usuario.

## Git local: autenticación GitHub (arreglado 2026-07-07)

El push local a otro repo propio murió con 401: el remoto HTTPS estaba anclado
a rapiman@github.com pero el credential manager de Windows solo tenía
tokens de jprapiman, y la llave SSH histórica de la máquina
(~/.ssh/id_ed25519) está asociada a la cuenta jprapiman (GitHub responde
"Repository not found" al repo de rapiman con ella).

Arreglo definitivo (mismo esquema que wedwed):
- Llave dedicada ~/.ssh/id_ed25519_rapiman, registrada en la cuenta
  rapiman de GitHub (título "local").
- El repo afectado quedó: remoto git@github.com:rapiman/<repo>.git y
  core.sshCommand "ssh -i C:/Users/jprapiman_transforma/.ssh/id_ed25519_rapiman
  -o IdentitiesOnly=yes" (IdentitiesOnly evita que ofrezca primero la
  llave de jprapiman).
- La llave vieja id_ed25519 sigue intacta para lo de jprapiman.
- Si se clona OTRO repo de la misma cuenta en local, repetir el core.sshCommand
  en ese repo (la config es por-repo, a propósito).

En la máquina finoli (2026-08-07) se repitió el esquema con llave PROPIA de
esa máquina, no copiando la privada:
- `~/.ssh/id_ed25519_rapiman` sin passphrase, generada ahí y registrada en la
  cuenta rapiman con título "finoli". Una llave por máquina: si se pierde el
  equipo se revoca esa sola.
- El repo witral quedó: remoto git@github.com:rapiman/Witral.git y
  core.sshCommand "C:/PROGRA~1/Git/usr/bin/ssh.exe
  -i C:/Users/insan/.ssh/id_ed25519_rapiman -o IdentitiesOnly=yes".
- ATENCION: ahí el binario NO es el ssh de Windows sino el que trae Git, porque el
  de System32 no funciona bajo el sandbox del cliente MCP (ver sección 4). La
  ruta corta 8.3 PROGRA~1 evita las comillas anidadas del espacio en
  "Program Files", que se pierden al pasar por cmd/PowerShell.

Diagnóstico útil si vuelve a fallar: `ssh -T git@github.com` dice con qué
cuenta autentica la llave por defecto; `cmdkey /list | findstr github`
muestra los tokens HTTPS. ATENCION Witral: ssh-keygen y otros comandos que
piden input interactivo NO funcionan vía run local (cmd sin TTY) — esos
se le piden al usuario en PowerShell.

## Verificación en vivo ronda 2 (2026-07-07)

Tras el reinicio de Claude Desktop quedaron VERIFICADOS: stdin remoto con
EOF (test: `timeout 3 cat` en wedwed vuelve al tiro con código 0 — antes
colgaba 4 min), psql multi-sentencia (3 SELECT -> 3 result sets; el fallo
visto en la madrugada del 07 fue ANTERIOR al reinicio), mojibake de
commits resuelto, psql pide confirmación en UPDATE/DDL, psql_aplicar con
origen='local' usado en las migraciones 028-036 sin boilerplate, y
git_publicar lista los untracked NUEVOS. Sin probar aún: max_salida y
a_archivo de http_request. Sigue abierto: el misterio de cuelgues de 4 min
del run LOCAL con redirecciones/pipes complejos (ssh-keygen interactivo,
captura de stderr de ssh) — regla práctica: lo interactivo se pide al
usuario en PowerShell; capturas complejas, mejor a archivo y leer.
