# modelbox

Hub de inferencia local multi-modelo. **Hoy** corre modelos de texto-a-voz (TTS)
intercambiables tras una interfaz única que se adapta a las capacidades de cada
modelo, con monitor de recursos (CPU / RAM / almacenamiento) en vivo. Todo en tu
máquina, sin llamadas a la nube.

> **Visión:** más adelante podrá alojar otros tipos de modelos locales
> (transcripción/STT como Whisper, texto/LLMs, etc.). La arquitectura de
> `models/` + backends por capacidad está pensada para crecer a eso de a poco,
> sin reescrituras. Por ahora, el foco es TTS.

## Modelos incluidos

| Modelo        | Runtime       | RAM aprox.   | Clonación de voz            | Presets        |
|---------------|---------------|--------------|-----------------------------|----------------|
| Supertonic-3  | ONNX (CPU)    | <1 GB        | No (solo presets)           | M1–M5, F1–F5   |
| Pocket-TTS    | PyTorch (CPU) | ~0.7–1.3 GB  | Sí (pesos gated — ver abajo)| 22+ voces      |
| Qwen3-TTS     | Transformers  | ~9.6 GB      | Sí (audio de referencia)    | 9 voces        |

> **¿Poco RAM (VPS)?** Supertonic y Pocket-TTS corren en ~1 GB (Pocket llega a
> ~1.3 GB clonando) y sirven para un VPS de 4 GB sin GPU. Qwen necesita ~9.6 GB
> residentes (no apto para VPS chico).

Cada modelo declara sus capacidades en `shared/backends.py`; la interfaz muestra
u oculta controles según el modelo activo (p. ej. la clonación por audio solo
aparece con Qwen).

## Estructura

```
.
├─ app.py                 # interfaz unificada (Gradio)
├─ requirements.txt       # dependencias de la app (gradio, psutil)
├─ shared/
│  ├─ backends.py         # adaptadores por modelo + capacidades
│  └─ monitor.py          # monitor de CPU/RAM/almacenamiento (psutil)
├─ models/
│  ├─ qwen3/              # run.py · requirements.txt
│  ├─ pockettts/          # run.py · requirements.txt
│  └─ supertonic/         # run.py · requirements.txt
└─ outputs/               # audios generados (se crea solo)
```

## Instalación (desde cero)

Requiere **Python 3.10+**. Se recomienda [`uv`](https://github.com/astral-sh/uv)
para gestionar el entorno (también funciona con `pip` y `venv` estándar).

```bash
# 1) Clonar
git clone <URL-del-repo> && cd <carpeta>

# 2) Crear el entorno virtual
uv venv                       # crea .venv
#   (con pip estándar:  python -m venv .venv)

# 3) Activar el entorno
#   Windows (PowerShell):  .venv\Scripts\Activate.ps1
#   Linux / macOS:         source .venv/bin/activate

# 4) Instalar dependencias de la app + del/los modelo(s) que quieras usar
uv pip install -r requirements.txt
uv pip install -r models/supertonic/requirements.txt   # para Supertonic
uv pip install -r models/qwen3/requirements.txt        # para Qwen
#   (con pip estándar, reemplazá "uv pip" por "pip")
```

> Los **modelos se descargan automáticamente** la primera vez que los usás
> (Supertonic ~385 MB; Qwen ~3 GB por modelo). La primera generación de Qwen en
> CPU puede tardar 1–2 minutos.

## Uso

### Interfaz unificada (recomendado)

```bash
python app.py
```
Abrí la URL local que imprime Gradio (`http://127.0.0.1:7860`). Elegí modelo,
escribí texto y generá. El panel de recursos se actualiza en vivo.

### Modelos por separado (CLI)

```bash
# Supertonic
python models/supertonic/run.py --text "Hola" --lang es --voice F1

# Pocket TTS (liviano, ideal para VPS)
python models/pockettts/run.py --text "Hola" --voice alba

# Qwen — preset
python models/qwen3/run.py --text "Hola" --voice serena
# Qwen — clonación de voz (poné tu propio audio)
python models/qwen3/run.py --text "Hola" --ref_audio mi_voz.wav
```

Los audios se guardan en `outputs/`.

### Clonación de voz (Qwen)

Para clonar una voz necesitás un audio de referencia. **Vos ponés el tuyo**: en
la interfaz podés grabarlo con el micrófono o subir un archivo; por CLI usá
`--ref_audio`. Alternativamente, si colocás un `reference.ogg`/`.wav`/`.mp3` en
`models/qwen3/`, el script CLI lo detecta solo. **Cloná solo voces para las que
tengas consentimiento.**

### Habilitar clonación en Pocket-TTS (opcional, gated)

Pocket-TTS soporta clonación, pero esos pesos son **gated** en Hugging Face. Por
defecto se descarga la variante de solo-presets. Para habilitar la clonación:

1. Entrá a https://huggingface.co/kyutai/pocket-tts y **aceptá los términos**.
2. Descargá el `model.safetensors` del idioma que quieras (p. ej. español:
   `languages/spanish/model.safetensors`) y guardalo en:
   ```
   models/pockettts/weights/model.safetensors
   ```
   (Alternativa: `hf auth login` y dejar que se baje solo.)

Listo — el backend **detecta el archivo y habilita la clonación solo**, sin tocar
código. La clonación de Pocket-TTS aparece en la interfaz como en Qwen. Si el
archivo no está, Pocket queda en modo solo-presets.

> Nota: el config `models/pockettts/clone_spanish.yaml` está armado para español.
> Para clonar en otro idioma, descargá ese `model.safetensors` y ajustá las rutas
> de idioma del yaml.

## Agregar un modelo nuevo

1. Crear `models/<nombre>/` con su `run.py` y `requirements.txt`.
2. Agregar un `Backend` en `shared/backends.py` declarando sus `capabilities`
   y su método `synthesize`.
3. Registrarlo en el dict `BACKENDS`. La interfaz se adapta sola.

## Licencias

El **código** está bajo licencia MIT (ver `LICENSE`). Los **modelos** que se
descargan tienen licencias propias (Supertonic: OpenRAIL-M; Qwen: ver su repo
en Hugging Face) que debés respetar por separado.
