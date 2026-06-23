"""
ADB y Gradle, acotados por parámetros (no línea de comando libre).

ADB tiene dos coordenadas de "dónde": `donde` (qué máquina corre el binario adb)
y `serial` (qué dispositivo de esa máquina). Gradle invoca el `gradlew` del
proyecto.
"""

from __future__ import annotations

import os

from .config import Lugar
from .seguridad import normalizar
from . import transporte as T


# --- ADB --------------------------------------------------------------------

def adb_devices(lugar: Lugar) -> T.Resultado:
    return T.ejecutar(lugar, ["adb", "devices", "-l"])


def adb_shell(lugar: Lugar, serial: str, comando: str) -> T.Resultado:
    """
    Ejecuta `adb -s <serial> shell <comando>`. Acotado: siempre invoca adb shell;
    'comando' es lo que corre dentro del shell del dispositivo.
    """
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", comando])


def adb_logcat(lugar: Lugar, serial: str, tags: str = "", nivel: str = "V",
               lineas: int = 200, limpiar_antes: bool = False) -> T.Resultado:
    """
    Captura logcat del dispositivo en modo DUMP (-d): vuelca lo que hay y sale,
    NO se queda en streaming (que colgaría la tool). 'tags': uno o varios tags
    separados por coma (p. ej. "NavMenuOperacion,AnulacionScreen"); vacío = todo.
    'nivel': mínimo (V/D/I/W/E). 'lineas': cuántas líneas finales devolver (tail).
    'limpiar_antes': si True, hace 'logcat -c' antes para capturar solo lo nuevo
    (útil: limpiar, reproducir el caso en el POS, luego capturar).
    """
    if limpiar_antes:
        T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-c"])
        return T.Resultado(0, "logcat limpiado. Reproducí el caso y volvé a "
                              "llamar adb_logcat sin limpiar_antes para capturar.", "")
    args = ["adb", "-s", serial, "logcat", "-d"]
    if tags:
        # Filtro por tag: "Tag:Nivel ... *:S" silencia el resto.
        for t in [x.strip() for x in tags.split(",") if x.strip()]:
            args.append(f"{t}:{nivel}")
        args.append("*:S")
    else:
        args.append(f"*:{nivel}")
    r = T.ejecutar(lugar, args, timeout=30)
    # tail: quedarnos con las últimas 'lineas' para no inundar.
    if r.ok and r.salida:
        partes = r.salida.splitlines()
        if len(partes) > lineas:
            r = T.Resultado(r.codigo, "\n".join(partes[-lineas:]), r.error)
    return r


def adb_install(lugar: Lugar, serial: str, apk: str, reemplazar: bool = True) -> T.Resultado:
    # Normalizar el APK como las tools de archivo: acepta ruta relativa (la
    # resuelve contra la raíz del lugar) o absoluta, y la acota a la raíz. Así
    # adb recibe siempre una ruta absoluta y no falla por interpretarla desde
    # su propio directorio de trabajo.
    apk_abs = str(normalizar(lugar.raiz, apk)) if lugar.es_local else apk
    args = ["adb", "-s", serial, "install"]
    if reemplazar:
        args.append("-r")
    args.append(apk_abs)
    return T.ejecutar(lugar, args, timeout=300)


def adb_forcestop(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", paquete])


def adb_relanzar(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    """force-stop seguido de monkey -p para relanzar la app."""
    return T.ejecutar(
        lugar,
        ["adb", "-s", serial, "shell", "monkey", "-p", paquete,
         "-c", "android.intent.category.LAUNCHER", "1"],
    )


# --- Gradle -----------------------------------------------------------------

def gradle_build(lugar: Lugar, proyecto: str, tarea: str) -> str:
    """
    Compila con el gradlew del proyecto.

    En unix/remoto compila síncrono y devuelve la salida. En local Windows NO
    puede compilar: el build necesita sockets loopback que el sandbox del cliente
    MCP bloquea (ver Notas técnicas del README). Devuelve un aviso para correr el
    build en una terminal propia.
    """
    if lugar.es_local:
        p = normalizar(lugar.raiz, proyecto)
        if lugar.es_windows:
            return (
                "No puedo compilar desde acá: el sandbox del cliente MCP bloquea "
                "los sockets loopback que Gradle/Java necesitan. Corré el build en "
                "tu terminal:\n"
                f'    cd "{p}"\n'
                f"    .\\gradlew {tarea}\n"
                "Una vez generado el APK, puedo desplegarlo con adb_install."
            )
        salida = T.ejecutar(lugar, ["./gradlew", tarea], cwd=str(p), timeout=1800)
        return _fmt_resultado(salida)
    salida = T.ejecutar(lugar, f"cd '{proyecto}' && ./gradlew {tarea}", timeout=1800)
    return _fmt_resultado(salida)


def _fmt_resultado(r: T.Resultado) -> str:
    cuerpo = (r.salida or "").rstrip()
    if r.error:
        cuerpo += ("\n--- stderr ---\n" + r.error.rstrip())
    return f"[código {r.codigo}]\n{cuerpo}".rstrip()


# --- Android DataStore (Preferences protobuf) -------------------------------
# Las apps Jetpack DataStore (Preferences) guardan sus prefs en archivos
# <nombre>.preferences_pb dentro de files/datastore/ del paquete. El formato es
# un protobuf: mensaje raiz con un map (field 1, repetido) de
# entry{ key(1,string), value(2,Value) }, donde Value es un oneof por tipo:
#   field 1 -> bool, 2 -> float, 3 -> int (int32/varint), 4 -> long (int64),
#   field 5 -> string, 6 -> double, 7 -> string_set.
# Esto permite leer/escribir una pref sin tener la app, util para alternar
# parametros en QA. Requiere run-as (app debuggable); en release no hay acceso.

# Mapa field-de-Value -> nombre de tipo legible.
_DS_TIPO_POR_FIELD = {1: "bool", 2: "float", 3: "int", 4: "long",
                      5: "string", 6: "double", 7: "string_set"}
_DS_FIELD_POR_TIPO = {v: k for k, v in _DS_TIPO_POR_FIELD.items()}


def _ds_read_varint(b: bytes, i: int):
    shift = 0
    res = 0
    while True:
        byte = b[i]
        i += 1
        res |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return res, i


def _ds_encode_varint(n: int) -> bytes:
    out = bytearray()
    if n < 0:
        # complemento a dos en 64 bits (para int/long negativos)
        n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)


def _ds_parse(b: bytes, i: int, end: int):
    """Parser de wire format: devuelve [(field, wire_type, valor, raw_bytes)]."""
    out = []
    while i < end:
        start = i
        tag, i = _ds_read_varint(b, i)
        field = tag >> 3
        wt = tag & 7
        if wt == 0:
            val, i = _ds_read_varint(b, i)
            out.append((field, 0, val, b[start:i]))
        elif wt == 2:
            ln, i = _ds_read_varint(b, i)
            chunk = b[i:i + ln]
            i += ln
            out.append((field, 2, chunk, b[start:i]))
        elif wt == 5:  # 32-bit (float)
            chunk = b[i:i + 4]
            i += 4
            out.append((field, 5, chunk, b[start:i]))
        elif wt == 1:  # 64-bit (double)
            chunk = b[i:i + 8]
            i += 8
            out.append((field, 1, chunk, b[start:i]))
        else:
            raise ValueError(f"wire type {wt} no soportado (field {field})")
    return out


def _ds_decode_value(valmsg: bytes):
    """Decodifica un mensaje Value -> (tipo, valor_python)."""
    import struct
    for vf, vwt, vv, _ in _ds_parse(valmsg, 0, len(valmsg)):
        tipo = _DS_TIPO_POR_FIELD.get(vf)
        if tipo == "bool":
            return tipo, bool(vv)
        if tipo in ("int", "long"):
            return tipo, int(vv)
        if tipo == "string":
            return tipo, vv.decode("utf-8", "replace")
        if tipo == "float":
            return tipo, struct.unpack("<f", vv)[0]
        if tipo == "double":
            return tipo, struct.unpack("<d", vv)[0]
        if tipo == "string_set":
            # set: sub-mensaje con strings repetidos en field 1
            vals = [s.decode("utf-8", "replace")
                    for sf, swt, s, _ in _ds_parse(vv, 0, len(vv)) if sf == 1]
            return tipo, vals
    return "desconocido", None


def _ds_encode_value(tipo: str, valor: str) -> bytes:
    """Codifica un Value protobuf desde un valor en texto y su tipo."""
    import struct
    field = _DS_FIELD_POR_TIPO.get(tipo)
    if field is None:
        raise ValueError(f"tipo '{tipo}' no soportado")
    if tipo == "bool":
        v = 1 if str(valor).strip().lower() in ("1", "true", "si", "sí", "yes") else 0
        return bytes([(field << 3) | 0]) + _ds_encode_varint(v)
    if tipo in ("int", "long"):
        return bytes([(field << 3) | 0]) + _ds_encode_varint(int(valor))
    if tipo == "string":
        s = valor.encode("utf-8")
        return bytes([(field << 3) | 2]) + _ds_encode_varint(len(s)) + s
    if tipo == "float":
        return bytes([(field << 3) | 5]) + struct.pack("<f", float(valor))
    if tipo == "double":
        return bytes([(field << 3) | 1]) + struct.pack("<d", float(valor))
    raise ValueError(f"tipo '{tipo}' no soportado para escritura")


def _ds_ruta(paquete: str, archivo: str) -> str:
    """Ruta relativa del .preferences_pb dentro del run-as del paquete."""
    nombre = archivo if archivo.endswith(".preferences_pb") else f"{archivo}.preferences_pb"
    return f"files/datastore/{nombre}"


def _ds_leer_bytes(lugar: Lugar, serial: str, paquete: str, archivo: str) -> bytes:
    """Lee el .preferences_pb del device via run-as + base64."""
    import base64
    ruta = _ds_ruta(paquete, archivo)
    r = T.ejecutar(lugar, ["adb", "-s", serial, "shell",
                           "run-as", paquete, "base64", ruta])
    if not r.ok or not r.salida.strip():
        raise ValueError(
            f"no pude leer {ruta} (¿app debuggable? ¿paquete/archivo correctos?). "
            f"salida: {r.salida.strip()} {r.error.strip()}")
    return base64.b64decode(r.salida.strip())


def datastore_get(lugar: Lugar, serial: str, paquete: str, archivo: str) -> str:
    """Lista todas las claves del datastore con su tipo y valor decodificado."""
    try:
        data = _ds_leer_bytes(lugar, serial, paquete, archivo)
    except ValueError as e:
        return f"error: {e}"
    lineas = [f"datastore: {_ds_ruta(paquete, archivo)} ({len(data)} bytes)"]
    for field, wt, chunk, _ in _ds_parse(data, 0, len(data)):
        if field != 1 or wt != 2:
            continue
        key = None
        valmsg = None
        for sf, swt, sval, _ in _ds_parse(chunk, 0, len(chunk)):
            if sf == 1:
                key = sval.decode("utf-8", "replace")
            elif sf == 2:
                valmsg = sval
        if key is None:
            continue
        if valmsg is None:
            lineas.append(f"  {key} = (vacío)")
            continue
        tipo, valor = _ds_decode_value(valmsg)
        lineas.append(f"  {key} [{tipo}] = {valor!r}")
    return "\n".join(lineas)


def datastore_set(lugar: Lugar, serial: str, paquete: str, archivo: str,
                  clave: str, valor: str, tipo: str = "auto") -> str:
    """
    Cambia el valor de una clave en el datastore, conservando el resto intacto.
    Hace backup en /sdcard, detiene la app (DataStore cachea en memoria), escribe
    y avisa de relanzar. tipo='auto' detecta y respeta el tipo actual de la clave.
    """
    import base64
    try:
        data = _ds_leer_bytes(lugar, serial, paquete, archivo)
    except ValueError as e:
        return f"error: {e}"

    # Localizar la clave y detectar su tipo actual.
    entries = _ds_parse(data, 0, len(data))
    tipo_actual = None
    existe = False
    for field, wt, chunk, _ in entries:
        if field != 1 or wt != 2:
            continue
        key = None
        valmsg = None
        for sf, swt, sval, _ in _ds_parse(chunk, 0, len(chunk)):
            if sf == 1:
                key = sval.decode("utf-8", "replace")
            elif sf == 2:
                valmsg = sval
        if key == clave:
            existe = True
            if valmsg is not None:
                tipo_actual, _ = _ds_decode_value(valmsg)
            break

    if tipo == "auto":
        if not existe:
            return (f"error: la clave '{clave}' no existe en el datastore; "
                    f"'auto' solo sirve para claves existentes. Indicá 'tipo' "
                    f"explícito (string/int/long/bool/float/double) para crearla.")
        if tipo_actual in (None, "desconocido"):
            return (f"error: no pude detectar el tipo actual de '{clave}'. "
                    f"Indicá 'tipo' explícito.")
        tipo_final = tipo_actual
    else:
        tipo_final = tipo

    if tipo_final not in _DS_FIELD_POR_TIPO:
        return (f"error: tipo '{tipo_final}' no soportado. "
                f"Válidos: {', '.join(_DS_FIELD_POR_TIPO)}.")

    # Reconstruir el protobuf: copiar entries tal cual, salvo la clave objetivo.
    try:
        nuevo_valmsg = _ds_encode_value(tipo_final, valor)
    except (ValueError, TypeError) as e:
        return f"error: no pude codificar el valor '{valor}' como {tipo_final}: {e}"

    key_bytes = clave.encode("utf-8")
    entry_obj = (bytes([0x0A]) + _ds_encode_varint(len(key_bytes)) + key_bytes +
                 bytes([0x12]) + _ds_encode_varint(len(nuevo_valmsg)) + nuevo_valmsg)
    entry_full = bytes([0x0A]) + _ds_encode_varint(len(entry_obj)) + entry_obj

    nuevo = bytearray()
    reemplazada = False
    for field, wt, chunk, raw in entries:
        if field == 1 and wt == 2:
            key = None
            for sf, swt, sval, _ in _ds_parse(chunk, 0, len(chunk)):
                if sf == 1:
                    key = sval.decode("utf-8", "replace")
                    break
            if key == clave:
                nuevo += entry_full
                reemplazada = True
                continue
        nuevo += raw
    if not reemplazada:
        nuevo += entry_full  # clave nueva al final
    nuevo_b64 = base64.b64encode(bytes(nuevo)).decode()

    ruta = _ds_ruta(paquete, archivo)
    # Los comandos con redirect/pipe se pasan como UN solo string remoto dentro de
    # `adb shell` (mismo patron que adb_shell, que funciona): el `run-as ... sh -c
    # '...'` viaja entero al device y el `cat > ruta_relativa` resuelve en el home
    # del paquete. Si se trocea en tokens de lista, adb los reensambla y el `>` se
    # evalua en el contexto equivocado (run-as pierde el cwd). Comillas simples
    # protegen el comando remoto.
    def _adb_sh(remoto: str) -> "T.Resultado":
        return T.ejecutar(lugar, ["adb", "-s", serial, "shell", remoto])

    # 1) Backup del original en /sdcard.
    bak = f"/sdcard/{archivo.replace('/', '_')}.{serial}.bak.pb"
    _adb_sh(f"run-as {paquete} sh -c 'cat {ruta} > {bak}'")
    # 2) Detener la app para que no sobrescriba el cambio desde su cache.
    T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", paquete])
    # 3) Escribir el nuevo .pb: base64 -> /sdcard -> run-as cat al datastore.
    tmp = f"/sdcard/{archivo.replace('/', '_')}.{serial}.new.pb"
    w = _adb_sh(f"sh -c 'echo {nuevo_b64} | base64 -d > {tmp}'")
    if not w.ok:
        return f"error: no pude escribir el archivo temporal en el device: {w.error.strip()}"
    cp = _adb_sh(f"run-as {paquete} sh -c 'cat {tmp} > {ruta}'")
    if not cp.ok:
        return f"error: no pude copiar al datastore via run-as: {cp.error.strip()}"

    # 4) Verificar releyendo.
    try:
        verif = _ds_leer_bytes(lugar, serial, paquete, archivo)
        tipo_v = None
        valor_v = None
        for field, wt, chunk, _ in _ds_parse(verif, 0, len(verif)):
            if field != 1 or wt != 2:
                continue
            k = None
            vm = None
            for sf, swt, sval, _ in _ds_parse(chunk, 0, len(chunk)):
                if sf == 1:
                    k = sval.decode("utf-8", "replace")
                elif sf == 2:
                    vm = sval
            if k == clave and vm is not None:
                tipo_v, valor_v = _ds_decode_value(vm)
                break
    except ValueError as e:
        return f"escrito, pero no pude verificar: {e}"

    return (f"OK: '{clave}' [{tipo_final}] = {valor_v!r} en {ruta}.\n"
            f"Backup: {bak}. App detenida (force-stop): relanzala con "
            f"adb_relanzar para que cargue el cambio.")
