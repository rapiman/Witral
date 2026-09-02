"""Comprobaciones de _sin_atribucion: que saque lo que tiene que sacar y nada mas."""
from witral.gitops import _sin_atribucion

CASOS = [
    (
        "Arreglar el timeout de la caja\n\n"
        "Cuerpo del commit.\n\n"
        "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
        "Claude-Session: https://claude.ai/code/session_01ABC\n",
        "Arreglar el timeout de la caja\n\nCuerpo del commit.",
        2,
    ),
    (
        "Mensaje limpio\n\nSin nada raro.\n",
        "Mensaje limpio\n\nSin nada raro.\n",
        0,
    ),
    (
        "Con coautor de verdad\n\n"
        "Co-Authored-By: Andres Zamorano <azamorano@bci.cl>\n",
        "Con coautor de verdad\n\n"
        "Co-Authored-By: Andres Zamorano <azamorano@bci.cl>\n",
        0,
    ),
    (
        "Pie de pull request\n\n"
        "Generated with [Claude Code](https://claude.com/claude-code)\n",
        "Pie de pull request",
        1,
    ),
    (
        "co-authored-by: claude opus 5 <noreply@anthropic.com>\n",
        None,  # queda vacio: debe devolver cadena vacia
        1,
    ),
]

fallos = 0
for i, (entrada, esperado, n) in enumerate(CASOS, 1):
    limpio, quitadas = _sin_atribucion(entrada)
    ok_n = len(quitadas) == n
    ok_txt = True if esperado is None else limpio == esperado
    if esperado is None:
        ok_txt = limpio == ""
    if ok_n and ok_txt:
        print(f"  ok  caso {i}  (quitadas: {len(quitadas)})")
    else:
        fallos += 1
        print(f"FALLA  caso {i}")
        print(f"       esperado: {esperado!r}")
        print(f"       obtenido: {limpio!r}")
        print(f"       quitadas: {len(quitadas)} (esperaba {n})")

print()
print("TODO OK" if fallos == 0 else f"{fallos} caso(s) con problema")
raise SystemExit(1 if fallos else 0)
