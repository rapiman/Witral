"""
Pruebas de la ronda 18: id="ultimo" y lista ante un id inexistente, delta de
líneas en run_status/run_esperar, y al_terminar en run_async.

Las de al_terminar lanzan procesos REALES en un temporal (sin dispositivos ni
bases): es la única forma de comprobar que el wrapper corre el hook, que
WITRAL_CODIGO llega, que run_matar lo dispara y que el tope corta.

Correr:  .venv\\Scripts\\python.exe pruebas_ronda18.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, ".")

from witral import trabajos as TR       # noqa: E402
from witral import transporte as T      # noqa: E402


fallos = []


def ok(cond, etiqueta):
    if cond:
        print(f"  OK   {etiqueta}")
    else:
        print(f"  FALL {etiqueta}")
        fallos.append(etiqueta)


class LugarFalso:
    def __init__(self, raiz, local=True):
        self.raiz = str(raiz)
        self.nombre = "falso"
        self.es_local = local
        self.es_windows = os.name == "nt"
        self.sensible = False


def esperar_archivo(ruta, segundos, contiene=""):
    t0 = time.time()
    while time.time() - t0 < segundos:
        if ruta.exists():
            txt = ruta.read_text(encoding="utf-8", errors="replace")
            if contiene in txt:
                return txt
        time.sleep(0.5)
    return ruta.read_text(encoding="utf-8", errors="replace") if ruta.exists() else ""


tmp = Path(tempfile.mkdtemp(prefix="witral_r18_"))
lg = LugarFalso(tmp)
PID_MUERTO = 999999
original_dir_jobs = TR._dir_jobs_local
TR._dir_jobs_local = lambda lugar: Path(lugar.raiz) / "jobs"


def job(nombre, *, codigo=None, pid=None, out="", err=""):
    base = tmp / "jobs" / nombre
    base.mkdir(parents=True, exist_ok=True)
    (base / "cmd.txt").write_text("bateria.py", encoding="utf-8")
    if codigo is not None:
        (base / "codigo").write_text(str(codigo), encoding="utf-8")
    if pid is not None:
        (base / "pid").write_text(str(pid), encoding="ascii")
    if out:
        (base / "out.log").write_bytes(out.encode("utf-8"))
    if err:
        (base / "err.log").write_bytes(err.encode("utf-8"))
    return base


try:
    print("\n--- id=\"ultimo\" ---")
    ok(TR.resolver_id(lg, "ultimo")[0] == "",
       "sin trabajos, 'ultimo' no resuelve a nada")
    ok("Sin trabajos" in TR.estado(lg, "ultimo"), "y lo dice")

    job("20261004-100000-aaaa", codigo=0, out="viejo\n")
    job("20261004-110000-bbbb", codigo=1, out="nuevo\n")
    job("20261003-235959-cccc", codigo=0, out="anterior\n")
    real, nota = TR.resolver_id(lg, "ultimo")
    ok(real == "20261004-110000-bbbb", "resuelve al más reciente por timestamp")
    ok("20261004-110000-bbbb" in nota, "la nota dice a cuál resolvió")
    for alias in ("último", "ULTIMO", "last"):
        ok(TR.resolver_id(lg, alias)[0] == real, f"alias '{alias}'")
    ok(TR.resolver_id(lg, "20261004-100000-aaaa") == ("20261004-100000-aaaa", ""),
       "un id concreto pasa tal cual y sin nota")
    salida = TR.estado(lg, "ultimo")
    ok("nuevo" in salida and "-> 20261004-110000-bbbb" in salida,
       "run_status(id='ultimo') muestra el trabajo y a cuál resolvió")
    salida = TR.esperar(lg, "ultimo", 5, 10)
    ok("TERMINADO" in salida and "-> 20261004-110000-bbbb" in salida,
       "run_esperar(id='ultimo') también")

    print("\n--- id inexistente: la lista viene en la misma respuesta ---")
    for nombre, salida in (("estado", TR.estado(lg, "inventado")),
                           ("esperar", TR.esperar(lg, "inventado", 5))):
        ok("No existe el trabajo 'inventado'" in salida, f"{nombre}: dice que no existe")
        ok("20261004-110000-bbbb" in salida and "20261004-100000-aaaa" in salida,
           f"{nombre}: y trae la lista de trabajos")
        ok("Ver run_status sin id" not in salida,
           f"{nombre}: ya no manda a hacer otra llamada")
        ok("ultimo" in salida, f"{nombre}: y sugiere id=\"ultimo\"")

    print("\n--- delta de líneas ---")
    out = "".join(f"linea {i}\n" for i in range(1, 11))
    job("delta", pid=os.getpid(), out=out, err="aviso 1\r\naviso 2\r\n")
    salida = TR.estado(lg, "delta", 40)
    ok("desde_out=10, desde_err=2" in salida,
       "el pie dice en qué línea quedó cada log (CRLF incluido)")
    salida = TR.estado(lg, "delta", 40, desde_out=7, desde_err=2)
    ok("linea 8" in salida and "linea 10" in salida and "linea 7\n" not in salida,
       "desde_out=7 trae solo 8..10")
    ok("sin líneas nuevas después de la 2" in salida, "err sin novedades lo dice")
    salida = TR.estado(lg, "delta", 2, desde_out=3)
    ok("se omiten las 5 primeras" in salida and "linea 9" in salida
       and "linea 4" not in salida,
       "si lo nuevo pasa 'lineas', lo dice y muestra el final")
    salida = TR.estado(lg, "delta", 40, desde_out=50)
    ok("pasa el total" in salida, "un desde mayor que el log se explica")

    # Última línea a medio escribir: se muestra, pero no se cuenta.
    job("parcial", pid=os.getpid(), out="a\nb\nc a medio")
    salida = TR.estado(lg, "parcial", 40)
    ok("desde_out=2" in salida and "c a medio" in salida,
       "la línea sin salto se ve pero no entra en la cuenta")
    with open(tmp / "jobs" / "parcial" / "out.log", "a", encoding="utf-8") as f:
        f.write(" camino\nd\n")
    salida = TR.estado(lg, "parcial", 40, desde_out=2)
    ok("c a medio camino" in salida and "\nd" in salida,
       "la próxima llamada la trae entera")

    mostrar, completas, _ = TR._tramo("", 3, 10)
    ok(mostrar == [] and completas == 0, "log vacío")

    salida = TR.esperar(lg, "delta", 1, 40, desde_out=9)
    ok("linea 10" in salida and "linea 9\n" not in salida,
       "run_esperar acepta el delta")
    ok("sigue CORRIENDO" in salida and "desde_out" in salida
       and "al_terminar" in salida,
       "el pie de 'volver a llamar' nombra el delta y al_terminar")

    print("\n--- estado remoto: el pie se arma con lo que cuenta wc -l ---")
    capturado = {}

    def falso_ejecutar(lugar, linea, **kw):
        capturado["linea"] = linea
        return T.Resultado(0, "Trabajo x en falso\nestado: CORRIENDO (pid 1)\n"
                              "__WITRAL_N out.log 120\n--- out.log ---\nl120\n"
                              "__WITRAL_N err.log 4\n--- err.log ---\n", "")

    ejecutar_real = T.ejecutar
    T.ejecutar = falso_ejecutar
    try:
        remoto = LugarFalso("/srv/app", local=False)
        salida = TR.estado(remoto, "x", 40, desde_out=100)
        ok("desde_out=120, desde_err=4" in salida, "pie con los totales remotos")
        ok("__WITRAL_N" not in salida, "las marcas internas no se ven")
        ok("tail -n +101" in capturado["linea"], "pide desde la línea 101")
    finally:
        T.ejecutar = ejecutar_real

    print("\n--- al_terminar: textos del hook unix ---")
    h = TR._hook_unix(".witral/jobs/j1")
    ok("timeout 60 sh" in h and "al_terminar.log" in h and "124" in h,
       "hook.sh con tope, log aparte y aviso de corte")

    print("\n--- run: SQL inline se detiene antes de ejecutar ---")
    from witral import server as SV
    casos_si = [
        ('psql -h db -U app -c "UPDATE t SET x=1"', "psql"),
        ('sqlcmd -S srv -Q "select * from R5EVENTS"', "sqlcmd"),
        ('C:\\tools\\sqlite3.exe app.db "DELETE FROM cola"', "sqlite3"),
        ("mysql -e 'insert into t values (1)'", "mysql"),
        ('cd x && psql.exe -c "create table t(a int)"', "psql"),
    ]
    for comando, cliente in casos_si:
        ok(SV._sql_inline(comando) == cliente, f"detecta: {comando}")
    casos_no = [
        "psql -f migracion_028.sql",
        "psql --version",
        "sqlcmd -S srv -i script.sql",
        "git log --grep=select",
        'findstr /s "DELETE FROM" *.sql',
        "echo psqlite",
    ]
    for comando in casos_no:
        ok(SV._sql_inline(comando) == "", f"no confunde: {comando}")
    aviso = SV.run('psql -c "UPDATE t SET x=1"', confirmado=True)
    ok("SQL INLINE" in aviso and "sql(donde, comando)" in aviso,
       "run se niega aunque venga confirmado=True y nombra la tool")
    ok("BEGIN" in aviso and "ROLLBACK" in aviso, "y recuerda la regla del fixture")
    aviso = SV.run('mysql -e "select 1"', confirmado=True)
    ok("sql_inline=True" in aviso and "No hay tool tipada" in aviso,
       "para un cliente sin tool tipada, dice cómo pasar igual")

    if os.name == "nt":
        print("\n--- al_terminar: procesos reales (Windows) ---")
        jid = TR.lanzar(lg, "echo trabajo & exit /b 3",
                        al_terminar="echo fin %WITRAL_JOB% %WITRAL_CODIGO% > centinela.txt")
        base = tmp / "jobs" / jid
        txt = esperar_archivo(tmp / "centinela.txt", 20, "fin")
        ok(f"fin {jid} 3" in txt, f"al_terminar corrió con WITRAL_JOB y WITRAL_CODIGO=3 ({txt.strip()!r})")
        ok((base / "codigo").read_text().strip() == "3",
           "el código del trabajo sigue siendo el del comando")
        salida = TR.estado(lg, jid)
        ok("al_terminar: echo fin" in salida, "run_status muestra el al_terminar")

        (tmp / "centinela.txt").unlink()
        jid = TR.lanzar(lg, "ping -n 30 127.0.0.1 >nul",
                        al_terminar="echo fin %WITRAL_CODIGO% > centinela.txt")
        time.sleep(3)
        r = TR.matar(lg, jid)
        ok("al_terminar lanzado" in r, "run_matar dispara al_terminar")
        txt = esperar_archivo(tmp / "centinela.txt", 20, "fin")
        ok("fin matado" in txt, f"con WITRAL_CODIGO=matado ({txt.strip()!r})")

        TR._TOPE_AL_TERMINAR = 3
        jid = TR.lanzar(lg, "echo rapido", al_terminar="ping -n 30 127.0.0.1")
        log = esperar_archivo(tmp / "jobs" / jid / "al_terminar.log", 25, "cortado")
        ok("cortado por el tope de 3s" in log, "un al_terminar colgado se corta en el tope")
        ok(TR._diagnostico_local(tmp / "jobs" / jid)[0] == "terminado",
           "y el trabajo nunca figuró como corriendo por culpa del hook")
    else:
        print("\n(al_terminar con procesos reales: solo en Windows)")

finally:
    TR._dir_jobs_local = original_dir_jobs
    time.sleep(1)
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS LAS PRUEBAS OK")
