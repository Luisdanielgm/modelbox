# Modelbox — imagen CPU para correr la interfaz multi-modelo TTS en un VPS sin GPU.
FROM python:3.11-slim

# Qué modelos incluir en la imagen (sus librerías se instalan según esto).
# Valores: combinación de supertonic,pocket,qwen,whisper,embeddings separada por comas.
#   - solo "supertonic"            -> imagen liviana, SIN torch
#   - "supertonic,whisper"         -> agrega STT (faster-whisper), SIN torch
#   - "supertonic,embeddings"      -> agrega embeddings (EmbeddingGemma ONNX), SIN torch
#   - "supertonic,pocket"          -> agrega torch (CPU)
#   - "...,qwen"                   -> agrega transformers
ARG MODELS="supertonic,pocket,qwen,whisper,embeddings"

# Dependencias de sistema: libsndfile (soundfile) y ffmpeg/ffprobe.
# Aunque Qwen es el caso mas obvio, Gradio/audio puede invocar ffprobe tambien
# al servir o procesar WAVs generados por Supertonic/Pocket.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Solo los requirements primero (mejor cache: no se reinstala si no cambian).
COPY requirements.txt ./
COPY models/supertonic/requirements.txt models/supertonic/
COPY models/pockettts/requirements.txt models/pockettts/
COPY models/qwen3/requirements.txt models/qwen3/
COPY models/whisper/requirements.txt models/whisper/
COPY models/embeddings/requirements.txt models/embeddings/

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
         pip install --no-cache-dir -r models/whisper/requirements.txt; fi \
    && if echo ",$MODELS," | grep -q ",embeddings,"; then \
         pip install --no-cache-dir -r models/embeddings/requirements.txt; fi

# Código de la app.
COPY . .

# MODELBOX_DATA_DIR: un unico volumen persistente. Adentro quedan HF cache,
# estado, pesos de Supertonic, audios y pesos gated de Pocket.
# MODELBOX_MODELS: el set elegido en build; la app oculta los modelos que no
# estan incluidos en esta imagen (sus librerias no se instalaron).
ENV MODELBOX_DATA_DIR=/modelbox-data \
    HF_HOME=/modelbox-data/hf \
    XDG_CACHE_HOME=/modelbox-data/cache \
    MODELBOX_MODELS=$MODELS

EXPOSE 7860

# uvicorn sirve la API FastAPI + el panel Gradio montado (server.py).
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
