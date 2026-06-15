# Modelbox — imagen CPU para correr la interfaz multi-modelo TTS en un VPS sin GPU.
FROM python:3.11-slim

# Qué modelos incluir en la imagen (sus librerías se instalan según esto).
# Valores: combinación de supertonic,pocket,qwen,whisper separada por comas.
#   - solo "supertonic"        -> imagen liviana, SIN torch
#   - "supertonic,whisper"     -> agrega STT (faster-whisper), SIN torch
#   - "supertonic,pocket"      -> agrega torch (CPU)
#   - "...,qwen"               -> agrega transformers + ffmpeg
ARG MODELS="supertonic,pocket,qwen,whisper"

# Dependencias de sistema: libsndfile (soundfile) siempre; ffmpeg SOLO si se
# incluye un modelo que clona desde audio mp3/ogg (qwen). ffmpeg pesa ~450 MB.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 \
    && if echo ",$MODELS," | grep -q ",qwen,"; then \
         apt-get install -y --no-install-recommends ffmpeg; \
       fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Solo los requirements primero (mejor cache: no se reinstala si no cambian).
COPY requirements.txt ./
COPY models/supertonic/requirements.txt models/supertonic/
COPY models/pockettts/requirements.txt models/pockettts/
COPY models/qwen3/requirements.txt models/qwen3/
COPY models/whisper/requirements.txt models/whisper/

# PyTorch CPU (desde el índice CPU, evita las libs CUDA ~2 GB) SOLO si se
# incluye un modelo que lo usa (pocket o qwen). Luego las deps de la app y de
# cada modelo seleccionado. torch ya queda satisfecho para qwen3/requirements.
RUN if echo ",$MODELS," | grep -qE ",(pocket|qwen),"; then \
        pip install --no-cache-dir torch==2.10.0 torchaudio==2.10.0 \
            --index-url https://download.pytorch.org/whl/cpu; \
    fi \
    && pip install --no-cache-dir -r requirements.txt \
    && if echo ",$MODELS," | grep -q ",supertonic,"; then \
         pip install --no-cache-dir -r models/supertonic/requirements.txt; fi \
    && if echo ",$MODELS," | grep -q ",pocket,"; then \
         pip install --no-cache-dir -r models/pockettts/requirements.txt; fi \
    && if echo ",$MODELS," | grep -q ",qwen,"; then \
         pip install --no-cache-dir -r models/qwen3/requirements.txt; fi \
    && if echo ",$MODELS," | grep -q ",whisper,"; then \
         pip install --no-cache-dir -r models/whisper/requirements.txt; fi

# Código de la app.
COPY . .

# HF_HOME: caché de modelos (volumen). MODELBOX_STATE_DIR: estado persistente.
# MODELBOX_MODELS: el set elegido en build; la app oculta los modelos que no
# están incluidos en esta imagen (sus librerías no se instalaron).
ENV HF_HOME=/data/hf \
    MODELBOX_STATE_DIR=/data/state \
    MODELBOX_MODELS=$MODELS

EXPOSE 7860

# uvicorn sirve la API FastAPI + el panel Gradio montado (server.py).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
