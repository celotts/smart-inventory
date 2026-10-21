/**
 * Base Agent - Clase padre para todos los agentes especializados.
 *
 * CADA AGENTE TIENE:
 * - systemPrompt: Instrucciones especificas de su dominio
 * - tools: Subconjunto de herramientas permitidas
 * - canHandoff: A que otros agentes puede delegar
 * - memory: Contexto persistente (se guarda en sessionStorage)
 */

import { OllamaClient, createOllamaClient } from './ollama.js';
import { api } from '../api.js';

export class BaseAgent {
  /**
   * @param {Object} config
   * @param {string} config.name - Identificador único: 'inventory', 'maintenance', 'purchasing', 'reporting'
   * @param {string} config.systemPrompt - Prompt de sistema especializado
   * @param {Array<string>} config.allowedTools - Nombres de tools permitidas (ver ToolRegistry)
   * @param {Array<string>} config.canHandoff - Nombres de agentes a los que puede delegar
   * @param {string} [config.model] - Modelo Ollama a usar
   */
  constructor({ name, systemPrompt, allowedTools, canHandoff, model }) {
    this.name = name;
    this.systemPrompt = systemPrompt;
    this.allowedTools = allowedTools || [];
    this.canHandoff = canHandoff || [];
    this.model = model;
    this.client = null;
    this.memory = this.#cargarMemoria();
  }

  async #inicializarCliente() {
    if (!this.client) {
      this.client = await createOllamaClient(this.model);
    }
    return this.client;
  }

  #cargarMemoria() {
    try {
      const raw = sessionStorage.getItem(`agent.memory.${this.name}`);
      return raw ? JSON.parse(raw) : { conversations: [], context: {}, lastActive: null };
    } catch {
      return { conversations: [], context: {}, lastActive: null };
    }
  }

  #guardarMemoria() {
    try {
      this.memory.lastActive = new Date().toISOString();
      sessionStorage.setItem(`agent.memory.${this.name}`, JSON.stringify(this.memory));
    } catch { /* ignore */ }
  }

  /**
   * Agrega mensaje a memoria de conversacion.
   */
  agregarMemoria(role, content, metadata = {}) {
    this.memory.conversations.push({
      role,
      content,
      timestamp: new Date().toISOString(),
      ...metadata,
    });
    // Mantener solo últimas 20 interacciones
    if (this.memory.conversations.length > 20) {
      this.memory.conversations = this.memory.conversations.slice(-20);
    }
    this.#guardarMemoria();
  }

  /**
   * Obtiene contexto relevante para la query actual.
   */
  obtenerContexto(query) {
    // Buscar en memoria conversaciones relevantes (últimas 5)
    const recent = this.memory.conversations.slice(-5);
    return recent.map(m => `${m.role}: ${m.content}`).join('\n');
  }

  /**
   * Ejecuta una tool permitida.
   * @param {string} toolName
   * @param {Object} args
   */
  async ejecutarTool(toolName, args) {
    if (!this.allowedTools.includes(toolName)) {
      throw new Error(`Tool ${toolName} no permitida para agente ${this.name}. Permitidas: ${this.allowedTools.join(', ')}`);
    }

    const tool = ToolRegistry.obtener(toolName);
    if (!tool) throw new Error(`Tool ${toolName} no registrada`);

    return tool.ejecutar(args);
  }

  /**
   * Procesa mensaje del usuario (o de otro agente via handoff).
   *
   * @param {string} userMessage - Mensaje entrante
   * @param {Object} [options]
   * @param {boolean} [options.stream] - Si true, usa streaming
   * @param {Function} [options.onToolCall] - Callback cuando se llama tool
   * @param {Function} [options.onHandoff] - Callback cuando delega a otro agente
   * @returns {Promise<Object>} { response, handoff?, toolCalls? }
   */
  async procesar(userMessage, options = {}) {
    const client = await this.#inicializarCliente();

    // Construir mensajes: system + contexto memoria + user
    const messages = [
      { role: 'system', content: this.systemPrompt },
      ...this.obtenerContexto(userMessage).split('\n').filter(Boolean).map((c, i) => ({
        role: i % 2 === 0 ? 'user' : 'assistant',
        content: c,
      })),
      { role: 'user', content: userMessage },
    ];

    // Filtrar tools permitidas
    const tools = this.allowedTools
      .map(name => ToolRegistry.obtener(name))
      .filter(Boolean)
      .map(t => t.definicion);

    // Agregar tool de handoff si puede delegar
    if (this.canHandoff.length) {
      tools.push(HandoffTool.definicion);
    }

    this.agregarMemoria('user', userMessage);

    if (options.stream) {
      return this.#procesarStream(messages, tools, options);
    }

    return this.#procesarCompleto(messages, tools, options);
  }

  async #procesarCompleto(messages, tools, options) {
    const client = await this.#inicializarCliente();

    let response = await client.chat(messages, tools);

    // Manejar tool calls en bucle (máx 5 iteraciones)
    for (let i = 0; i < 5 && response.tool_calls?.length; i++) {
      const toolResults = [];

      for (const call of response.tool_calls) {
        const { name, arguments: args } = call.function;
        try {
          if (options.onToolCall) await options.onToolCall(name, args);

          const result = await this.ejecutarTool(name, args);
          toolResults.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify(result),
          });
        } catch (error) {
          toolResults.push({
            role: 'tool',
            tool_call_id: call.id,
            content: JSON.stringify({ error: error.message }),
          });
        }
      }

      // Agregar resultados y continuar conversacion
      messages.push(response, ...toolResults);
      response = await client.chat(messages, tools);
    }

    // Respuesta final
    const finalContent = response.content || 'Sin respuesta';

    // Verificar handoff
    if (response.tool_calls?.some(c => c.function.name === 'handoff')) {
      const handoffCall = response.tool_calls.find(c => c.function.name === 'handoff');
      const { targetAgent, reason, context } = JSON.parse(handoffCall.function.arguments);
      return { response: finalContent, handoff: { targetAgent, reason, context }, toolCalls: response.tool_calls };
    }

    this.agregarMemoria('assistant', finalContent);
    return { response: finalContent, toolCalls: response.tool_calls };
  }

  async *#procesarStream(messages, tools, options) {
    const client = await this.#inicializarCliente();

    for await (const chunk of client.chatStream(messages, tools)) {
      yield chunk;

      // Si hay tool_calls en el chunk final, procesarlos
      if (chunk.tool_calls?.length) {
        // Acumular tool_calls completos...
        // (Simplificado: en producción necesitas buffer para chunks parciales)
      }
    }
  }

  /**
   * Limpia memoria del agente.
   */
  limpiarMemoria() {
    this.memory = { conversations: [], context: {}, lastActive: null };
    try { sessionStorage.removeItem(`agent.memory.${this.name}`); } catch {}
  }
}

/**
 * Registro central de tools disponibles.
 * Cada tool define: nombre, descripción, parámetros (JSON Schema), y función ejecutar().
 */
export class ToolRegistry {
  static #tools = new Map();

  static registrar(tool) {
    this.#tools.set(tool.nombre, tool);
  }

  static obtener(nombre) {
    return this.#tools.get(nombre);
  }

  static listar() {
    return Array.from(this.#tools.values());
  }

  static obtenerDefiniciones(nombres) {
    return nombres.map(n => this.#tools.get(n)?.definicion).filter(Boolean);
  }
}

/**
 * Tool base para type safety.
 */
export class Tool {
  constructor({ nombre, descripcion, parametros, ejecutar }) {
    this.nombre = nombre;
    this.descripcion = descripcion;
    this.parametros = parametros; // JSON Schema
    this.ejecutar = ejecutar; // async (args) => result

    this.definicion = {
      type: 'function',
      function: {
        name: nombre,
        description: descripcion,
        parameters: parametros,
      },
    };

    ToolRegistry.registrar(this);
  }
}

/**
 * Tool especial: Handoff a otro agente.
 * Permite delegar tareas a agentes especializados.
 */
export const HandoffTool = new Tool({
  nombre: 'handoff',
  descripcion: 'Delega la tarea a otro agente especializado. Usa cuando la query requiera expertise fuera de tu dominio.',
  parametros: {
    type: 'object',
    properties: {
      targetAgent: { type: 'string', enum: ['inventory', 'maintenance', 'purchasing', 'reporting'], description: 'Agente destino' },
      reason: { type: 'string', description: 'Por que delegar (ej: "requiere crear orden de compra")' },
      context: { type: 'object', description: 'Contexto relevante para el agente destino' },
    },
    required: ['targetAgent', 'reason'],
  },
  ejecutar: async ({ targetAgent, reason, context }) => {
    // El handoff se maneja en el orquestador, no aquí
    return { handoff: true, targetAgent, reason, context };
  },
});