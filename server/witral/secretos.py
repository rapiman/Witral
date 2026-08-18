"""
Secretos: leer credenciales del Credential Manager de Windows sin que el valor
pase nunca por la conversación.

La idea es cerrar el hueco que llevaba a escribir scripts de PowerShell
descartables cada vez que hacía falta un token (GitHub, una API interna): el
secreto se guarda UNA vez en el Credential Manager (`cmdkey /generic:...` o la
UI de Windows) y desde ahí Witral lo usa por NOMBRE. Quien llama ve el nombre;
el valor se queda dentro del proceso.

Contrato:
- `leer(nombre)` devuelve (usuario, secreto) para uso INTERNO (armar un header
  de autorización). No es una tool.
- La tool `secreto` solo expone METADATOS: si existe, qué usuario tiene, cuántos
  caracteres mide y cuándo se escribió. Nunca el valor.
- `enmascarar(texto, *secretos)` limpia cualquier salida antes de devolverla.

En lugares que no son Windows (o si el Credential Manager no está disponible)
cae a la variable de entorno WITRAL_SECRETO_<NOMBRE_EN_MAYUSCULAS>, con los
caracteres no alfanuméricos convertidos a "_".
"""

from __future__ import annotations

import os

CRED_TYPE_GENERIC = 1


class SecretoNoEncontrado(Exception):
    pass


def _es_windows() -> bool:
    return os.name == "nt"


def _var_entorno(nombre: str) -> str:
    limpio = "".join(c if c.isalnum() else "_" for c in nombre).upper()
    return "WITRAL_SECRETO_" + limpio


# --- Credential Manager de Windows (ctypes, sin dependencias) ---------------

def _estructuras():
    """Define las estructuras de advapi32 una sola vez, al usarlas."""
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("dwLowDateTime", wintypes.DWORD),
                    ("dwHighDateTime", wintypes.DWORD)]

    class CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", wintypes.DWORD),
            ("Type", wintypes.DWORD),
            ("TargetName", wintypes.LPWSTR),
            ("Comment", wintypes.LPWSTR),
            ("LastWritten", FILETIME),
            ("CredentialBlobSize", wintypes.DWORD),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
            ("Persist", wintypes.DWORD),
            ("AttributeCount", wintypes.DWORD),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", wintypes.LPWSTR),
            ("UserName", wintypes.LPWSTR),
        ]

    return ctypes, wintypes, CREDENTIALW


def _decodificar_blob(blob: bytes) -> str:
    """El blob es libre; Windows y cmdkey guardan UTF-16LE, otros UTF-8."""
    if not blob:
        return ""
    try:
        texto = blob.decode("utf-16-le")
        if "\x00" not in texto:
            return texto
    except UnicodeDecodeError:
        pass
    return blob.decode("utf-8", "replace")


def _leer_windows(nombre: str) -> tuple[str, str, str]:
    """(usuario, secreto, escrito_en) desde el Credential Manager."""
    import ctypes
    from ctypes import wintypes

    ctypes_mod, _wt, CREDENTIALW = _estructuras()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                 wintypes.DWORD,
                                 ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
    advapi.CredReadW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None

    puntero = ctypes.POINTER(CREDENTIALW)()
    ok = advapi.CredReadW(nombre, CRED_TYPE_GENERIC, 0, ctypes.byref(puntero))
    if not ok:
        err = ctypes.get_last_error()
        if err == 1168:  # ERROR_NOT_FOUND
            raise SecretoNoEncontrado(
                f"no hay credencial genérica '{nombre}' en el Credential "
                f"Manager de Windows. Guardala una vez con:\n"
                f"  cmdkey /generic:{nombre} /user:<usuario> /pass\n"
                f"(o desde Panel de control > Administrador de credenciales > "
                f"Credenciales de Windows).")
        raise SecretoNoEncontrado(
            f"CredRead('{nombre}') falló con error {err} de Windows.")
    try:
        cred = puntero.contents
        tam = int(cred.CredentialBlobSize)
        blob = ctypes.string_at(cred.CredentialBlob, tam) if tam else b""
        usuario = cred.UserName or ""
        escrito = _fecha(cred.LastWritten)
        return usuario, _decodificar_blob(blob), escrito
    finally:
        advapi.CredFree(puntero)


def _fecha(ft) -> str:
    """FILETIME -> ISO corto, o "" si no se puede."""
    try:
        import datetime
        cien_ns = (int(ft.dwHighDateTime) << 32) | int(ft.dwLowDateTime)
        if not cien_ns:
            return ""
        base = datetime.datetime(1601, 1, 1)
        return (base + datetime.timedelta(microseconds=cien_ns // 10)).strftime(
            "%Y-%m-%d %H:%M")
    except Exception:
        return ""


def listar_windows(filtro: str = "") -> list[str]:
    """Nombres de las credenciales GENÉRICAS visibles (sin valores)."""
    import ctypes
    from ctypes import wintypes

    ctypes_mod, _wt, CREDENTIALW = _estructuras()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi.CredEnumerateW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.POINTER(ctypes.POINTER(ctypes.POINTER(CREDENTIALW)))]
    advapi.CredEnumerateW.restype = wintypes.BOOL
    advapi.CredFree.argtypes = [ctypes.c_void_p]
    advapi.CredFree.restype = None

    cuenta = wintypes.DWORD()
    lista = ctypes.POINTER(ctypes.POINTER(CREDENTIALW))()
    patron = filtro if filtro else None
    if not advapi.CredEnumerateW(patron, 0, ctypes.byref(cuenta),
                                 ctypes.byref(lista)):
        err = ctypes.get_last_error()
        if err == 1168:  # ERROR_NOT_FOUND: no hay ninguna que coincida
            return []
        raise SecretoNoEncontrado(f"CredEnumerate falló con error {err}.")
    try:
        nombres = []
        for i in range(cuenta.value):
            cred = lista[i].contents
            if int(cred.Type) != CRED_TYPE_GENERIC:
                continue
            nombres.append(cred.TargetName or "")
        return sorted(n for n in nombres if n)
    finally:
        advapi.CredFree(lista)


# --- API que usa el resto de Witral ----------------------------------------

def leer(nombre: str) -> tuple[str, str]:
    """
    (usuario, secreto) de la credencial 'nombre'. USO INTERNO: el valor no debe
    devolverse a la conversación, solo usarse para armar la petición.
    """
    nombre = (nombre or "").strip()
    if not nombre:
        raise SecretoNoEncontrado("falta el nombre de la credencial.")
    if _es_windows():
        try:
            usuario, secreto, _ = _leer_windows(nombre)
            if secreto:
                return usuario, secreto
        except SecretoNoEncontrado:
            if _var_entorno(nombre) not in os.environ:
                raise
    valor = os.environ.get(_var_entorno(nombre))
    if valor:
        return os.environ.get(_var_entorno(nombre) + "_USUARIO", ""), valor
    raise SecretoNoEncontrado(
        f"no hay credencial '{nombre}' (ni Credential Manager ni la variable "
        f"de entorno {_var_entorno(nombre)}).")


def describir(nombre: str) -> str:
    """Metadatos de una credencial. NUNCA el valor."""
    nombre = (nombre or "").strip()
    if _es_windows():
        try:
            usuario, secreto, escrito = _leer_windows(nombre)
            cuando = f", escrita {escrito}" if escrito else ""
            return (f"'{nombre}': existe en el Credential Manager "
                    f"(usuario: {usuario or '—'}, {len(secreto)} caracteres"
                    f"{cuando}). El valor no se muestra: usalo por nombre, "
                    f"p. ej. http_request(..., auth=\"bearer:{nombre}\").")
        except SecretoNoEncontrado as e:
            if _var_entorno(nombre) in os.environ:
                valor = os.environ[_var_entorno(nombre)]
                return (f"'{nombre}': no está en el Credential Manager, pero sí "
                        f"en la variable de entorno {_var_entorno(nombre)} "
                        f"({len(valor)} caracteres).")
            return f"error: {e}"
    valor = os.environ.get(_var_entorno(nombre))
    if valor:
        return (f"'{nombre}': en la variable de entorno {_var_entorno(nombre)} "
                f"({len(valor)} caracteres).")
    return (f"error: este lugar no es Windows y no existe la variable "
            f"{_var_entorno(nombre)}.")


def listar(filtro: str = "") -> str:
    """Nombres de credenciales disponibles (sin valores)."""
    entorno = sorted(k[len("WITRAL_SECRETO_"):] for k in os.environ
                     if k.startswith("WITRAL_SECRETO_")
                     and not k.endswith("_USUARIO"))
    lineas = []
    if _es_windows():
        try:
            nombres = listar_windows(filtro)
        except SecretoNoEncontrado as e:
            nombres = []
            lineas.append(f"(Credential Manager: {e})")
        if nombres:
            lineas.append("Credential Manager (genéricas):")
            lineas += [f"  - {n}" for n in nombres]
    if entorno:
        lineas.append("Variables de entorno WITRAL_SECRETO_*:")
        lineas += [f"  - {n}" for n in entorno]
    if not lineas:
        return ("No hay credenciales visibles. Guardá una con:\n"
                "  cmdkey /generic:<nombre> /user:<usuario> /pass")
    lineas.append("Los valores nunca se muestran; se usan por nombre "
                  "(auth=\"bearer:<nombre>\" en http_request).")
    return "\n".join(lineas)


def enmascarar(texto: str, *secretos: str) -> str:
    """Reemplaza cualquier aparición de un secreto por ···· en una salida."""
    for s in secretos:
        if s and len(s) >= 4:
            texto = texto.replace(s, "····")
    return texto


# --- Autorización HTTP ------------------------------------------------------

def headers_auth(auth: str) -> tuple[dict, str]:
    """
    Traduce la especificación 'auth' a headers, resolviendo la credencial por
    nombre. Formas aceptadas:

      bearer:<credencial>            -> Authorization: Bearer <secreto>
      token:<credencial>             -> Authorization: token <secreto>   (GitHub)
      basic:<credencial>             -> Authorization: Basic b64(usuario:secreto)
      header:<Nombre>:<credencial>   -> <Nombre>: <secreto>  (X-Api-Key y demás)

    Devuelve (headers, secreto) — el secreto vuelve solo para poder enmascararlo
    en la salida, no para mostrarlo.
    """
    spec = (auth or "").strip()
    if not spec:
        return {}, ""
    partes = spec.split(":")
    modo = partes[0].strip().lower()
    if modo in ("bearer", "token") and len(partes) == 2:
        _u, secreto = leer(partes[1])
        prefijo = "Bearer" if modo == "bearer" else "token"
        return {"Authorization": f"{prefijo} {secreto}"}, secreto
    if modo == "basic" and len(partes) == 2:
        usuario, secreto = leer(partes[1])
        import base64
        par = f"{usuario}:{secreto}".encode("utf-8")
        return ({"Authorization": "Basic " + base64.b64encode(par).decode()},
                secreto)
    if modo == "header" and len(partes) == 3:
        _u, secreto = leer(partes[2])
        return {partes[1].strip(): secreto}, secreto
    raise SecretoNoEncontrado(
        f"auth '{auth}' no reconocido. Usar bearer:<cred>, token:<cred>, "
        f"basic:<cred> o header:<Nombre>:<cred>.")
