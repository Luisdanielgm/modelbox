# Modelbox — Roadmap

Plan de evolución del servicio. Está organizado por horizonte (corto, medio y
largo plazo) y por eje técnico. El backlog de mejoras menores ya identificadas
está integrado al final de cada sección que corresponde.

Este documento es orientativo: define dirección y prioridades, no compromisos de
fecha.

## Estado actual

Modelbox es un servicio de inferencia self-hosted, CPU-only, con tres
modalidades:

- **Voz (TTS):** Supertonic-3 (ONNX), Pocket-TTS (PyTorch), Qwen3-TTS.
- **Transcripción (STT):** Whisper (faster-whisper, int8).
- **Embeddings (RAG):** EmbeddingGemma-300M (ONNX).

Características operativas vigentes:

- API REST con dos superficies: nativa `/api/*` y compatible con OpenAI `/v1/*`.
- Panel web (Gradio) con descarga manual de modelos, habilitación por modelo,
  e historial de uso unificado (panel + API).
- Autenticación por token Bearer único (`API_TOKEN`).
- Cola de inferencia en proceso (semáforo, `MODELBOX_MAX_CONCURRENT`).
- Volumen único persistente (`/modelbox-data`).
- Registro de uso en JSONL con rotación por tamaño y campo `surface`.
- CORS opcional por variable de entorno.
- Precio actual USD 0 (periodo de prueba); andamiaje de `pricing` ya presente.
- Despliegue con Dokploy en `inference.cauce.me`, detrás de Cloudflare.

## Principio rector: compatibilidad OpenAI y contrato con Cauce

Modelbox tiene clientes en producción que consumen la API (en particular,
**Cauce**). El objetivo número uno de todo el roadmap es que **Cauce siga
funcionando sin interrupción**. Las reglas siguientes son obligatorias.

### Superficie OpenAI que se mantiene

La API debe seguir siendo consumible con el **SDK de OpenAI** apuntando a
`/v1`. Las rutas compatibles que deben preservar su contrato son:

- `GET /v1/models`
- `POST /v1/audio/speech`
- `POST /v1/audio/transcriptions`
- `POST /v1/embeddings`
- `POST /v1/chat/completions` *(futuro, con el modelo LLM; ver pista opcional)*

Toda nueva capacidad expuesta por `/v1/*` debe respetar la forma de
solicitud/respuesta y de error de OpenAI, para que clientes y SDKs existentes no
requieran código especial.

### Compatibilidad hacia atrás

Cualquier cambio en `/api/*` o `/v1/*` debe ser **retrocompatible** salvo
decisión explícita y versionada. Las funcionalidades nuevas deben ser
**aditivas**. Los cambios de contrato requieren aviso previo y, de ser necesario,
versionado de la ruta. La verificación de regresiones es obligatoria antes de
desplegar (smoke de todas las rutas + revisión del Historial confirmando que
Cauce sigue en `200`).

### Reporte de impacto a Cauce (obligatorio)

Antes de desplegar, **cada cambio se clasifica por su impacto sobre la
comunicación que Cauce ya mantiene**:

- 🟢 **Transparente:** aditivo o interno. Cauce no nota nada y no debe adaptarse.
- 🟡 **Aditivo con preparación:** Cauce sigue funcionando, pero conviene que
  adopte algo (por ejemplo, manejar reintentos ante `429`, o adoptar un token
  propio). **Requiere aviso a Cauce.**
- 🔴 **Cambio de contrato o comercial:** requiere **coordinación previa con
  Cauce** antes de desplegar (por ejemplo, activar precios distintos de cero).

Para todo cambio 🟡 o 🔴 se emite una **Nota para integradores (Cauce)** antes del
despliegue, y se registra en un `CHANGELOG`. Si un cambio degrada o altera la
comunicación actual, se comunica explícitamente y no se despliega sin acuerdo.

### Matriz de impacto del roadmap

| Cambio planificado | Impacto en Cauce | Nota |
|---|:--:|---|
| Detección de descarga por modelo (S1) | 🟢 | Interno |
| Rate-limit por token (S1) | 🟡 | Introduce `429`; fijar el límite por encima del uso real de Cauce y avisar para que maneje reintentos/backoff |
| `response_format` mp3 (S1) | 🟢 | `wav` sigue siendo el default |
| Limpiezas internas (S1) | 🟢 | Interno |
| Identidad de cliente / multi-token (S2) | 🟢 | El `API_TOKEN` único actual sigue válido; darle token propio a Cauce sería 🟡 (coordinado) |
| Trabajos asíncronos (S3) | 🟢 | Endpoints nuevos; la vía síncrona queda intacta |
| Probes, métricas, warm-up (S4) | 🟢 | Interno |
| Cuotas por cliente (S5) | 🟡 | `429` al exceder; coordinar la cuota de Cauce |
| Facturación / precios (S5) | 🔴 | Al dejar de ser USD 0 es un cambio comercial: coordinación previa |
| Retención / evicción de modelos (S6) | 🟢 | Respeta los modelos habilitados; avisar las políticas |
| Escala horizontal (S7) | 🟢 | Debe ser transparente; riesgo operativo (no de contrato): verificación obligatoria |
| Multi-tenant / aislamiento (S8) | 🟡 | Límites por cliente; coordinar con Cauce |
| Modelo LLM desactivado (pista) | 🟢 | Aditivo; al activarlo agrega `/v1/chat/completions`, sin afectar lo existente |
| Interfaz propia vs Gradio (pista) | 🟢 | Solo afecta el panel; **no toca la API**, Cauce no se entera |

### Documentación

Todo cambio actualiza la documentación que corresponda, en el mismo entregable:

- Pública / panel: la pestaña **API** del panel (`app.py`, `_api_md`).
- API: `docs/API.md`, `docs/OPENAI_COMPATIBILITY.md`, `docs/AGENT_INTEGRATION.md`
  (esta última se sirve en `/api/agent-guide`).
- Repositorio: `README.md`, este `ROADMAP.md` y el `CHANGELOG`.

---

## Corto plazo — robustez y limpieza

Cambios de bajo riesgo y alcance acotado. No alteran el contrato de la API.

- **Detección de descarga por modelo.** `is_downloaded()` evalúa el tamaño de
  todo `HF_HOME`, que es compartido entre modelos; un marcador obsoleto más otro
  modelo presente puede reportar un falso "descargado". Cambiar a una
  comprobación específica por modelo (Whisper, Qwen, EmbeddingGemma).
- **Rate-limit por token.** Hoy solo existe la cola de concurrencia global; un
  cliente puede encolar sin tope. Agregar un límite de solicitudes por token y
  ventana de tiempo.
- **`response_format` real en TTS.** Actualmente se acepta el campo pero la
  respuesta siempre es `audio/wav`. Soportar al menos `mp3` para reducir tamaño
  de transferencia.
- **Limpieza interna.** Eliminar `read_calls()` (sin uso) y ajustar el `Timer`
  del panel de 1.5s a 3–5s para reducir carga de polling por cliente conectado.

---

## Medio plazo — confiabilidad y observabilidad

Cambios que mejoran la operación bajo carga real y la visibilidad del servicio.

- **Identidad de cliente en el registro de uso.** Con un único `API_TOKEN` no se
  distingue *quién* llama. Introducir múltiples tokens (uno por cliente) y
  registrar el cliente en cada llamada, como base para cuotas y facturación.
- **Trabajos asíncronos para solicitudes largas.** El timeout de Cloudflare
  (~100s) es un techo real para TTS o transcripciones extensas. Definir un patrón
  de job asíncrono (encolar, devolver un identificador, consultar estado o
  notificar por webhook) en lugar de una solicitud bloqueante.
- **Probes de salud y readiness.** Separar liveness de readiness; exponer si los
  modelos habilitados están cargados y listos. Útil para balanceadores y
  reinicios sin pérdida de tráfico.
- **Métricas.** Complementar el log de uso con métricas agregadas (latencia p95,
  tasa de error, profundidad de cola) en un formato consumible por herramientas
  de monitoreo.
- **Warm-up y gestión de memoria.** Pre-cargar modelos habilitados al iniciar y
  endurecer el manejo de condiciones de memoria insuficiente en CPU.

---

## Largo plazo — escala y negocio

Cambios estructurales. Requieren diseño previo porque tocan estado, despliegue y
modelo de negocio.

- **Escala horizontal.** La cola actual es en proceso: serializa correctamente en
  una réplica, pero no coordina entre varias. Escalar a múltiples réplicas exige:
  estado compartido (modelos habilitados, marcadores de descarga), caché de
  modelos compartida o replicada, y una cola distribuida en lugar del semáforo
  local.
- **Facturación real.** El objeto `pricing` ya existe con valores en cero. Cuando
  termine el periodo de prueba, conectar el registro de uso a un esquema de
  precios por llamada, por carácter o por minuto de audio, con resúmenes por
  cliente.
- **Multi-tenant.** Sobre la identidad de cliente del medio plazo: cuotas,
  límites y aislamiento por cliente, con panel de administración.
- **Gestión del crecimiento de almacenamiento.** El volumen único acumula cachés
  de modelos y salidas; definir políticas de retención y, si hace falta, evicción
  de modelos poco usados.

---

## Evaluación: interfaz propia vs Gradio

Se evaluó reemplazar el panel Gradio por una interfaz propia más adelante.

**Punto clave de contrato:** el panel **no forma parte de la API**. Cauce y
cualquier cliente consumen `/api/*` y `/v1/*`; el panel es una herramienta de
administración y demostración montada aparte. Por lo tanto, **migrar la interfaz
no afecta en nada a Cauce** (impacto 🟢): se puede hacer o posponer sin riesgo
para el cliente.

**A favor de una interfaz propia:**

- Control total de UX, marca y textos.
- Posibilidad de una consola para clientes (consumo, cuotas, facturación), que
  encaja con el medio y largo plazo (multi-token, cuotas, facturación).
- Funcionalidades que Gradio no cubre cómodamente.

**En contra:**

- Costo real: stack frontend, autenticación propia, build y hosting, y
  mantenimiento continuo.
- Gradio ya cubre bien el caso actual (administración interna: descargar,
  habilitar, probar, ver historial).
- No aporta nada a la API ni al cliente; es esfuerzo que no mueve el contrato.

**Recomendación:** mantenerlo como **pista opcional, fuera de la ruta crítica**.
No migrar por ahora. Reconsiderar solo si aparece una necesidad concreta —
principalmente, exponer una **consola para clientes** una vez que existan
multi-token, cuotas y facturación (Semanas 5 y 8). En ese momento conviene
construir la interfaz propia *encima* de esas APIs, no antes. La API
OpenAI-compatible se mantiene igual en cualquier escenario.

## Pista opcional: modelo LLM (desactivado por defecto)

Se prevé incorporar una cuarta modalidad: un **LLM de texto**, expuesto de forma
**compatible con el SDK de OpenAI** vía `POST /v1/chat/completions` (y,
opcionalmente, `/v1/completions`). Esto extiende la superficie OpenAI sin alterar
las rutas existentes (impacto 🟢, aditivo).

**Política de activación:** igual que Qwen3-TTS hoy — **incluido pero
desactivado**. No se auto-descarga, se habilita manualmente desde el panel y solo
queda disponible en la API cuando se marca como habilitado. Mientras esté
desactivado, no cambia nada para Cauce.

**Restricción de hardware (a tener presente):** en CPU, un LLM de **1–2 mil
millones de parámetros (1–2B)** es viable solo **cuantizado** (por ejemplo, GGUF
con `llama.cpp`) y aun así con latencia alta. Un modelo de ese tamaño, o mayor,
probablemente requiera **más RAM y un servidor más grande**. Por eso esta pista
queda **condicionada a la capacidad del servidor**: se implementa el soporte, se
deja desactivado, y se activa cuando el hardware lo permita.

Checklist para cuando se decida implementarlo (modelo desactivado por defecto):

- **Día 1 — Selección de modelo y runtime**
  - [ ] Elegir modelo 1–2B y runtime de CPU cuantizado (`llama.cpp`/GGUF; sin torch si es posible)
  - [ ] Verificar: inferencia local de prueba con latencia aceptable
- **Día 2 — Backend y registro**
  - [ ] Implementar el backend del LLM y registrarlo filtrado por `MODELBOX_MODELS`, **desactivado por defecto**
  - [ ] Verificar: aparece en el panel, no auto-descarga, deshabilitado en API
- **Día 3 — Endpoint compatible OpenAI**
  - [ ] Implementar `POST /v1/chat/completions` con la forma de solicitud/respuesta de OpenAI (sin streaming primero)
  - [ ] Verificar: el **SDK de OpenAI** apuntando a `/v1` recibe una respuesta válida
- **Día 4 — Streaming y límites**
  - [ ] Streaming SSE opcional + límites (`max_tokens`, contexto) por `shared/limits.py`
  - [ ] Verificar: streaming y límites funcionan; errores con shape OpenAI
- **Día 5 — Documentación y verificación**
  - [ ] Actualizar `docs/API.md`, `docs/OPENAI_COMPATIBILITY.md`, panel y `README.md`
  - [ ] Verificar que las rutas existentes no cambiaron y que Cauce sigue en `200`

## Plan de ejecución por semanas

Plan estimado a 8 semanas, asumiendo 5 días hábiles por semana y un desarrollador
a tiempo parcial. Cada día incluye su verificación. Toda semana cierra con
regresión y, cuando corresponde, despliegue con verificación de que el cliente en
producción (Cauce) sigue respondiendo `200`.

Las estimaciones son orientativas; el orden respeta dependencias (la identidad de
cliente habilita cuotas y facturación; el diseño de estado compartido precede a
la escala horizontal).

### Semana 1 — Corto plazo: robustez y limpieza

Objetivo: cerrar el backlog de bajo riesgo sin tocar el contrato de la API.

**Impacto en Cauce:** 🟢 mayormente transparente. El rate-limit (Día 2) es 🟡:
introduce `429`; fijar el límite por encima del uso real de Cauce y avisarle.

- **Día 1 — Detección de descarga por modelo**
  - [ ] Reemplazar la medición de todo `HF_HOME` por una comprobación específica por modelo en `shared/embeddings.py`, `shared/transcribe.py` y backends
  - [ ] Verificar: marcador presente + caché de otro modelo no produce falso "descargado"
- **Día 2 — Rate-limit por token**
  - [ ] Definir política configurable por entorno (solicitudes por ventana)
  - [ ] Implementar la dependencia en `server.py` (en memoria, por token)
  - [ ] Devolver `429` con shape correcto (nativo y OpenAI)
  - [ ] Verificar: una ráfaga que supera el límite recibe `429`
- **Día 3 — `response_format` real en TTS**
  - [ ] Soportar `mp3` además de `wav` en `/api/tts` y `/v1/audio/speech`
  - [ ] Mantener `wav` como default (retrocompatibilidad)
  - [ ] Verificar: pedir `mp3` devuelve `audio/mpeg`; omitirlo devuelve `wav`
- **Día 4 — Limpiezas internas**
  - [ ] Eliminar `read_calls()` sin uso en `shared/usage.py`
  - [ ] Ajustar el `Timer` del panel de 1.5s a 3–5s en `app.py`
  - [ ] Revisar imports y código huérfano
- **Día 5 — Integración y despliegue**
  - [ ] Smoke completo de `/api/*` y `/v1/*`
  - [ ] Commit convencional + push a `main` + redeploy en Dokploy
  - [ ] Verificar en el Historial que las llamadas de Cauce siguen en `200`

### Semana 2 — Identidad de cliente (multi-token)

Objetivo: distinguir quién llama, como base para cuotas y facturación.

**Impacto en Cauce:** 🟢 transparente. El `API_TOKEN` único sigue válido; darle
un token propio a Cauce sería 🟡 y se coordina.

- **Día 1 — Diseño**
  - [ ] Definir el modelo de tokens múltiples (mapa token → cliente) y su almacenamiento en el volumen
  - [ ] Definir compatibilidad: el `API_TOKEN` único actual sigue válido
- **Día 2 — Almacenamiento y carga de tokens**
  - [ ] Implementar carga de tokens desde estado/entorno
  - [ ] Verificar: token válido autentica; token desconocido recibe `401`
- **Día 3 — Identidad en autenticación**
  - [ ] Resolver el cliente en la dependencia de auth y propagarlo a la llamada
  - [ ] Verificar: el cliente resuelto es correcto por token
- **Día 4 — Registro por cliente**
  - [ ] Agregar el campo `client` al registro de uso (aditivo)
  - [ ] Exponer filtro por cliente en `/api/usage` y en el Historial del panel
  - [ ] Verificar: las llamadas quedan atribuidas al cliente correcto
- **Día 5 — Regresión y despliegue**
  - [ ] Confirmar retrocompatibilidad con el token único
  - [ ] Push + redeploy + verificación en producción

### Semana 3 — Trabajos asíncronos para solicitudes largas

Objetivo: superar el techo de ~100s de Cloudflare en TTS/STT extensos.

**Impacto en Cauce:** 🟢 transparente. Endpoints nuevos; la vía síncrona actual
queda intacta.

- **Día 1 — Diseño del patrón de jobs**
  - [ ] Definir contrato: encolar → `job_id` → consultar estado → recoger resultado
  - [ ] Decidir almacenamiento de jobs y expiración de resultados
- **Día 2 — Encolado y estado**
  - [ ] Implementar `POST` de encolado y `GET` de estado
  - [ ] Verificar: un job pasa por `queued` → `running` → `done`
- **Día 3 — Ejecución y recolección**
  - [ ] Ejecutar el job sobre la cola de inferencia existente
  - [ ] Implementar la recolección del resultado (audio/texto) y su expiración
  - [ ] Verificar: el resultado se recupera una vez y luego expira
- **Día 4 — Errores y límites**
  - [ ] Manejar fallos del job y reflejarlos en el estado
  - [ ] Aplicar los límites de servicio también a la vía asíncrona
  - [ ] Verificar: un job fallido reporta error consultable
- **Día 5 — Documentación y despliegue**
  - [ ] Documentar el patrón asíncrono en `docs/API.md`
  - [ ] Push + redeploy + verificación; la vía síncrona sigue intacta

### Semana 4 — Observabilidad y confiabilidad

Objetivo: visibilidad bajo carga real y arranques sin pérdida de tráfico.

**Impacto en Cauce:** 🟢 transparente. Todo interno (probes, métricas, warm-up).

- **Día 1 — Liveness y readiness**
  - [ ] Separar liveness de readiness; readiness refleja modelos cargados
  - [ ] Verificar: readiness es negativo hasta que los modelos habilitados cargan
- **Día 2 — Warm-up de modelos**
  - [ ] Pre-cargar modelos habilitados al iniciar (configurable)
  - [ ] Verificar: la primera solicitud no paga el costo de carga
- **Día 3 — Métricas agregadas**
  - [ ] Exponer latencia p95, tasa de error y profundidad de cola
  - [ ] Verificar: las métricas reflejan tráfico de prueba
- **Día 4 — Manejo de memoria**
  - [ ] Endurecer el comportamiento ante memoria insuficiente en CPU
  - [ ] Verificar: una condición de OOM degrada con mensaje claro, sin tumbar el proceso
- **Día 5 — Regresión y despliegue**
  - [ ] Suite completa + push + redeploy + verificación

### Semana 5 — Cuotas y facturación base

Objetivo: convertir el uso en cuotas y precios por cliente (depende de la Semana 2).

**Impacto en Cauce:** 🟡/🔴. Las cuotas introducen `429` (coordinar la cuota de
Cauce). Activar precios distintos de cero es 🔴: cambio comercial con
coordinación previa obligatoria.

- **Día 1 — Diseño de cuotas**
  - [ ] Definir cuotas por cliente (por periodo) y su almacenamiento
- **Día 2 — Aplicación de cuotas**
  - [ ] Aplicar la cuota en la vía de solicitud; `429` al excederla
  - [ ] Verificar: superar la cuota bloquea; reiniciar el periodo la restablece
- **Día 3 — Esquema de precios**
  - [ ] Conectar el registro de uso con `pricing` (por llamada / carácter / minuto)
  - [ ] Verificar: un conjunto de llamadas produce el costo esperado
- **Día 4 — Resúmenes por cliente**
  - [ ] Exponer resumen de consumo y costo por cliente
  - [ ] Verificar: el resumen coincide con el registro
- **Día 5 — Regresión y despliegue**
  - [ ] Confirmar que con precio 0 el comportamiento es idéntico al actual
  - [ ] Push + redeploy + verificación

### Semana 6 — Almacenamiento y diseño de escala

Objetivo: controlar el crecimiento del volumen y diseñar la escala horizontal.

**Impacto en Cauce:** 🟢 transparente. La evicción respeta los modelos
habilitados; se avisan las políticas de retención.

- **Día 1 — Políticas de retención**
  - [ ] Definir retención de salidas y cachés de modelos en el volumen único
- **Día 2 — Evicción de modelos**
  - [ ] Implementar evicción opcional de modelos poco usados
  - [ ] Verificar: la evicción respeta los modelos habilitados
- **Día 3 — Diseño de estado compartido**
  - [ ] Diseñar cómo compartir modelos habilitados y marcadores entre réplicas
- **Día 4 — Diseño de cola distribuida**
  - [ ] Diseñar el reemplazo del semáforo en proceso por una cola coordinada
- **Día 5 — Documento de diseño**
  - [ ] Consolidar el diseño de escala horizontal y revisarlo antes de implementar

### Semana 7 — Escala horizontal: implementación

Objetivo: operar con múltiples réplicas de forma coordinada.

**Impacto en Cauce:** 🟢 debe ser transparente. Riesgo operativo (no de
contrato): la verificación de no-regresión es obligatoria antes de exponer.

- **Día 1 — Estado compartido**
  - [ ] Implementar el backend de estado compartido (habilitación, marcadores)
  - [ ] Verificar: dos réplicas ven el mismo estado
- **Día 2 — Caché de modelos compartida o replicada**
  - [ ] Resolver el acceso a modelos desde varias réplicas
  - [ ] Verificar: una réplica nueva sirve sin re-descargar innecesariamente
- **Día 3 — Cola coordinada**
  - [ ] Implementar la cola distribuida que reemplaza al semáforo local
  - [ ] Verificar: la concurrencia global se respeta entre réplicas
- **Día 4 — Despliegue multi-réplica**
  - [ ] Configurar el despliegue con más de una réplica detrás del balanceador
  - [ ] Verificar: el tráfico se reparte y la cola se mantiene coherente
- **Día 5 — Pruebas de carga**
  - [ ] Ejecutar pruebas de carga y ajustar concurrencia
  - [ ] Verificar: throughput escala y no hay corrupción de estado

### Semana 8 — Multi-tenant y cierre

Objetivo: aislamiento por cliente y consolidación.

**Impacto en Cauce:** 🟡. El aislamiento y los límites por cliente se coordinan
con Cauce antes de aplicarlos.

- **Día 1 — Aislamiento por cliente**
  - [ ] Aplicar límites y aislamiento por cliente sobre la identidad existente
- **Día 2 — Panel de administración**
  - [ ] Exponer administración de tokens, cuotas y consumo por cliente
  - [ ] Verificar: alta/baja de token y ajuste de cuota funcionan
- **Día 3 — Facturación final**
  - [ ] Finalizar el flujo de facturación y exportación de consumo
  - [ ] Verificar: el reporte de facturación es correcto por cliente
- **Día 4 — Endurecimiento**
  - [ ] Revisión de seguridad y de límites en todas las vías
- **Día 5 — Cierre**
  - [ ] Regresión completa + despliegue + verificación
  - [ ] Actualizar este roadmap con lo aprendido y el siguiente ciclo

---

## Referencias de código

| Área | Archivo |
|---|---|
| Rutas `/api/*` y `/v1/*`, auth, CORS, validaciones | `server.py` |
| Cola de inferencia (semáforo) | `shared/inference.py` |
| Registro de uso (JSONL, rotación, `surface`) | `shared/usage.py` |
| Estado persistente (descargas, habilitación) | `shared/state.py` |
| Límites de servicio | `shared/limits.py` |
| Backends TTS / STT / Embeddings | `shared/backends.py`, `shared/transcribe.py`, `shared/embeddings.py` |
| Panel web e historial | `app.py` |
| Referencia de API | `docs/API.md`, `docs/OPENAI_COMPATIBILITY.md`, `docs/AGENT_INTEGRATION.md` |
