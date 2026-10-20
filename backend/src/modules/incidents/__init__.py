"""
Modulo de incidencias.

QUE ES
-----
El reporte de un problema: "este equipo no prende", "el cliente no tiene
internet". Es la unidad de trabajo del dia a dia del soporte, y tiene tres
cosas que la diferencian de un ticket generico:

  1. Un SLA por severidad, con los plazos calculados y guardados.
  2. Una maquina de estados validada por la base.
  3. Un historial inmutable de que paso, cuando y quien lo hizo.

El modulo de MANTENIMIENTO (el preventivo y el correctivo) es el hermano:
las tablas `maintenance` y `maintenance_plans` ya existen, y el vinculo con la
incidencia se hace con la columna `incident_id`.
"""
