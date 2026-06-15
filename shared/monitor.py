"""Monitor de recursos: CPU, memoria y almacenamiento del proceso/sistema.

Independiente del modelo: sirve para cualquier backend TTS.
"""
import os
import psutil

_proc = psutil.Process(os.getpid())
# Primer llamado a cpu_percent() siempre devuelve 0.0; lo "cebamos" al importar.
psutil.cpu_percent(interval=None)
_proc.cpu_percent(interval=None)


def _dir_size_mb(path: str) -> float:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / 1e6


def _gpu_info():
    """Uso de VRAM si hay GPU CUDA disponible. None si no hay."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return {
            "name": torch.cuda.get_device_name(0),
            "vram_used_gb": (total - free) / 1e9,
            "vram_total_gb": total / 1e9,
        }
    except Exception:
        return None


def snapshot(outputs_dir: str) -> dict:
    """Foto instantánea de recursos. Liviano: outputs/ suele ser chico."""
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage(os.path.abspath(outputs_dir) if os.path.exists(outputs_dir) else ".")
    return {
        "cpu_total_pct": psutil.cpu_percent(interval=None),
        "cpu_proc_pct": _proc.cpu_percent(interval=None),
        "ram_proc_mb": _proc.memory_info().rss / 1e6,
        "ram_sys_used_gb": vm.used / 1e9,
        "ram_sys_total_gb": vm.total / 1e9,
        "ram_sys_pct": vm.percent,
        "outputs_mb": _dir_size_mb(outputs_dir) if os.path.exists(outputs_dir) else 0.0,
        "disk_free_gb": disk.free / 1e9,
        "gpu": _gpu_info(),
    }


def format_markdown(s: dict) -> str:
    md = (
        f"**CPU**: {s['cpu_total_pct']:.0f}% sistema · {s['cpu_proc_pct']:.0f}% proceso\n\n"
        f"**RAM proceso**: {s['ram_proc_mb']:.0f} MB\n\n"
        f"**RAM sistema**: {s['ram_sys_used_gb']:.1f} / {s['ram_sys_total_gb']:.1f} GB "
        f"({s['ram_sys_pct']:.0f}%)\n\n"
    )
    gpu = s.get("gpu")
    if gpu:
        md += (
            f"**GPU** ({gpu['name']}): {gpu['vram_used_gb']:.1f} / "
            f"{gpu['vram_total_gb']:.1f} GB VRAM\n\n"
        )
    md += f"**Almacenamiento**: outputs/ {s['outputs_mb']:.1f} MB · libre {s['disk_free_gb']:.0f} GB"
    return md
