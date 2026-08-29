"""
Base de datos = correr el CLIENTE NATIVO del motor en el lugar, donde la base
es local (o alcanzable) para ese lugar. No se exponen puertos ni se usan
drivers Python.

El motor es un eje más de la config del lugar (`db.motor`), igual que `donde`
es el eje de máquina: la misma tool sirve para postgres (`psql`), SQL Server
(`sqlcmd`) y —cuando se agregue— Oracle (`sqlplus`). Lo común (partir el
bloque en sentencias, decidir qué es destructivo, reintentar ante caída de
conexión) vive aquí una sola vez; lo que cambia por motor son tres cosas
acotadas: cómo se arman los argumentos, cómo viaja la password, y cómo se
entrega el SQL.

Distingue lectura de escritura: las sentencias destructivas requieren que la
capa de tools haya confirmado con el usuario (el parámetro `confirmado`). En
lugares marcados como sensibles (prod), la confirmación es obligatoria incluso
para cosas que en dev pasarían — esa decisión la toma la capa superior; aquí
solo se expone la señal `destructivo` para que la tool actúe en consecuencia.
"""

from __future__ import annotations

import re

from .config import Lugar, DBConfig, ConfigError
from . import transporte as T


# Palabras que indican modificación de datos o esquema. Incluye las de T-SQL
# (merge, exec/execute, backup/restore): un EXEC puede ser inofensivo, pero el
# costo de pedir confirmación de más es mucho menor que el de escribir de menos.
_DESTRUCTIVO = re.compile(
    r"\b(update|delete|drop|truncate|alter|insert|create|grant|revoke"
    r"|merge|exec|execute|backup|restore)\b",
    re.IGNORECASE,
)


def es_destructivo(sql: str) -> bool:
    """Heurística: ¿el SQL modifica datos o esquema?"""
    # Quitar comentarios de línea para no confundir.
    limpio = re.sub(r"--.*?$", "", sql, flags=re.MULTILINE)
    return bool(_DESTRUCTIVO.search(limpio))


def partir_sentencias(sql: str) -> list[str]:
    """
    Parte un bloque SQL en sentencias por ';' de tope, respetando strings
    ('...'  y "..."), comentarios (-- de línea y /* */ de bloque) y
    dollar-quoting ($tag$...$tag$). Devuelve las sentencias con texto (sin las
    vacías); el ';' NO se incluye. Es una heurística suficiente para separar
    lecturas de escrituras, no un parser completo de SQL.
    """
    sentencias: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        par = sql[i:i + 2]
        # Comentario de línea.
        if par == "--":
            j = sql.find("\n", i)
            j = n if j == -1 else j + 1
            buf.append(sql[i:j])
            i = j
            continue
        # Comentario de bloque.
        if par == "/*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            buf.append(sql[i:j])
            i = j
            continue
        # Dollar-quoting: $tag$ ... $tag$ (tag alfanumérico o vacío).
        if c == "$":
            m = re.match(r"\$[A-Za-z0-9_]*\$", sql[i:])
            if m:
                etiqueta = m.group(0)
                fin = sql.find(etiqueta, i + len(etiqueta))
                fin = n if fin == -1 else fin + len(etiqueta)
                buf.append(sql[i:fin])
                i = fin
                continue
        # Strings con comilla simple o doble (dobla-comilla escapa).
        if c in ("'", '"'):
            j = i + 1
            while j < n:
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            buf.append(sql[i:j])
            i = j
            continue
        if c == ";":
            texto = "".join(buf).strip()
            if texto:
                sentencias.append(texto)
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    resto = "".join(buf).strip()
    if resto:
        sentencias.append(resto)
    return sentencias


def separar_lectura_escritura(sql: str) -> tuple[list[str], list[str]]:
    """
    Separa un bloque en (lecturas, escrituras) sentencia a sentencia, con la
    MISMA heurística es_destructivo por sentencia. Una sentencia que no matchea
    ninguna palabra destructiva es lectura; el resto, escritura. Permite correr
    las lecturas sin confirmación y pedirla solo por las escrituras.
    """
    lecturas, escrituras = [], []
    for s in partir_sentencias(sql):
        (escrituras if es_destructivo(s) else lecturas).append(s)
    return lecturas, escrituras


# Señales de caída de CONEXIÓN (no error de SQL), por motor. Sirven para
# decidir el reintento único de una LECTURA.
_SEÑALES_CONEXION = {
    "postgres": (
        "10054", "could not receive data from server",
        "server closed the connection unexpectedly",
        "could not connect", "connection reset", "connection refused",
        "no connection to the server", "terminating connection",
    ),
    "sqlserver": (
        # Mensajes del ODBC Driver / SQL Native Client.
        "login timeout expired", "communication link failure",
        "tcp provider", "named pipes provider", "shared memory provider",
        "unable to complete login process",
        "connection was successfully established with the server, but then an error occurred",
        "existing connection was forcibly closed", "10054", "10060",
        "no such host is known", "server is not found or not accessible",
    ),
    "oracle": (
        "ora-03113", "ora-03114", "ora-12170", "ora-12541", "ora-12514",
        "tns:", "no listener",
    ),
}


def _fallo_conexion(err: str, motor: str = "postgres") -> bool:
    """¿El stderr del cliente delata una caída de CONEXIÓN (no de SQL)?"""
    e = (err or "").lower()
    return any(s in e for s in _SEÑALES_CONEXION.get(motor, ()))


def _args_postgres(db: DBConfig) -> list[str]:
    args = [db.cliente]
    # Modo peer (db.como): conexión por socket local como usuario del sistema.
    # No se pasan -h/-U: psql usa el socket Unix y el rol del usuario del SO.
    if not db.como:
        if db.host:
            args += ["-h", db.host]
        if db.puerto:
            args += ["-p", str(db.puerto)]
        if db.usuario:
            args += ["-U", db.usuario]
    if db.base:
        args += ["-d", db.base]
    # Salida limpia para el modelo.
    args += ["-v", "ON_ERROR_STOP=1", "--no-psqlrc"]
    # -w (--no-password): nunca pedir password interactivo. Si la base lo exige y
    # no hay credencial, psql falla AL INSTANTE en vez de colgarse esperando un
    # prompt que nadie va a responder (el cuelgue de 4 minutos).
    args += ["-w"]
    return args


def _servidor_sqlserver(db: DBConfig) -> str:
    """
    El -S de sqlcmd: host, host\\instancia, host,puerto o host\\instancia,puerto.
    Con instancia con nombre el puerto lo resuelve el SQL Browser, así que solo
    se agrega el puerto si la config lo declaró distinto del 1433 por defecto.
    """
    s = db.host or "127.0.0.1"
    if db.instancia:
        s += "\\" + db.instancia
    if db.puerto and db.puerto != 1433:
        s += "," + str(db.puerto)
    return s


def _args_sqlserver(db: DBConfig) -> list[str]:
    args = [db.cliente, "-S", _servidor_sqlserver(db)]
    if db.integrada or not db.usuario:
        # Autenticación integrada de Windows: sin usuario ni password.
        args += ["-E"]
    else:
        args += ["-U", db.usuario]
        # La password NO va por línea de comandos (queda visible en la lista de
        # procesos): viaja por la variable de entorno SQLCMDPASSWORD.
    if db.base:
        args += ["-d", db.base]
    if db.cifrar:
        args += ["-N"]
    if db.confiar_cert:
        args += ["-C"]
    args += [
        "-l", "15",      # timeout de LOGIN: falla rápido, no se cuelga.
        "-t", "60",      # timeout de QUERY.
        "-b",            # abortar el lote ante error = el ON_ERROR_STOP de psql.
        "-W",            # sin espacios de relleno a la derecha.
        "-s", "|",       # separador de columnas compacto.
        "-w", "8000",    # ancho amplio: que no corte filas largas.
        "-f", "i:65001,o:65001",   # UTF-8 de entrada y de salida.
    ]
    return args


_ARGS = {
    "postgres": _args_postgres,
    "sqlserver": _args_sqlserver,
}


def _args_motor(db: DBConfig) -> list[str]:
    constructor = _ARGS.get(db.motor)
    if constructor is None:
        raise ConfigError(
            f"Motor '{db.motor}' aún no implementado en Witral. "
            f"Implementados: {', '.join(sorted(_ARGS))}."
        )
    return constructor(db)


def _con_base(db: DBConfig, base: str | None) -> DBConfig:
    """Copia de la config de base con la base override, si se pidió otra."""
    if not base or base == db.base:
        return db
    import dataclasses
    return dataclasses.replace(db, base=base)


def psql_comando(lugar: Lugar, comando: str, base: str | None = None) -> T.Resultado:
    """
    Ejecuta SQL/meta-comandos vía psql con el SQL por STDIN (no -c): con
    varias sentencias en una llamada psql imprime TODOS los result sets,
    no solo el último (el modo -c ocultaba los anteriores).
    'base' permite apuntar a otra base del mismo lugar sin tocar config.
    """
    db = _con_base(lugar.requiere_db(), base)
    args = _args_motor(db)
    entrada = comando if comando.endswith("\n") else comando + "\n"
    # Solo reintentar en caída de conexión si NO es destructivo: reintentar una
    # escritura podría duplicar efectos. Las lecturas se reintentan sin riesgo.
    return _correr(lugar, db, args, entrada=entrada,
                   reintentable=not es_destructivo(comando))


def psql_archivo(lugar: Lugar, ruta_sql: str, origen: Lugar | None = None,
                 base: str | None = None) -> T.Resultado:
    """
    Aplica un archivo .sql: Witral LEE el archivo (con sus tools de archivo,
    desde 'origen' — por defecto el mismo lugar de la base) y manda el
    contenido por STDIN al psql del lugar de la BASE. Así se desacopla
    "dónde está el .sql" de "dónde corre psql": sirve para bases detrás de
    túnel (el psql no ve el filesystem local) y evita el boilerplate psycopg.
    'base' permite apuntar a otra base del mismo lugar.
    """
    from . import archivos as A
    db = _con_base(lugar.requiere_db(), base)
    org = origen if origen is not None else lugar
    contenido = A._leer_bytes(org, ruta_sql).decode("utf-8-sig", "replace")
    if not contenido.strip():
        return T.Resultado(1, "", f"el archivo {ruta_sql} está vacío")
    if not contenido.endswith("\n"):
        contenido += "\n"
    args = _args_motor(db)
    return _correr(lugar, db, args, entrada=contenido)


# Tope de la SENTENCIA, del lado del servidor (statement_timeout / -t), y tope
# de la LLAMADA, del lado de Witral. El de la sentencia va primero a propósito:
# si el que corta es el servidor, la sentencia queda cancelada y deshecha, y se
# puede afirmar qué pasó. Ambos por debajo del corte del cliente MCP (~60s), que
# antes se comía la llamada con un "Device did not respond within 60s" que no
# distinguía "no alcancé a mandarla" de "la mandé y no sé cómo terminó".
_TOPE_SENTENCIA = 40
_TOPE_LLAMADA = 45


def _entorno(db: DBConfig) -> dict[str, str]:
    """
    Variables de entorno del cliente, por motor. La password SIEMPRE viaja por
    entorno, nunca por línea de comandos (donde quedaría visible en la lista de
    procesos de la máquina).
    """
    if db.motor == "postgres":
        # Timeout de conexión corto (no colgarse si la base no responde) y
        # salida en UTF-8 (evita mojibake al decodificar).
        # statement_timeout: que la CANCELACIÓN la haga el servidor, no el
        # cliente. Es la diferencia entre "no sé si el UPDATE commiteó" y "el
        # servidor abortó la sentencia y la transacción quedó deshecha". Se fija
        # por debajo del tope de Witral para que gane siempre el de la base.
        env = {"PGCONNECT_TIMEOUT": "10", "PGCLIENTENCODING": "UTF8",
               "PGOPTIONS": f"-c statement_timeout={_TOPE_SENTENCIA * 1000}"}
        if db.password:
            env["PGPASSWORD"] = db.password
        return env
    if db.motor == "sqlserver":
        env: dict[str, str] = {}
        if db.password and not db.integrada:
            env["SQLCMDPASSWORD"] = db.password
        return env
    return {}


def _archivo_temporal_sql(lugar: Lugar, texto: str) -> str:
    """
    Deja el SQL en un archivo temporal del lugar LOCAL, en UTF-8 CON BOM, y
    devuelve su ruta.

    Por qué existe: sqlcmd IGNORA la codepage de entrada (-f i:) cuando el SQL
    llega por stdin — lo lee como OEM y destroza cualquier no-ASCII (una ñ
    entra como dos caracteres basura y termina en la base). Con `-i archivo` sí
    respeta el -f, y el BOM despeja toda ambigüedad. Verificado contra SQL
    Server 2017: por stdin la ñ llega rota; por archivo, intacta.
    """
    import os as _os
    import tempfile as _tf
    raiz = lugar.raiz or _tf.gettempdir()
    carpeta = _os.path.join(raiz, ".witral", "tmp")
    _os.makedirs(carpeta, exist_ok=True)
    fd, ruta = _tf.mkstemp(prefix="sql_", suffix=".sql", dir=carpeta)
    with _os.fdopen(fd, "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(texto)
    return ruta


def _limpiar(motor: str, texto: str) -> str:
    """sqlcmd emite CR CR LF: normalizarlo antes de que lo vea el modelo."""
    if motor == "sqlserver" and texto:
        return texto.replace("\r\r\n", "\n").replace("\r\n", "\n")
    return texto


def _correr(lugar: Lugar, db: DBConfig, args: list[str],
            entrada: str | None = None, reintentable: bool = False) -> T.Resultado:
    env_extra = _entorno(db)
    # sqlcmd no tiene statement_timeout: su equivalente es -t (query timeout).
    if db.motor == "sqlserver" and "-t" not in args:
        args = list(args) + ["-t", str(_TOPE_SENTENCIA)]
    # sqlcmd en Windows: el SQL va por archivo (-i), no por stdin (ver
    # _archivo_temporal_sql). En unix sqlcmd sí lee UTF-8 de stdin.
    por_archivo = (db.motor == "sqlserver" and lugar.es_local
                   and lugar.es_windows and entrada is not None)
    temporal: str | None = None
    if por_archivo:
        temporal = _archivo_temporal_sql(lugar, entrada or "")
        args = list(args) + ["-i", temporal]
        entrada = None

    def _una_vez() -> T.Resultado:
        if lugar.es_local:
            # Con -w (psql) / -l corto (sqlcmd), si la base pide password y no
            # hay credencial el cliente falla al instante en vez de esperar un
            # prompt que nadie va a responder.
            return T.ejecutar(lugar, args, entrada=entrada,
                              timeout=_TOPE_LLAMADA,
                              env_extra=dict(env_extra))
        linea = " ".join(_q(a) for a in args)
        prefijo = " ".join(f"{k}={_q(v)}" for k, v in env_extra.items())
        if db.como:
            # Peer auth (solo postgres): ejecutar como el usuario del sistema
            # vía sudo. Sin password TCP; psql usa el socket local con el rol de
            # ese usuario. 'env' para que las variables lleguen bajo sudo.
            linea = f"sudo -u {_q(db.como)} env {prefijo} {linea}"
        elif prefijo:
            linea = f"{prefijo} {linea}"
        return T.ejecutar(lugar, linea, entrada=entrada, timeout=_TOPE_LLAMADA)

    try:
        r = _una_vez()
        # Reintento único ante caída de CONEXIÓN (no error de SQL). Solo cuando
        # el llamador marcó la operación como segura de repetir (lectura):
        # absorbe el 10054 / "communication link failure" transitorio sin
        # arriesgar duplicar una escritura.
        if reintentable and r.codigo != 0 and _fallo_conexion(r.error, db.motor):
            import time as _t
            _t.sleep(1.0)
            r = _una_vez()
    finally:
        if temporal:
            import os as _os
            try:
                _os.remove(temporal)
            except OSError:
                pass
    return T.Resultado(r.codigo, _limpiar(db.motor, r.salida),
                       _limpiar(db.motor, r.error) + _veredicto(db, r))


# Marcas de que el SERVIDOR canceló la sentencia (y por lo tanto la deshizo).
_CANCELADA = (
    "canceling statement due to statement timeout",   # postgres
    "cancelando sentencia debido a statement timeout",
    "query timeout expired",                          # sqlcmd / ODBC
    "tiempo de espera",                               # sqlcmd en español
)


def _veredicto(db: DBConfig, r: T.Resultado) -> str:
    """
    Qué se puede AFIRMAR sobre la suerte de la sentencia cuando algo salió mal.
    En una herramienta de base de datos, "no respondió" a secas es inaceptable:
    ante un UPDATE hay que poder decir si commiteó, si quedó deshecho, o si ni
    siquiera se envió. Los tres casos se distinguen y se dicen con esas palabras.
    """
    if r.codigo == 0:
        return ""
    texto = f"{r.salida}\n{r.error}".lower()
    if any(m in texto for m in _CANCELADA):
        return (f"\n\nVEREDICTO: la sentencia se ENVIÓ y el SERVIDOR la CANCELÓ "
                f"al pasar {_TOPE_SENTENCIA}s (statement_timeout). Una sentencia "
                f"cancelada se deshace: NO quedó a medias ni commiteada. Para una "
                f"consulta legítimamente larga, correrla con run_async sobre el "
                f"cliente del motor en vez de por esta tool.")
    if r.codigo == 127 or "no se pudo lanzar el comando" in texto:
        return ("\n\nVEREDICTO: la sentencia NO se envió — falló el lanzamiento "
                "del cliente. La base quedó intacta.")
    if _fallo_conexion(r.error, db.motor):
        return ("\n\nVEREDICTO: se perdió la CONEXIÓN. Si era una lectura, "
                "reintentar es inocuo. Si era una escritura, el resultado es "
                "INDETERMINADO: verificar el estado con un SELECT antes de "
                "reintentar, nunca reintentar a ciegas.")
    if r.codigo == 124:
        return (f"\n\nVEREDICTO: Witral cortó la llamada a los {_TOPE_LLAMADA}s "
                f"sin que el servidor alcanzara a cancelar. La sentencia se "
                f"envió y su resultado es INDETERMINADO: verificar con un SELECT "
                f"antes de reintentar. (Con statement_timeout configurado esto "
                f"debería ser raro; si se repite, el corte no está en la "
                f"sentencia sino en la conexión.)")
    return ""


_q = T.comillas  # comilla POSIX: origen único en transporte.comillas


# --- SQLite (archivo, no servidor) -------------------------------------------

def _fmt_tabla(columnas: list[str], filas: list, maximo: int) -> str:
    """Tabla de ancho fijo, con tope de filas y aviso al truncar."""
    if not columnas:
        return "(sin resultados)"
    datos = [[("" if v is None else str(v)) for v in f] for f in filas[:maximo]]
    anchos = [len(c) for c in columnas]
    for fila in datos:
        for i, v in enumerate(fila):
            anchos[i] = max(anchos[i], min(len(v), 60))
    def linea(vals):
        return " | ".join(v[:60].ljust(anchos[i]) for i, v in enumerate(vals))
    salida = [linea(columnas), "-+-".join("-" * a for a in anchos)]
    salida += [linea(f) for f in datos]
    if len(filas) > maximo:
        salida.append(f"... y {len(filas) - maximo} filas más "
                      f"(subir 'maximo' o acotar con LIMIT)")
    salida.append(f"({len(filas)} fila(s))")
    return "\n".join(salida)


def sqlite_consulta(lugar: Lugar, archivo: str, comando: str,
                    confirmado: bool = False, maximo: int = 50) -> str:
    """
    Consulta un archivo SQLite. Witral ya corre en Python, así que usa el
    módulo `sqlite3` de la stdlib: sin dependencias ni cliente externo.

    En un servidor tan orientado a Android, sqlite es el motor que aparece —la
    base de una app, un .db traído con adb_pull— y hasta ahora obligaba a
    escribir `python -c "import sqlite3..."` a mano.

    Las LECTURAS abren la base en modo solo-lectura (URI `mode=ro`), así una
    consulta no puede tocar el archivo ni siquiera por error. Cualquier
    sentencia que modifique datos o esquema requiere confirmado=True.
    Sin 'comando', lista las tablas y sus columnas.
    """
    import sqlite3
    from .seguridad import normalizar

    if not lugar.es_local:
        return ("sqlite por ahora solo está implementado en lugares locales. "
                "Para una base remota: copiar el archivo con `copiar` y "
                "consultarlo aquí.")
    try:
        ruta = normalizar(lugar.raiz, archivo)
    except Exception as e:
        return f"error: {e}"
    if not ruta.exists():
        return (f"No existe el archivo {archivo} en {lugar.nombre}. "
                f"Si la base está en un dispositivo, traerla antes con "
                f"adb_pull(serial, remoto, destino, paquete=...).")

    comando = (comando or "").strip()
    if not comando:
        comando = ("SELECT name FROM sqlite_master WHERE type='table' "
                   "ORDER BY name")
    escribe = es_destructivo(comando)
    if escribe and not confirmado:
        return (f"CONFIRMACIÓN REQUERIDA: el SQL modifica la base "
                f"{archivo}.\n{comando}\n"
                f"Reintentar con confirmado=True. (Las lecturas no piden "
                f"confirmación y además abren la base en modo solo-lectura.)")

    try:
        if escribe:
            con = sqlite3.connect(str(ruta))
        else:
            uri = "file:" + str(ruta).replace("?", "%3f").replace("#", "%23")
            con = sqlite3.connect(uri + "?mode=ro", uri=True)
    except sqlite3.Error as e:
        return f"error abriendo {archivo}: {e}"
    try:
        cur = con.cursor()
        bloques = []
        for sentencia in partir_sentencias(comando):
            try:
                cur.execute(sentencia)
            except sqlite3.Error as e:
                bloques.append(f"error en «{sentencia[:60]}»: {e}")
                continue
            if cur.description:
                columnas = [d[0] for d in cur.description]
                bloques.append(_fmt_tabla(columnas, cur.fetchall(), maximo))
            else:
                bloques.append(f"OK ({cur.rowcount} fila(s) afectadas)")
        if escribe:
            con.commit()
        return "\n\n".join(bloques) if bloques else "(sin resultados)"
    finally:
        con.close()
