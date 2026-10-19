/**
 * Orquestador Multi-Agente - Router + Memoria Compartida + Handoffs
 *
 * FLUJO:
 * 1. Usuario hace query
 * 2. Router clasifica intención → selecciona agente inicial
 * 3. Agente procesa (puede llamar tools, hacer handoffs)
 * 4. Si handoff → orquestador invoca agente destino con contexto
 * 5. Respuesta final al usuario
 * 6. Todo se guarda en memoria compartida
 */

import { Agents, AgentNames } from './agents.js';
import { createOllamaClient } from './ollama.js';
import { api } from '../api.js';

export class AgentOrchestrator {
  constructor() {
    this.agents = {};
    this.classifier = null;
    this.sharedMemory = this.#cargarMemoriaCompartida();
    this.handoffHistory = [];
  }

  async inicializar() {
    // Instanciar agentes
    for (const [name, factory] of Object.entries(Agents)) {
      this.agents[name] = factory();
    }

    // Clasificador rápido (modelo pequeño) para routing inicial
    this.classifier = await createOllamaClient('llama3.2:3b');
    console.log('[Orchestrator] Agentes listos:', Object.keys(this.agents).join(', '));
  }

  #cargarMemoriaCompartida() {
    try {
      const raw = sessionStorage.getItem('orchestrator.sharedMemory');
      return raw ? JSON.parse(raw) : {
        userContext: {},
        activeEntities: {}, // device_id, po_id, maintenance_id mencionados recientemente
        lastQuery: null,
        queryCount: 0,
      };
    } catch {
      return { userContext: {}, activeEntities: {}, lastQuery: null, queryCount: 0 };
    }
  }

  #guardarMemoriaCompartida() {
    try {
      sessionStorage.setItem('orchestrator.sharedMemory', JSON.stringify(this.sharedMemory));
    } catch {}
  }

  /**
   * Clasifica intención y selecciona agente inicial.
   * Usa modelo rápido (3B) para latency <200ms.
   */
  async clasificarIntencion(query) {
    const systemPrompt = `Eres un clasificador de intención para sistema de inventario.
Devuelve SOLO JSON válido con:
{
  "agent": "inventory|maintenance|purchasing|reporting",
  "confidence": 0.0-1.0,
  "reason": "breve explicación",
  "entities": { "device_id": 123, "po_id": 456, "model_id": 789 }
}

REGLAS:
- inventory: stock, dispositivos, ubicaciones, escaneos, movimientos, historial
- maintenance: mantenimientos pendientes/vencidos, planes, checklists, técnicos, completar
- purchasing: órdenes de compra, proveedores, recepción, sugerir reorden, stock bajo → PO
- reporting: reportes ejecutivos, KPIs, estadísticas, tendencias, rotación, tiempo instalación

EJEMPLOS:
"Cuánto stock de Latitude 5440?" → inventory
"Mantenimientos vencidos esta semana" → maintenance
"Crear PO para 10 switches" → purchasing
"Reporte mensual de rotación" → reporting
"Historial del SN-DEMO-001" → inventory`;

    try {
      const response = await this.classifier.chat([
        { role: 'system', content: systemPrompt },
        { role: 'user', content: query },
      ], [], { temperature: 0.0, num_predict: 200 });

      const jsonMatch = response.content.match(/\{[\s\S]*\}/);
      if (jsonMatch) {
        const parsed = JSON.parse(jsonMatch[0]);
        if (AgentNames.includes(parsed.agent)) {
          return parsed;
        }
      }
    } catch (e) {
      console.warn('[Orchestrator] Error clasificando, fallback a inventory:', e.message);
    }

    // Fallback heurístico simple
    const q = query.toLowerCase();
    if (q.includes('mantenimiento') || q.includes('vencido') || q.includes('checklist') || q.includes('técnico')) return { agent: 'maintenance', confidence: 0.8, reason: 'keywords mantenimiento' };
    if (q.includes('orden') || q.includes('compra') || q.includes('proveedor') || q.includes('reorden') || q.includes('po')) return { agent: 'purchasing', confidence: 0.8, reason: 'keywords compras' };
    if (q.includes('reporte') || q.includes('estadística') || q.includes('kpi') || q.includes('rotación') || q.includes('dashboard')) return { agent: 'reporting', confidence: 0.8, reason: 'keywords reportes' };
    return { agent: 'inventory', confidence: 0.7, reason: 'default' };
  }

  /**
   * Procesa query completa con soporte multi-agente.
   */
  async procesar(query, options = {}) {
    const startTime = Date.now();
    this.sharedMemory.queryCount++;
    this.sharedMemory.lastQuery = query;

    // Extraer entidades mencionadas (device_id, po_id, etc.)
    this.#extraerEntidades(query);

    // Clasificar
    const clasificacion = await this.clasificarIntencion(query);
    const agenteInicial = this.agents[clasificacion.agent];

    if (!agenteInicial) {
      throw new Error(`Agente ${clasificacion.agent} no existe`);
    }

    console.log(`[Orchestrator] Query: "${query}" → ${clasificacion.agent} (${(clasificacion.confidence * 100).toFixed(0)}%)`);

    // Ejecutar con soporte de handoffs
    const resultado = await this.#ejecutarConHandoffs(agenteInicial, query, {
      ...options,
      clasificacionInicial: clasificacion,
      startTime,
    });

    // Guardar en memoria compartida
    this.#guardarInteraccion(query, resultado, clasificacion);

    return {
      ...resultado,
      metadata: {
        agenteInicial: clasificacion.agent,
        handoffs: this.handoffHistory.slice(-1), // último handoff
        duracionMs: Date.now() - startTime,
        timestamp: new Date().toISOString(),
      },
    };
  }

  async #ejecutarConHandoffs(agente, query, options) {
    this.handoffHistory = [];
    let agenteActual = agente;
    let queryActual = query;
    let contextoHandoff = { ...this.sharedMemory.activeEntities };

    for (let paso = 0; paso < 3; paso++) { // Máx 3 handoffs (evitar bucles)
      const resultado = await agenteActual.procesar(queryActual, {
        ...options,
        onHandoff: (targetAgent, reason, context) => {
          this.handoffHistory.push({
            from: agenteActual.name,
            to: targetAgent,
            reason,
            context,
            timestamp: new Date().toISOString(),
          });
        },
      });

      // Verificar handoff
      if (resultado.handoff) {
        const { targetAgent, reason, context } = resultado.handoff;
        const siguienteAgente = this.agents[targetAgent];

        if (!siguienteAgente) {
          console.warn(`[Orchestrator] Handoff a agente inexistente: ${targetAgent}`);
          return { ...resultado, response: resultado.response + `\n\n⚠️ No pude delegar a ${targetAgent}.` };
        }

        console.log(`[Orchestrator] Handoff: ${agenteActual.name} → ${targetAgent} (${reason})`);

        // Combinar contexto
        contextoHandoff = { ...contextoHandoff, ...context, previousResponse: resultado.response };

        // Preparar query para siguiente agente
        queryActual = `CONTEXTO PREVIO: ${resultado.response}\n\nTAREA DELEGADA: ${reason}\nCONTEXTO: ${JSON.stringify(context)}`;
        agenteActual = siguienteAgente;
        continue;
      }

      // Sin handoff → respuesta final
      return {
        response: resultado.response,
        agent: agenteActual.name,
        toolCalls: resultado.toolCalls,
        handoffs: this.handoffHistory,
      };
    }

    // Límite de handoffs alcanzado
    return {
      response: agenteActual.name === 'inventory'
        ? 'He llegado al límite de delegaciones. ¿Podrías ser más específico en qué necesitas?'
        : 'Procesamiento completado tras múltiples delegaciones.',
      agent: agenteActual.name,
      handoffs: this.handoffHistory,
    };
  }

  #extraerEntidades(query) {
    // Patrones comunes: SN-XXXX, INV-XXXX, PO-123, #123
    const patrones = {
      device_id: /(?:SN|INV|device|dispositivo|equipo)[\s\-:]*([A-Z0-9\-]{5,})/gi,
      po_id: /(?:PO|orden|purchase)[\s\-:#]*(\d+)/gi,
      model_id: /(?:modelo|model|SKU)[\s\-:]*([A-Z0-9\-]{3,})/gi,
    };

    for (const [tipo, regex] of Object.entries(patrones)) {
      let match;
      while ((match = regex.exec(query)) !== null) {
        this.sharedMemory.activeEntities[tipo] = match[1];
      }
    }
  }

  #guardarInteraccion(query, resultado, clasificacion) {
    this.sharedMemory.activeEntities = {
      ...this.sharedMemory.activeEntities,
      lastAgent: resultado.agent,
      lastQuery: query,
    };
    this.#guardarMemoriaCompartida();
  }

  /**
   * Obtiene estado del orquestador para debugging.
   */
  getEstado() {
    return {
      agentes: Object.keys(this.agents),
      memoria: this.sharedMemory,
      historialHandoffs: this.handoffHistory,
    };
  }

  /**
   * Limpia todo.
   */
  limpiar() {
    Object.values(this.agents).forEach(a => a.limpiarMemoria());
    this.sharedMemory = { userContext: {}, activeEntities: {}, lastQuery: null, queryCount: 0 };
    this.handoffHistory = [];
    try { sessionStorage.removeItem('orchestrator.sharedMemory'); } catch {}
  }
}

// Singleton global
let orchestratorInstance = null;

export async function getOrchestrator() {
  if (!orchestratorInstance) {
    orchestratorInstance = new AgentOrchestrator();
    await orchestratorInstance.inicializar();
  }
  return orchestratorInstance;
}

export function resetOrchestrator() {
  if (orchestratorInstance) {
    orchestratorInstance.limpiar();
    orchestratorInstance = null;
  }
}