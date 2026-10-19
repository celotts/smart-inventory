/**
 * Cliente Ollama para multi-agente.
 *
 * POR QUÉ OLLAMA LOCAL
 * --------------------
 * - Privacidad: los datos del inventario nunca salen de la red local
 * - Costo: $0 por request vs $0.01-0.03 en cloud
 * - Latencia: ~200-500ms en M3 vs 1-3s en API cloud
 * - Control: modelos especializados (codellama, llama3.1, mistral)
 *
 * TOOL CALLING CON OLLAMA
 * -----------------------
 * Ollama soporta function calling nativo desde v0.1.34+.
 * Formato: modelo recibe `tools` array, devuelve `tool_calls` en respuesta.
 * Nosotros ejecutamos la tool y devolvemos resultado como `tool` message.
 */

export class OllamaClient {
  #baseUrl;
  #model;
  #timeoutMs;

  constructor({ baseUrl = 'http://host.docker.internal:11434', model = 'llama3.1:8b', timeoutMs = 120000 } = {}) {
    this.#baseUrl = baseUrl.replace(/\/$/, '');
    this.#model = model;
    this.#timeoutMs = timeoutMs;
  }

  /**
   * Chat completion con soporte nativo de tool calling.
   *
   * @param {Array<Object>} messages - Historial: [{role, content, tool_calls?, tool_call_id?}]
   * @param {Array<Object>} tools - Definición OpenAI-compatible: [{type:'function', function:{name,description,parameters}}]
   * @param {Object} options - temperature, top_p, num_predict, etc.
   * @returns {Promise<Object>} { message: {role, content, tool_calls?}, done: boolean }
   */
  async chat(messages, tools = [], options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.#timeoutMs);

    try {
      const body = {
        model: this.#model,
        messages,
        tools: tools.length ? tools : undefined,
        stream: false,
        options: {
          temperature: 0.1,        // Determinístico para tool calling
          top_p: 0.9,
          num_predict: 2048,
          ...options,
        },
      };

      const response = await fetch(`${this.#baseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        const err = await response.text().catch(() => '');
        throw new Error(`Ollama ${response.status}: ${err}`);
      }

      const data = await response.json();
      return data.message; // { role: 'assistant', content: '', tool_calls?: [...] }
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Streaming chat - para UX progresiva.
   * Devuelve AsyncGenerator que yield cada chunk.
   */
  async *chatStream(messages, tools = [], options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), this.#timeoutMs);

    try {
      const response = await fetch(`${this.#baseUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model: this.#model,
          messages,
          tools: tools.length ? tools : undefined,
          stream: true,
          options: { temperature: 0.1, top_p: 0.9, num_predict: 2048, ...options },
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const err = await response.text().catch(() => '');
        throw new Error(`Ollama ${response.status}: ${err}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.trim()) continue;
          try {
            const chunk = JSON.parse(line);
            if (chunk.message) yield chunk.message;
            if (chunk.done) return;
          } catch {
            // Chunk incompleto, se acumula en buffer
          }
        }
      }
    } finally {
      clearTimeout(timeoutId);
    }
  }

  /**
   * Verifica disponibilidad del modelo.
   */
  async healthCheck() {
    try {
      const r = await fetch(`${this.#baseUrl}/api/tags`, { method: 'GET' });
      if (!r.ok) return { ok: false, error: `HTTP ${r.status}` };
      const data = await r.json();
      const hasModel = data.models?.some(m => m.name.startsWith(this.#model.split(':')[0]));
      return { ok: hasModel, model: this.#model, models: data.models?.map(m => m.name) || [] };
    } catch (e) {
      return { ok: false, error: e.message };
    }
  }

  /**
   * Lista modelos disponibles.
   */
  async listModels() {
    const r = await fetch(`${this.#baseUrl}/api/tags`);
    return r.json();
  }

  /**
   * Pull modelo si no existe.
   */
  async pullModel(name) {
    const r = await fetch(`${this.#baseUrl}/api/pull`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, stream: false }),
    });
    return r.ok;
  }
}

/**
 * Factory para crear cliente con modelo óptimo por tarea.
 */
export const OllamaModels = {
  // Modelo principal: balance velocidad/calidad
  DEFAULT: 'llama3.1:8b',

  // Para tool calling complejo (más parámetros)
  TOOLS: 'llama3.1:8b',        // o 'nemotron3:8b' si disponible

  // Para clasificación rápida (intent routing)
  CLASSIFIER: 'llama3.2:3b',   // Ultra rápido, 2GB

  // Para código SQL
  CODER: 'codellama:7b',       // Especializado en SQL

  // Para resúmenes largos
  LONG_CTX: 'llama3.1:8b-instruct-q4_K_M', // 8k context
};

/**
 * Crea cliente con fallback automático.
 */
export async function createOllamaClient(preferredModel = OllamaModels.DEFAULT) {
  const client = new OllamaClient({ model: preferredModel });
  const health = await client.healthCheck();

  if (!health.ok) {
    console.warn(`Ollama model ${preferredModel} no disponible:`, health.error);
    // Intentar pull
    await client.pullModel(preferredModel);
    const retry = await client.healthCheck();
    if (!retry.ok) throw new Error(`No se pudo cargar ${preferredModel}: ${retry.error}`);
  }

  return client;
}