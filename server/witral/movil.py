"""
ADB y Gradle, acotados por parámetros (no línea de comando libre).

ADB tiene dos coordenadas de "dónde": `donde` (qué máquina corre el binario adb)
y `serial` (qué dispositivo de esa máquina). Gradle invoca el `gradlew` del
proyecto.
"""

from __future__ import annotations

import os
import re

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
        return T.Resultado(0, "logcat limpiado. Reproducir el caso y volver a "
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


def _adb_modelo(lugar: Lugar, serial: str) -> str | None:
    """Modelo legible del dispositivo (ro.product.model), o None si no se puede."""
    try:
        r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", "getprop",
                               "ro.product.model"], timeout=15)
        m = (r.salida or "").strip()
        return m or None
    except Exception:
        return None


def adb_install(lugar: Lugar, serial: str, apk: str, reemplazar: bool = True,
                permitir_downgrade: bool = False) -> T.Resultado:
    # Normalizar el APK como las tools de archivo: acepta ruta relativa (la
    # resuelve contra la raíz del lugar) o absoluta, y la acota a la raíz. Así
    # adb recibe siempre una ruta absoluta y no falla por interpretarla desde
    # su propio directorio de trabajo.
    apk_abs = str(normalizar(lugar.raiz, apk)) if lugar.es_local else apk
    args = ["adb", "-s", serial, "install"]
    if reemplazar:
        args.append("-r")
    if permitir_downgrade:
        args.append("-d")
    args.append(apk_abs)
    r = T.ejecutar(lugar, args, timeout=300)
    # Encabezar con modelo + serial: entre pruebas el POS puede cambiar de serial
    # y eso explica params/estado inesperados; que la respuesta lo deje claro.
    modelo = _adb_modelo(lugar, serial)
    quien = f"{modelo} (serial {serial})" if modelo else f"serial {serial}"
    salida = f"Dispositivo: {quien}\n{r.salida or ''}".rstrip()
    error = r.error
    salida += _diagnostico_install(lugar, serial, apk, r, permitir_downgrade)
    return T.Resultado(r.codigo, salida, error)


# Fallos de install cuya causa real no se deduce del mensaje de adb. La
# traducción va en la MISMA respuesta: leer "INSTALL_FAILED_VERSION_DOWNGRADE"
# no dice que el problema es la build que ya está en el equipo.
def _diagnostico_install(lugar: Lugar, serial: str, apk: str,
                         r: T.Resultado, ya_downgrade: bool) -> str:
    texto = f"{r.salida or ''}\n{r.error or ''}"
    if "INSTALL_FAILED_VERSION_DOWNGRADE" in texto:
        extra = ""
        if not ya_downgrade:
            extra = ("\nReintentar con permitir_downgrade=True (agrega -d) para "
                     "instalar igual sin desinstalar la app ni perder sus datos.")
        return ("\n\nCAUSA: en el equipo hay instalada una build con versionCode "
                "MAYOR que la del APK. Android rechaza el downgrade aunque el "
                "versionName parezca el mismo, porque compara versionCode."
                + extra +
                "\nPara ver qué está instalado: adb_estado_app(serial, paquete).")
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in texto:
        return ("\n\nCAUSA: la app instalada está firmada con otra clave (típico "
                "entre una build de release y una de debug). No hay flag que lo "
                "salte: hay que desinstalar la existente, lo que borra sus datos.")
    if "INSTALL_FAILED_INSUFFICIENT_STORAGE" in texto:
        return "\n\nCAUSA: no hay espacio en el dispositivo."
    if "device unauthorized" in texto or "unauthorized" in texto:
        return ("\n\nCAUSA: el equipo no tiene autorizada la depuración para esta "
                "máquina. Aceptar el diálogo de depuración USB en la pantalla.")
    return ""


def adb_estado_app(lugar: Lugar, serial: str, paquete: str) -> str:
    """
    Qué build está instalada: versionName, versionCode, cuándo se instaló y se
    actualizó por última vez, el instalador y la ruta del APK.

    Es la pregunta natural después de cada install, y a mano son varios
    `dumpsys package | grep` seguidos.
    """
    r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", "dumpsys", "package",
                           paquete], timeout=60)
    texto = r.salida or ""
    if not texto.strip() or "Unable to find package" in texto:
        return (f"El paquete '{paquete}' no está instalado en {serial} "
                f"(o el serial no corresponde a ningún equipo conectado).")
    campos = (
        ("versionName", r"versionName=(\S+)"),
        ("versionCode", r"versionCode=(\d+)"),
        ("minSdk", r"minSdk=(\d+)"),
        ("targetSdk", r"targetSdk=(\d+)"),
        ("firstInstallTime", r"firstInstallTime=(.+)"),
        ("lastUpdateTime", r"lastUpdateTime=(.+)"),
        ("installerPackageName", r"installerPackageName=(\S+)"),
        ("codePath", r"codePath=(\S+)"),
        ("flags", r"flags=\[([^\]]*)\]"),
    )
    lineas = [f"{paquete} en {serial}:"]
    for etiqueta, patron in campos:
        m = re.search(patron, texto)
        if m:
            lineas.append(f"  {etiqueta}: {m.group(1).strip()}")
    if "DEBUGGABLE" in texto:
        lineas.append("  debuggable: sí (run-as disponible: datastore_*, "
                      "sqlite y adb_pull sobre datos de la app)")
    return "\n".join(lineas)


def adb_pull(lugar: Lugar, serial: str, remoto: str, destino: str,
             paquete: str = "") -> T.Resultado:
    """
    Trae un archivo del dispositivo al lugar. Con 'paquete', usa run-as para
    alcanzar el sandbox privado de una app DEBUGGABLE (donde `adb pull` directo
    no llega): `run-as <paquete> cat <ruta>` y el binario se escribe aquí.
    Sin 'paquete', es un `adb pull` normal.
    """
    destino_abs = str(normalizar(lugar.raiz, destino)) if lugar.es_local else destino
    if not paquete:
        return T.ejecutar(lugar, ["adb", "-s", serial, "pull", remoto,
                                  destino_abs], timeout=300)
    if not lugar.es_local:
        return T.Resultado(2, "", "pull con run-as solo está implementado en "
                                  "lugares locales por ahora.")
    # exec-out y captura en BYTES CRUDOS, igual que adb_captura: pasar un .db
    # por la decodificación de texto del transporte lo destruiría. Por eso aquí
    # se invoca subprocess directo en vez de T.ejecutar.
    import subprocess as _sp
    try:
        p = _sp.run(["adb", "-s", serial, "exec-out", "run-as", paquete,
                     "cat", remoto], capture_output=True, timeout=300)
    except Exception as e:
        return T.Resultado(1, "", f"no se pudo traer {remoto}: {e}")
    datos = p.stdout or b""
    if p.returncode != 0 or not datos:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        return T.Resultado(p.returncode or 1, "", (
            f"run-as {paquete} cat {remoto} falló: {err or 'sin salida'}\n"
            f"run-as requiere que la app sea DEBUGGABLE; verificarlo con "
            f"adb_estado_app(serial, paquete)."))
    from pathlib import Path as _P
    ruta = _P(destino_abs)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_bytes(datos)
    return T.Resultado(0, f"Traído {remoto} -> {destino} ({len(datos)} bytes) "
                          f"vía run-as {paquete}.", "")


def adb_push(lugar: Lugar, serial: str, origen: str, remoto: str) -> T.Resultado:
    """Sube un archivo del lugar al dispositivo (adb push)."""
    origen_abs = str(normalizar(lugar.raiz, origen)) if lugar.es_local else origen
    return T.ejecutar(lugar, ["adb", "-s", serial, "push", origen_abs, remoto],
                      timeout=300)


def adb_forcestop(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", paquete])


def adb_relanzar(lugar: Lugar, serial: str, paquete: str) -> T.Resultado:
    """force-stop seguido de monkey -p para relanzar la app."""
    return T.ejecutar(
        lugar,
        ["adb", "-s", serial, "shell", "monkey", "-p", paquete,
         "-c", "android.intent.category.LAUNCHER", "1"],
    )


# --- Captura de pantalla y árbol de vistas ----------------------------------

def adb_captura(lugar: Lugar, serial: str) -> bytes:
    """
    Captura la pantalla del dispositivo y devuelve los BYTES PNG, en UNA pasada
    (sin el rodeo screencap -> pull -> stage). Usa `exec-out screencap -p`, que
    entrega el PNG por stdout sin la traducción CRLF de `adb shell`. En local se
    captura directo a memoria; en remoto se vuelca a un temporal del host y se
    baja por SFTP (bytes intactos, nunca se decodifican como texto).
    """
    import subprocess as _sp
    if lugar.es_local:
        try:
            p = _sp.run(["adb", "-s", serial, "exec-out", "screencap", "-p"],
                        capture_output=True, timeout=60)
        except Exception as e:
            raise ValueError(f"no pude capturar (serial {serial}): {e}")
        data = p.stdout or b""
        if p.returncode != 0 or not data:
            err = (p.stderr or b"").decode("utf-8", "replace").strip()
            raise ValueError(f"screencap falló (serial {serial}): {err or 'sin salida'}")
    else:
        tmp = f"/tmp/witral_cap_{serial}.png"
        r = T.ejecutar(
            lugar,
            f"adb -s {T.comillas(serial)} exec-out screencap -p > {T.comillas(tmp)}",
            timeout=60)
        if not r.ok:
            raise ValueError(f"screencap remoto falló (serial {serial}): "
                             f"{r.error.strip() or r.salida.strip()}")
        try:
            data = T.leer_remoto(lugar, tmp)
        finally:
            T.ejecutar(lugar, f"rm -f {T.comillas(tmp)}")
    if not data.startswith(b"\x89PNG"):
        raise ValueError("la captura no es un PNG válido (¿exec-out no soportado "
                         "o pantalla protegida/DRM?).")
    return data


_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")

# Textos que en un POS NO se tapean sin pedido explícito (lista negra). Se
# comparan normalizados (sin acentos, minúsculas, contains). tap_texto y el
# runner de guiones se niegan salvo confirmación explícita.
_LISTA_NEGRA = ("cierre de turno", "cierre de lote", "anulacion",
                "borrar llaves", "reversa", "devolucion")


def _norm(s: str) -> str:
    """Normaliza para matchear texto de UI: sin acentos, minúsculas, espacios colapsados."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def es_peligroso(texto: str) -> bool:
    """¿'texto' cae en la lista negra del POS (contains normalizado)?"""
    n = _norm(texto)
    return any(bl in n for bl in _LISTA_NEGRA)


def _ui_dump_xml(lugar: Lugar, serial: str):
    """Corre uiautomator dump y devuelve (xml, None) o (None, mensaje_error).
    UN solo round-trip adb: dump a un path fijo y cat encadenados en el device
    (antes eran dos llamadas). uiautomator imprime 'UI hierchary dumped to: ...'
    y el cat imprime el XML; el parser salta al primer '<', así que el prefijo no
    molesta. Si el dump falla (p. ej. 'null root node'), el && corta y queda el
    mensaje de error (sin '<') para reportarlo."""
    ruta = "/sdcard/witral_ui.xml"
    cmd = f"uiautomator dump {ruta} && cat {ruta}"
    r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", cmd], timeout=40)
    salida = r.salida or ""
    i = salida.find("<")
    if i < 0:
        err = f"{salida} {r.error or ''}".strip()
        if "ERROR" in err.upper():
            return None, f"uiautomator dump falló: {err[:200]}"
        return None, f"sin XML: {err[:200]}"
    return salida[i:], None


def _ui_nodos(lugar: Lugar, serial: str) -> list:
    """Parsea el dump a una lista de nodos (dicts texto/desc/clase/rid/clk/cx/cy)."""
    xml_txt, err = _ui_dump_xml(lugar, serial)
    if xml_txt is None:
        raise ValueError(err)
    import xml.etree.ElementTree as ET
    try:
        root = ET.fromstring(xml_txt)
    except Exception as e:
        raise ValueError(f"no pude parsear el dump: {e}")
    nodos = []
    for nodo in root.iter("node"):
        a = nodo.attrib
        mb = _BOUNDS_RE.search(a.get("bounds") or "")
        if mb:
            x1, y1, x2, y2 = map(int, mb.groups())
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        else:
            cx = cy = 0
        nodos.append({
            "texto": (a.get("text") or "").strip(),
            "desc": (a.get("content-desc") or "").strip(),
            "clase": (a.get("class") or "").rsplit(".", 1)[-1],
            "rid": (a.get("resource-id") or "").rsplit("/", 1)[-1],
            "clk": a.get("clickable") == "true",
            "cx": cx, "cy": cy,
        })
    return nodos


def _firma(nodos: list) -> tuple:
    return tuple(sorted((n["texto"], n["desc"], n["cx"], n["cy"]) for n in nodos))


def _ui_nodos_estable(lugar: Lugar, serial: str):
    """Dos volcados con una pausa: un dump agarrado en medio de una transición
    devuelve la pantalla anterior. Devuelve (nodos_del_2do, estable); 'estable'
    es True solo si ambos coinciden — el llamador decide si confiar.

    Tolera el error transitorio de uiautomator ('null root node' mientras la app
    carga o anima): si un volcado falla, NO propaga la excepción — devuelve
    ([], False) o el volcado que sí salió, para que el poll de esperar/tap
    reintente hasta el timeout en vez de crashear la tool."""
    import time as _t
    try:
        a = _ui_nodos(lugar, serial)
    except ValueError:
        a = None
    _t.sleep(0.35)
    try:
        b = _ui_nodos(lugar, serial)
    except ValueError:
        b = None
    if b is None:
        return ([], False) if a is None else (a, False)
    if a is None:
        return b, False
    return b, (_firma(a) == _firma(b))


def _ui_nodos_seguro(lugar: Lugar, serial: str) -> list:
    """UN solo volcado, tolerante a error (devuelve [] si falla). Rápido: para
    los poll loops de tap/esperar, donde el reintento del loop ya cubre las
    transiciones sin pagar el doble volcado de _ui_nodos_estable."""
    try:
        return _ui_nodos(lugar, serial)
    except ValueError:
        return []


def _buscar_nodo(nodos: list, texto: str, parcial: bool = True):
    """Busca un nodo por texto o content-desc. Prioridad: desc exacto, texto
    exacto, desc parcial, texto parcial (en Compose los IDs vienen vacíos: se
    matchea por texto/desc, y se prefiere desc, que cambia menos)."""
    objetivo = _norm(texto)
    if not objetivo:
        return None
    for n in nodos:
        if _norm(n["desc"]) == objetivo:
            return n
    for n in nodos:
        if _norm(n["texto"]) == objetivo:
            return n
    if parcial:
        for n in nodos:
            if n["desc"] and objetivo in _norm(n["desc"]):
                return n
        for n in nodos:
            if n["texto"] and objetivo in _norm(n["texto"]):
                return n
    return None


def _tap_xy(lugar: Lugar, serial: str, x: int, y: int):
    return T.ejecutar(lugar, ["adb", "-s", serial, "shell", "input", "tap",
                              str(x), str(y)])


def _ahora_device(lugar: Lugar, serial: str) -> str:
    """Hora del dispositivo en formato de logcat ('MM-DD HH:MM:SS.mmm'), para
    filtrar solo lo NUEVO al esperar en el log."""
    r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", "date",
                           "+%m-%d %H:%M:%S.000"], timeout=15)
    return (r.salida or "").strip()


def _tags_filtro(tags: str) -> list:
    tl = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    if not tl:
        return []
    return [f"{t}:V" for t in tl] + ["*:S"]


def adb_ui(lugar: Lugar, serial: str, solo_clickeables: bool = False) -> str:
    """
    Vuelca el árbol de vistas (uiautomator dump) parseado: por cada nodo con
    texto / content-desc / clickable, el CENTRO (x,y) para tapear por texto en
    vez de por píxel, si es clickeable, su clase y su resource-id.
    """
    try:
        nodos = _ui_nodos(lugar, serial)
    except ValueError as e:
        return f"error: {e} (serial {serial})"
    filas = []
    for n in nodos:
        if solo_clickeables and not n["clk"]:
            continue
        etiqueta = n["texto"] or n["desc"]
        if not etiqueta and not n["clk"]:
            continue
        centro = f"({n['cx']},{n['cy']})" if (n["cx"] or n["cy"]) else "(?,?)"
        marca = "clk" if n["clk"] else "   "
        et = f'"{etiqueta}"' if etiqueta else "(sin texto)"
        extra = f" id={n['rid']}" if n["rid"] else ""
        filas.append(f"{centro:>13} {marca}  {et}  [{n['clase']}]{extra}")
    if not filas:
        return ("(sin elementos con texto/desc/clickables; ¿pantalla vacía, "
                "protegida, o hay que encender la pantalla?)")
    enc = (f"UI de {serial} — {len(filas)} elementos. El centro (x,y) es la coord "
           f"para 'adb_shell input tap x y':")
    return enc + "\n" + "\n".join(filas)


def adb_tap_texto(lugar: Lugar, serial: str, texto: str, timeout: int = 12,
                  parcial: bool = True, confirmado: bool = False) -> str:
    """
    Busca 'texto' (o content-desc) en pantalla, saca su centro y tapea. ESPERA a
    que aparezca (hasta 'timeout' s, tope 40) en vez de tapear al vacío. Reemplaza
    el ciclo volcado -> leer -> elegir -> tapear por una llamada, y mata el error
    de coordenada vieja. Si el texto está en la lista negra del POS, se niega
    salvo confirmado=True.
    """
    # Si trae '+', es una SECUENCIA sobre la misma pantalla (ej. '10%+Continuar').
    if "+" in texto:
        return _ejecutar_cadena(lugar, serial, texto, timeout, confirmado)
    if es_peligroso(texto) and not confirmado:
        return (f"BLOQUEADO: '{texto}' está en la lista negra del POS "
                f"(cierre/anulación/borrar llaves/reversa/devolución). "
                f"Si es intencional, reintentar con confirmado=True.")
    import time as _t
    timeout = min(max(1, timeout), 40)
    t0 = _t.time()
    ultimos = []
    # SEN20260804 El nodo puede estar ANIMANDO: entrada de pantalla tras
    # relanzar, popup que se despliega, lista que asienta. En un frame
    # intermedio el volcado ya trae el texto, pero en una coordenada que no es
    # la final -> el tap pega al vacío y el guión falla sin motivo aparente
    # (visto el 04-08: el icono del selector de operación dumpeado en x=413
    # mientras animaba, cuando su lugar definitivo es x=351).
    # Por eso no se tapea el primer match: se exige que DOS volcados seguidos
    # den el mismo centro. En pantalla quieta no cuesta un volcado extra — es
    # el mismo poll de siempre, solo que se tapea en la segunda vuelta.
    previo = None
    while True:
        nodos = _ui_nodos_seguro(lugar, serial)
        n = _buscar_nodo(nodos, texto, parcial)
        vencido = _t.time() - t0 >= timeout
        if n and (n["cx"] or n["cy"]):
            actual = (n["cx"], n["cy"])
            # Al vencer el plazo se tapea igual la última posición conocida
            # (mejor intento) en vez de fallar: es lo que hacía antes.
            if previo == actual or vencido:
                _tap_xy(lugar, serial, n["cx"], n["cy"])
                et = n["desc"] or n["texto"] or "(sin texto)"
                aviso = "" if previo == actual else " [sin confirmar: seguía moviéndose]"
                return (f'OK: tap "{et}" en ({n["cx"]},{n["cy"]}) '
                        f'[{n["clase"]}]{aviso}')
            previo = actual
        else:
            previo = None
            ultimos = [(nn["desc"] or nn["texto"]) for nn in nodos
                       if (nn["desc"] or nn["texto"])]
            if vencido:
                vis = ", ".join(f'"{v}"' for v in ultimos[:12]) or "(ninguno con texto)"
                return f'no encontré "{texto}" tras {timeout}s. Textos visibles: {vis}'
        _t.sleep(0.2)


def _es_solo_digitos(s: str) -> bool:
    s = s.strip()
    return bool(s) and s.isdigit()


def _mapa_digitos(nodos: list) -> dict:
    """Dígito -> centro del botón. Prefiere 'Botón número N' (content-desc, más
    estable); si no, un nodo cuyo texto es exactamente ese dígito."""
    mapa: dict = {}
    for n in nodos:
        md = re.match(r"boton numero (\d)\b", _norm(n["desc"]))
        if md and (n["cx"] or n["cy"]):
            mapa.setdefault(md.group(1), (n["cx"], n["cy"]))
    for n in nodos:
        t = n["texto"].strip()
        if len(t) == 1 and t.isdigit() and (n["cx"] or n["cy"]):
            mapa.setdefault(t, (n["cx"], n["cy"]))
    return mapa


def _ejecutar_cadena(lugar: Lugar, serial: str, arg: str, timeout: int = 12,
                     confirmado: bool = False) -> str:
    """
    Ejecuta una secuencia de acciones separadas por '+' sobre la MISMA pantalla,
    con UN solo volcado: cada segmento se TECLEA si es solo dígitos, o se TAPEA
    si es texto. Ej.: '4730+Continuar' (teclea 4730, tapea Continuar);
    '10%+Continuar' (tapea 10%, tapea Continuar). Todos los targets deben estar
    en la misma pantalla; para cruzar pantallas, usar pasos separados.
    """
    tokens = [t.strip() for t in arg.split("+") if t.strip()]
    if not tokens:
        return "error: secuencia vacía."
    for tk in tokens:  # lista negra: ningún tap peligroso sin confirmar
        if not _es_solo_digitos(tk) and es_peligroso(tk) and not confirmado:
            return (f"BLOQUEADO: '{tk}' está en la lista negra del POS. "
                    f"Reintentar con confirmado=True (no encadenes lo peligroso).")
    import time as _t
    timeout = min(max(1, timeout), 40)

    def _resolver(nodos):
        """(plan, None) si TODOS los tokens están en el volcado, o
        (None, token_faltante) si falta alguno. plan = lista de
        (kind, etiqueta, coords). NO ejecuta nada — solo resuelve coordenadas."""
        mapa = _mapa_digitos(nodos)
        plan = []
        for tk in tokens:
            if _es_solo_digitos(tk):
                if any(d not in mapa for d in tk):
                    return None, tk
                plan.append(("type", tk, [mapa[d] for d in tk]))
            else:
                n = _buscar_nodo(nodos, tk, True)
                if not n or not (n["cx"] or n["cy"]):
                    return None, tk
                plan.append(("tap", tk, (n["cx"], n["cy"])))
        return plan, None

    # Esperar a que TODOS los tokens estén en UN mismo volcado ANTES de ejecutar
    # nada: así una cadena nunca se ejecuta a medias sobre la pantalla equivocada
    # (el bug de "15001500" al no encontrar un token DESPUÉS de haber tecleado).
    t0 = _t.time()
    plan = falta = None
    while True:
        nodos = _ui_nodos_seguro(lugar, serial)
        plan, falta = _resolver(nodos) if nodos else (None, tokens[0])
        if plan is not None:
            break
        if _t.time() - t0 >= timeout:
            return (f'secuencia: no encontré "{falta}" en la pantalla tras {timeout}s '
                    f'(no se ejecutó nada).')
        _t.sleep(0.2)

    # UN solo round-trip adb para TODO el plan: los taps de todos los segmentos
    # encadenados en un shell del device (antes: un viaje por segmento). El
    # `sleep 0.2` ENTRE segmentos da aire a la UI (habilitar el botón tras el
    # monto) sin pagar la latencia de un round-trip adb.
    hechos = []
    partes = []
    for kind, tk, coords in plan:
        if kind == "type":
            partes.append("; ".join(f"input tap {x} {y}" for (x, y) in coords))
            hechos.append(f'"{tk}"(tecleado)')
        else:
            partes.append(f"input tap {coords[0]} {coords[1]}")
            hechos.append(f'"{tk}"(tap)')
    cmd = "; sleep 0.2; ".join(partes)
    T.ejecutar(lugar, ["adb", "-s", serial, "shell", cmd], timeout=30)
    return "OK: " + " + ".join(hechos)


def adb_escribir(lugar: Lugar, serial: str, texto: str) -> str:
    """
    Teclea/tapea una secuencia sobre la pantalla actual con UN solo volcado.
    Segmentos separados por '+': cada uno se TECLEA si es solo dígitos, o se
    TAPEA si es texto. Ej.: 'escribir 4730' (teclea el monto),
    'escribir 4730+Continuar' (teclea y continúa), 'escribir 10%+Continuar'
    (tapea 10% y continúa). Mucho más rápido que un tap por dígito/acción.
    """
    return _ejecutar_cadena(lugar, serial, texto)


def adb_esperar(lugar: Lugar, serial: str, texto: str = "", patron_log: str = "",
                timeout: int = 15, tags: str = "") -> str:
    """
    Espera una condición (elimina el 'sleep N' adivinado). Con 'texto': hasta que
    ese texto/desc aparezca en la UI. Con 'patron_log' (regex): hasta que una
    línea de logcat lo matchee — determinista, ideal para aserciones tipo
    'Scan C2C: code=00'. 'tags' filtra el logcat por tag (coma-separados). Tope
    de espera por llamada: 40s (el cliente MCP corta las largas).
    """
    import time as _t
    if not texto and not patron_log:
        return "error: indicá 'texto' (esperar en la UI) o 'patron_log' (esperar en logcat)."
    timeout = min(max(1, timeout), 40)
    if patron_log:
        return _esperar_log(lugar, serial, patron_log, timeout, tags)
    t0 = _t.time()
    while True:
        nodos = _ui_nodos_seguro(lugar, serial)
        if _buscar_nodo(nodos, texto, True):
            return f'apareció "{texto}" tras {int(_t.time() - t0)}s.'
        if _t.time() - t0 >= timeout:
            return f'NO apareció "{texto}" tras {timeout}s.'
        _t.sleep(0.2)


def _es_eco_adb(linea: str) -> bool:
    """¿La línea es un eco de adbd (tag ADB_SERVICES/adbd) del comando `adb
    shell` que se está corriendo? Esas líneas contienen el TEXTO del comando —
    incluido el patrón buscado — y dan falsos positivos si no se descartan
    (aprendido en vivo: el logcat -e se matcheaba a sí mismo)."""
    return ("ADB_SERVICES" in linea) or (" adbd " in linea)


def _esperar_log(lugar: Lugar, serial: str, patron: str, timeout: int,
                 tags: str) -> str:
    import time as _t
    try:
        rx = re.compile(patron)
    except re.error as e:
        return f"error: patron_log no es una regex válida ({e})."
    filtro = _tags_filtro(tags)
    t0 = _t.time()
    # 1) Barrido INMEDIATO del buffer reciente (-t 2000, sin filtro por hora):
    #    si el evento ya está logueado (guión que pausó, disparo previo) se
    #    detecta de inmediato y no se pierde. En un guión, `inicio`/`limpiar_log`
    #    hicieron `logcat -c`, así que no hay líneas viejas de falso positivo.
    r = T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-d", "-t", "2000"]
                   + filtro, timeout=20)
    for linea in (r.salida or "").splitlines():
        if rx.search(linea) and not _es_eco_adb(linea):
            return f"log OK tras {int(_t.time() - t0)}s: {linea.strip()}"
    # 2) Espera BLOQUEANTE del lado del device: `logcat -e <regex> -m 1` sale
    #    en cuanto una línea matchea (latencia ~0, UN round-trip), en vez del
    #    poll de 0.6s que volcaba 2000 líneas por vuelta. El timeout lo pone el
    #    transporte (código 124 => no apareció).
    #    ATENCION (aprendido en vivo): adbd LOGUEA la línea de comando de cada
    #    `adb shell`, así que si el patrón viajara EN CLARO, el logcat -e se
    #    matchearía A SÍ MISMO al instante (falso positivo: el guión dio por
    #    APROBADA una venta que seguía en el PIN). Por eso el patrón viaja en
    #    base64 y se decodifica recién en el shell del device, y toda línea de
    #    adbd (_es_eco_adb) se descarta. La candidata se RE-VERIFICA con la
    #    regex de Python (los dialectos difieren un pelo).
    import base64 as _b64
    b64 = _b64.b64encode(patron.encode("utf-8")).decode("ascii")
    extra = "".join(f" '{f}'" for f in filtro)
    cmd = f'logcat -e "$(echo {b64} | base64 -d)" -m 1{extra}'
    restante = max(1, int(timeout - (_t.time() - t0)))
    r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", cmd], timeout=restante)
    if r.codigo != 124:
        for linea in (r.salida or "").splitlines():
            if rx.search(linea) and not _es_eco_adb(linea):
                return f"log OK tras {int(_t.time() - t0)}s: {linea.strip()}"
    # 3) Fallback (logcat viejo sin -e/-m, device sin base64 — ahí el $() da
    #    vacío y -e "" matchea cualquier línea, que la re-verificación de
    #    Python rechaza —, dialecto de regex distinto): poll clásico.
    while True:
        if _t.time() - t0 >= timeout:
            return f"NO apareció el patrón en logcat tras {timeout}s: /{patron}/"
        r = T.ejecutar(lugar, ["adb", "-s", serial, "logcat", "-d", "-t", "2000"]
                       + filtro, timeout=20)
        for linea in (r.salida or "").splitlines():
            if rx.search(linea) and not _es_eco_adb(linea):
                return f"log OK tras {int(_t.time() - t0)}s: {linea.strip()}"
        _t.sleep(0.6)


# --- Gradle -----------------------------------------------------------------

def gradle_build(lugar: Lugar, proyecto: str, tarea: str) -> str:
    """
    Compila con el gradlew del proyecto.

    En unix/remoto compila síncrono y devuelve la salida. En local Windows
    compila como TRABAJO asíncrono (run_esperar/run_status para seguirlo) con
    el fix del sandbox: los pipes NIO de Java (JDK 16+) crean un socket
    AF_UNIX en el TMP, donde el sandbox del cliente MCP lo rompe con EINVAL
    ("Unable to establish loopback connection"); JAVA_TOOL_OPTIONS con
    -Djdk.net.unixdomain.tmpdir apuntando a una carpeta de la raíz lo evita
    (diagnosticado y verificado en vivo, ronda 10).
    """
    if lugar.es_local:
        p = normalizar(lugar.raiz, proyecto)
        if lugar.es_windows:
            from pathlib import Path as _P
            from . import trabajos as TR
            if not (p / "gradlew.bat").exists():
                return f"error: no encuentro gradlew.bat en {p}"
            # El fix del sandbox de la JVM ya NO se arma aquí: vive en
            # transporte.entorno_jvm y lo aplican por igual run, run_async y
            # este build. Tenerlo solo en esta tool hacía que el mismo comando
            # funcionara o muriera según por dónde entrara.
            # -Pkotlin.compiler.execution.strategy=daemon: si el proyecto fija
            # in-process (algunos proyectos lo fijan), el compilador Kotlin infla el
            # metaspace del daemon de Gradle y muere con OOM; el daemon de
            # Kotlin usa TCP loopback (funciona bajo el sandbox) y compila
            # aparte. Si el proyecto no fija nada, daemon ya era el default.
            cmd = (f'cd /d "{p}" && gradlew.bat {tarea} '
                   f'-Pkotlin.compiler.execution.strategy=daemon')
            jid = TR.lanzar(lugar, cmd)
            return (f"Build lanzado como trabajo: id {jid} ({proyecto} :: {tarea}).\n"
                    f"Seguirlo con run_esperar(id=\"{jid}\") hasta que termine "
                    f"(un build frío puede tomar varios minutos; re-llamar si "
                    f"sigue). Al final: código 0 = BUILD SUCCESSFUL.\n"
                    f"Si falla: gradle_errores(\"{jid}\") devuelve solo las "
                    f"líneas 'e:' deduplicadas. Los errores de Kotlin salen por "
                    f"err.log, no por out.log — gradle_errores mira los dos, "
                    f"así no hay que acordarse de cuál es.")
        salida = T.ejecutar(lugar, ["./gradlew", tarea], cwd=str(p), timeout=1800)
        return _fmt_resultado(salida)
    salida = T.ejecutar(lugar, f"cd '{proyecto}' && ./gradlew {tarea}", timeout=1800)
    return _fmt_resultado(salida)


# Un error de compilación de Kotlin/Java sale como línea 'e:'. Gradle las emite
# por STDERR, así que viven en err.log, no en out.log: el mensaje anterior
# mandaba al log equivocado y costaba dos búsquedas.

def gradle_errores(lugar: Lugar, jid: str, maximo: int = 60) -> str:
    """
    Solo los errores de compilación de un build lanzado como trabajo: las
    líneas 'e:' de los DOS logs del job, deduplicadas y en orden.

    Es lo primero que se quiere después de un build fallido, y evita tener que
    recordar en qué log están (err.log) ni armar el buscar_contenido a mano.
    Si no hay líneas 'e:', cae al bloque "What went wrong" de Gradle, que es lo
    que explica los fallos que no son de compilación (dependencias, tareas,
    configuración). Solo lectura.
    """
    import re

    textos = []
    if lugar.es_local:
        from pathlib import Path as _P
        base = _P(lugar.raiz) / ".witral" / "jobs" / jid
        if not base.exists():
            return (f"No existe el trabajo '{jid}' en {lugar.nombre}. "
                    f"Ver run_status sin id para listar los trabajos.")
        for nombre in ("err.log", "out.log"):
            ruta = base / nombre
            if ruta.exists():
                textos.append(ruta.read_text(encoding="utf-8", errors="replace"))
    else:
        b = f".witral/jobs/{jid}"
        r = T.ejecutar(lugar, f"cat {b}/err.log {b}/out.log 2>/dev/null",
                       timeout=30)
        if not r.salida.strip():
            return f"Sin logs para el trabajo '{jid}' en {lugar.nombre}."
        textos.append(r.salida)

    vistas, errores = set(), []
    for texto in textos:
        for linea in texto.splitlines():
            if re.match(r"^\s*e:\s", linea):
                limpia = linea.strip()
                if limpia not in vistas:
                    vistas.add(limpia)
                    errores.append(limpia)

    if errores:
        cabecera = (f"{len(errores)} error(es) de compilación en el trabajo "
                    f"{jid}" + (f" (mostrando {maximo})" if len(errores) > maximo
                                else ""))
        return cabecera + ":\n" + "\n".join(errores[:maximo])

    # Sin líneas 'e:': el fallo no es de compilación. El bloque de Gradle que
    # lo explica arranca en "* What went wrong:" y termina en la línea vacía
    # anterior a "* Try:".
    for texto in textos:
        if "What went wrong" in texto or "FAILURE:" in texto:
            lineas = texto.splitlines()
            i = next(k for k, l in enumerate(lineas)
                     if "What went wrong" in l or l.startswith("FAILURE:"))
            bloque = []
            for l in lineas[i:i + 25]:
                if l.startswith("* Try:"):
                    break
                bloque.append(l)
            return (f"Sin errores de compilación en {jid}; el fallo es de "
                    f"Gradle:\n" + "\n".join(bloque))
    return (f"Sin líneas 'e:' ni bloque de fallo en los logs de {jid}. "
            f"Si el build terminó con código 0, no hubo errores; "
            f"si no, mirar el estado completo con run_status(id=\"{jid}\").")


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
#   field 5 -> string, 6 -> string_set, 7 -> double.
# Esto permite leer/escribir una pref sin tener la app, util para alternar
# parametros en QA. Requiere run-as (app debuggable); en release no hay acceso.

# Mapa field-de-Value -> nombre de tipo legible.
# OJO con el orden de los ultimos dos: en PreferencesProto.Value el field 6 es
# string_set y el 7 es double (no al reves). Estuvieron invertidos hasta la
# ronda 16: un double escrito por datastore_set salia como field 6, que la app
# lee como StringSet y revienta al cargar la pref.
_DS_TIPO_POR_FIELD = {1: "bool", 2: "float", 3: "int", 4: "long",
                      5: "string", 6: "string_set", 7: "double"}
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
            # protobuf codifica los enteros NEGATIVOS (int32 e int64) como
            # complemento a dos de 64 bits, asi que el varint crudo vuelve como
            # un numero gigante: -7 se lee 18446744073709551609. Sin este
            # reajuste, datastore_get muestra basura para cualquier pref
            # negativa y datastore_set con tipo="auto" la reescribe mal.
            n = int(vv)
            if n >= (1 << 63):
                n -= (1 << 64)
            return tipo, n
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


def _ds_encode_value(tipo: str, valor) -> bytes:
    """
    Codifica un Value protobuf desde un valor y su tipo. 'valor' puede venir en
    texto (datastore_set, que recibe todo como string) o ya tipado desde JSON
    (datastore_poblar): int/float/bool/str/list.
    """
    import struct
    field = _DS_FIELD_POR_TIPO.get(tipo)
    if field is None:
        raise ValueError(f"tipo '{tipo}' no soportado")
    if tipo == "bool":
        if isinstance(valor, bool):
            v = 1 if valor else 0
        else:
            v = 1 if str(valor).strip().lower() in ("1", "true", "si", "sí", "yes") else 0
        return bytes([(field << 3) | 0]) + _ds_encode_varint(v)
    if tipo in ("int", "long"):
        return bytes([(field << 3) | 0]) + _ds_encode_varint(int(valor))
    if tipo == "string":
        s = str(valor).encode("utf-8")
        return bytes([(field << 3) | 2]) + _ds_encode_varint(len(s)) + s
    if tipo == "float":
        return bytes([(field << 3) | 5]) + struct.pack("<f", float(valor))
    if tipo == "double":
        return bytes([(field << 3) | 1]) + struct.pack("<d", float(valor))
    if tipo == "string_set":
        items = valor if isinstance(valor, (list, tuple, set)) else [valor]
        cuerpo = b""
        for s in items:
            sb = str(s).encode("utf-8")
            cuerpo += bytes([0x0A]) + _ds_encode_varint(len(sb)) + sb
        return bytes([(field << 3) | 2]) + _ds_encode_varint(len(cuerpo)) + cuerpo
    raise ValueError(f"tipo '{tipo}' no soportado para escritura")


# Rango de int32: decide int vs long al inferir el tipo de un entero JSON. En
# Kotlin son claves DISTINTAS (intPreferencesKey vs longPreferencesKey), asi que
# escribir un long donde la app espera int deja la pref invisible para la app.
_DS_INT32_MIN, _DS_INT32_MAX = -(2 ** 31), 2 ** 31 - 1


def _ds_tipo_inferido(valor) -> str:
    """Tipo DataStore a partir del tipo Python/JSON del valor."""
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, int):
        return "int" if _DS_INT32_MIN <= valor <= _DS_INT32_MAX else "long"
    if isinstance(valor, float):
        return "double"
    if isinstance(valor, (list, tuple, set)):
        return "string_set"
    return "string"


def _ds_entradas(data: bytes):
    """[(clave, valmsg|None, raw_entry)] del mapa raiz, en orden de archivo."""
    salida = []
    for field, wt, chunk, raw in _ds_parse(data, 0, len(data)):
        if field != 1 or wt != 2:
            continue
        clave = None
        valmsg = None
        for sf, swt, sval, _ in _ds_parse(chunk, 0, len(chunk)):
            if sf == 1:
                clave = sval.decode("utf-8", "replace")
            elif sf == 2:
                valmsg = sval
        if clave is not None:
            salida.append((clave, valmsg, raw))
    return salida


def _ds_entrada(clave: str, valmsg: bytes) -> bytes:
    """Una entrada del map: entry{ key(1,string), value(2,Value) } con su tag."""
    kb = clave.encode("utf-8")
    obj = (bytes([0x0A]) + _ds_encode_varint(len(kb)) + kb +
           bytes([0x12]) + _ds_encode_varint(len(valmsg)) + valmsg)
    return bytes([0x0A]) + _ds_encode_varint(len(obj)) + obj


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


def datastore_poblar(lugar: Lugar, serial: str, paquete: str, archivo: str,
                     claves, modo: str = "fusionar") -> str:
    """
    Escribe MUCHAS claves de un DataStore en UNA pasada: un solo force-stop, un
    solo backup, un solo archivo escrito. Es la version en bloque de
    datastore_set, que hace todo eso por cada clave.

    Nacio de un caso real (2026-08-20): dejar un POS operativo sin descargar
    parametros desde el TMS. La app convierte los .json de parametros en cinco
    DataStore (~60 claves) dentro de su flujo de carga; replicar eso con
    datastore_set eran ~60 llamadas, cada una con su force-stop y su backup.

    'claves': dict (o JSON) {clave: valor}. El valor puede ser:
      - un escalar JSON -> el tipo se resuelve asi: si la clave YA existe en el
        archivo se respeta su tipo actual; si no, se infiere del tipo JSON
        (bool -> bool, entero -> int o long segun quepa en int32, decimal ->
        double, lista -> string_set, resto -> string).
      - {"tipo": "long", "valor": 0} -> tipo explicito. OBLIGATORIO cuando el
        tipo JSON no alcanza: en Kotlin intPreferencesKey("x") y
        longPreferencesKey("x") son claves DISTINTAS, asi que un long escrito
        donde la app espera int queda invisible para la app.
      - null -> BORRA la clave.

    'modo': "fusionar" (por defecto) conserva las claves que no se mencionan —
    es lo que hay que usar cuando el propio terminal ya escribio algo ahi (un
    TID de registro, contadores). "reemplazar" deja el archivo con exactamente
    las claves entregadas.

    Si el archivo no existe, lo crea (junto con files/datastore/ si falta).
    """
    import base64
    import json as _json

    if isinstance(claves, str):
        try:
            claves = _json.loads(claves)
        except Exception as e:
            return f"error: 'claves' no es JSON válido: {e}"
    if not isinstance(claves, dict) or not claves:
        return ("error: 'claves' debe ser un objeto no vacío "
                "{clave: valor} o {clave: {\"tipo\": ..., \"valor\": ...}}.")
    if modo not in ("fusionar", "reemplazar"):
        return "error: 'modo' debe ser 'fusionar' o 'reemplazar'."

    ruta = _ds_ruta(paquete, archivo)

    def _adb_sh(remoto: str) -> "T.Resultado":
        return T.ejecutar(lugar, ["adb", "-s", serial, "shell", remoto])

    # ¿Existe el archivo? Se pregunta aparte de leerlo para poder distinguir
    # "no existe todavía" (caso normal: datastore que la app aún no creó) de
    # "no tengo run-as" (app release), que con una sola lectura fallida se
    # confunden y mandan a diagnosticar lo que no es.
    chk = _adb_sh(f"run-as {paquete} sh -c 'if [ -f {ruta} ]; then echo _SI_; "
                  f"else echo _NO_; fi'")
    txt = (chk.salida or "").strip()
    if "_SI_" not in txt and "_NO_" not in txt:
        return (f"error: no pude usar run-as sobre '{paquete}' en {serial} "
                f"(¿app debuggable? ¿paquete correcto?). "
                f"salida: {txt} {(chk.error or '').strip()}")
    existia = "_SI_" in txt

    if existia:
        try:
            data = _ds_leer_bytes(lugar, serial, paquete, archivo)
        except ValueError as e:
            return f"error: {e}"
    else:
        data = b""

    entradas = _ds_entradas(data)
    tipos_actuales = {k: (_ds_decode_value(vm)[0] if vm is not None else None)
                      for k, vm, _ in entradas}

    nuevas = {}
    borrar = set()
    detalle = []
    for clave, spec in claves.items():
        if spec is None:
            borrar.add(clave)
            detalle.append(f"  - {clave}  (borrada)")
            continue
        if isinstance(spec, dict) and "valor" in spec:
            tipo = (spec.get("tipo") or "").strip()
            valor = spec["valor"]
        else:
            tipo = ""
            valor = spec
        if not tipo:
            actual = tipos_actuales.get(clave)
            tipo = (actual if actual and actual != "desconocido"
                    else _ds_tipo_inferido(valor))
        if tipo not in _DS_FIELD_POR_TIPO:
            return (f"error: clave '{clave}': tipo '{tipo}' no soportado. "
                    f"Válidos: {', '.join(_DS_FIELD_POR_TIPO)}.")
        try:
            nuevas[clave] = (tipo, _ds_encode_value(tipo, valor))
        except (ValueError, TypeError) as e:
            return (f"error: clave '{clave}': no pude codificar {valor!r} "
                    f"como {tipo}: {e}")
        marca = "~" if clave in tipos_actuales else "+"
        detalle.append(f"  {marca} {clave} [{tipo}] = {valor!r}")

    # Reconstruir el archivo. En "fusionar" se copian CRUDAS las entradas que no
    # se tocan (no se re-serializan: lo que no se pidió cambiar sale byte a byte
    # igual que entró).
    salida = bytearray()
    usadas = set()
    if modo == "fusionar":
        for clave, _vm, raw in entradas:
            if clave in borrar:
                continue
            if clave in nuevas:
                salida += _ds_entrada(clave, nuevas[clave][1])
                usadas.add(clave)
            else:
                salida += raw
    for clave, (_tipo, vm) in nuevas.items():
        if clave not in usadas:
            salida += _ds_entrada(clave, vm)

    nuevo_b64 = base64.b64encode(bytes(salida)).decode()
    base = archivo.replace("/", "_").replace(".preferences_pb", "")
    tmp_b64 = f"/sdcard/{base}.{serial}.b64"
    tmp_pb = f"/sdcard/{base}.{serial}.new.pb"
    bak = f"/sdcard/{base}.{serial}.bak.pb"

    # 1) Backup del original (si había).
    if existia:
        _adb_sh(f"run-as {paquete} sh -c 'cat {ruta} > {bak}'")
    # 2) Detener la app: DataStore cachea en memoria y sobrescribiría el cambio.
    T.ejecutar(lugar, ["adb", "-s", serial, "shell", "am", "force-stop", paquete])
    # 3) Subir el base64 POR TROZOS. Un datastore poblado pasa los pocos cientos
    #    de bytes de datastore_set; un `echo <b64>` de varios KB en una sola
    #    línea choca con el límite de argumentos del shell del device.
    _adb_sh(f"sh -c 'rm -f {tmp_b64} {tmp_pb}'")
    for i in range(0, len(nuevo_b64), 1200):
        w = _adb_sh(f"sh -c 'echo -n {nuevo_b64[i:i + 1200]} >> {tmp_b64}'")
        if not w.ok:
            return (f"error: no pude subir el base64 al device "
                    f"(trozo en {i}): {(w.error or '').strip()}")
    d = _adb_sh(f"sh -c 'cat {tmp_b64} | base64 -d > {tmp_pb}'")
    if not d.ok:
        return f"error: no pude decodificar el base64 en el device: {(d.error or '').strip()}"
    # 4) Copiar al sandbox de la app con los permisos que usa DataStore.
    cp = _adb_sh(f"run-as {paquete} sh -c 'mkdir -p files/datastore && "
                 f"cat {tmp_pb} > {ruta} && chmod 600 {ruta}'")
    if not cp.ok:
        return f"error: no pude copiar al datastore via run-as: {(cp.error or '').strip()}"
    _adb_sh(f"sh -c 'rm -f {tmp_b64} {tmp_pb}'")

    # 5) Verificar releyendo: se comparan los valores pedidos contra los que
    #    quedaron en el archivo, para no reportar OK sobre una escritura parcial.
    try:
        verif = _ds_leer_bytes(lugar, serial, paquete, archivo)
    except ValueError as e:
        return f"escrito, pero no pude verificar: {e}"
    quedaron = {k: (_ds_decode_value(vm) if vm is not None else (None, None))
                for k, vm, _ in _ds_entradas(verif)}
    faltantes = [k for k in nuevas if k not in quedaron]
    sobrantes = [k for k in borrar if k in quedaron]

    cab = (f"{'OK' if not faltantes and not sobrantes else 'PARCIAL'}: "
           f"{ruta} — {len(quedaron)} claves, {len(verif)} bytes "
           f"(modo {modo}, archivo {'existente' if existia else 'creado'}).")
    cuerpo = "\n".join(detalle)
    pie = (f"Backup: {bak}. " if existia else "Sin backup (el archivo no existía). ")
    pie += "App detenida (force-stop): relanzala con adb_relanzar para que cargue."
    if faltantes:
        cuerpo += f"\n  !! no quedaron escritas: {', '.join(faltantes)}"
    if sobrantes:
        cuerpo += f"\n  !! no se borraron: {', '.join(sobrantes)}"
    return f"{cab}\n{cuerpo}\n{pie}"
