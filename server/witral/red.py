"""
Red: ping, HTTP y TCP. Todo lo que conecta afuera pasa por la regla de borde,
que aplica la capa de tools (host como dato del usuario, nunca sacado de un
archivo sin confirmar).

- ping: usa el binario del sistema en el lugar (`donde` permite pingear desde
  un server).
- http_request: petición HTTP/HTTPS desde la máquina donde corre Witral (local).
- tcp_socket: conexión TCP cruda desde local, enviar/recibir bytes.
"""

from __future__ import annotations

import socket

from .config import Lugar
from . import transporte as T


def ping(lugar: Lugar, host: str, cuenta: int = 4) -> T.Resultado:
    """Ping desde el lugar indicado hacia 'host'."""
    # -n en Windows local, -c en Unix/remoto. Detectar por es_local + OS.
    import os
    if lugar.es_local and os.name == "nt":
        args = ["ping", "-n", str(cuenta), host]
    else:
        args = ["ping", "-c", str(cuenta), host]
    return T.ejecutar(lugar, args, timeout=30)


def _truncar(texto: str, limite: int) -> str:
    """Trunca con aviso explícito de cuánto se muestra y cuánto había."""
    if limite <= 0 or len(texto) <= limite:
        return texto
    return (texto[:limite] +
            f"\n...[truncado: mostrando {limite} de {len(texto)} chars; "
            f"para respuestas grandes usar a_archivo o subir max_salida]")


def http_request(url: str, metodo: str = "GET", cuerpo: str | None = None,
                 headers: dict | None = None, timeout: int = 30,
                 params: dict | None = None,
                 lugar: Lugar | None = None,
                 a_archivo: str | None = None,
                 max_salida: int = 4000) -> str:
    """
    Petición HTTP/HTTPS desde un lugar. Devuelve status, headers y body
    (truncado). En local usa urllib (stdlib); en remoto arma y ejecuta curl.

    'params': query params como dict. Se codifican en Python (urlencode,
    UTF-8 -> percent-encoding) ANTES de tocar cualquier shell, así los
    no-ASCII (ü, ñ, etc.) llegan intactos sin importar locale ni codepage.
    Es la forma correcta de pasar texto no-ASCII en la URL; no armarla a mano.

    'lugar': si es remoto, la petición se hace DESDE ese lugar (curl), lo que
    permite probar servicios que solo escuchan en localhost del server.

    'a_archivo': si se da, el CUERPO de la respuesta se guarda en esa ruta del
    lugar (relativa a su raíz) y se devuelve solo status + tamaño + ruta. Es
    la forma correcta de traer respuestas grandes sin atascar el transporte
    MCP; después se procesa el archivo con leer/buscar_contenido/run.
    'max_salida': tope de chars del cuerpo mostrado inline (con aviso).
    """
    import urllib.parse

    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)

    if lugar is not None and not lugar.es_local:
        return _http_remoto(lugar, url, metodo, cuerpo, headers, timeout,
                            a_archivo, max_salida)

    import urllib.request
    import urllib.error

    data = cuerpo.encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(url, data=data, method=metodo.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            crudo = resp.read()
            if a_archivo:
                if lugar is None:
                    return ("error: a_archivo requiere un lugar resuelto "
                            "(llamar vía la tool http_request)")
                from . import archivos as A
                A._escribir_bytes(lugar, a_archivo, crudo)
                return (f"HTTP {resp.status}\nGuardado en {a_archivo} "
                        f"({lugar.nombre if lugar else 'local'}, "
                        f"{len(crudo)} bytes). Procesar con leer / "
                        f"buscar_contenido / run.")
            body = crudo.decode("utf-8", "replace")
            hdrs = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
            return f"HTTP {resp.status}\n{hdrs}\n\n{_truncar(body, max_salida)}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return f"HTTP {e.code} {e.reason}\n{_truncar(body, 2000)}"
    except Exception as e:
        return f"error: {e}"


def _http_remoto(lugar: Lugar, url: str, metodo: str, cuerpo: str | None,
                 headers: dict | None, timeout: int,
                 a_archivo: str | None = None, max_salida: int = 4000) -> str:
    """
    Petición HTTP desde un lugar remoto, vía curl. La URL ya llega
    percent-encodeada (ASCII puro) desde http_request, así que la línea de
    comando es inmune a problemas de locale. El cuerpo viaja por stdin
    (--data-binary @-) para no pasar por el quoting del shell.
    Con 'a_archivo', curl escribe la respuesta a esa ruta del lugar (-o) y
    solo vuelven status y tamaño (respuestas grandes sin atascar el MCP).
    """
    args = ["curl", "-sS", "--max-time", str(timeout), "-X", metodo.upper()]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if cuerpo is not None:
        args += ["--data-binary", "@-"]
    if a_archivo:
        ruta = a_archivo
        if not ruta.startswith("/") and lugar.raiz:
            ruta = lugar.raiz.rstrip("/") + "/" + ruta
        args += ["-o", ruta,
                 "-w", "HTTP %{http_code} — %{size_download} bytes descargados"]
    else:
        args.append("-i")
    args.append(url)
    r = T.ejecutar(lugar, args, entrada=cuerpo, timeout=timeout + 10)
    if not r.ok and not r.salida:
        return f"error (curl en {lugar.nombre}): {r.error.strip()}"
    if a_archivo:
        out = (f"[desde {lugar.nombre}] {r.salida.strip()}\n"
               f"Guardado en {a_archivo}. Procesar con leer / "
               f"buscar_contenido / run.")
        if r.error.strip():
            out += f"\n--- stderr ---\n{r.error.strip()}"
        return out
    out = f"[desde {lugar.nombre}]\n{_truncar(r.salida, max_salida)}"
    if r.error.strip():
        out += f"\n--- stderr ---\n{r.error.strip()}"
    return out


def tcp_socket(host: str, puerto: int, enviar: str | None = None,
               recibir_bytes: int = 4096, timeout: int = 15) -> str:
    """
    Abre una conexión TCP a host:puerto, opcionalmente envía 'enviar' y devuelve
    lo recibido. Útil para pruebas tipo ISO8583 / SocketSSL.
    """
    try:
        with socket.create_connection((host, puerto), timeout=timeout) as s:
            if enviar is not None:
                s.sendall(enviar.encode("utf-8"))
            s.settimeout(timeout)
            try:
                data = s.recv(recibir_bytes)
            except socket.timeout:
                data = b""
            return (
                f"Conectado a {host}:{puerto}\n"
                f"Recibido ({len(data)} bytes):\n{data.decode('utf-8', 'replace')}"
            )
    except Exception as e:
        return f"error: {e}"


# --- SonarCloud ---------------------------------------------------------------

def _prop_gradle(clave: str) -> str | None:
    """
    Lee una propiedad del gradle.properties del USUARIO
    (~/.gradle/gradle.properties): el mismo archivo que usa `gradlew`, así hay
    UNA sola fuente de verdad y nada secreto ni específico de un proyecto
    dentro del repo de Witral.
    """
    import os
    ruta = os.path.join(os.path.expanduser("~"), ".gradle", "gradle.properties")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            for linea in f:
                linea = linea.strip()
                if linea.startswith(clave + "="):
                    return linea.split("=", 1)[1].strip() or None
    except OSError:
        return None
    return None


def _token_sonar() -> str | None:
    """Token de SonarCloud: systemProp.sonar.token, el de `gradlew sonar`."""
    return _prop_gradle("systemProp.sonar.token")


def _proyecto_sonar() -> str | None:
    """
    Project key por defecto. NO se cablea en el código (depende de en qué
    proyecto se esté trabajando): sale de la variable de entorno
    WITRAL_SONAR_PROYECTO o de systemProp.sonar.projectKey del
    gradle.properties del usuario.
    """
    import os
    return (os.environ.get("WITRAL_SONAR_PROYECTO")
            or _prop_gradle("systemProp.sonar.projectKey"))


_SONAR_ORDEN = {"BLOCKER": 0, "CRITICAL": 1, "MAJOR": 2, "MINOR": 3, "INFO": 4}


def sonar_issues(ruta: str = "", proyecto: str = "",
                 nuevos: bool = False,
                 host: str = "https://sonarcloud.io") -> str:
    """
    Issues ABIERTOS en SonarCloud, formateados compactos (sin JSON crudo).

    Con 'ruta' (relativa a la raíz del repo): el detalle de ESE archivo
    (L<línea> [SEVERIDAD] regla: mensaje), ordenado por severidad y línea.
    Sin 'ruta': resumen del proyecto por severidad. 'nuevos'=True acota al
    código nuevo (leak period). Solo lectura; refleja el ÚLTIMO análisis
    subido, no el working tree.

    'proyecto' vacío => se resuelve con WITRAL_SONAR_PROYECTO o con
    systemProp.sonar.projectKey del gradle.properties del usuario.
    """
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    token = _token_sonar()
    if not token:
        return ("error: no hay systemProp.sonar.token en "
                "~/.gradle/gradle.properties (el mismo token que usa "
                "`gradlew sonar`).")

    proyecto = proyecto or _proyecto_sonar() or ""
    if not proyecto:
        return ("error: falta el project key de SonarCloud. Pasalo en "
                "'proyecto', o fijalo una vez en la variable de entorno "
                "WITRAL_SONAR_PROYECTO o en systemProp.sonar.projectKey de "
                "~/.gradle/gradle.properties.")

    ruta_norm = ruta.replace("\\", "/").strip().strip("/")
    comp = f"{proyecto}:{ruta_norm}" if ruta_norm else proyecto
    params = {"componentKeys": comp, "resolved": "false", "ps": "200",
              "facets": "severities"}
    if nuevos:
        params["sinceLeakPeriod"] = "true"
    url = f"{host}/api/issues/search?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = _json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        cuerpo = e.read().decode("utf-8", "replace")[:300]
        return (f"error: SonarCloud respondió HTTP {e.code} ({e.reason}). "
                f"{cuerpo}\n(¿token vigente? ¿ruta/proyecto correctos?)")
    except Exception as e:
        return f"error consultando SonarCloud: {e}"

    total = data.get("total", 0)
    alcance = " (solo código nuevo)" if nuevos else ""

    if not ruta_norm:
        sev: dict = {}
        for f in data.get("facets", []):
            if f.get("property") == "severities":
                sev = {v.get("val"): v.get("count", 0)
                       for v in f.get("values", [])}
        resumen = "  ".join(f"{k}:{sev.get(k, 0)}" for k in _SONAR_ORDEN)
        return (f"{proyecto}{alcance}: {total} issues abiertos — {resumen}\n"
                f"(pasar 'ruta' para el detalle de un archivo)")

    lineas = [f"{total} issue(s) abiertos en {ruta_norm}{alcance}"]
    issues = sorted(
        data.get("issues", []),
        key=lambda i: (_SONAR_ORDEN.get(i.get("severity"), 9),
                       i.get("line") or 0))
    for i in issues:
        lineas.append(f"  L{i.get('line', '?')} [{i.get('severity')}] "
                      f"{i.get('rule')}: {i.get('message')}")
    if total > len(issues):
        lineas.append(f"  ... y {total - len(issues)} más (tope de página 200)")
    return "\n".join(lineas)
