# Service Desk: el flujo del caso

Documento de referencia del modulo de incidencias: como entra un caso, quien
lo atiende, como se avisa y que pasa con el equipo.

---

## 1. Que es (y que no es) este modulo

**Es** el control de los equipos danados:

- un equipo se rompe (o se pierde, o se daña de forma total),
- alguien lo reporta,
- el taller lo toma, lo arregla o lo declara irrecuperable,
- el inventario refleja lo que pasó.

**No es** un help desk de software: no hay chats, no hay adjuntos, no hay
portales de cliente, no hay tickets de "la impresora no imprime".

La consequence de esa decision es una regla que atraviesa todo el modulo:

> **Un caso no se cierra con un texto: se cierra con el equipo.**
>
> Si el caso tiene un equipo asociado, resolverlo obliga a decir que paso con
> ese equipo. Si no se dice, el inventario sigue contando como disponible un
> equipo que esta roto, y el KPI "en deposito" miente.

---

## 2. El flujo, de punta a punta

```text
  REPORTA                  TOMA                  RESUELVE
  (cualquiera)      (tecnico, un clic)     (tecnico, con el destino)
       |                    |                        |
       v                    v                        v
  +---------+         +---------+              +-------------+
  |  OPEN   | ------> |IN_PROG.| ------------> |  RESOLVED  |
  +---------+         +---------+              +-------------+
       |                    |                        |
       |                    |                        +--> el reportador recibe el aviso
       |                    |                        +--> el equipo:
       |                    |                              TALLER     -> IN_REPAIR (+ orden de trabajo)
       |                    |                              REPARADO  -> IN_WAREHOUSE
       |                    |                              DANO_TOTAL-> baja + certificado
       |                    |
       |                    +--> el reportador recibe el aviso ("lo tomaron")
       |
       +--> queda en la BANDEJA (nadie lo respondio)
```

### 2.1 La bandeja

Los casos reportados que **nadie respondio todavia** (`first_response_at IS
NULL` y estado vivo).

No es lo mismo que "sin asignar":

| Filtro | Que mira | Para que sirve |
| --- | --- | --- |
| `sin_atender` | la primera respuesta | El trabajo sin dueno: se pierde. |
| `solo_sin_asignar` | `assigned_to IS NULL` | Lo que nadie se adjudico. |

Un caso asignado a alguien que lo tiene abandonado sigue sin atender: por
eso el backlog mira la primera respuesta y no la asignacion.

La pantalla de incidencias tiene tres segmentos:

- **Bandeja** — lo que nadie took. Es la vista con la que arranca.
- **Mis casos** — lo que tenes asignado y sigue abierto.
- **Todas** — todo, para el supervisor.

### 2.2 Tomar el caso

`POST /api/incidents/{id}/claim`

- Asigna el caso a quien lo toma.
- Arranca el reloj de respuesta (`first_response_at`).
- Pasa el caso de `OPEN` a `IN_PROGRESS`.
- Avisa al que lo reporto.
- Es idempotente para el mismo usuario; a otro usuario le responde 409.

La fila se toma con `FOR UPDATE`: dos tecnicos no pueden quedarse con el
mismo caso.

### 2.3 Resolver

`POST /api/incidents/{id}/cierre`

```json
{
  "resolution": "Se cambio la placa madre.",
  "estado": "RESOLVED",
  "destino_equipo": "TALLER"
}
```

El `destino_equipo` es lo que hace que este modulo sea el control del stock:

| Destino | Que pasa con el equipo | Que pasa con la base |
| --- | --- | --- |
| `NINGUNO` | No cambia. | — |
| `TALLER` | Pasa a `IN_REPAIR` y deja de contar como disponible. | Se abre una **orden de trabajo** (`repairs`) ligada al caso. |
| `REPARADO` | Vuelve a `IN_WAREHOUSE` y vuelve a estar disponible. | La orden de trabajo se cierra como `REPAIRED`. |
| `DANO_TOTAL` | Sale del stock: baja logica y deja de contar. | La orden se cierra como `UNREPAIRABLE` y se emite el **certificado de baja**, que apunta a esa orden. |

**El destino se valida ANTES de resolver el caso.** Si el destino es imposible
para el estado actual del equipo (por ejemplo, "reparado" de un equipo
instalado en el cliente: no vuelve solo al deposito), el backend responde 409
y el caso **sigue abierto**. Resolver primero y fallar despues deja el caso
cerrado y el equipo donde estaba: dos verdades sobre lo mismo.

### 2.4 La orden de trabajo

Un equipo no puede pasar a `IN_REPAIR` sin una reparacion abierta: lo exige
el trigger `validar_transicion_estado` de la base, porque un equipo "en el
taller" sin orden de trabajo es un equipo que el taller no sabe que tiene.

Por eso el cierre con destino `TALLER` **abre la orden** (`repairs`, con
`incident_id` al caso) y despues mueve el equipo. La orden es lo que guarda
el diagnostico, el trabajo hecho y el costo, y es lo que permite responder
"de que se estaba quejar este equipo" con un solo `JOIN`.

---

## 3. Los avisos

Los avisos viven en `incident_notifications` (no en un cartel flotante): un
aviso de SLA tiene que quedar, y tiene que saber a quien se le dio y cuando.

| Tipo | Cuando | A quien |
| --- | --- | --- |
| `CASE_CLAIMED` | Alguien toma el caso. | Al que lo reporto. |
| `CASE_RESOLVED` | El caso se resuelve. | Al que lo reporto. |
| `SLA_DUE_SOON` | Faltan 30 minutos para el plazo. | Al responsable (o a quien puede resolver). |
| `SLA_RESOLUTION_DUE` | Falta poco para el plazo de resolucion. | Al responsable. |
| `SLA_ESCALATED` | Se paso el plazo de RESPUESTA y nadie tomo el caso. | Al supervisor (politica de escalamiento). |
| `SLA_RESOLUTION_BREACHED` | Se paso el plazo de RESOLUCION y sigue abierto. | Al supervisor. |

La clave `dedup_key` es UNIQUE: el barrido puede correr cada minuto sin
llenar la bandeja de copias del mismo aviso.

El escalamiento sale de `incident_escalation_policies` (por severidad): el
supervisor es el rol `ADMIN` por defecto, y se puede cambiar por usuario.

---

## 4. El backlog

En `GET /api/incidents/metrics`:

| Campo | Que es |
| --- | --- |
| `sin_atender` | Casos abiertos que nadie respondio. |
| `sin_atender_antiguedad_h` | Horas que lleva esperando el caso sin atender MAS VIEJO. |
| `en_espera` | Casos en `WAITING` (esperando repuesto o aprobacion). |
| `mias` | Casos del usuario que pregunta, todavia abiertos. |

En la pantalla, el backlog es la primera tarjeta del tablero del Service
Desk y el KPI "Sin atender", que lleva a la bandeja con el filtro puesto.

---

## 5. Los permisos

| Permiso | Que habilita |
| --- | --- |
| `incidents:read` | Ver los casos y el tablero. |
| `incidents:create` | Reportar un caso. |
| `incidents:manage` | Tomar casos, cambiar el estado, resolver, comentar. |

Reportar un problema es de cualquiera; resolverlo es del taller.
