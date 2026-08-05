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
gradle y búsqueda. (El número de tools cambia; verlo con tool_search, no aquí.)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from . import config as C
from . import archivos as A
from . import basedatos as DB
from . import copiar as CP
from . import transporte as T
from . import gitops as G
from . import red as R
from . import movil as M
from . import guion as GU
from . import sistema as S
from . import busqueda as B
from . import sintaxis as SX
from . import puerto_serial as PS
from . import trabajos as TR
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


_MAX_SALIDA = 40000  # tope global de chars por bloque de salida


def _truncar(texto: str, limite: int = _MAX_SALIDA) -> str:
    """Trunca con aviso explícito (salidas gigantes atascan el transporte MCP)."""
    if limite <= 0 or len(texto) <= limite:
        return texto
    return (texto[:limite] +
            f"\n...[truncado: mostrando {limite} de {len(texto)} chars; "
            f"acotar la salida del comando, o volcarla a un archivo y "
            f"leerla por rangos]")


def _fmt(r: T.Resultado, max_salida: int = _MAX_SALIDA) -> str:
    """Formatea un Resultado de comando para devolver al modelo."""
    out = f"[código {r.codigo}]\n{_truncar(r.salida, max_salida)}"
    if r.error:
        out += "\n--- stderr ---\n" + _truncar(r.error, max_salida)
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
def leer(archivo: str, desde: int = 0, hasta: int = 0, donde: str = "local",
         cola: int = 0) -> str:
    """
    Lee un archivo. Sin desde/hasta: archivo completo (chicos). Con desde/hasta:
    solo ese rango de líneas, numeradas (forma correcta de mirar archivos
    grandes). Con cola=N: las últimas N líneas (logs, resultados). Autodefensa:
    un archivo grande leído sin rango no se vuelca entero — se devuelve el
    comienzo + totales + cómo seguir (rango, cola o buscar_contenido).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        if cola:
            return A.leer_cola(lg, archivo, cola)
        if desde or hasta:
            return A.leer_rango(lg, archivo, desde, hasta)
        texto = A.leer(lg, archivo)
        if len(texto) > _MAX_SALIDA:
            lineas = texto.count("\n") + 1
            corte = texto[:_MAX_SALIDA]
            hasta_linea = corte.count("\n") + 1
            return (corte +
                    f"\n...[autodefensa: el archivo tiene {lineas} líneas / "
                    f"{len(texto)} chars; se muestran ~{hasta_linea} líneas. "
                    f"Seguir con desde/hasta (rango), cola=N (final) o "
                    f"buscar_contenido (grep)]")
        return texto
    except (RutaFueraDeRaiz, FileNotFoundError, ValueError) as e:
        return f"error: {e}"


@mcp.tool()
def verificar_sintaxis(archivo: str, donde: str = "local") -> str:
    """
    Verifica la sintaxis de un archivo en dos capas:
    1) UNIVERSAL (siempre, todos los lenguajes): balance de ()[]{}, comillas y
       comentarios sin cerrar, ignorando strings y comentarios. Atrapa el error
       de edición más común. Funciona en local y remoto.
    2) NATIVA (si la herramienta está instalada y el lugar es local): chequeo
       real con el verificador del lenguaje (node --check, py_compile, php -l,
       gcc -fsyntax-only, perl -c, ruby -c).
    Reconoce: kt, kts, java, c, h, cpp, js, jsx, ts, php, py, sql, html, xml,
    css, sh, rb, pl. No reemplaza al compilador: es una red rápida antes de
    mover o compilar.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _verificar_sintaxis_texto(lg, archivo)


def _verificar_sintaxis_texto(lg, archivo: str) -> str:
    """Cuerpo de verificar_sintaxis con el lugar ya resuelto (reusable)."""
    import os
    ext = os.path.splitext(archivo)[1].lower()
    lang = SX.EXTENSIONES.get(ext)
    if not lang:
        return (f"No tengo perfil de sintaxis para '{ext}'. "
                f"Extensiones soportadas: {', '.join(sorted(SX.EXTENSIONES))}.")
    # Leer el texto (local o remoto).
    try:
        texto = A.leer(lg, archivo)
    except (RutaFueraDeRaiz, FileNotFoundError) as e:
        return f"error: {e}"

    # Capa 1: universal.
    hallazgos = SX.revisar_balance(texto, lang)
    partes = [f"Lenguaje: {lang.nombre}"]
    if hallazgos:
        partes.append("CAPA UNIVERSAL — problemas de balance:")
        for h in hallazgos[:20]:
            partes.append(f"  línea {h.linea}, col {h.columna}: {h.mensaje}")
    else:
        partes.append("CAPA UNIVERSAL — balance OK.")

    # Capa 2a: validación por librería Python (JSON/YAML/TOML). Opera sobre el
    # texto, así que funciona local Y remoto.
    lib = SX.validar_por_libreria(ext, texto)
    if lib is not None:
        ok, detalle = lib
        if ok:
            partes.append(f"CAPA NATIVA — {detalle}")
        else:
            partes.append("CAPA NATIVA — errores:")
            partes.append(detalle)
        return "\n".join(partes)
    if ext in SX.LIBRERIA:
        # Es un formato de datos pero la librería no está (yaml/toml).
        partes.append(
            f"CAPA NATIVA — librería para {ext} no disponible (solo capa universal).")
        return "\n".join(partes)

    # Capa 2b: nativa por binario (solo local, si está instalado).
    if lg.es_local:
        from .seguridad import normalizar
        ruta_abs = str(normalizar(lg.raiz, archivo))
        nat = SX.correr_nativo(ext, ruta_abs)
        if nat is None:
            bin_falta = SX.NATIVOS.get(ext)
            # Honestidad ante todo: un "balance OK" solo se lee tranquilizador
            # si no se aclara qué NO cubre. Los errores reales de una sesión de
            # Kotlin (referencias sin resolver, campos borrados, falta de
            # suspend) son invisibles para el balance de delimitadores.
            que_no_ve = (
                "OJO: el 'balance OK' de arriba SOLO cubre delimitadores — NO "
                "detecta referencias sin resolver, imports faltantes, tipos, "
                "campos borrados ni suspend/when; eso solo lo ve el compilador. "
                "Mitigación: buscar_contenido del símbolo tocado para enumerar "
                "todos sus usos.")
            if bin_falta:
                partes.append(
                    f"CAPA NATIVA — '{bin_falta.binario}' no está instalado; "
                    f"sin verificación nativa para {ext}. {que_no_ve}")
            else:
                partes.append(
                    f"CAPA NATIVA — no hay verificador nativo para {ext}. "
                    f"{que_no_ve}")
        else:
            ok, salida = nat
            if ok:
                partes.append("CAPA NATIVA — sintaxis OK.")
            else:
                partes.append("CAPA NATIVA — errores:")
                partes.append(salida or "(sin detalle)")
    else:
        partes.append("CAPA NATIVA — omitida (lugar remoto; solo capa universal).")

    return "\n".join(partes)


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
def subir_b64(archivo: str, contenido_b64: str, donde: str = "local",
              anexar_trozo: bool = False) -> str:
    """
    Escribe BYTES (decodificados de base64) en un archivo del lugar. Es el
    puente para traer contenido binario o grande desde afuera (p. ej. desde el
    sandbox de análisis de Claude) sin pelear con el escapado de texto JSON.
    Con anexar_trozo=True agrega al final del archivo: subir archivos grandes
    en trozos de ~100-200 KB de base64 por llamada. Acepta base64 con o sin
    saltos de línea.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.subir_b64(lg, archivo, contenido_b64, anexar_trozo)
    except (RutaFueraDeRaiz, A.EdicionError) as e:
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
def convertir_eol(archivo: str, a: str, donde: str = "local") -> str:
    """
    Convierte el fin de línea de un archivo entero a LF o CRLF ('a'="lf"|"crlf").
    Útil para pasar archivos clonados en Windows (CRLF) a LF para proyectos
    Linux, o limpiar saltos mezclados. Hace backup. OJO: reescribe todo el
    archivo, así que en git aparece como muchas líneas cambiadas (es esperado).
    Para editar contenido NO se usa esto; las tools de edición preservan el EOL.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return A.convertir_eol(lg, archivo, a)
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"


@mcp.tool()
def editar_literal(archivo: str, viejo: str, nuevo: str, verificar: bool = False,
                   donde: str = "local") -> str:
    """
    Reemplaza una ocurrencia EXACTA y única de 'viejo' por 'nuevo'. Falla si no
    aparece o aparece más de una vez. Backup automático, CRLF preservado.
    Con 'verificar'=True corre verificar_sintaxis tras editar y agrega el
    resultado en la misma respuesta (al editar código, ahorra una llamada).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        res = A.editar(lg, archivo, literales=[A.EdicionLiteral(viejo, nuevo)])
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"
    if verificar:
        res += "\n\n=== verificar_sintaxis ===\n" + _verificar_sintaxis_texto(lg, archivo)
    return res


@mcp.tool()
def editar_linea(archivo: str, desde: int = 0, hasta: int = 0, nuevo: str = "",
                 ancla: str = "", verificar: bool = False, linea: int = 0,
                 donde: str = "local") -> str:
    """
    Reemplaza el rango de líneas [desde, hasta] por 'nuevo'. Inmune a CRLF/
    whitespace. Backup automático y devuelve el fragmento resultante para
    verificar en el acto.

    UNA SOLA LÍNEA: pasá 'linea=N' (alias cómodo de desde=hasta=N), o bien 'desde'
    y omitir 'hasta' (toma el valor de 'desde'). Para vaciar/borrar una línea, dejá
    'nuevo'="" (o pasá varias líneas en 'nuevo' con \\n para expandir el rango).

    PARÁMETRO 'ancla' (muy recomendado): si lo pasás con el contenido que
    ESPERÁS que tengan esas líneas, la edición se aplica SOLO si coincide
    (comparación inmune a CRLF/espacios); si no coincide, aborta sin tocar el
    archivo y muestra esperado vs encontrado. Es la red de seguridad contra
    perder la cuenta de líneas. Sin 'ancla', edita el rango directo confiando
    en los números. Usá leer con rango antes para ubicar las líneas.
    El ancla puede ser PARCIAL: con menos líneas que el rango valida solo el
    COMIENZO; y con una línea "..." en el medio (ej. "primera\\n...\\núltima")
    valida COMIENZO y FINAL — ideal para borrar bloques largos (nuevo="") sin
    copiar las N líneas del rango: cubre el riesgo de desfase con dos líneas.

    PARÁMETRO 'verificar': si True, tras editar corre verificar_sintaxis sobre
    el archivo y agrega el resultado (ahorra una llamada aparte al editar código).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    # 'linea' es el alias cómodo para una sola línea (desde=hasta=linea): evita
    # el error de schema al llamar con linea=N esperando editar esa línea.
    if linea > 0:
        desde = hasta = linea
    if desde <= 0:
        return ("Indicá 'linea' (una sola línea) o 'desde'/'hasta' (rango). "
                "Ej.: linea=33, nuevo=\"...\".")
    if hasta <= 0:
        hasta = desde  # una sola línea: hasta = desde
    try:
        if ancla:
            res = A.editar(lg, archivo,
                           ancladas=[A.EdicionAnclada(desde, hasta, ancla, nuevo)])
        else:
            res = A.editar(lg, archivo, lineas=[A.EdicionLinea(desde, hasta, nuevo)])
    except (RutaFueraDeRaiz, FileNotFoundError, A.EdicionError) as e:
        return f"error: {e}"
    if verificar:
        res += "\n\n=== verificar_sintaxis ===\n" + _verificar_sintaxis_texto(lg, archivo)
    return res


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
def copiar(origen: str = "", destino: str = "", origen_ruta: str = "",
           destino_lugar: str = "", destino_ruta: str = "",
           origen_lugar: str = "local", confirmado: bool = False) -> str:
    """
    Copia un archivo entre dos lugares (SFTP). Dos formas de indicar los extremos:

    - COMPACTA (recomendada): origen="local:folil/web/app.py",
      destino="wedwed:/srv/app/app.py". El prefijo antes del ':' es el lugar
      (debe ser un lugar conocido; si no hay prefijo de lugar válido —p. ej.
      'C:\\...' o '/srv/...'— se asume el lugar 'local' y todo es la ruta).
    - EXPLÍCITA (compatibilidad): origen_ruta + origen_lugar +
      destino_lugar + destino_ruta.

    Copiar HACIA un lugar sensible (prod) requiere confirmado=True tras
    confirmar con el usuario.
    """
    # Forma compacta: si se dan, sobreescriben a los parámetros explícitos.
    if origen:
        origen_lugar, origen_ruta = CP.partir_lugar_ruta(origen, _cfg.nombres)
    if destino:
        destino_lugar, destino_ruta = CP.partir_lugar_ruta(destino, _cfg.nombres)
    if not origen_ruta or not destino_lugar or not destino_ruta:
        return ("Faltan datos de la copia. Usá la forma compacta "
                "(origen=\"lugar:ruta\", destino=\"lugar:ruta\") o la explícita "
                "(origen_ruta, destino_lugar, destino_ruta).")
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


@mcp.tool()
def desplegar(origen: str, destino: str, servicio: str = "",
              prueba_url: str = "", espera: float = 3.0,
              confirmado: bool = False) -> str:
    """
    Despliegue en UNA pasada: copia un archivo al server, reinicia un servicio y
    hace una prueba de humo HTTP. Combina el patrón más repetido
    (copiar -> restart -> esperar -> curl) en una sola llamada.

    origen/destino: forma compacta 'lugar:ruta' (como copiar). Ej.:
      origen="local:folil/web/app.py", destino="folil:/srv/app/app.py".
      El servicio y la prueba de humo corren en el lugar de DESTINO.
    servicio: servicio a reiniciar en el destino (vacío = no reinicia).
    prueba_url: si se da, tras 'espera' segundos hace GET y reporta el status
      (curl DESDE el lugar de destino: sirve para endpoints en localhost del
      server). espera: segundos entre restart y prueba de humo (tope 30).

    Escribe en un server y reinicia un servicio => requiere confirmado=True;
    reforzado si el destino es sensible. Si un paso falla, corta y lo reporta.
    """
    import time as _time
    origen_lugar, origen_ruta = CP.partir_lugar_ruta(origen, _cfg.nombres)
    destino_lugar, destino_ruta = CP.partir_lugar_ruta(destino, _cfg.nombres)
    if not origen_ruta or not destino_lugar or not destino_ruta:
        return ("Faltan datos. Usá origen=\"lugar:ruta\" y destino=\"lugar:ruta\" "
                "(ej. destino=\"folil:/srv/app/app.py\").")
    d, aviso = _resolver(destino_lugar)
    if aviso:
        return aviso
    o, aviso = _resolver(origen_lugar)
    if aviso:
        return aviso
    if not confirmado:
        extra = " (LUGAR SENSIBLE)" if d.sensible else ""
        pasos = [f"1) copiar {origen_lugar}:{origen_ruta} -> "
                 f"{destino_lugar}:{destino_ruta}"]
        if servicio:
            pasos.append(f"2) reiniciar servicio '{servicio}' en {destino_lugar}")
        if prueba_url:
            pasos.append(f"3) esperar {espera}s y probar {prueba_url}")
        return (f"CONFIRMACIÓN REQUERIDA: desplegar en '{destino_lugar}'{extra}.\n"
                + "\n".join(pasos) +
                "\nConfirmá con el usuario y reintentá con confirmado=True.")
    partes = []
    # 1) copiar (si falla, se aborta: no tiene sentido reiniciar con lo viejo).
    try:
        r_copia = CP.copiar(_cfg, origen_lugar, origen_ruta,
                            destino_lugar, destino_ruta)
        partes.append("COPIA: " + r_copia)
    except (RutaFueraDeRaiz, FileNotFoundError, T.TransporteError) as e:
        return f"DESPLIEGUE ABORTADO en la copia: {e}"
    # 2) restart.
    if servicio:
        rs = S.servicio(d, "restart", servicio)
        partes.append(f"RESTART ({servicio}):\n{_fmt(rs)}")
        if not rs.ok:
            partes.append("⚠️  el restart devolvió error: revisá antes de "
                          "confiar en la prueba de humo.")
    # 3) prueba de humo.
    if prueba_url:
        espera_cap = max(0.0, min(float(espera), 30.0))
        if espera_cap:
            _time.sleep(espera_cap)
        prueba = R.http_request(prueba_url, "GET", None, None, lugar=d,
                                max_salida=2000)
        partes.append(f"PRUEBA DE HUMO {prueba_url}:\n{prueba}")
    return "\n\n".join(partes)


# --- Ejecución de comandos (run) --------------------------------------------

# Prefijos de comando claramente de SOLO LECTURA. Si TODO el comando se compone
# de estos (encadenados solo con && o ;, sin redirecciones/pipes/sustitución/
# background), `run` NO pide confirmación: ahorra una vuelta en vistazos como
# `git status --porcelain && git log --oneline -3`.
_RUN_LECTURA = (
    "git status", "git log", "git diff", "git show", "git branch",
    "git remote -v", "git remote show", "git rev-parse", "git describe",
    "git config --get", "git config --list",
    "ls", "dir", "cat", "type", "findstr", "grep", "rg", "head", "tail",
    "wc", "echo", "pwd", "cd", "whoami", "hostname", "date", "where", "which",
    "tree", "stat", "du", "df", "env", "printenv",
)


def _es_solo_lectura(comando: str) -> bool:
    """
    True si 'comando' es claramente de solo lectura y seguro de correr sin
    confirmación: cada segmento (separado por && o ;) arranca con un prefijo de
    _RUN_LECTURA y no hay redirecciones (`>`/`<`), pipes (`|`), sustitución de
    comandos (`` ` ``/`$(`) ni background (`&` suelto). Conservador a propósito.
    """
    c = comando.strip()
    if not c or "\n" in c:
        return False
    for p in (">", "<", "|", "`", "$(", "${"):
        if p in c:
            return False
    if "&" in c.replace("&&", ""):  # permite '&&', rechaza '&' de fondo
        return False
    import re as _re
    segmentos = [s.strip() for s in _re.split(r"&&|;", c) if s.strip()]
    if not segmentos:
        return False
    for seg in segmentos:
        low = seg.lower()
        if not any(low == p or low.startswith(p + " ") for p in _RUN_LECTURA):
            return False
    return True


def _envolver_shell(lg, comando: str, shell: str) -> str:
    """Con shell="powershell" (solo lugares Windows), envuelve el comando para
    PowerShell vía -EncodedCommand (base64 de UTF-16LE): el comando se escribe
    en sintaxis PowerShell normal y viaja intacto, sin peleas de escapado con
    cmd. Con shell vacío devuelve el comando tal cual."""
    if not shell:
        return comando
    if shell not in ("powershell", "ps", "pwsh"):
        raise ValueError(f"shell '{shell}' no soportado (usar \"powershell\").")
    if not getattr(lg, "es_windows", False):
        raise ValueError("shell=\"powershell\" es solo para lugares Windows.")
    import base64 as _b64
    cod = _b64.b64encode(comando.encode("utf-16-le")).decode("ascii")
    return f"powershell -NoProfile -NonInteractive -EncodedCommand {cod}"


@mcp.tool()
def run(comando: str, donde: str = "local", confirmado: bool = False,
        max_salida: int = 40000, shell: str = "") -> str:
    """
    Ejecuta un comando arbitrario en un lugar (local o remoto) y devuelve la
    salida. SIEMPRE requiere confirmado=True: es una escotilla de propósito
    general. Para operaciones comunes (archivos, git, procesos, servicios)
    preferí las tools tipadas con eje 'donde', que son más seguras y claras.
    El directorio de trabajo es la RAÍZ del lugar (en local, Proyectos\\), así
    que las rutas relativas se resuelven contra ella. 'max_salida' acota los
    chars devueltos (trunca con aviso; salidas gigantes atascan el MCP).
    'shell'="powershell" (solo Windows): el comando se escribe en PowerShell y
    corre vía -EncodedCommand — recomendado en Windows en cuanto cmd empiece a
    pelear (findstr sin alternación, %% en los for, comillas anidadas, head/tr
    que a veces no están).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    # Comando de SOLO LECTURA en un lugar no sensible: no se pide confirmación
    # (allowlist en _es_solo_lectura). Todo lo demás sigue pidiéndola.
    if not confirmado and not (_es_solo_lectura(comando) and not lg.sensible):
        extra = " (LUGAR SENSIBLE)" if lg.sensible else ""
        return (
            f"CONFIRMACIÓN REQUERIDA para ejecutar un comando en '{donde}'{extra}.\n"
            f"Comando: {comando}\n"
            f"Si existe una tool tipada para esto (borrar, editar, git_*, "
            f"matar_proceso, servicio, etc.) usala mejor. "
            f"Para continuar igual, reintentá con confirmado=True."
        )
    try:
        comando = _envolver_shell(lg, comando, shell)
    except ValueError as e:
        return f"error: {e}"
    try:
        # cwd = raíz del lugar (si está definida): rutas relativas predecibles.
        return _fmt(T.ejecutar(lg, comando, cwd=lg.raiz or None),
                    max_salida=max_salida)
    except T.TransporteError as e:
        return f"error: {e}"


@mcp.tool()
def run_async(comando: str, donde: str = "local", confirmado: bool = False,
              shell: str = "") -> str:
    """
    Lanza un comando LARGO en segundo plano (detached) y devuelve un id al
    instante. Es la forma correcta de correr trabajos de minutos: el cliente
    MCP corta las llamadas largas (~60s), así que `run` no sirve para eso.
    Ciclo: run_async -> polling con run_status(id) -> (si hace falta)
    run_matar(id). La salida queda en .witral/jobs/<id>/ del lugar (out.log,
    err.log, codigo) y sobrevive a reinicios. cwd = raíz del lugar.
    Como `run`, SIEMPRE requiere confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        extra = " (LUGAR SENSIBLE)" if lg.sensible else ""
        return (
            f"CONFIRMACIÓN REQUERIDA{extra}: run_async ejecutará en segundo plano "
            f"en '{donde}':\n  {comando}\n"
            f"Mostrá el comando al usuario y reintentá con confirmado=True."
        )
    try:
        comando = _envolver_shell(lg, comando, shell)
    except ValueError as e:
        return f"error: {e}"
    try:
        jid = TR.lanzar(lg, comando)
        return (f"Trabajo lanzado: id {jid} en {donde}.\n"
                f"Consultar con run_status(id=\"{jid}\", donde=\"{donde}\"); "
                f"matar con run_matar si hace falta.")
    except T.TransporteError as e:
        return f"error: {e}"


@mcp.tool()
def run_status(id: str = "", donde: str = "local", lineas: int = 40) -> str:
    """
    Estado de un trabajo lanzado con run_async: corriendo/terminado, código de
    salida y las últimas 'lineas' de out.log y err.log. Sin id, lista los
    últimos trabajos del lugar. Lectura libre (no pide confirmación).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        if not id:
            return TR.listar(lg)
        return TR.estado(lg, id, lineas)
    except T.TransporteError as e:
        return f"error: {e}"


@mcp.tool()
def run_esperar(id: str, hasta_segundos: int = 600, lineas: int = 40,
                donde: str = "local") -> str:
    """
    Espera (del lado de Witral) a que termine un trabajo de run_async y devuelve
    su estado final, en vez de hacer polling a mano con sleep + run_status.

    Como el cliente MCP corta las llamadas largas (~45s), cada run_esperar
    bloquea a lo sumo ~40s: si el trabajo termina antes, vuelve AL INSTANTE con
    el código y las últimas 'lineas' de salida; si sigue corriendo al llegar al
    tope, devuelve el estado parcial y hay que VOLVER A LLAMAR run_esperar(id)
    para seguir esperando. Aun con el tope, colapsa el polling: una sola llamada
    cubre la espera y detecta el fin en 1-3s (no en el próximo sleep a ciegas).
    'hasta_segundos' es el techo deseado; se acota al tope por llamada. Lectura
    libre (no pide confirmación).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return TR.esperar(lg, id, hasta_segundos, lineas)
    except T.TransporteError as e:
        return f"error: {e}"


@mcp.tool()
def run_matar(id: str, donde: str = "local", confirmado: bool = False) -> str:
    """
    Mata un trabajo lanzado con run_async: termina el ÁRBOL completo de
    procesos (taskkill /T en Windows, kill del grupo en unix) y marca el
    trabajo como 'matado'. Requiere confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (f"CONFIRMACIÓN REQUERIDA: run_matar terminará el trabajo '{id}' "
                f"en '{donde}' con todo su árbol de procesos. "
                f"Reintentá con confirmado=True.")
    try:
        return TR.matar(lg, id)
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
def psql(donde: str, comando: str, confirmado: bool = False,
         base: str = "") -> str:
    """
    Corre psql en un lugar (la base es local allí). El SQL viaja por stdin:
    con VARIAS sentencias en una llamada se muestran TODOS los result sets
    (ya no solo el último). Lectura libre; sentencias destructivas
    (UPDATE/DELETE/DROP/TRUNCATE/ALTER/INSERT/CREATE) requieren
    confirmado=True. En lugares sensibles, cualquier ejecución pide confirmación.
    'base': nombre de base alternativa del mismo lugar (override del -d).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    destructivo = DB.es_destructivo(comando)
    if (destructivo or lg.sensible) and not confirmado:
        # En un lugar NO sensible con bloque MIXTO (SELECT + UPDATE, etc.), no
        # frenar por todo: correr las LECTURAS al toque y pedir confirmación
        # solo por las ESCRITURAS. Así el SELECT no espera una segunda llamada.
        if destructivo and not lg.sensible:
            lecturas, escrituras = DB.separar_lectura_escritura(comando)
            if lecturas and escrituras:
                try:
                    salida = _fmt(DB.psql_comando(
                        lg, ";\n".join(lecturas) + ";", base=base or None))
                except Exception as e:
                    salida = f"(las lecturas fallaron: {e})"
                return (
                    "LECTURAS ejecutadas (sin confirmación):\n" + salida +
                    "\n\nCONFIRMACIÓN REQUERIDA para las ESCRITURAS "
                    "(no se ejecutaron):\n" + ";\n".join(escrituras) + ";\n"
                    f"Lugar: {donde}. Reintentá con confirmado=True para "
                    "aplicar el bloque completo."
                )
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
        return _fmt(DB.psql_comando(lg, comando, base=base or None))
    except Exception as e:
        return f"error: {e}"


@mcp.tool()
def psql_aplicar(donde: str, ruta_sql: str, confirmado: bool = False,
                 origen: str = "", base: str = "") -> str:
    """
    Aplica un archivo .sql en la base de 'donde' (caso central de migración).
    Witral LEE el .sql y lo manda por STDIN al psql del lugar de la BASE, así
    que "dónde está el archivo" y "dónde corre psql" quedan desacoplados:
    - 'origen': lugar donde vive el .sql (por defecto, el mismo 'donde').
      Ej.: origen="local" aplica un .sql local contra una base detrás de
      túnel cuyo psql no ve el filesystem local.
    - 'base': base alternativa del mismo lugar (override del -d de config).
    Siempre requiere confirmado=True porque aplica cambios; reforzado en
    lugares sensibles.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    org = None
    if origen and origen != donde:
        org, aviso = _resolver(origen)
        if aviso:
            return aviso
    if not confirmado:
        extra = " (LUGAR SENSIBLE — revisá el contenido del .sql primero)" if lg.sensible else ""
        de = f" (archivo en '{origen}')" if origen and origen != donde else ""
        en_base = f" base: {base}" if base else ""
        return (
            f"CONFIRMACIÓN REQUERIDA para aplicar migración{extra}.\n"
            f"Archivo: {ruta_sql}{de}\nLugar: {donde}{en_base}\n"
            f"Mostrá al usuario qué se va a aplicar y reintentá con confirmado=True."
        )
    try:
        return _fmt(DB.psql_archivo(lg, ruta_sql, origen=org,
                                    base=base or None))
    except (RutaFueraDeRaiz, FileNotFoundError, T.TransporteError) as e:
        return f"error: {e}"
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
    """
    Muestra un commit (con --stat) o el CONTENIDO de un archivo en una rama/commit.
    Para ver la versión de un archivo en otra rama (útil en merges), pasá
    'ref' como "rama:ruta" o "commit:ruta" (ej. "develop:app/src/Main.kt") y
    devuelve ese archivo tal cual está en esa rama. Sin ':' muestra el commit.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.show(lg, repo, ref))


@mcp.tool()
def git_conflictos(repo: str, archivo: str = "", donde: str = "local") -> str:
    """
    Conflictos de merge. Sin 'archivo': lista los archivos del repo en
    conflicto sin resolver. Con 'archivo' (ruta relativa al repo, como la
    lista git): muestra los hunks numerados con sus líneas y una vista previa
    de ours/theirs (los lados largos se resumen). Solo lectura. Resolver con
    git_resolver.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        if not archivo:
            r = G.conflictos_listar(lg, repo)
            if r.codigo == 0 and not (r.salida or "").strip():
                return "Sin archivos en conflicto."
            return _fmt(r)
        return G.conflictos(lg, repo, archivo)
    except (RutaFueraDeRaiz, FileNotFoundError, T.TransporteError) as e:
        return f"error: {e}"


@mcp.tool()
def git_resolver(repo: str, archivo: str, lado: str, hunk: int = 0,
                 donde: str = "local") -> str:
    """
    Resuelve hunks de conflicto de merge eligiendo lado: 'ours', 'theirs' o
    'ambos' (ours seguido de theirs, sin marcadores). 'hunk'=N según la
    numeración de git_conflictos, o 0 (defecto) para TODOS los hunks del
    archivo. Backup automático antes de escribir; las líneas sobrevivientes
    conservan su EOL. Tras resolver todo: git_add del archivo para marcarlo
    resuelto (git_conflictos sin 'archivo' confirma qué queda).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return G.resolver(lg, repo, archivo, hunk, lado)
    except (RutaFueraDeRaiz, FileNotFoundError, T.TransporteError) as e:
        return f"error: {e}"


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
def git_commit(repo: str, mensaje: str = "", todos: bool = False,
               merge: bool = False, donde: str = "local") -> str:
    """
    git commit -m. Con todos=True agrega -a. Para sellar un MERGE en curso sin
    escribir mensaje, usar merge=True (toma el mensaje automático de git con
    --no-edit). Si se da mensaje, se usa ese.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.commit(lg, repo, mensaje, todos, merge))


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
def git_publicar(repo: str, mensaje: str, donde: str = "local", rutas: str = "",
                 excluir: str = "", empujar: bool = True, forzar: bool = False,
                 confirmado: bool = False) -> str:
    """
    Ciclo de commit completo EN UNA PASADA: status -> add -> diff (staged) ->
    commit -> push. Ahorra encadenar las 5 tools a mano. Muestra el diff --stat
    antes del commit (no se pierde el punto de control) y para si un paso falla.
    'mensaje': el del commit. 'rutas': qué agregar separado por espacios (por
    defecto todo). 'excluir': rutas/patrones que NO se agregan (pathspec
    ':(exclude)', separado por espacios) — para dejar afuera archivos sueltos
    del working tree. Al agregar todo, los NUEVOS (untracked) se listan
    explícitamente en la confirmación y en la salida. 'empujar': si False,
    solo commitea local (no push, no pide confirmado). 'forzar': push con
    --force-with-lease.
    Como publica al remoto, con empujar=True requiere confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if empujar and not confirmado:
        modo = " (FORZADO: reescribe la rama remota)" if forzar else ""
        # Polizones a la vista ANTES de aprobar: si se agrega todo, listar
        # los untracked que el add se va a llevar.
        nota_nuevos = ""
        if not rutas:
            try:
                nuevos = G.untracked(lg, repo)
                if nuevos:
                    nota_nuevos = (
                        "\nNUEVOS (untracked) que se agregarán: "
                        + ", ".join(nuevos)
                        + "\n(Para dejar alguno afuera, usar 'excluir' o 'rutas'.)"
                    )
            except Exception:
                pass
        return (
            f"CONFIRMACIÓN REQUERIDA: git_publicar hará commit y push desde "
            f"'{donde}'{modo}.{nota_nuevos}\nConfirmá con el usuario y reintentá "
            f"con confirmado=True. (O usá empujar=False para commitear solo local.)"
        )
    lista = rutas.split() if rutas else None
    lista_excluir = excluir.split() if excluir else None
    try:
        return G.publicar(lg, repo, mensaje, lista, empujar, forzar,
                          excluir=lista_excluir)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


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
def git_clone(url: str, destino: str, rama: str = "", donde: str = "local") -> str:
    """
    Clona el repositorio 'url' en 'destino'. El destino no debe existir todavía
    (o estar vacío). En local, 'destino' se acota a la raíz autorizada (no se
    puede clonar fuera de ella); en remoto se interpreta en ese lugar. 'rama'
    opcional clona solo esa rama (--branch). Trae código de la red pero es de
    solo descarga (no publica ni destruye), por eso no pide confirmación, igual
    que git_pull/git_fetch. Timeout amplio porque puede tardar.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(G.clone(lg, url, destino, rama))


@mcp.tool()
def git_remote(repo: str, nombre: str = "", url: str = "", donde: str = "local") -> str:
    """
    Gestiona remotos del repo. Sin 'nombre'/'url': lista los remotos con sus URLs
    (git remote -v). Con 'nombre' y 'url': agrega un remoto (git remote add).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if nombre and url:
        return _fmt(G.remote_add(lg, repo, nombre, url))
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
                 headers_json: str = "", params_json: str = "",
                 donde: str = "local", a_archivo: str = "",
                 max_salida: int = 4000) -> str:
    """
    Petición HTTP/HTTPS desde un lugar. Solo a hosts que indique el usuario;
    nunca a URLs sacadas de archivos sin confirmar.

    params_json: query params como JSON (ej. '{"tema": "üllku"}'). Witral los
    percent-encodea en Python, así el texto no-ASCII llega intacto SIN pelear
    con el shell. Para pasar no-ASCII en la URL, usar SIEMPRE esto (no armar
    la URL a mano ni usar curl por run).

    donde: lugar desde el que sale la petición. En remoto usa curl del lugar;
    sirve para probar servicios que solo escuchan en localhost del server.
    headers_json: headers como JSON opcional.

    a_archivo: si se da, el cuerpo de la respuesta se GUARDA en esa ruta del
    lugar (relativa a su raíz) y solo vuelven status + tamaño + ruta. Es la
    forma correcta para respuestas grandes (JSON de APIs, dumps): las
    respuestas gigantes inline atascan el transporte MCP. Después procesar
    el archivo con leer / buscar_contenido / run.
    max_salida: tope de chars del cuerpo mostrado inline (trunca con aviso).
    """
    import json
    hdrs = json.loads(headers_json) if headers_json else None
    prms = json.loads(params_json) if params_json else None
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return R.http_request(url, metodo, cuerpo or None, hdrs,
                          params=prms, lugar=lg,
                          a_archivo=a_archivo or None,
                          max_salida=max_salida)


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


@mcp.tool()
def adb_logcat(serial: str, tags: str = "", nivel: str = "V", lineas: int = 200,
               limpiar_antes: bool = False, donde: str = "local") -> str:
    """
    Captura logcat del dispositivo (modo dump: vuelca y sale, no streaming).
    'tags': tags separados por coma para filtrar (ej. "NavMenuOperacion,Anulacion");
    vacío = todo. 'nivel': mínimo V/D/I/W/E. 'lineas': últimas N líneas (tail).
    'limpiar_antes': limpia el buffer para capturar solo lo nuevo (flujo: limpiar,
    reproducir el caso en el POS, volver a llamar sin limpiar_antes).
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return _fmt(M.adb_logcat(lg, serial, tags, nivel, lineas, limpiar_antes))


@mcp.tool()
def adb_captura(serial: str, donde: str = "local"):
    """
    Captura la pantalla del dispositivo y devuelve la IMAGEN directamente (PNG),
    en UNA sola llamada — sin el rodeo screencap -> pull -> stage. Usa `exec-out
    screencap -p`. 'serial': dispositivo; 'donde': la máquina que corre adb.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        data = M.adb_captura(lg, serial)
    except (ValueError, T.TransporteError) as e:
        return f"error: {e}"
    return Image(data=data, format="png")


@mcp.tool()
def adb_ui(serial: str, solo_clickeables: bool = False, donde: str = "local") -> str:
    """
    Vuelca el árbol de vistas (uiautomator dump) PARSEADO: por cada elemento con
    texto / content-desc / clickable, muestra el CENTRO (x,y) para tapear POR
    TEXTO en vez de por píxel, si es clickeable, su clase y su resource-id. Así,
    si un botón se mueve, no te equivocás de coordenada, y podés decidir varios
    pasos por llamada. Con 'solo_clickeables'=True filtra a los tapeables. Tras
    ubicar el centro, tapeás con adb_shell "input tap x y".
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return M.adb_ui(lg, serial, solo_clickeables)


@mcp.tool()
def adb_tap_texto(serial: str, texto: str, timeout: int = 12,
                  parcial: bool = True, confirmado: bool = False,
                  donde: str = "local") -> str:
    """
    Busca 'texto' (o content-desc) en pantalla, saca su centro y TAPEA — en una
    llamada, sin volcado -> leer -> elegir -> tapear. ESPERA a que aparezca
    (hasta 'timeout' s) en vez de tapear al vacío, y mata el error de coordenada
    vieja si el botón se movió. 'parcial'=True permite match parcial. Los textos
    de la lista negra del POS (cierre de turno, anulación, borrar llaves...) se
    niegan salvo confirmado=True.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return M.adb_tap_texto(lg, serial, texto, timeout, parcial, confirmado)


@mcp.tool()
def adb_escribir(serial: str, texto: str, donde: str = "local") -> str:
    """
    Teclea una secuencia de dígitos en un teclado numérico EN PANTALLA de una
    sola pasada: ubica los botones con UN volcado y tapea todos sin esperar entre
    teclas. Mucho más rápido que un tap por dígito (ideal para montos). 'texto':
    los dígitos (ej. "4730"); no-dígitos se ignoran.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return M.adb_escribir(lg, serial, texto)


@mcp.tool()
def adb_esperar(serial: str, texto: str = "", patron_log: str = "",
                timeout: int = 15, tags: str = "", donde: str = "local") -> str:
    """
    Espera una CONDICIÓN en vez de un 'sleep N' adivinado. Con 'texto': hasta que
    ese texto/desc aparezca en la UI. Con 'patron_log' (regex): hasta que una
    línea de logcat lo matchee — determinista y barato, ideal para aserciones de
    tags estables (ej. 'Scan C2C: code=00'). 'tags' filtra por tag (coma-
    separados). Devuelve una línea. Tope de espera por llamada: 40s.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return M.adb_esperar(lg, serial, texto, patron_log, timeout, tags)


@mcp.tool()
def serial_puertos(donde: str = "local") -> str:
    """
    Lista los puertos serie visibles con su descripción (COM3, /dev/ttyUSB0…).
    Solo lectura. Por ahora solo 'local': el puerto es físico de la máquina que
    corre Witral.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return PS.serial_puertos(lg)


@mcp.tool()
def serial_enviar(puerto: str, texto: str, baud: int = 9600, bits: int = 8,
                  paridad: str = "N", stop: float = 1, framing: str = "crudo",
                  ack: bool = True, timeout: int = 30, reintentos: int = 3,
                  hexa: bool = False, donde: str = "local") -> str:
    """
    Envía 'texto' por un puerto serie y espera la respuesta.

    GENÉRICA A PROPÓSITO: el encuadre es un PARÁMETRO, no algo cableado.
      - framing="crudo": se manda tal cual y se lee lo que vuelva. Sirve para
        cualquier equipo que hable por serial (balanza, impresora, módem, placa).
      - framing="stx_etx_crc16arc": encuadre STX+payload+ETX+CRC16. La
        herramienta CALCULA el CRC al enviar y lo VALIDA al recibir; el que
        llama nunca ve un CRC. Es el protocolo del POS BCI en modo POS
        Integrado / Cuna. Se llama 'arc' porque "CRC-16" a secas es ambiguo
        (ARC, MODBUS, CCITT y XMODEM difieren); este es ARC, poly 0xA001.

    'ack'=True (solo con encuadre) acusa recibo con ACK cuando el CRC da bien y
    manda NAK cuando da mal. El POS ESPERA ese ACK: sin él reenvía la respuesta
    y parece que llegara duplicada. También reenvía si el otro lado manda NAK,
    hasta 'reintentos' veces, y corta si recibe EOT.

    'hexa'=True agrega el volcado hexadecimal de la trama, que es lo primero que
    se quiere ver cuando algo no contesta. Tope de espera por llamada: 40s.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return PS.serial_enviar(lg, puerto, texto, baud, bits, paridad, stop,
                            framing, ack, timeout, reintentos, hexa)


@mcp.tool()
def adb_guion(serial: str, archivo: str, paquete: str = "", desde: int = 1,
              origen: str = "", donde: str = "local", confirmado: bool = False,
              valores: str = ""):
    """
    Corre un GUIÓN de UI del lado del dispositivo y devuelve UNA línea si pasó;
    ante el primer fallo junta captura + textos de pantalla + logcat (barato
    cuando pasa, caro solo cuando falla). Verbos: inicio (estado conocido:
    logcat 16M/limpio + force-stop + relanzar), limpiar_log (logcat -c sin
    force-stop), tap, escribir (teclea/tapea; acepta cadenas 'a+b+c'), permitir,
    esperar, esperar_log, verificar, no_debe, atras, captura, humano (pausa y
    pide el paso al usuario), pin (teclea un PIN sin valor de seguridad; SOLO
    con confirmado=True). 'archivo' es la ruta del guión (en 'origen', por
    defecto el mismo 'donde'); 'paquete' la app (o la directiva 'paquete <pkg>'
    dentro del guión). 'valores': pares "nombre=valor" separados por ';' que
    expanden las variables $nombre del guión (ej. guión con `pin $clave` y
    valores="clave=1234"): el valor vive SOLO en esta llamada — ni en el .txt
    ni en disco — y en los `pin` se enmascara en toda salida. 'confirmado'
    habilita los pasos pin de la corrida (un dale por corrida). ALEATORIOS por
    corrida: en 'valores', monto=rnd(10000,30000,500) resuelve UN valor que
    todos los $monto comparten; inline, $rnd(min,max,paso) y
    $rnd_opcion(10%,20%,30%) ruedan un valor por ocurrencia. Lo elegido sale
    en la traza y en la línea final ("Aleatorios: ..."). Como el cliente
    MCP corta las llamadas largas, un guión largo se pausa y devuelve 'seguí
    con desde=K'.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    org = None
    if origen and origen != donde:
        org, aviso = _resolver(origen)
        if aviso:
            return aviso
    try:
        res = GU.correr(lg, serial, archivo, origen=org, paquete=paquete,
                        desde=desde, confirmado=confirmado, valores=valores)
    except Exception as e:
        return f"error inesperado corriendo el guión: {e}"
    contenido = [res["texto"]]
    for png in res.get("imagenes", []):
        contenido.append(Image(data=png, format="png"))
    return contenido if len(contenido) > 1 else res["texto"]


@mcp.tool()
def datastore_get(serial: str, paquete: str, archivo: str,
                  donde: str = "local") -> str:
    """
    Lista las claves de un Jetpack DataStore (Preferences) de una app Android,
    con su tipo y valor decodificado. 'archivo' es el nombre del .preferences_pb
    en files/datastore/ del paquete (con o sin extensión, ej. "indicators_data").
    Solo lectura. Requiere que la app sea debuggable (usa run-as); en release no
    hay acceso. Útil para inspeccionar parámetros del POS en QA.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    return M.datastore_get(lg, serial, paquete, archivo)


@mcp.tool()
def datastore_set(serial: str, paquete: str, archivo: str, clave: str,
                  valor: str, tipo: str = "auto", donde: str = "local",
                  confirmado: bool = False) -> str:
    """
    Cambia el valor de UNA clave en un Jetpack DataStore (Preferences) de una app
    Android, dejando el resto del archivo intacto. Pensado para alternar
    parámetros en QA (ej. operativa REST/RETAIL) sin tener UI para ello.

    'archivo': nombre del .preferences_pb (ej. "indicators_data"). 'clave': la
    preferencia (ej. "operativa"). 'valor': el nuevo valor en texto. 'tipo':
    "auto" (por defecto) detecta y respeta el tipo actual de la clave; si no
    existe, hay que indicar tipo explícito (string/int/long/bool/float/double).

    DESTRUCTIVO (modifica datos persistentes de la app) => requiere
    confirmado=True. Antes de escribir hace backup en /sdcard y detiene la app
    (force-stop), porque DataStore cachea en memoria y podría sobrescribir el
    cambio. Tras escribir hay que relanzar la app (adb_relanzar) para que cargue.
    Requiere app debuggable (run-as); en release no funciona.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    if not confirmado:
        return (
            f"CONFIRMACIÓN REQUERIDA: vas a cambiar '{clave}' = '{valor}' "
            f"(tipo {tipo}) en el datastore '{archivo}' del paquete '{paquete}' "
            f"en '{donde}'.\n"
            f"Modifica datos persistentes de la app. Se hace backup en /sdcard y "
            f"se detiene la app antes de escribir; después hay que relanzarla.\n"
            f"Confirmá con el usuario y reintentá con confirmado=True."
        )
    return M.datastore_set(lg, serial, paquete, archivo, clave, valor, tipo)


# --- Gradle -----------------------------------------------------------------

@mcp.tool()
def gradle_build(proyecto: str, tarea: str = "assembleDebug",
                 donde: str = "local") -> str:
    """
    Compila un proyecto con su gradlew. En local Windows se lanza como TRABAJO
    asíncrono (con el fix del sandbox: JAVA_TOOL_OPTIONS redirige el socket
    AF_UNIX de los pipes NIO fuera del TMP) y esta función RETORNA AL TOQUE
    con el id del trabajo: seguirlo con run_esperar(id) hasta el código final
    (0 = BUILD SUCCESSFUL). Si falla, los errores de Kotlin son las líneas
    'e:' del out.log del job. En unix/remoto compila síncrono y devuelve la
    salida directamente.
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
    """
    Busca por NOMBRE de archivo en un proyecto. El patrón es REGEX (ej. '\\.apk$'),
    pero si no compila como regex y parece un glob (ej. '*.apk'), se interpreta
    como glob automáticamente en vez de tirar error.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return B.buscar_nombre(lg, proyecto, patron)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"


@mcp.tool()
def buscar_contenido(objetivo: str, patron: str, incluir: str = "",
                     antes: int = 0, despues: int = 0,
                     max_resultados: int = 200, donde: str = "local") -> str:
    """
    grep de contenido (regex) en un ARCHIVO o una CARPETA/proyecto.
    Si 'objetivo' es un archivo, busca solo en él (reemplaza al viejo
    buscar_en_archivo). Si es carpeta, recorre recursivo aplicando 'incluir'
    (globs separados por espacio; por defecto *.kt *.java *.xml *.kts *.gradle).

    'antes'/'despues': líneas de CONTEXTO antes/después de cada match (como -B/-A
    de grep). Con contexto, las líneas de alrededor salen con '-' (ruta-linea-
    texto) y los grupos se separan con '--' — así el match llega con su entorno
    sin un `leer` posterior. Salida sin contexto: ruta:linea: texto.

    'max_resultados' (por defecto 200): tope de coincidencias; al alcanzarlo corta
    y avisa, para que una búsqueda amplia no devuelva un muro. 0 = sin tope.
    """
    lg, aviso = _resolver(donde)
    if aviso:
        return aviso
    try:
        return B.buscar_contenido(lg, objetivo, patron,
                                  incluir.split() if incluir else None,
                                  antes=antes, despues=despues,
                                  max_resultados=max_resultados)
    except RutaFueraDeRaiz as e:
        return f"error: {e}"

def main():
    mcp.run()


if __name__ == "__main__":
    main()
