"""
Pruebas de la ronda 15: diagnóstico de trabajos (el estado contradictorio de
run_esperar), errores de Gradle, diagnóstico de adb_install, parseo de
adb_estado_app, cliente sqlite y entorno de la JVM.

Todo corre contra archivos temporales; no toca dispositivos ni bases reales.

Correr:  .venv\\Scripts\\python.exe pruebas_ronda15.py
"""

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from witral import basedatos as DB       # noqa: E402
from witral import movil as M            # noqa: E402
from witral import trabajos as TR        # noqa: E402
from witral import transporte as T       # noqa: E402


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


tmp = Path(tempfile.mkdtemp(prefix="witral_r15_"))
PID_MUERTO = 999999   # inexistente en cualquier sistema razonable


def job(nombre, *, codigo=None, pid=None, out="", err=""):
    base = tmp / "jobs" / nombre
    base.mkdir(parents=True, exist_ok=True)
    (base / "cmd.txt").write_text("gradlew.bat assembleDevDebug", encoding="utf-8")
    if codigo is not None:
        (base / "codigo").write_text(str(codigo), encoding="utf-8")
    if pid is not None:
        (base / "pid").write_text(str(pid), encoding="ascii")
    if out:
        (base / "out.log").write_text(out, encoding="utf-8")
    if err:
        (base / "err.log").write_text(err, encoding="utf-8")
    return base


try:
    print("\n--- _diagnostico_local: un solo lugar decide el estado ---")

    b = job("terminado", codigo=0, pid=PID_MUERTO, out="BUILD SUCCESSFUL in 4m")
    est, cod, _ = TR._diagnostico_local(b)
    ok((est, cod) == ("terminado", "0"), "con archivo 'codigo' => TERMINADO")

    b = job("corriendo", pid=os.getpid(), out="> Task :app:compileKotlin")
    est, _, det = TR._diagnostico_local(b)
    ok(est == "corriendo", "pid vivo y sin código => CORRIENDO")

    # El caso del feedback: build terminado bien, proceso muerto, sin 'codigo'.
    b = job("exito_sin_codigo", pid=PID_MUERTO,
            out="> Task :app:assemble\nBUILD SUCCESSFUL in 4m 2s\n")
    est, cod, det = TR._diagnostico_local(b)
    ok(est == "terminado_sin_codigo", "proceso muerto => NO se reporta corriendo")
    ok(cod == "0", "BUILD SUCCESSFUL => código 0 inferido del log")
    ok("BUILD SUCCESSFUL" in det, "el detalle dice en qué se basó")

    b = job("fallado_sin_codigo", pid=PID_MUERTO,
            out="FAILURE: Build failed with an exception.\nBUILD FAILED in 12s\n")
    est, cod, _ = TR._diagnostico_local(b)
    ok((est, cod) == ("terminado_sin_codigo", "distinto de 0"),
       "BUILD FAILED => terminado con código distinto de 0")

    b = job("abortado", pid=PID_MUERTO, out="> Task :app:compileKotlin\n")
    est, cod, det = TR._diagnostico_local(b)
    ok((est, cod) == ("terminado_sin_codigo", ""),
       "sin marca de cierre => terminado sin código, sin inventar uno")
    ok("abortado" in det, "y el detalle lo llama por su nombre")

    b = job("recien_lanzado")   # sin pid todavía
    est, _, det = TR._diagnostico_local(b)
    ok(est == "corriendo", "sin pid y recién creado => CORRIENDO (no muerto)")
    ok("recién lanzado" in det, "el detalle explica la ausencia de pid")

    ok(TR._diagnostico_local(tmp / "jobs" / "no_existe")[0] == "no_existe",
       "carpeta inexistente => no_existe")

    print("\n--- el pie de 'volver a llamar' es inalcanzable si terminó ---")
    # esperar() devuelve estado() sin el pie cuando el estado es terminal.
    lg = LugarFalso(tmp)
    TR._dir_jobs_local = lambda lugar: tmp / "jobs"   # apuntar al temporal
    salida = TR.esperar(lg, "exito_sin_codigo", 5, 10)
    ok("run_esperar: sigue CORRIENDO" not in salida,
       "terminado sin código => sin pie de 'volver a llamar'")
    ok("TERMINADO" in salida, "y el estado dice TERMINADO")
    ok("código 0" in salida, "informando el código inferido")

    salida = TR.estado(lg, "abortado", 10)
    ok("¿abortado o recién lanzado?" not in salida,
       "se fue el mensaje ambiguo que juntaba dos hipótesis")

    print("\n--- gradle_errores: deduplica y mira los dos logs ---")
    job("con_errores", codigo=1,
        out="> Task :app:compileDebugKotlin FAILED\nBUILD FAILED in 30s\n",
        err=("e: file:///a/App2AppActivity.kt:31:5 unresolved reference: foo\n"
             "e: file:///a/App2AppActivity.kt:31:5 unresolved reference: foo\n"
             "e: file:///a/Otro.kt:9:1 expecting '}'\n"
             "w: file:///a/Otro.kt:2:1 unused import\n"))
    M_lugar = LugarFalso(tmp.parent)   # raiz tal que raiz/.witral/jobs no exista
    lg_jobs = LugarFalso(tmp)
    # gradle_errores arma la ruta <raiz>/.witral/jobs/<id>: replicarla.
    destino = tmp / ".witral" / "jobs"
    destino.mkdir(parents=True, exist_ok=True)
    shutil.copytree(tmp / "jobs" / "con_errores", destino / "con_errores",
                    dirs_exist_ok=True)
    salida = M.gradle_errores(lg_jobs, "con_errores")
    ok(salida.count("unresolved reference: foo") == 1, "duplicados colapsados")
    ok("expecting '}'" in salida, "toma todos los errores distintos")
    ok("unused import" not in salida, "las advertencias (w:) no son errores")
    ok(salida.startswith("2 error"), "encabeza con el conteo real")

    shutil.copytree(tmp / "jobs" / "fallado_sin_codigo",
                    destino / "sin_e", dirs_exist_ok=True)
    salida = M.gradle_errores(lg_jobs, "sin_e")
    ok("FAILURE: Build failed" in salida and "fallo es de Gradle" in salida,
       "sin líneas 'e:' cae al fallo de Gradle en vez de devolver vacío")
    ok("no existe" in M.gradle_errores(lg_jobs, "fantasma").lower(),
       "trabajo inexistente se dice claro")

    print("\n--- adb_install: traducir el fallo, no repetirlo ---")
    r = T.Resultado(1, "", "adb: failed to install app.apk: Failure "
                           "[INSTALL_FAILED_VERSION_DOWNGRADE]")
    txt = M._diagnostico_install(lg, "S1", "app.apk", r, False)
    ok("versionCode" in txt, "explica que la comparación es por versionCode")
    ok("permitir_downgrade=True" in txt, "ofrece la salida concreta")
    txt = M._diagnostico_install(lg, "S1", "app.apk", r, True)
    ok("permitir_downgrade=True" not in txt,
       "si ya se usó el flag, no lo vuelve a sugerir")
    r2 = T.Resultado(1, "", "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")
    ok("firmada" in M._diagnostico_install(lg, "S1", "a.apk", r2, False),
       "firma incompatible se explica aparte")
    ok(M._diagnostico_install(lg, "S1", "a.apk",
                              T.Resultado(0, "Success", ""), False) == "",
       "un install exitoso no agrega ruido")

    print("\n--- adb_estado_app: parseo de dumpsys ---")
    DUMPSYS = """Packages:
  Package [cl.bci.pagos] (a1b2):
    versionCode=2040100 minSdk=24 targetSdk=34
    versionName=2.4.1
    flags=[ DEBUGGABLE HAS_CODE ALLOW_BACKUP ]
    codePath=/data/app/~~xyz==/cl.bci.pagos-1
    firstInstallTime=2026-07-01 10:00:00
    lastUpdateTime=2026-08-18 09:12:33
    installerPackageName=com.android.shell
"""
    original = T.ejecutar
    T.ejecutar = lambda *a, **k: T.Resultado(0, DUMPSYS, "")
    try:
        salida = M.adb_estado_app(lg, "S1", "cl.bci.pagos")
    finally:
        T.ejecutar = original
    for campo in ("versionName: 2.4.1", "versionCode: 2040100",
                  "lastUpdateTime: 2026-08-18 09:12:33",
                  "installerPackageName: com.android.shell"):
        ok(campo in salida, f"extrae {campo.split(':')[0]}")
    ok("debuggable: sí" in salida, "detecta DEBUGGABLE y dice para qué sirve")

    T.ejecutar = lambda *a, **k: T.Resultado(0, "Unable to find package: x", "")
    try:
        ok("no está instalado" in M.adb_estado_app(lg, "S1", "x"),
           "paquete ausente se dice en castellano, no con el texto de dumpsys")
    finally:
        T.ejecutar = original

    print("\n--- sqlite: lectura protegida y escritura confirmada ---")
    ruta_db = tmp / "boleta.db"
    con = sqlite3.connect(ruta_db)
    con.execute("CREATE TABLE caf (id INTEGER PRIMARY KEY, folio INT, rut TEXT)")
    con.executemany("INSERT INTO caf (folio, rut) VALUES (?, ?)",
                    [(101, "1-9"), (102, "2-7")])
    con.commit()
    con.close()

    salida = DB.sqlite_consulta(lg, "boleta.db", "")
    ok("caf" in salida, "sin comando, lista las tablas")

    salida = DB.sqlite_consulta(lg, "boleta.db", "SELECT folio, rut FROM caf")
    ok("101" in salida and "2-7" in salida, "la lectura devuelve las filas")
    ok("(2 fila(s))" in salida, "y el conteo")

    salida = DB.sqlite_consulta(lg, "boleta.db", "DELETE FROM caf")
    ok("CONFIRMACIÓN REQUERIDA" in salida, "una escritura pide confirmación")
    con = sqlite3.connect(ruta_db)
    ok(con.execute("SELECT count(*) FROM caf").fetchone()[0] == 2,
       "y NO tocó la base mientras tanto")
    con.close()

    salida = DB.sqlite_consulta(lg, "boleta.db",
                                "DELETE FROM caf WHERE folio=101", True)
    con = sqlite3.connect(ruta_db)
    ok(con.execute("SELECT count(*) FROM caf").fetchone()[0] == 1,
       "con confirmado=True sí escribe")
    con.close()

    salida = DB.sqlite_consulta(lg, "no_existe.db", "SELECT 1")
    ok("adb_pull" in salida,
       "base inexistente sugiere el camino desde el dispositivo")

    salida = DB.sqlite_consulta(lg, "boleta.db", "SELECT * FROM tabla_fantasma")
    ok("error en" in salida, "un SQL malo informa el error sin reventar")

    print("\n--- entorno_jvm: el fix vive en el transporte ---")
    env = T.entorno_jvm(str(tmp))
    if os.name == "nt":
        ok("JAVA_TOOL_OPTIONS" in env, "en Windows aporta JAVA_TOOL_OPTIONS")
        ok("unixdomain.tmpdir" in env.get("JAVA_TOOL_OPTIONS", ""),
           "apuntando el tmpdir de los sockets AF_UNIX")
        ok((tmp / ".witral" / "tmpjava").is_dir(), "y crea la carpeta destino")
    else:
        ok(env == {}, "fuera de Windows no aplica")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS LAS PRUEBAS OK")
