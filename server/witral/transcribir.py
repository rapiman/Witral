"""
Transcribe un audio con faster-whisper (ronda 19). Lo lanza la tool
`transcribir` como TRABAJO (run_esperar para seguirlo): un audio de minutos
tarda minutos, y el cliente MCP corta las llamadas a los ~60 s.

Es un script SUELTO, sin imports de Witral, para poder llevarlo tal cual a un
lugar remoto (~/.witral/transcribir.py) y correrlo con el python de allá.

Salidas, con el mismo nombre base del audio, junto a él o en --salida:
  .txt          el texto limpio, un segmento por línea
  .tiempos.txt  [mm:ss.ss] por segmento; las palabras de baja confianza van
                como [palabra?] y los segmentos dudosos empiezan con (?)
  .srt          subtítulos
  .lrc          letra con tiempos (reproductores, karaoke)
El progreso sale por stdout, segmento a segmento.

Uso: python transcribir.py <audio> [--modelo medium] [--idioma es|auto]
     [--salida carpeta] [--vad si|no] [--prompt "vocabulario"]
     [--umbral 0.5] [--dispositivo cpu|cuda]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

# Un segmento cuyo avg_logprob cae bajo esto se marca como dudoso en
# .tiempos.txt (valor habitual de Whisper para "probablemente mal").
_LOGPROB_DUDOSO = -1.0


def _mmss(s: float) -> str:
    m = int(s // 60)
    return f"{m:02d}:{s - 60 * m:05.2f}"


def _srt_t(s: float) -> str:
    ms = int(round(s * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    seg, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{seg:02d},{ms:03d}"


def _lrc_t(s: float) -> str:
    cs = int(round(s * 100))
    m, cs = divmod(cs, 6000)
    seg, cs = divmod(cs, 100)
    return f"[{m:02d}:{seg:02d}.{cs:02d}]"


def _marcado(seg, umbral: float) -> tuple[str, int]:
    """Texto del segmento con las palabras de baja confianza como [palabra?]."""
    if not seg.words:
        return seg.text.strip(), 0
    partes, dudosas = [], 0
    for w in seg.words:
        t = w.word
        if w.probability < umbral and t.strip():
            lead = t[: len(t) - len(t.lstrip())]
            t = f"{lead}[{t.strip()}?]"
            dudosas += 1
        partes.append(t)
    return "".join(partes).strip(), dudosas


def main(argv: list[str] | None = None) -> int:
    # El log del trabajo es un archivo: sin esto, en Windows Python escribe
    # en cp1252 y revienta con cualquier carácter fuera de esa tabla.
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="Transcribe un audio con faster-whisper.")
    ap.add_argument("audio")
    ap.add_argument("--modelo", default="medium")
    ap.add_argument("--idioma", default="es")
    ap.add_argument("--salida", default="")
    ap.add_argument("--vad", default="si", choices=("si", "no"))
    ap.add_argument("--prompt", default="")
    ap.add_argument("--umbral", type=float, default=0.5)
    ap.add_argument("--dispositivo", default="cpu")
    a = ap.parse_args(argv)

    # En Windows sin modo desarrollador la caché de Hugging Face no puede usar
    # symlinks: funciona igual (copia), solo que avisa con un párrafo por archivo.
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(f"error: falta faster-whisper en este python ({sys.executable}). "
              f"En el venv de Witral: uv sync --extra audio --inexact; "
              f"en otro lugar: pip install faster-whisper", file=sys.stderr)
        return 3

    if not os.path.isfile(a.audio):
        print(f"error: no existe el audio {a.audio}", file=sys.stderr)
        return 2

    base = os.path.splitext(os.path.basename(a.audio))[0]
    carpeta = a.salida or os.path.dirname(os.path.abspath(a.audio))
    os.makedirs(carpeta, exist_ok=True)
    ruta = os.path.join(carpeta, base)

    compute = "int8" if a.dispositivo == "cpu" else "float16"
    print(f"cargando modelo {a.modelo} ({a.dispositivo}, {compute}); "
          f"la primera vez se descarga", flush=True)
    t0 = time.time()
    modelo = WhisperModel(a.modelo, device=a.dispositivo, compute_type=compute)
    idioma = None if a.idioma.lower() in ("", "auto") else a.idioma

    def pasada(vad: bool):
        segs, info = modelo.transcribe(
            a.audio, language=idioma, beam_size=5, word_timestamps=True,
            vad_filter=vad, initial_prompt=a.prompt or None,
        )
        print(f"idioma {info.language} ({info.language_probability:.2f}), "
              f"duración {_mmss(info.duration)}{', con VAD' if vad else ''}", flush=True)
        filas, dudosas_tot = [], 0  # (inicio, fin, texto, marcado, dudoso)
        for s in segs:
            texto = s.text.strip()
            if not texto:
                continue
            marcado, dudosas = _marcado(s, a.umbral)
            dudosas_tot += dudosas
            filas.append((s.start, s.end, texto, marcado, s.avg_logprob < _LOGPROB_DUDOSO))
            print(f"[{_mmss(s.end)}/{_mmss(info.duration)}] {marcado}", flush=True)
        return filas, dudosas_tot

    filas, total_dudosas = pasada(a.vad == "si")
    if not filas and a.vad == "si":
        # El VAD (silero) está entrenado con habla: sobre una mezcla con
        # instrumentos puede descartar TODO el canto. Comprobado en la ronda 19.
        print("el VAD no dejó nada (pasa con canto sobre música): se repite sin VAD",
              flush=True)
        filas, total_dudosas = pasada(False)

    with open(ruta + ".txt", "w", encoding="utf-8") as f:
        f.write("\n".join(r[2] for r in filas) + "\n")
    with open(ruta + ".tiempos.txt", "w", encoding="utf-8") as f:
        for ini, _fin, _t, marc, dud in filas:
            f.write(f"[{_mmss(ini)}] {'(?) ' if dud else ''}{marc}\n")
    with open(ruta + ".srt", "w", encoding="utf-8") as f:
        for i, (ini, fin, texto, _m, _d) in enumerate(filas, 1):
            f.write(f"{i}\n{_srt_t(ini)} --> {_srt_t(fin)}\n{texto}\n\n")
    with open(ruta + ".lrc", "w", encoding="utf-8") as f:
        for ini, _fin, texto, _m, _d in filas:
            f.write(f"{_lrc_t(ini)}{texto}\n")

    n_dud = sum(1 for r in filas if r[4])
    print(f"\nlisto en {time.time() - t0:.0f}s: {len(filas)} segmentos, "
          f"{n_dud} dudosos, {total_dudosas} palabras de baja confianza", flush=True)
    for ext in (".txt", ".tiempos.txt", ".srt", ".lrc"):
        print(f"  {ruta}{ext}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
