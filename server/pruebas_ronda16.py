"""
Pruebas de la ronda 16: espera por patrón en run_esperar, EOL y carpetas en
escribir, ubicaciones del literal ambiguo, y el veredicto de la capa de base de
datos ante un corte.

Correr:  .venv\\Scripts\\python.exe pruebas_ronda16.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from witral import archivos as A        # noqa: E402
from witral import basedatos as DB      # noqa: E402
from witral import trabajos as TR       # noqa: E402
from witral import transporte as T      # noqa: E402
from witral.config import DBConfig      # noqa: E402


fallos = []


def ok(cond, etiqueta):
    if cond:
        print(f"  OK   {etiqueta}")
    else:
        print(f"  FALL {etiqueta}")
        fallos.append(etiqueta)


class LugarFalso:
    def __init__(self, raiz):
        self.raiz = str(raiz)
        self.nombre = "falso"
        self.es_local = True
        self.es_windows = os.name == "nt"
        self.sensible = False


tmp = Path(tempfile.mkdtemp(prefix="witral_r16_"))
lg = LugarFalso(tmp)
PID_MUERTO = 999999

try:
    print("\n--- escribir: conserva el EOL del archivo que sobrescribe ---")
    (tmp / "crlf.txt").write_bytes(b"uno\r\ndos\r\ntres\r\n")
    A.escribir(lg, "crlf.txt", "uno\ndos MODIFICADO\ntres\n")
    datos = (tmp / "crlf.txt").read_bytes()
    ok(datos.count(b"\n") == datos.count(b"\r\n") > 0,
       "archivo CRLF sobrescrito con contenido LF sigue en CRLF (sin LF sueltos)")
    ok(datos.count(b"\r\n") == 3, "y no duplica ni pierde saltos")
    ok(b"MODIFICADO" in datos, "el contenido nuevo quedó")

    (tmp / "lf.txt").write_bytes(b"uno\ndos\n")
    A.escribir(lg, "lf.txt", "uno\r\ndos\r\n")
    ok((tmp / "lf.txt").read_bytes() == b"uno\ndos\n",
       "archivo LF sobrescrito con contenido CRLF vuelve a LF")

    A.escribir(lg, "nuevo.txt", "uno\r\ndos\r\n")
    ok((tmp / "nuevo.txt").read_bytes() == b"uno\r\ndos\r\n",
       "archivo NUEVO se escribe tal como llega")

    A.escribir(lg, "crlf.txt", "a\nb\n", eol="lf")
    ok((tmp / "crlf.txt").read_bytes() == b"a\nb\n", 'eol="lf" fuerza LF')
    A.escribir(lg, "lf.txt", "a\nb\n", eol="crlf")
    ok((tmp / "lf.txt").read_bytes() == b"a\r\nb\r\n", 'eol="crlf" fuerza CRLF')

    salida = A.escribir(lg, "nuevo.txt", "x\ny\n")
    ok("EOL conservado" in salida, "la respuesta dice qué EOL quedó")

    print("\n--- escribir: la carpeta destino se crea sola ---")
    A.escribir(lg, "corpus/win_mapuzungun/glosario.tsv", "a\tb\n")
    ok((tmp / "corpus" / "win_mapuzungun" / "glosario.tsv").exists(),
       "escribe en una carpeta que no existía, sin mkdir previo")

    print("\n--- editar_literal: dónde está cada aparición, no solo cuántas ---")
    (tmp / "amb.py").write_text(
        "def uno():\n    total = 0\n    return total\n\n"
        "def dos():\n    total = 0\n    return total\n", encoding="utf-8")
    try:
        A.editar(lg, "amb.py", literales=[A.EdicionLiteral("total = 0", "total = 1")])
        ok(False, "debía fallar por ambiguo")
    except A.EdicionError as e:
        msj = str(e)
        ok("aparece 2 veces" in msj, "dice cuántas veces aparece")
        ok("linea 2" in msj and "linea 6" in msj,
           "y en qué líneas está cada una")
        ok("editar_linea" in msj, "y qué hacer al respecto")
    ok((tmp / "amb.py").read_text(encoding="utf-8").count("total = 0") == 2,
       "el archivo no se tocó")

    print("\n--- run_esperar(hasta_patron): corta en cuanto sale la línea ---")
    TR._dir_jobs_local = lambda lugar: tmp / "jobs"

    def job(nombre, *, codigo=None, pid=None, out=""):
        base = tmp / "jobs" / nombre
        base.mkdir(parents=True, exist_ok=True)
        (base / "cmd.txt").write_text("bateria.py", encoding="utf-8")
        if codigo is not None:
            (base / "codigo").write_text(str(codigo), encoding="utf-8")
        if pid is not None:
            (base / "pid").write_text(str(pid), encoding="ascii")
        if out:
            (base / "out.log").write_text(out, encoding="utf-8")
        return base

    # Job VIVO cuya línea esperada ya salió: debe volver sin esperar el proceso.
    job("con_match", pid=os.getpid(),
        out="p01 ok\np02 ok\nSONDA DIFIERE en p04\np05 ok\n")
    import time
    t0 = time.time()
    salida = TR.esperar(lg, "con_match", 30, 10, hasta_patron="SONDA IDENTICA|SONDA DIFIERE")
    tardanza = time.time() - t0
    ok("MATCH" in salida, "avisa que fue un match de patrón")
    ok("SONDA DIFIERE en p04" in salida, "y devuelve la línea que matcheó")
    ok(tardanza < 5, f"vuelve de inmediato, no a los 40s (tardó {tardanza:.1f}s)")
    ok("sigue CORRIENDO" not in salida,
       "y no arrastra el pie de 'volver a llamar'")

    # Job TERMINADO sin que el patrón aparezca: vuelve igual y lo dice.
    job("sin_match", codigo=0, pid=PID_MUERTO, out="p01 ok\np02 ok\n")
    salida = TR.esperar(lg, "sin_match", 30, 10, hasta_patron="SONDA DIFIERE")
    ok("TERMINÓ sin que apareciera" in salida,
       "si el trabajo termina sin el patrón, lo dice en vez de esperar de más")

    salida = TR.esperar(lg, "con_match", 30, 10, hasta_patron="((")
    ok("no es una regex válida" in salida, "una regex rota se explica")

    # Sin patrón, el comportamiento anterior se mantiene.
    salida = TR.esperar(lg, "sin_match", 30, 10)
    ok("TERMINADO" in salida and "MATCH" not in salida,
       "sin hasta_patron, la espera funciona como antes")

    print("\n--- sql: veredicto sobre la suerte de la sentencia ---")
    db = DBConfig(motor="postgres", host="h", base="b", usuario="u")

    v = DB._veredicto(db, T.Resultado(1, "", "ERROR: canceling statement due to "
                                             "statement timeout"))
    ok("CANCEL" in v and "NO quedó a medias" in v,
       "cancelada por el servidor => se afirma que se deshizo")

    v = DB._veredicto(db, T.Resultado(127, "", "no se pudo lanzar el comando: x"))
    ok("NO se envió" in v, "fallo de lanzamiento => la base quedó intacta")

    v = DB._veredicto(db, T.Resultado(124, "", "timeout tras 45s"))
    ok("INDETERMINADO" in v and "SELECT" in v,
       "corte de Witral => indeterminado, con la acción concreta")

    v = DB._veredicto(db, T.Resultado(2, "", "server closed the connection "
                                             "unexpectedly"))
    ok("INDETERMINADO" in v and "lectura" in v,
       "conexión perdida => distingue lectura de escritura")

    ok(DB._veredicto(db, T.Resultado(0, "UPDATE 3", "")) == "",
       "una sentencia exitosa no agrega veredicto")

    print("\n--- sql: los topes quedan por debajo del corte del cliente ---")
    ok(DB._TOPE_SENTENCIA < DB._TOPE_LLAMADA < 60,
       "sentencia < llamada < corte del cliente MCP")
    env = DB._entorno(db)
    ok(f"statement_timeout={DB._TOPE_SENTENCIA * 1000}" in env.get("PGOPTIONS", ""),
       "postgres recibe statement_timeout por PGOPTIONS")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS LAS PRUEBAS OK")
