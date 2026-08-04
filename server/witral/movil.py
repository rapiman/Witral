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


def _adb_modelo(lugar: Lugar, serial: str) -> str | None:
    """Modelo legible del dispositivo (ro.product.model), o None si no se puede."""
    try:
        r = T.ejecutar(lugar, ["adb", "-s", serial, "shell", "getprop",
                               "ro.product.model"], timeout=15)
        m = (r.salida or "").strip()
        return m or None
    except Exception:
        return None


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
    r = T.ejecutar(lugar, args, timeout=300)
    # Encabezar con modelo + serial: entre pruebas el POS puede cambiar de serial
    # y eso explica params/estado inesperados; que la respuesta lo deje claro.
    modelo = _adb_modelo(lugar, serial)
    quien = f"{modelo} (serial {serial})" if modelo else f"serial {serial}"
    return T.Resultado(r.codigo, f"Dispositivo: {quien}\n{r.salida or ''}".rstrip(),
                       r.error)


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
                f"Si es intencional, reintentá con confirmado=True.")
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
                    f"Reintentá con confirmado=True (no encadenes lo peligroso).")
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
    #    detecta al toque y no se pierde. En un guión, `inicio`/`limpiar_log`
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
    #    OJO (aprendido en vivo): adbd LOGUEA la línea de comando de cada
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
