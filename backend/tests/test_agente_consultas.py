"""
Tests del agente de consultas.

QUE SE PRUEBA
-------------
Tres cosas que fallaron en la operacion real, con el mismo sintoma para el
operador ("la IA no sirve"):

1. LA INTENCION DE ESCRITURA. "Agrega un producto al inventario" se
   respondia con un SELECT de productos de la categoria "desarrollo" y un
   "no hay datos". La pregunta del operador se perdia en el camino.

2. LA MEMORIA. "Cual es ese equipo que se encontro?" se respondia con una
   consulta de equipos INSTALADOS: otra cosa, y una respuesta que contradecia
   a la anterior.

3. LA RESPUESTA. El modelo repetia la tabla de datos en la prosa y, en un
   agregado, completaba el detalle inventado ("id: 1, serial: SN-001") de
   un resultado que era un solo numero.

POR QUE NO HAY UN MODELO DE VERDAD
----------------------------------
El LLM real (Ollama) no esta en los tests: es lento, no siempre esta y su
respuesta cambia entre versiones. Lo que se prueba aca es la DETERMINISTICA
del agente: la deteccion de intencion, el formato del contexto, la limpieza
de la respuesta y el armado del historial. El modelo es la parte que cambia;
el armazon es el que no tiene que cambiar.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.modules.ai import intencion, sql_agent


class TestIntencionDeEscritura:
    @pytest.mark.parametrize(
        "pregunta",
        [
            "Agrega un producto al inventario que tenga relacion al desarrollo",
            "agregá un producto nuevo",
            "Crea una incidencia para el cliente Acme",
            "borra el producto DEMO-CATALYST-1",
            "Actualiza el precio del notebook",
            "Dame de baja el equipo 3",
            "podes agregar un producto de development?",
            "necesito que cargues un producto nuevo",
            "quisiera que actualices el costo",
        ],
    )
    def test_detecta_la_intencion_de_escribir(self, pregunta):
        assert intencion.detectar(pregunta).es_escritura, pregunta

    @pytest.mark.parametrize(
        "pregunta",
        [
            "Cuantos equipos hay en deposito?",
            "cual es ese equipo que se encontro?",
            "Cuantos equipos hay que reparar?",
            "Que equipos estan instalados hace mas de un ano?",
            "Cuantas reparaciones abiertas hay?",
            "Que modelos se estan rompiendo mas?",
            # "agregaron" es pasado: cuenta, no agrega.
            "Cuantos equipos se agregaron este mes?",
            "se ve el select mas no el producto",
        ],
    )
    def test_no_confunde_una_consulta_con_una_escritura(self, pregunta):
        assert not intencion.detectar(pregunta).es_escritura, pregunta

    def test_la_respuesta_de_escritura_dice_que_no_puede_y_a_donde_ir(self):
        guia = intencion.respuesta_de_lectura(
            "Agrega un producto al inventario"
        )

        # Dice que es de SOLO LECTURA: si no, el operador cree que el modelo
        # va a hacerlo.
        assert "consultas" in guia["respuesta"]
        # Y dice donde hacerlo.
        assert "Productos" in guia["respuesta"]
        # Y ofrece una alternativa util.
        assert "buscá" in guia["respuesta"].lower()

    def test_la_sugerencia_apunta_a_la_pantalla_correcta(self):
        guia = intencion.respuesta_de_lectura("Crea una incidencia nueva")

        assert guia["sugerencia"] is not None
        assert guia["sugerencia"]["vista"] == "incidencias"


class TestSugerenciaContraElFront:
    """
    La vista que sugiere el backend TIENE que existir en el registro del
    front.

    El boton de la sugerencia despacha el evento `app:ir-a` con la clave de
    la vista, y `navegacion.js` la busca en su registro `VISTAS`. Si la clave
    no esta (o esta con otra mayuscula), el boton no lleva a ningun lado: el
    operador hace clic y no pasa nada, que es el peor sintoma posible.
    """

    def _vistas_del_front(self) -> set[str]:
        import re

        raiz = Path(__file__).resolve().parents[2] / "frontend" / "src"
        if not raiz.exists():  # pragma: no cover
            pytest.skip("No se encuentra el front.")
        texto = (raiz / "navegacion.js").read_text(encoding="utf-8")
        bloque = re.search(r"export const VISTAS = \{(.*?)\n\};", texto, re.S)
        if not bloque:  # pragma: no cover
            pytest.skip("No se encuentra el registro VISTAS.")
        return set(re.findall(r"^\s*([a-z_]+):\s*\{", bloque.group(1), re.M))

    def test_toda_vista_sugerida_existe_en_el_front(self):
        vistas = self._vistas_del_front()

        for _patron, vista, _guia in intencion.DESTINOS_POR_TEMA:
            assert vista in vistas, (
                f"La IA sugiere la vista '{vista}', que no existe en el registro "
                "del front: el boton no lleva a ningun lado."
            )


class TestLimpiezaDeRespuesta:
    def test_saca_la_tabla_markdown(self):
        texto = (
            "Se encontro 1 equipo.\n"
            "\n"
            "| id | count |\n"
            "|---|---|\n"
            "| 1 | 1 |\n"
        )

        limpio = sql_agent._limpiar_respuesta(texto, es_agregado=False)

        assert "|" not in limpio
        assert "Se encontro 1 equipo." in limpio

    def test_en_un_agregado_solo_queda_la_primera_frase(self):
        texto = (
            "Se encontro 1 equipo en el Deposito.\n"
            "- id: 1\n"
            "- serial_number: SN-001\n"
            "- name: Dell Latitude 5520\n"
        )

        limpio = sql_agent._limpiar_respuesta(texto, es_agregado=True)

        assert limpio == "Se encontro 1 equipo en el Deposito."
        # El detalle inventado no puede quedar: el operador lo tomaria como
        # dato real.
        assert "SN-001" not in limpio

    def test_una_frase_con_salto_de_linea_no_se_corta_mal(self):
        texto = "Se encontro 1 reparacion abierta.\nLa consulta conto el total."

        limpio = sql_agent._limpiar_respuesta(texto, es_agregado=True)

        assert limpio.startswith("Se encontro 1 reparacion abierta.")
        assert "conto el total" not in limpio


class TestResumenDelHistorial:
    def test_un_agregado_no_muestra_el_numero_como_si_fuera_un_id(self):
        """
        El numero de un total NO puede viajar al historial: el modelo lo
        tomaba por un id y lo metia de filtro en la consulta siguiente
        ("WHERE d.id = 1"), que no devolvia nada.
        """
        resumen = sql_agent.resumen_de_resultado(["count"], [{"count": 1}], 1)

        assert "1" not in resumen.replace("un total", "")
        assert "total" in resumen.lower()

    def test_un_detalle_si_muestra_los_valores_identificadores(self):
        resumen = sql_agent.resumen_de_resultado(
            ["serial_number", "model"],
            [{"serial_number": "SN-001", "model": "Latitude 5440"}],
            1,
        )

        assert "SN-001" in resumen
        assert "Latitude 5440" in resumen

    def test_sin_filas(self):
        assert sql_agent.resumen_de_resultado(["a"], [], 0) == "sin filas"


class TestArmazonDelAgente:
    def test_la_pregunta_de_escritura_no_llega_al_modelo(self):
        """
        La deteccion es ANTES de la llamada al modelo: ademas de ser mas
        rapida, garantiza que la respuesta sea la misma siempre.
        """
        class LLMQueNoDeberiaHablar:
            name = "falso"

            def chat(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError("No se deberia llamar al modelo.")

        resultado = sql_agent.responder_pregunta(
            "Agrega un producto al inventario",
            llm=LLMQueNoDeberiaHablar(),
        )

        assert resultado["sql"] is None
        assert "consultas" in resultado["respuesta"]
        assert resultado["sugerencia"] is not None

    def test_la_consulta_de_lectura_llega_al_modelo(self):
        class LLMFalso:
            name = "falso"

            def __init__(self):
                self.prompts = []

            def chat(self, prompt, system=None, **kwargs):
                self.prompts.append((prompt, system))
                return type(
                    "Respuesta",
                    (),
                    {
                        "text": "```sql\nSELECT 1 AS total\n```",
                        "model_used": "falso",
                        "used_fallback": False,
                    },
                )()

        llm = LLMFalso()
        sql_agent.responder_pregunta("Cuantos equipos hay en deposito?", llm=llm)

        # La primera llamada es la del SQL.
        assert llm.prompts
        assert "SELECT" in llm.prompts[0][0]

    def test_el_historial_llega_al_prompt(self):
        """
        Sin el historial en el prompt, la pregunta de seguimiento se responde
        sola y el modelo contesta de otra cosa.
        """

        class LLMFalso:
            name = "falso"

            def __init__(self):
                self.prompts = []

            def chat(self, prompt, system=None, **kwargs):
                self.prompts.append(prompt)
                return type(
                    "Respuesta",
                    (),
                    {
                        "text": "```sql\nSELECT 1 AS total\n```",
                        "model_used": "falso",
                        "used_fallback": False,
                    },
                )()

        llm = LLMFalso()
        sql_agent.responder_pregunta(
            "cual es ese equipo que se encontro?",
            llm=llm,
            historial=[
                {
                    "pregunta": "Cuantos equipos hay en deposito?",
                    "sql": "SELECT COUNT(*) FROM devices WHERE current_status = 'IN_WAREHOUSE'",
                    "resumen": "un total",
                }
            ],
        )

        prompt = llm.prompts[0]
        assert "HISTORIAL" in prompt
        assert "current_status = 'IN_WAREHOUSE'" in prompt
