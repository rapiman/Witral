"""
Witral — servidor MCP. Modelo "lugares × acciones".

Punto de entrada con FastMCP (transporte stdio, para Claude Desktop). Cada tool
es una acción que acepta `donde` (lugar). La política de seguridad vive aquí:

- Destino desconocido (no está en config) => no se conecta; se devuelve un
  aviso para que el usuario confirme/agregue el lugar. Nunca conexión a ciegas.
- Operaciones destructivas (SQL que modifica, push, etc.) => requieren el flag
  `confirmado=True`, que el modelo solo debe pasar tras confirmar con el usuario.
- Lugares sensibles (prod) => confirmación reforzada para psql y copiar-hacia.

Cubre el flujo de migraciones (archivos, ssh, copiar, psql) más git, red, adb,
gradle y búsqueda. 36 tools.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import config as C
from . import archivos as A
from . import basedatos as DB
from . import copiar as CP
from . import transporte as T
from . import gitops as G
from . import red as R
from . import movil as M
from . import sistema as S
from . import busqueda as B
from .config import DestinoDesconocido
from .seguridad import RutaFueraDeRaiz

mcp = FastMCP("witral")

_cfg = C.cargar()


def _aviso_config() -> str:
    """Banner de config rota, o cadena vacía si todo bien."""
    if _cfg.error_config:
        return (
            "⚠️  CONFIG CON ERRORES — Witral arrancó solo con el lugar 'local'.\n"
            f"{_cfg.error_config}\n"
            "Corregí el archivo y reiniciá para recuperar los demás lugares "
            "e identidades.\n\n"
        )
    return ""


def _resolver(donde: str | None):
    """Resuelve un lugar o devuelve (None, aviso) si es destino desconocido."""
    try:
        return _cfg.resolver(donde), None
    except DestinoDesconocido as e:
        # Si la config está rota, ese es el motivo real de que falte el lugar.
        if _cfg.error_config and donde not in (None, C.LOCAL):
            return None, (
                _aviso_config() +
                f"Por eso el lugar '{donde}' no está disponible."
            )
        return None, (
            f"DESTINO DESCONOCIDO: '{donde}'. {e}\n"
            f"No se conectó. Confirmá con el usuario y agregá el lugar a la "
            f"config antes de reintentar."
        )


def _fmt(r: T.Resultado) -> str:
    """Formatea un Resultado de comando para devolver al modelo."""
    out = f"[código {r.codigo}]\n{r.salida}"
    if r.error:
        out += "\n--- stderr ---\n" + r.error
    return out


# --- Lugares ----------------------------------------------------------------

@mcp.tool()
def lugares() -> str:
    """Lista los lugares definidos (local + remotos). No expone secretos."""
    out = []
    for nombre in _cfg.nombres:
        lg = _cfg.resolver(nombre)
        tipo = "local" if lg.es_local else "remoto"
        sens = " [sensible]" if lg.sensible else ""
        out.append(f"- {nombre} ({tipo}){sens}")
    return _aviso_config() + "\n".join(out)


# --- Archivos ---------------------------------------------------------------

@mcp.tool()
def leer(archivo: str, donde: str = "local") -> str:
    """Lee un archivo completo (chicos). Para grandes usar leer_rango."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.leer(lg, archivo)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"


@mcp.tool()
def leer_rango(archivo: str, desde: int, hasta: int, donde: str = "local") -> str:
    """Lee líneas [desde, hasta] numeradas. Forma correcta de mirar archivos grandes."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.leer_rango(lg, archivo, desde, hasta)
    except (RutaFueraDeRaiz, FileNotFoundError, ValueError) as e:
        return f"error: {e}"


@mcp.tool()
def buscar_en_archivo(archivo: str, patron: str, donde: str = "local") -> str:
    """Números de línea donde aparece 'patron' (regex) dentro de un archivo grande."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.buscar_en_archivo(lg, archivo, patron)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"


@mcp.tool()
def escribir(archivo: str, contenido: str, donde: str = "local") -> str:
    """Crea o sobrescribe un archivo entero (chicos o nuevos)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.escribir(lg, archivo, contenido)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


@mcp.tool()
def anexar(archivo: str, contenido: str, donde: str = "local") -> str:
    """Agrega contenido al final de un archivo sin reescribirlo entero."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.anexar(lg, archivo, contenido)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


@mcp.tool()
def editar_literal(archivo: str, viejo: str, nuevo: str, donde: str = "local") -> str:
    """
    Reemplaza una ocurrencia EXACTA y única de 'viejo' por 'nuevo'. Falla si no
    aparece o aparece más de una vez. Backup automático, CRLF preservado.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.editar(lg, archivo, literales=[A.EdicionLiteral(viejo, nuevo)])
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"


@mcp.tool()
def editar_linea(archivo: str, desde: int, hasta: int, nuevo: str,
                 donde: str = "local") -> str:
    """
    Reemplaza el rango de líneas [desde, hasta] por 'nuevo'. Inmune a CRLF/
    whitespace. Usar leer_rango antes para ubicar los números. Backup automático.
    Devuelve el fragmento resultante para verificar en el acto.
    Si dudás de la cuenta de líneas, preferí editar_anclado (verifica el contenido).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.editar(lg, archivo, lineas=[A.EdicionLinea(desde, hasta, nuevo)])
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"


@mcp.tool()
def editar_anclado(archivo: str, desde: int, hasta: int, ancla: str, nuevo: str,
                   donde: str = "local") -> str:
    """
    Reemplaza el rango [desde, hasta] por 'nuevo', PERO solo si el contenido
    actual de esas líneas coincide con 'ancla' (comparación inmune a CRLF y a
    espacios al borde). Si no coincide, aborta sin tocar el archivo y muestra
    qué esperaba vs qué encontró. Es el modo de edición más seguro: une la
    inmunidad a CRLF de editar_linea con una red contra perder la cuenta de
    líneas. Preferilo cuando edites por número de línea. Devuelve el fragmento
    resultante para verificar en el acto.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.editar(lg, archivo,
                        ancladas=[A.EdicionAnclada(desde, hasta, ancla, nuevo)])
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"


@mcp.tool()
def listar(ruta: str = ".", donde: str = "local") -> str:
    """Lista el contenido de un directorio."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.listar(lg, ruta)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"


@mcp.tool()
def crear_carpeta(ruta: str, donde: str = "local") -> str:
    """Crea una carpeta (y sus padres)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.crear_carpeta(lg, ruta)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


@mcp.tool()
def mover(origen: str, destino: str, donde: str = "local") -> str:
    """Mueve o renombra DENTRO de un mismo lugar (para cruzar lugares: copiar)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.mover(lg, origen, destino)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"


@mcp.tool()
def borrar(ruta: str, donde: str = "local", confirmado: bool = False) -> str:
    """
    Borra un archivo o carpeta moviéndolo a la papelera (.witral/papelera) con
    timestamp; es recuperable, no definitivo. DESTRUCTIVO => requiere
    confirmado=True. La carpeta se borra con todo su contenido.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: vas a borrar '{ruta}' en '{donde}'.\n"
            f"Va a la papelera (recuperable), pero confirmá con el usuario y "
            f"reintentá con confirmado=True."
        )
    try:
        return A.borrar(lg, ruta)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"


@mcp.tool()
def vaciar_papelera(donde: str = "local", confirmado: bool = False) -> str:
    """
    Vacía DEFINITIVAMENTE la papelera de un lugar (esto sí es irreversible).
    Requiere confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: vaciar la papelera de '{donde}' es "
            f"DEFINITIVO e irreversible. Confirmá con el usuario y reintentá "
            f"con confirmado=True."
        )
    try:
        return A.vaciar_papelera(lg)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


# --- Mover entre lugares ----------------------------------------------------

@mcp.tool()
def copiar(origen_ruta: str, destino_lugar: str, destino_ruta: str,
           origen_lugar: str = "local", confirmado: bool = False) -> str:
    """
    Copia un archivo entre dos lugares (SFTP). Copiar HACIA un lugar sensible
    (prod) requiere confirmado=True tras confirmar con el usuario.
    """
    d, aviso = _resolver(destino_lugar)
    if aviso:
        return aviso
    o, aviso = _resolver(origen_lugar)
    if aviso:
        return aviso
    if d.sensible and not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: vas a copiar hacia '{destino_lugar}' "
            f"(sensible). Mostrá al usuario qué archivo ({origen_ruta}) y a qué "
            f"ruta ({destino_ruta}), y reintentá con confirmado=True."
        )
    try:
        return CP.copiar(_cfg, origen_lugar, origen_ruta, destino_lugar, destino_ruta)
    except (RutaFueraDeRaiz, FileNotFoundError, T.TransporteError) as e:
        return f"error: {e}"


# --- Ejecución de comandos (run) --------------------------------------------

@mcp.tool()
def run(comando: str, donde: str = "local", confirmado: bool = False) -> str:
    """
    Ejecuta un comando arbitrario en un lugar (local o remoto) y devuelve la
    salida. SIEMPRE requiere confirmado=True: es una escotilla de propósito
    general. Para operaciones comunes (archivos, git, procesos, servicios)
    preferí las tools tipadas con eje 'donde', que son más seguras y claras.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        extra = " (LUGAR SENSIBLE)" if lg.sensible else ""
        return (
            f"CONFIRMACIÓN REQUERIDA para ejecutar un comando en '{donde}'{extra}.\n"
            f"Comando: {comando}\n"
            f"Si existe una tool tipada para esto (borrar, editar, git_*, "
            f"matar_proceso, servicio, etc.) usala mejor. "
            f"Para continuar igual, reintentá con confirmado=True."
        )
    try:
        return _fmt(T.ejecutar(lg, comando))
    except T.TransporteError as e:
        return f"error: {e}"


# --- Sistema (procesos y servicios, por SO del lugar) -----------------------

@mcp.tool()
def procesos(donde: str = "local", filtro: str = "") -> str:
    """Lista procesos en un lugar. 'filtro' acota por nombre/patrón. Solo lectura."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(S.procesos(lg, filtro))


@mcp.tool()
def matar_proceso(patron: str, donde: str = "local", confirmado: bool = False) -> str:
    """
    Mata procesos cuyo nombre/línea coincide con 'patron' (taskkill en Windows,
    pkill en unix). DESTRUCTIVO => requiere confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: matar procesos que coincidan con "
            f"'{patron}' en '{donde}'.\n"
            f"Confirmá con el usuario y reintentá con confirmado=True."
        )
    return _fmt(S.matar_proceso(lg, patron))


@mcp.tool()
def servicio(accion: str, nombre: str, donde: str = "local",
             confirmado: bool = False) -> str:
    """
    Controla un servicio: status | start | stop | restart (systemctl en unix,
    sc en Windows). 'status' es lectura; start/stop/restart requieren confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if accion.lower() != "status" and not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: '{accion}' sobre el servicio '{nombre}' "
            f"en '{donde}'.\n"
            f"Confirmá con el usuario y reintentá con confirmado=True."
        )
    return _fmt(S.servicio(lg, accion, nombre))


# --- Base de datos (psql en un lugar) --------------------------------------

@mcp.tool()
def psql(donde: str, comando: str, confirmado: bool = False) -> str:
    """
    Corre psql en un lugar (la base es local allí). Lectura libre; sentencias
    destructivas (UPDATE/DELETE/DROP/TRUNCATE/ALTER/INSERT/CREATE) requieren
    confirmado=True. En lugares sensibles, cualquier ejecución pide confirmación.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    destructivo = DB.es_destructivo(comando)
    if (destructivo or lg.sensible) and not confirmado:
        razon = []
        if destructivo:
            razon.append("la sentencia modifica datos/esquema")
        if lg.sensible:
            razon.append(f"'{donde}' es sensible")
        return (
            f"CONFIRMACIÓN REQUERIDA ({'; '.join(razon)}).\n"
            f"Sentencia: {comando}\nLugar: {donde}\n"
            f"Mostrá esto al usuario y reintentá con confirmado=True."
        )
    try:
        return _fmt(DB.psql_comando(lg, comando))
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def psql_aplicar(donde: str, ruta_sql: str, confirmado: bool = False) -> str:
    """
    Aplica un archivo .sql con psql -f en un lugar (caso central de migración).
    El .sql ya debe estar en el lugar (llegó por git o copiar). Siempre requiere
    confirmado=True porque aplica cambios; reforzado en lugares sensibles.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        extra = " (LUGAR SENSIBLE — revisá el contenido del .sql primero)" if lg.sensible else ""
        return (
            f"CONFIRMACIÓN REQUERIDA para aplicar migración{extra}.\n"
            f"Archivo: {ruta_sql}\nLugar: {donde}\n"
            f"Mostrá al usuario qué se va a aplicar y reintentá con confirmado=True."
        )
    try:
        return _fmt(DB.psql_archivo(lg, ruta_sql))
    except Exception as e:
        return f"error: {e}"


# --- Git --------------------------------------------------------------------

@mcp.tool()
def git_status(repo: str, donde: str = "local") -> str:
    """Estado del repo (git status -sb)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.status(lg, repo))


@mcp.tool()
def git_log(repo: str, n: int = 15, donde: str = "local") -> str:
    """Últimos n commits (oneline)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.log(lg, repo, n))


@mcp.tool()
def git_diff(repo: str, args: str = "", donde: str = "local") -> str:
    """git diff (args opcionales separados por espacio, p. ej. 'HEAD~1')."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.diff(lg, repo, args.split() if args else None))


@mcp.tool()
def git_branch(repo: str, donde: str = "local") -> str:
    """Lista de ramas (git branch -vv)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.branch(lg, repo))


@mcp.tool()
def git_show(repo: str, ref: str, donde: str = "local") -> str:
    """Muestra un commit/objeto con estadísticas."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.show(lg, repo, ref))


@mcp.tool()
def git_pull(repo: str, donde: str = "local") -> str:
    """git pull --ff-only. Trae cambios en un lugar (transporte de cambios)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.pull(lg, repo))


@mcp.tool()
def git_fetch(repo: str, donde: str = "local") -> str:
    """git fetch --all. Actualiza refs remotas sin tocar el working tree."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.fetch(lg, repo))


@mcp.tool()
def git_add(repo: str, rutas: str, donde: str = "local") -> str:
    """git add (rutas separadas por espacio)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.add(lg, repo, rutas.split()))


@mcp.tool()
def git_commit(repo: str, mensaje: str, todos: bool = False, donde: str = "local") -> str:
    """git commit -m. Con todos=True agrega -a."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.commit(lg, repo, mensaje, todos))


@mcp.tool()
def git_push(repo: str, donde: str = "local", forzar: bool = False,
             confirmado: bool = False) -> str:
    """
    git push. Publica => requiere confirmado=True. Con forzar=True usa
    --force-with-lease (reescribe la rama remota; úsese con cuidado).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        modo = " (FORZADO: reescribe la rama remota)" if forzar else ""
        return (
            f"CONFIRMACIÓN REQUERIDA: git push publica cambios desde '{donde}'{modo}.\n"
            f"Confirmá con el usuario y reintentá con confirmado=True."
        )
    return _fmt(G.push(lg, repo, forzar))


@mcp.tool()
def git_reset_hard(repo: str, ref: str = "HEAD", donde: str = "local",
                   confirmado: bool = False) -> str:
    """git reset --hard. DESTRUCTIVO (descarta cambios) => requiere confirmado=True."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: reset --hard a {ref} DESCARTA cambios en '{donde}'.\n"
            f"Confirmá con el usuario y reintentá con confirmado=True."
        )
    return _fmt(G.reset_hard(lg, repo, ref))


@mcp.tool()
def git_init(repo: str, rama: str = "main", donde: str = "local") -> str:
    """Inicializa un repo git en 'repo' (el directorio debe existir) y fija la rama inicial."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.init(lg, repo, rama))


@mcp.tool()
def git_remote_add(repo: str, nombre: str, url: str, donde: str = "local") -> str:
    """Agrega un remoto al repo (git remote add <nombre> <url>)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.remote_add(lg, repo, nombre, url))


@mcp.tool()
def git_remote(repo: str, donde: str = "local") -> str:
    """Lista los remotos del repo con sus URLs (git remote -v)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.remote_list(lg, repo))


@mcp.tool()
def git_identidad(repo: str, identidad: str = "", donde: str = "local") -> str:
    """
    Fija el autor (user.name/email) de un repo según una identidad definida en
    la config. Sin 'identidad', usa la identidad por defecto del lugar; si el
    lugar no define ninguna, muestra la identidad actual del repo.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    nombre_id = identidad or lg.identidad
    if not nombre_id:
        actual = _fmt(G.get_identidad(lg, repo))
        disp = ", ".join(_cfg.identidades) or "(ninguna definida)"
        return (
            f"Identidad actual del repo:\n{actual}\n"
            f"No se indicó identidad ni el lugar '{donde}' define una por "
            f"defecto. Identidades disponibles: {disp}."
        )
    try:
        ident = _cfg.identidad(nombre_id)
    except C.ConfigError as e:
        return f"error: {e}"
    return _fmt(G.set_identidad(lg, repo, ident.nombre, ident.email))


# --- Red --------------------------------------------------------------------

@mcp.tool()
def ping(host: str, cuenta: int = 4, donde: str = "local") -> str:
    """Ping a 'host'. 'donde' permite pingear desde un server."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(R.ping(lg, host, cuenta))


@mcp.tool()
def http_request(url: str, metodo: str = "GET", cuerpo: str = "",
                 headers_json: str = "") -> str:
    """
    Petición HTTP/HTTPS desde local. Solo a hosts que indique el usuario; nunca
    a URLs sacadas de archivos sin confirmar. headers_json: JSON opcional.
    """
    import json
    hdrs = json.loads(headers_json) if headers_json else None
    return R.http_request(url, metodo, cuerpo or None, hdrs)


@mcp.tool()
def tcp_socket(host: str, puerto: int, enviar: str = "", recibir_bytes: int = 4096) -> str:
    """Conecta TCP a host:puerto, opcionalmente envía y devuelve lo recibido."""
    return R.tcp_socket(host, puerto, enviar or None, recibir_bytes)


# --- ADB --------------------------------------------------------------------

@mcp.tool()
def adb_devices(donde: str = "local") -> str:
    """Lista dispositivos ADB. 'donde' = máquina que corre adb."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_devices(lg))


@mcp.tool()
def adb_shell(serial: str, comando: str, donde: str = "local") -> str:
    """Ejecuta 'comando' en el shell del dispositivo 'serial'."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_shell(lg, serial, comando))


@mcp.tool()
def adb_install(serial: str, apk: str, donde: str = "local") -> str:
    """Instala un APK en el dispositivo (con -r)."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_install(lg, serial, apk))


@mcp.tool()
def adb_forcestop(serial: str, paquete: str, donde: str = "local") -> str:
    """force-stop de un paquete en el dispositivo."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_forcestop(lg, serial, paquete))


@mcp.tool()
def adb_relanzar(serial: str, paquete: str, donde: str = "local") -> str:
    """Relanza la app (LAUNCHER) en el dispositivo."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_relanzar(lg, serial, paquete))


# --- Gradle -----------------------------------------------------------------

@mcp.tool()
def gradle_build(proyecto: str, tarea: str = "assembleDebug",
                 donde: str = "local") -> str:
    """
    Compila un proyecto con su gradlew. En local Windows el build NO puede correr
    dentro del sandbox del cliente MCP (Gradle necesita sockets loopback), así que
    se lanza como tarea programada y esta función RETORNA AL TOQUE con el nombre
    de la tarea: seguí el avance con tarea_estado(nombre). En unix/remoto compila
    de forma síncrona (ahí no hay sandbox) y devuelve la salida directamente.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return M.gradle_build(lg, proyecto, tarea)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


# --- Búsqueda ---------------------------------------------------------------

@mcp.tool()
def buscar_nombre(proyecto: str, patron: str, donde: str = "local") -> str:
    """Busca por NOMBRE de archivo (regex) en un proyecto."""
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return B.buscar_nombre(lg, proyecto, patron)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


@mcp.tool()
def buscar_contenido(proyecto: str, patron: str, incluir: str = "",
                     donde: str = "local") -> str:
    """
    grep de contenido (regex) en un proyecto. 'incluir': globs separados por
    espacio (por defecto *.kt *.java *.xml *.kts *.gradle). Salida ruta:linea: texto.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return B.buscar_contenido(lg, proyecto, patron, incluir.split() if incluir else None)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


def main():
    mcp.run()


if __name__ == "__main__":
    main()
