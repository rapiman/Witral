"""
Base de datos = correr el CLIENTE NATIVO del motor en el lugar, donde la base
es local (o alcanzable) para ese lugar. No se exponen puertos ni se usan
drivers Python.

El motor es un eje más de la config del lugar (`db.motor`), igual que `donde`
es el eje de máquina: la misma tool sirve para postgres (`psql`), SQL Server
(`sqlcmd`) y —cuando se agregue— Oracle (`sqlplus`). Lo común (partir el
bloque en sentencias, decidir qué es destructivo, reintentar ante caída de
conexión) vive acá una sola vez; lo que cambia por motor son tres cosas
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


def _entorno(db: DBConfig) -> dict[str, str]:
    """
    Variables de entorno del cliente, por motor. La password SIEMPRE viaja por
    entorno, nunca por línea de comandos (donde quedaría visible en la lista de
    procesos de la máquina).
    """
    if db.motor == "postgres":
        # Timeout de conexión corto (no colgarse si la base no responde) y
        # salida en UTF-8 (evita mojibake al decodificar).
        env = {"PGCONNECT_TIMEOUT": "10", "PGCLIENTENCODING": "UTF8"}
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
            return T.ejecutar(lugar, args, entrada=entrada, timeout=60,
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
        return T.ejecutar(lugar, linea, entrada=entrada, timeout=60)

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
                       _limpiar(db.motor, r.error))


_q = T.comillas  # comilla POSIX: origen único en transporte.comillas
