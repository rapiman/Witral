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
                 headers: dict | None = None, timeout: int = 30) -> str:
    """
    Petición HTTP/HTTPS desde local. Devuelve status, headers y body (truncado).
    Usa urllib de la stdlib para no agregar dependencias.
    """
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
