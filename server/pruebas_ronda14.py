"""
Pruebas de lógica pura de la ronda 14: shell="auto" (cmd vs PowerShell),
tope propio de `run`, y el módulo de secretos (parsing de auth + enmascarado).

No tocan disco, red ni dispositivos. La única parte que sí toca el sistema es
la lectura del Credential Manager, y solo para comprobar que la estructura de
ctypes está bien definida (que no reviente y devuelva una lista).

Correr:  .venv\\Scripts\\python.exe pruebas_ronda14.py
"""

import base64
import sys

sys.path.insert(0, ".")

from witral import server as S          # noqa: E402
from witral import secretos as SEC      # noqa: E402
from witral import transporte as T      # noqa: E402


fallos = []


def ok(cond, etiqueta):
    if cond:
        print(f"  OK   {etiqueta}")
    else:
        print(f"  FALL {etiqueta}")
        fallos.append(etiqueta)


class LugarFalso:
    def __init__(self, es_windows=True):
        self.es_windows = es_windows
        self.nombre = "falso"
        self.raiz = ""
        self.sensible = False


print("\n--- _motivo_powershell: cuándo cmd pelea ---")
hostiles = [
    r'findstr "error\|warning" out.log',
    r'for %%i in (*.kt) do echo %%i',
    r'echo "dijo \"hola\""',
    r'echo ""anidadas""',
    r"python -c 'print(1 + 2)'",
]
for c in hostiles:
    ok(S._motivo_powershell(c) != "", f"hostil: {c}")

benignos = [
    "dir",
    "git status",
    "echo %USERPROFILE%",                      # %VAR% es cmd a propósito
    r'type "C:\carpeta con espacios\a.txt"',
    r'findstr "error\|warn" a.log && dir',     # && => nunca desviar (PS 5.1)
    "git log -5 || dir",
]
for c in benignos:
    ok(S._motivo_powershell(c) == "", f"benigno: {c}")


print("\n--- _envolver_shell ---")
win, nix = LugarFalso(True), LugarFalso(False)

cmd, nota = S._envolver_shell(win, r'findstr "a\|b" x.log', "auto")
ok(cmd.startswith("powershell -NoProfile"), "auto+hostil => PowerShell")
ok("PowerShell por" in nota, "auto+hostil => la nota dice por qué")
cod = cmd.rsplit(" ", 1)[1]
ok(base64.b64decode(cod).decode("utf-16-le") == r'findstr "a\|b" x.log',
   "el comando viaja intacto en -EncodedCommand")

cmd, nota = S._envolver_shell(win, "git status", "auto")
ok(cmd == "git status" and nota == "", "auto+benigno => cmd tal cual, sin nota")

cmd, nota = S._envolver_shell(nix, r"grep -n 'a b' x.log", "auto")
ok(cmd == r"grep -n 'a b' x.log", "auto en lugar unix => nunca toca el comando")

cmd, _ = S._envolver_shell(win, r'echo ""x""', "cmd")
ok(cmd == r'echo ""x""', 'shell="cmd" fuerza cmd aunque sea hostil')

cmd, _ = S._envolver_shell(win, "dir", "powershell")
ok(cmd.startswith("powershell -NoProfile"), 'shell="powershell" fuerza siempre')

for shell_malo, lugar in (("bash", win), ("powershell", nix)):
    try:
        S._envolver_shell(lugar, "dir", shell_malo)
        ok(False, f"debía rechazar shell={shell_malo} en {lugar.es_windows}")
    except ValueError:
        ok(True, f"rechaza shell={shell_malo} (windows={lugar.es_windows})")


print("\n--- _huele_a_fallo_de_cmd: reintento seguro ---")
ok(S._huele_a_fallo_de_cmd(
    T.Resultado(1, "", "'head' is not recognized as an internal or external command")),
   "inglés: comando no reconocido")
ok(S._huele_a_fallo_de_cmd(
    T.Resultado(255, "", "no se esperaba en este momento.")),
   "español: no se esperaba en este momento")
ok(not S._huele_a_fallo_de_cmd(T.Resultado(0, "algo", "")),
   "código 0 no es fallo de cmd")
ok(not S._huele_a_fallo_de_cmd(T.Resultado(1, "salida parcial", "was unexpected at this time")),
   "con salida ya emitida NO se reintenta (podría duplicar efectos)")
ok(not S._huele_a_fallo_de_cmd(T.Resultado(1, "", "fatal: not a git repository")),
   "error real del comando no dispara reintento")

print("\n--- el reintento solo aplica a comandos de solo lectura ---")
ok(S._es_solo_lectura("findstr error a.log"), "findstr es lectura")
ok(not S._es_solo_lectura("del a.log"), "del NO es lectura")


print("\n--- secretos: parsing de auth (sin tocar el Credential Manager) ---")
original = SEC.leer
SEC.leer = lambda nombre: ("juan", f"SECRETO-{nombre}")
try:
    h, s = SEC.headers_auth("bearer:GitHub")
    ok(h == {"Authorization": "Bearer SECRETO-GitHub"}, "bearer")
    ok(s == "SECRETO-GitHub", "devuelve el secreto para poder enmascararlo")

    h, _ = SEC.headers_auth("token:GitHub")
    ok(h == {"Authorization": "token SECRETO-GitHub"}, "token (GitHub clásico)")

    h, _ = SEC.headers_auth("basic:Jira")
    esperado = "Basic " + base64.b64encode(b"juan:SECRETO-Jira").decode()
    ok(h == {"Authorization": esperado}, "basic usa el usuario de la credencial")

    h, _ = SEC.headers_auth("header:X-Api-Key:Interna")
    ok(h == {"X-Api-Key": "SECRETO-Interna"}, "header custom")

    ok(SEC.headers_auth("") == ({}, ""), "auth vacío => sin headers")

    for malo in ("bearer", "raro:X", "header:X"):
        try:
            SEC.headers_auth(malo)
            ok(False, f"debía rechazar auth='{malo}'")
        except SEC.SecretoNoEncontrado:
            ok(True, f"rechaza auth='{malo}'")
finally:
    SEC.leer = original

print("\n--- secretos: enmascarado ---")
ok(SEC.enmascarar("Authorization: Bearer ghp_abc123", "ghp_abc123")
   == "Authorization: Bearer ····", "el secreto no sobrevive a la salida")
ok(SEC.enmascarar("hola", "ab") == "hola",
   "no enmascara cadenas de menos de 4 chars (destrozaría el texto)")
ok(SEC._var_entorno("GitHub Token") == "WITRAL_SECRETO_GITHUB_TOKEN",
   "nombre de variable de entorno equivalente")


print("\n--- secretos: Credential Manager en vivo (solo estructura) ---")
if SEC._es_windows():
    try:
        nombres = SEC.listar_windows("")
        ok(isinstance(nombres, list),
           f"CredEnumerate responde ({len(nombres)} credenciales genéricas)")
    except Exception as e:
        ok(False, f"CredEnumerate reventó: {e}")
    salida = SEC.describir("witral_credencial_que_no_existe")
    ok("cmdkey /generic:" in salida,
       "credencial inexistente => explica cómo guardarla")
else:
    print("  (saltado: no es Windows)")


print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS LAS PRUEBAS OK")
