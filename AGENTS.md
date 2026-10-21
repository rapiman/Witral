# Witral — servidor MCP propio

- Documento de referencia: `witral\WITRAL_PARA_CLAUDE.md` dentro del repo.
- Repo `rapiman/Witral`; código en `witral\server\`; entry `uv run --directory <server> python -m witral.server`; config `witral\server\witral\lugares.json` (`WITRAL_CONFIG`).
- Modelo lugares × acciones: `donde` apunta al lugar (`local` o cualquiera de los definidos en `lugares.json`).
- Reglas: acciones destructivas o generales requieren `confirmado=True`; ediciones con `editar_linea` + `ancla` o `editar_literal`; `verificar_sintaxis` tras cada bloque; los cambios de código de Witral se cargan al recargar el profile o reiniciar la app.
- En este harness, Witral está conectado como MCP (`mcp__witral__*`); usar sus tools para archivos, git, psql, adb y ejecución.
