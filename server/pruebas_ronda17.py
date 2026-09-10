"""
Pruebas de la ronda 17: sincronizar con ensayo obligatorio, esquema de archivo,
lectura de varios archivos, alias de editar_literal y capa nativa remota.

Correr:  .venv\\Scripts\\python.exe pruebas_ronda17.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from witral import archivos as A        # noqa: E402
from witral import copiar as CP         # noqa: E402
from witral import sintaxis as SX       # noqa: E402
from witral import transporte as T      # noqa: E402


fallos = []


def ok(cond, etiqueta):
    if cond:
        print(f"  OK   {etiqueta}")
    else:
        print(f"  FALL {etiqueta}")
        fallos.append(etiqueta)


class LugarFalso:
    def __init__(self, raiz, nombre="falso", local=True, windows=False):
        self.raiz = str(raiz)
        self.nombre = nombre
        self.es_local = local
        self.es_windows = windows
        self.sensible = False


class CfgFalso:
    def __init__(self, lugares):
        self._l = {l.nombre: l for l in lugares}
        self.nombres = list(self._l)
        self.error_config = None

    def resolver(self, nombre):
        return self._l[nombre or "local"]


tmp = Path(tempfile.mkdtemp(prefix="witral_r17_"))
lg = LugarFalso(tmp, nombre="local")

try:
    print("\n--- sincronizar: el --delete no corre a ciegas ---")
    wed = LugarFalso("/srv", nombre="wedwed", local=False)
    cfg = CfgFalso([lg, wed])

    llamadas = []
    SALIDA_ENSAYO = (">f+++++++++ index.php\n"
                     ">f.st...... css/app.css\n"
                     "*deleting   images/uploads/foto1.jpg\n"
                     "*deleting   images/uploads/foto2.jpg\n")

    def falso_ejecutar(lugar, args, **kw):
        llamadas.append(args)
        return T.Resultado(0, SALIDA_ENSAYO, "")

    original = T.ejecutar
    CP.T.ejecutar = falso_ejecutar
    try:
        # Sin confirmado: ensayo automatico y lista de lo que se borraria.
        salida = CP.sincronizar(cfg, "wedwed:/repo", "wedwed:/var/www",
                                [".git", "images/uploads"], True, False, False)
        ok("ENSAYO" in salida and "nada se tocó" in salida,
           "sin confirmado se hace ensayo y se dice que no se tocó nada")
        ok("foto1.jpg" in salida and "foto2.jpg" in salida,
           "y se listan los archivos concretos que se borrarían")
        ok("confirmado=True" in salida, "con la instrucción para ejecutar")
        ok("--dry-run" in llamadas[-1], "el rsync del ensayo lleva --dry-run")
        ok("--delete" in llamadas[-1], "y el --delete, para que la lista sea real")
        ok(llamadas[-1].count("--exclude") == 2, "los excludes se pasan uno por uno")
        ok("/repo/" in llamadas[-1], "al origen se le fuerza la barra final")

        # Con confirmado: se ejecuta de verdad (sin --dry-run).
        llamadas.clear()
        salida = CP.sincronizar(cfg, "wedwed:/repo", "wedwed:/var/www",
                                [".git"], True, False, True)
        ok("--dry-run" not in llamadas[-1], "con confirmado ya no es ensayo")
        ok(salida.startswith("Sincronizado"), "y la respuesta lo dice")

        # seco=True fuerza ensayo aunque venga confirmado.
        llamadas.clear()
        CP.sincronizar(cfg, "wedwed:/repo", "wedwed:/var/www", [], True, True, True)
        ok("--dry-run" in llamadas[-1], "seco=True fuerza el ensayo igual")

        # borrar=False: sin --delete y sin pedir confirmación.
        llamadas.clear()
        salida = CP.sincronizar(cfg, "wedwed:/repo", "wedwed:/var/www",
                                [], False, False, False)
        ok("--delete" not in llamadas[-1], "borrar=False no manda --delete")
        ok("ENSAYO" not in salida, "y no exige confirmación")
    finally:
        CP.T.ejecutar = original

    salida = CP.sincronizar(cfg, "local:a", "wedwed:/b", [], True, False, True)
    ok("mismo lugar" in salida and "copiar" in salida,
       "entre lugares distintos se rechaza y se nombra la alternativa")
    cfg_win = CfgFalso([LugarFalso(tmp, nombre="local", windows=True)])
    salida = CP.sincronizar(cfg_win, "local:a", "local:b", [], True, False, True)
    ok("robocopy" in salida, "en Windows se explica que no hay rsync")

    print("\n--- leer(esquema=True): el índice antes del contenido ---")
    (tmp / "doc.md").write_text(
        "# Titulo\ntexto\n## Seccion uno\nmas texto\n### Sub\n#hashtag no\n"
        "## Seccion dos\n", encoding="utf-8")
    esq = A.esquema(lg, "doc.md")
    ok("1: # Titulo" in esq, "encabezado nivel 1 con su línea")
    ok("3: ## Seccion uno" in esq and "7: ## Seccion dos" in esq,
       "todos los niveles, con número de línea")
    ok("hashtag" not in esq, "un # sin espacio no es encabezado")

    (tmp / "cod.py").write_text(
        "import os\n\n\nclass Uno:\n    def metodo(self):\n        pass\n\n"
        "async def dos():\n    return 1\n", encoding="utf-8")
    esq = A.esquema(lg, "cod.py")
    ok("class Uno" in esq and "def metodo" in esq and "async def dos" in esq,
       "en Python toma clases y funciones, anidadas incluidas")
    ok("import os" not in esq, "y no arrastra los imports")

    (tmp / "cod.ts").write_text(
        "import x from 'y';\nexport function uno() {}\n"
        "interface Dos { a: string }\nconst tres = async () => {};\n",
        encoding="utf-8")
    esq = A.esquema(lg, "cod.ts")
    ok(all(s in esq for s in ("export function uno", "interface Dos",
                             "const tres")),
       "en TypeScript toma function, interface y const flecha")

    (tmp / "raro.dat").write_text("una linea\notra\n", encoding="utf-8")
    ok("sin encabezados reconocibles" in A.esquema(lg, "raro.dat"),
       "sin perfil ni encabezados, lo dice y sugiere cómo seguir")

    print("\n--- leer_varios: una llamada, varios archivos ---")
    salida = A.leer_varios(lg, "doc.md cod.py")
    ok("===== doc.md =====" in salida and "===== cod.py =====" in salida,
       "cada archivo llega con su delimitador")
    ok("# Titulo" in salida and "class Uno" in salida, "y con su contenido")
    salida = A.leer_varios(lg, "doc.md, no_existe.txt")
    ok("(no existe)" in salida,
       "un archivo faltante no rompe la llamada entera")
    ok(A.leer_varios(lg, "") .startswith("error:"), "sin rutas, error claro")

    print("\n--- separador de rutas ---")
    ok(A._partir_rutas("a.py b.py") == ["a.py", "b.py"], "espacios")
    ok(A._partir_rutas("a.py, b.py") == ["a.py", "b.py"], "comas")
    ok(A._partir_rutas("a.py\nb.py") == ["a.py", "b.py"], "saltos de línea")
    ok(A._partir_rutas('"C:\\Mis Cosas\\a.txt" b.py')
       == ["C:\\Mis Cosas\\a.txt", "b.py"], "ruta con espacios entre comillas")

    print("\n--- sintaxis: candidatos de binario y capa nativa remota ---")
    ok(SX._candidatos("python") == ("python3", "python"),
       "en unix se prueba python3 antes que python")
    ok(SX._candidatos("php") == ("php",), "sin alternativas, el binario tal cual")

    vistos = []

    def falso_remoto(lugar, args, **kw):
        vistos.append(args)
        if args[:2] == ["command", "-v"]:
            return T.Resultado(0, "/usr/bin/php", "")
        return T.Resultado(255, "", "PHP Parse error: syntax error, line 12")

    SX_original = SX.__dict__.get("_ejecutar_original")
    import witral.transporte as TT
    orig = TT.ejecutar
    TT.ejecutar = falso_remoto
    try:
        r = SX.correr_nativo_remoto(wed, ".php", "app/index.php")
        ok(r is not None and r[0] is False, "el error del php remoto llega como fallo")
        ok("Parse error" in r[1], "con el detalle del verificador")
        ok("wedwed" in r[1], "y diciendo en qué lugar corrió")
        ok(any(a[:2] == ["command", "-v"] for a in vistos),
           "primero se comprueba que el binario exista")
    finally:
        TT.ejecutar = orig

    def sin_binario(lugar, args, **kw):
        return T.Resultado(1, "", "")

    TT.ejecutar = sin_binario
    try:
        ok(SX.correr_nativo_remoto(wed, ".php", "x.php") is None,
           "si el binario no está en el lugar, devuelve None (no inventa)")
    finally:
        TT.ejecutar = orig

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if fallos:
    print(f"FALLARON {len(fallos)}:")
    for f in fallos:
        print(f"  - {f}")
    sys.exit(1)
print("TODAS LAS PRUEBAS OK")
