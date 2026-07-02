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


def http_request(url: str, metodo: str = "GET", cuerpo: str | None = None,
                 headers: dict | None = None, timeout: int = 30,
                 params: dict | None = None,
                 lugar: Lugar | None = None) -> str:
    """
    Petición HTTP/HTTPS desde un lugar. Devuelve status, headers y body
    (truncado). En local usa urllib (stdlib); en remoto arma y ejecuta curl.

    'params': query params como dict. Se codifican en Python (urlencode,
    UTF-8 -> percent-encoding) ANTES de tocar cualquier shell, así los
    no-ASCII (ü, ñ, etc.) llegan intactos sin importar locale ni codepage.
    Es la forma correcta de pasar texto no-ASCII en la URL; no armarla a mano.

    'lugar': si es remoto, la petición se hace DESDE ese lugar (curl), lo que
    permite probar servicios que solo escuchan en localhost del server.
    """
    import urllib.parse

    if params:
        sep = "&" if "?" in url else "?"
        url = url + sep + urllib.parse.urlencode(params)

    if lugar is not None and not lugar.es_local:
        return _http_remoto(lugar, url, metodo, cuerpo, headers, timeout)

    import urllib.request
    import urllib.error

    data = cuerpo.encode("utf-8") if cuerpo is not None else None
    req = urllib.request.Request(url, data=data, method=metodo.upper())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            hdrs = "\n".join(f"{k}: {v}" for k, v in resp.headers.items())
            cuerpo_out = body if len(body) <= 4000 else body[:4000] + "\n...[truncado]"
            return f"HTTP {resp.status}\n{hdrs}\n\n{cuerpo_out}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        return f"HTTP {e.code} {e.reason}\n{body[:2000]}"
    except Exception as e:
        return f"error: {e}"


def _http_remoto(lugar: Lugar, url: str, metodo: str, cuerpo: str | None,
                 headers: dict | None, timeout: int) -> str:
    """
    Petición HTTP desde un lugar remoto, vía curl. La URL ya llega
    percent-encodeada (ASCII puro) desde http_request, así que la línea de
    comando es inmune a problemas de locale. El cuerpo viaja por stdin
    (--data-binary @-) para no pasar por el quoting del shell.
    """
    args = ["curl", "-sS", "-i", "--max-time", str(timeout),
            "-X", metodo.upper()]
    for k, v in (headers or {}).items():
        args += ["-H", f"{k}: {v}"]
    if cuerpo is not None:
        args += ["--data-binary", "@-"]
    args.append(url)
    r = T.ejecutar(lugar, args, entrada=cuerpo, timeout=timeout + 10)
    if not r.ok and not r.salida:
        return f"error (curl en {lugar.nombre}): {r.error.strip()}"
    salida = r.salida
    if len(salida) > 4000:
        salida = salida[:4000] + "\n...[truncado]"
    out = f"[desde {lugar.nombre}]\n{salida}"
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
