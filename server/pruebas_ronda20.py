"""
Ronda 20 (2026-10-22): `run` no debe colgarse cuando el comando lanza un
proceso que hereda la salida y queda vivo después de que cmd termina (caso
real: abrir una app con ventana mediante `start` dejó Witral colgado ~1 hora).

Se simula sin abrir ventanas: `start /b` deja un cmd hijo con ping de 25 s
sosteniendo el pipe heredado. Con el arreglo, _ejecutar_local vuelve en
~tope + 5 s con código 124 y explica que quedó un descendiente vivo.
Antes del arreglo quedaba bloqueado en close() hasta que ese proceso moría.

Correr:  .venv\\Scripts\\python.exe pruebas_ronda20.py
"""
import sys
import time

sys.path.insert(0, ".")
from witral import transporte as T  # noqa: E402

TOPE = 3
t0 = time.time()
r = T._ejecutar_local('start "" /b cmd /c "ping -n 25 127.0.0.1 >nul"',
                      cwd=".", entrada=None, timeout=TOPE)
dt = time.time() - t0

print(f"codigo={r.codigo} duracion={dt:.1f}s")
ok = r.codigo == 124 and dt < TOPE + 10 and "sigue vivo" in repr(r)
print("PRUEBA OK" if ok else "PRUEBA FALLA")
sys.exit(0 if ok else 1)
