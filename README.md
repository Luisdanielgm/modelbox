# Modelbox

Hub de inferencia local multi-modelo: **texto-a-voz (TTS)** y **transcripción
(STT)** intercambiables tras una interfaz única (con login opcional) y una **API
REST** con token, más monitor de recursos y cola de inferencia en vivo. Todo en
tu máquina, sin llamadas a la nube.

> **Arquitectura:** `models/` + backends por capacidad. Agregar un modelo (o un
> nuevo tipo, como LLMs de texto) no requiere reescribir la interfaz.

## Inicio rápido

**Con Docker (lo más rápido):**

```bash
git clone <URL-del-repo> && cd modelbox
cp .env.example .env          # opcional: credenciales y config (compose lo lee solo)
docker compose up -d --build
```

Abrí <http://localhost:7860>, elegí un modelo, apretá **Descargar** y generá.
Detalle y opciones en [Docker / Despliegue](#docker--despliegue).

**Sin Docker (Python 3.10+):**

```bash
git clone <URL-del-repo> && cd modelbox
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt -r models/supertonic/requirements.txt
python server.py
```

Abrí <http://127.0.0.1:7860>. Instalá los requirements de cada modelo que quieras
usar (ver [Instalación](#instalación-desde-cero)).

**En un VPS:** [Dokploy](#en-un-vps-con-dokploy) construye la imagen en tu propio
servidor desde el repo (sin Docker Hub).

## Modelos incluidos

| Modelo        | Tipo | Runtime          | RAM aprox.   | Clonación / Notas            |
|---------------|------|------------------|--------------|------------------------------|
| Supertonic-3  | TTS  | ONNX (CPU)       | <1 GB        | No (presets M1–M5, F1–F5)    |
| Pocket-TTS    | TTS  | PyTorch (CPU)    | ~0.7–1.3 GB  | Sí (pesos gated — ver abajo) |
| Qwen3-TTS     | TTS  | Transformers     | ~9.6 GB      | Sí (audio de referencia)     |
| Whisper       | STT  | faster-whisper   | ~1 GB (small)| Transcribe mp3/ogg/m4a/wav…  |

> **¿Poco RAM (VPS)?** Supertonic + Pocket + Whisper-small cargados a la vez
> ocupan **~2.8 GB medidos** — entran holgados en 5–6 GB con margen para
> concurrencia. Qwen necesita ~9.6 GB residentes él solo (no apto para VPS chico).

Cada modelo declara sus capacidades en `shared/backends.py` (TTS) o
`shared/transcribe.py` (STT); la interfaz muestra u oculta controles según el
modelo activo. **Concurrencia:** en CPU las inferencias se serializan en una cola
(una ya usa todos los cores); el resto espera turno, visible en el panel y en
`/api/health`.

## Estructura

```
.
├─ server.py              # entrypoint: API (FastAPI) + panel Gradio montado
├─ app.py                 # panel Gradio (pestañas TTS / Transcribir / API)
├─ requirements.txt       # deps de la app (gradio, fastapi, uvicorn, psutil…)
├─ Dockerfile · docker-compose.yml · .env.example
├─ shared/
│  ├─ backends.py         # adaptadores TTS + capacidades
│  ├─ transcribe.py       # backend STT (Whisper / faster-whisper)
│  ├─ state.py            # descargas/habilitados + limpieza de audios
│  ├─ inference.py        # cola de concurrencia
│  └─ monitor.py          # monitor de CPU/RAM/almacenamiento (psutil)
├─ models/                # supertonic/ · pockettts/ · qwen3/ · whisper/
│  └─ <modelo>/           #   run.py · requirements.txt
├─ docs/API.md            # guía completa de la API
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
uv pip install -r requirements.txt                     # app (gradio + API)
uv pip install -r models/supertonic/requirements.txt   # Supertonic (TTS, sin torch)
uv pip install -r models/pockettts/requirements.txt    # Pocket-TTS (TTS, clonación)
uv pip install -r models/whisper/requirements.txt      # Whisper (STT, sin torch)
uv pip install -r models/qwen3/requirements.txt        # Qwen (TTS pesado, opcional)
#   (con pip estándar, reemplazá "uv pip" por "pip")
```

> Los modelos **no se descargan solos**: cada uno se baja cuando le das
> **Descargar** en el panel (Supertonic ~385 MB; Pocket ~100 MB; Qwen ~3 GB por
> modelo). Así no arrastrás un modelo pesado como Qwen si no lo vas a usar. Lo
> descargado queda cacheado (en Docker, en un volumen). La primera generación de
> Qwen en CPU puede tardar 1–2 minutos.

### GPU (opcional, recomendado si tenés NVIDIA)

Por defecto se instala PyTorch CPU (sirve en cualquier lado, incluido VPS). Si
tenés una GPU NVIDIA, instalá el build CUDA para acelerar **Pocket-TTS** (~3x más
rápido y habilita streaming en vivo). Con ~1 GB de VRAM alcanza:

```bash
uv pip install --reinstall torch torchaudio --index-url https://download.pytorch.org/whl/cu128
```

`Pocket-TTS` detecta la GPU y la usa solo. Qwen sigue en CPU (no entra en GPUs
chicas). El panel de Recursos muestra la VRAM cuando hay GPU.

## Uso

### Servidor (panel + API)

```bash
python server.py        # o: uvicorn server:app --host 0.0.0.0 --port 7860
```

Abrí `http://127.0.0.1:7860`. **Panel** en `/` y **API REST** en `/api/*`, mismo
proceso y puerto. En el panel: elegí modelo, **Descargá** el que quieras usar,
escribí texto y generá. El panel de recursos se actualiza en vivo.

> Correr `python app.py` también funciona, pero levanta **solo el panel** (sin
> API ni login). Para el servicio completo usá `server.py`.

### Seguridad (opcional, por variables de entorno)

Los secretos son **opcionales**, pensado para que cualquiera pueda clonar y correr
el panel sin configurar nada:

| Variable | Efecto |
|----------|--------|
| *(ninguna)* | Panel abierto, API apagada. |
| `PANEL_USER` + `PANEL_PASSWORD` | El panel pide login (usuario/clave). |
| `API_TOKEN` | Habilita la API y la protege con `Authorization: Bearer <token>`. |

Variables operativas (opcionales, con default):

| Variable | Default | Efecto |
|----------|---------|--------|
| `MODELBOX_MAX_CONCURRENT` | `1` | Inferencias en paralelo (en CPU, 1 ya usa todos los cores). |
| `MODELBOX_WHISPER_SIZE` | `small` | Tamaño de Whisper: `small`/`medium`/`large-v3`. |
| `MODELBOX_MAX_UPLOAD_MB` | `25` | Tope de tamaño para audios subidos por API. |

### API REST

Un modelo se puede consumir por API si está **descargado** y **habilitado** (el
toggle "Habilitar en la API" del panel). El audio se devuelve en la respuesta —
no se guarda en el servidor.

```bash
# Listar modelos y su estado
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:7860/api/models

# Generar audio (se descarga directo como WAV)
curl -X POST http://localhost:7860/api/tts \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Supertonic-3","text":"Hola mundo","voice":"F1","lang":"es"}' \
  --output salida.wav
```

`GET /api/health` queda abierto (sin token) para chequeos de estado. Docs
interactivas en `/api/docs`. **Guía completa de la API:** [docs/API.md](docs/API.md).

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

# Whisper — transcripción (STT)
python models/whisper/run.py --audio grabacion.mp3 --lang es
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

## Docker / Despliegue

La imagen corre en **CPU** (sirve en un VPS sin GPU).

> **No hace falta Docker Hub.** La imagen se construye en el lugar donde la vayas
> a correr (tu máquina o el VPS) a partir del `Dockerfile`. Nunca se publica en
> ningún registro a menos que hagas `docker push` a propósito.

**Librerías vs. pesos** (dos cosas distintas):

| | Dónde vive | Cuándo |
|---|---|---|
| **Librerías** (torch, transformers…) | dentro de la **imagen** | en el build (cacheadas: no se reinstalan si no cambian los requirements) |
| **Pesos** de los modelos (los GB de HF) | en un **volumen** | los descargás desde el panel; sobreviven a los redeploys |

### Elegir qué modelos incluir (imagen liviana)

El build arg `MODELS` decide qué modelos (y por ende qué librerías) entran en la
imagen. Solo se instala lo necesario — p. ej. **torch (~742 MB) solo si incluís
Pocket o Qwen**, y **ffmpeg (~450 MB) solo si incluís Qwen**:

```bash
# Imagen mínima, sin torch (~0.7 GB)
docker build --build-arg MODELS=supertonic -t modelbox .

# Supertonic + Pocket (agrega torch CPU, ~1.6 GB)
docker build --build-arg MODELS=supertonic,pocket -t modelbox .

# Los tres (default, ~2.3 GB)
docker build -t modelbox .
```

Con compose: `MODELS=supertonic,pocket docker compose up -d --build`. En Dokploy
se setea como **Build Arg**. El panel y la API muestran solo los modelos
incluidos en el build.

### Local (probar en tu máquina)

```bash
cp .env.example .env       # opcional: configurá credenciales (compose lo lee solo)
docker compose up -d --build
```

Abrí `http://localhost:7860`. Los audios quedan en `./outputs/`. Para apagar:
`docker compose down` (los modelos descargados sobreviven en los volúmenes).

> El `.env` es opcional: sin él, el panel queda abierto y la API apagada (los
> defaults del compose alcanzan para probar). `.env.example` lista todas las
> variables configurables.

### En un VPS con Dokploy

Dokploy construye la imagen en tu propio servidor desde el repo Git (privado o
público) — sin pasar por Docker Hub:

1. **Create Application** → conectá este repositorio.
2. **Build Type: `Dockerfile`** (no Nixpacks: este proyecto es Python + libs de
   sistema y necesita el `Dockerfile`).
3. **Build Args** (opcional) → `MODELS=supertonic,pocket` para una imagen sin
   Qwen/torch-pesado. Por defecto entran los tres.
4. **Port: `7860`** y asigná un dominio (Dokploy maneja el proxy y el SSL).
5. **Environment** → configurá las credenciales (ver tabla de Seguridad arriba):
   - `PANEL_USER`, `PANEL_PASSWORD` → login del panel.
   - `API_TOKEN` → token de la API.
6. **Volumes / Mounts** (importante, para no re-descargar varios GB en cada
   redeploy):
   - `/data/hf` → caché de Hugging Face (Pocket-TTS y Qwen).
   - `/data/state` → estado (modelos descargados / habilitados para la API).
   - `/app/models/supertonic/assets` → pesos de Supertonic-3.
   - `/app/outputs` → audios generados.
7. **Deploy.** En cada push, Dokploy reconstruye desde el repo.

> **RAM:** un VPS de 4 GB alcanza para Supertonic y Pocket-TTS. **Qwen3-TTS
> necesita ~9.6 GB residentes** — en un VPS chico, simplemente no lo selecciones
> en la interfaz (la carga es perezosa: si no lo elegís, no ocupa RAM).

### Sin Dokploy (build directo en el VPS)

```bash
git clone <URL-del-repo> && cd modelbox
docker compose up -d --build
```

Mismo resultado: la imagen se construye en el VPS y solo existe ahí.

## Agregar un modelo nuevo

1. Crear `models/<nombre>/` con su `run.py` y `requirements.txt`.
2. Agregar un `Backend` en `shared/backends.py` declarando sus `capabilities`
   y su método `synthesize`.
3. Registrarlo en el dict `BACKENDS`. La interfaz se adapta sola.

## Licencias

El **código** está bajo licencia MIT (ver `LICENSE`). Los **modelos** que se
descargan tienen licencias propias (Supertonic: OpenRAIL-M; Qwen: ver su repo
en Hugging Face) que debés respetar por separado.
