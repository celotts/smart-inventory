/**
 * Multi-Agente Smart Inventory - Entry Point
 *
 * USO:
 *   import { procesarQuery, getOrchestrator } from './agents';
 *
 *   const resultado = await procesarQuery('Cuanto stock de Latitude 5440?');
 *   console.log(resultado.response);
 *
 *   // O con streaming:
 *   for await (const chunk of procesarQueryStream('Reporte mensual')) {
 *     // render chunk
 *   }
 */

export { AgentOrchestrator, getOrchestrator, resetOrchestrator } from './orchestrator.js';
export { BaseAgent, Tool, ToolRegistry, HandoffTool } from './base.js';
export { AllTools } from './tools.js';
export { Agents, AgentNames, InventoryAgent, MaintenanceAgent, PurchasingAgent, ReportingAgent } from './agents.js';
export { OllamaClient, createOllamaClient, OllamaModels } from './ollama.js';

// Función helper simple
export async function procesarQuery(query, options = {}) {
  const orchestrator = await getOrchestrator();
  return orchestrator.procesar(query, options);
}

// Streaming helper (para futuro)
export async function* procesarQueryStream(query, options = {}) {
  const orchestrator = await getOrchestrator();
  // TODO: implementar streaming real con onToolCall callbacks
  const resultado = await orchestrator.procesar(query, { ...options, stream: true });
  yield resultado.response;
}

// Health check
export async function healthCheck() {
  const { createOllamaClient } = await import('./ollama.js');
  const client = await createOllamaClient();
  return client.healthCheck();
}