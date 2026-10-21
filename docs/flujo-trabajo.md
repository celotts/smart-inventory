# El flujo de trabajo del repositorio

Qué rama es qué, y qué se puede hacer en cada una.

## Las tres ramas

| Rama | Qué es | Quién despliega desde acá |
|---|---|---|
| `main` | **Lo que está en producción.** Un commit acá es un commit que alguien está usando. | Sí, siempre. |
| `develop` | **La integración.** Todo lo terminado se mergea acá. Es el "próximo release". | No. |
| `feature/<nombre>` | **Una cosa que se está haciendo.** Vive hasta que se mergea a `develop`. | No. |

## Las reglas

1. **Nadie commitea directo en `main` ni en `develop`.** Si tocás una rama protegida, el merge se vuelve un problema en vez de una operación de un minuto.
2. **`main` solo avanza por merge de `develop`.** Nunca por un commit suelto: si `main` avanza con un commit, ese commit no pasó por la integración.
3. **Cada `feature/` sale de `develop`, nunca de `main`.** Si sale de `main`, se queda atrás el trabajo de `develop` y el próximo merge es un conflictos que no hacía falta.
4. **Una `feature/` se mergea a `develop` cuando TERMINA y está probada**, no cuando "más o menos". `develop` es el estado de "esto se puede probar en serio".
5. **`main` y `develop` están siempre en el mismo commit** mientras no haya una release. Cuando haya una, `develop` sigue avanzando y `main` se queda en el release, con un tag.

## Los tags

Una release es un tag en `main`:

```
v0.5.0   <- la versión que dice el pie de la app
```

El número del tag y el de `frontend/src/build.js` (la versión que muestra la pantalla) van juntos. Cuando salen a la vez, el tag es el que manda: el `build.js` se ajusta para que coincidan.

## Por qué no hay `release/` todavía

La rama `release/` del git-flow clásico sirve para preparar una versión: congelar las features, corregir solo lo crítico, y sacar el tag. **Este proyecto no tiene releases todavía** (no hay tags), así que la rama sería una formalidad.

El día que haya releases, el flujo pasa a ser:

```
feature/x  ->  develop  ->  (release/v0.6.0)  ->  main + tag v0.6.0
                             ^                  ^
                             |                  |
                     congelada: solo       lo que se
                     criticos              despliega
```

## Qué corre antes de mergear

Nada de esto corre solo hoy (no hay CI). **Los tests se corren a mano**, y son dos:

```bash
# El backend (205 tests): permisos, seguridad, SLA, escaneo, geolocalización
python3 -m pytest backend/tests/ -q

# El front (13 tests): login, arranque, cabecera, campos, acceso por rol
cd frontend && npx playwright test
```

El que mergea a `develop` es responsable de que los dos estén en verde. Cuando haya CI, esto deja de ser una costumbre y pasa a ser una regla del servidor.
