-- ============================================================================
-- 0013 — Corregir `audit_logs.record_id` y dejar el trigger de auditoría
--        en el repo.
--
-- EL BUG
-- -------
-- `audit_logs.record_id` estaba declarada como UUID, pero el trigger
-- `audit_soft_delete()` asignaba `OLD.id::text` / `NEW.id::text` a una
-- variable TEXT y la insertaba en esa columna. La columna era uuid y la
-- expresión era text: Postgres rechazaba el INSERT con
--
--     column "record_id" is of type uuid but expression is of type text
--
-- Como el trigger es AFTER, el error no.cancelaba la operación: el UPDATE
-- habia quedado hecho y la transaccion moria igual. En la práctica, fallaba
-- TODO lo que actualiza una tabla auditada —editar un producto, editar un
-- equipo, editar un cliente, cambiar un password— con un 500 "Error interno."
--
-- POR QUE LA COLUMNA ES TEXT Y NO UUID
-- -----------------------------------
-- El comentario de `init.sql` dice, textual: "`record_id` ... es POLIMORFICO:
-- el mismo UUID no puede apuntar a devices.id (INT) y a users.id (UUID) con
-- una sola restriccion. Por eso se guarda como texto y el `table_name` dice de
-- que tabla es." El DML hace lo que el comentario dice; el DDL no. Y el
-- comentario tiene razón: de las nueve tablas auditadas, SIETE tienen id
-- entero (devices, models, clients, vendors, categories, locations,
-- purchase_orders) y solo dos lo tienen uuid (roles, users).
--
-- Un solo tipo uuid no puede guardar las dos cosas. TEXT si: guarda el id
-- entero como '42' y el uuid como su texto, y `table_name` dice de que tabla
-- es cada fila. El índice `(table_name, record_id)` sigue sirviendo igual.
--
-- QUE HACE Está Migración
-- -----------------------
--  1. Cambia la columna a TEXT, preservando lo que haya.
--  2. Crea la función `audit_soft_delete()`, que antes vivia Solo en la base
--     y no en el repo: una base creada desde `init.sql` no tenia triggers de
--     auditoría, y una migrada los tenia con una función que no podia
--     ejecutarse. Con la función acá, las dos caminos quedan iguales.
--  3. Crea los nueve triggers.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. La columna
-- ---------------------------------------------------------------------------
-- `USING record_id::text` es obligatorio: sin el, Postgres no sabe como pasar
-- de uuid a text y se niega a hacer la conversión.
ALTER TABLE audit_logs
    ALTER COLUMN record_id TYPE TEXT USING record_id::text;


-- ---------------------------------------------------------------------------
-- 2. La función
-- ---------------------------------------------------------------------------
-- POR QUE `v_record_id` ES TEXT Y NO UUID
-- --------------------------------------
-- La función es generica: la misma sirve para `devices` (id entero) y para
-- `users` (id uuid). Guardar el id en una variable TEXT y castearlo al
-- insertar deja que cada tabla use su tipo sin que la función tenga que
-- saberlo.
--
-- El cast de `NEW.id::uuid` a `v_user_id` va envuelto en un bloque con
-- EXCEPTION: no todas las tablas auditadas tienen `deleted_by`,
-- `updated_by` ni `created_by`, y en las que no están, el error se
-- convierte en NULL en vez de tumbar la operación.
CREATE OR REPLACE FUNCTION audit_soft_delete()
RETURNS TRIGGER AS $$
DECLARE
    v_record_id TEXT;
    v_user_id UUID;
BEGIN
    -- Determinar record_id
    IF TG_OP = 'DELETE' THEN
        BEGIN
            v_record_id := OLD.id::text;
        EXCEPTION WHEN OTHERS THEN
            v_record_id := NULL;
        END;
    ELSE
        BEGIN
            v_record_id := NEW.id::text;
        EXCEPTION WHEN OTHERS THEN
            v_record_id := NULL;
        END;
    END IF;

    -- Determinar user_id si existe
    IF TG_OP != 'DELETE' THEN
        BEGIN
            IF NEW.deleted_by IS NOT NULL THEN
                v_user_id := NEW.deleted_by::uuid;
            ELSIF NEW.updated_by IS NOT NULL THEN
                v_user_id := NEW.updated_by::uuid;
            ELSIF NEW.created_by IS NOT NULL THEN
                v_user_id := NEW.created_by::uuid;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            v_user_id := NULL;
        END;
    END IF;

    IF (TG_OP = 'UPDATE') THEN
        IF (OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL) THEN
            -- Soft delete
            INSERT INTO audit_logs (action, table_name, record_id, old_value, new_value, user_id, created_at)
            VALUES (
                'DELETE',
                TG_TABLE_NAME,
                v_record_id,
                json_build_object('deleted_at', OLD.deleted_at),
                json_build_object('deleted_at', NEW.deleted_at, 'action', 'SOFT_DELETE'),
                v_user_id,
                NOW()
            );
            RETURN NEW;
        END IF;
        -- Actualización
        INSERT INTO audit_logs (action, table_name, record_id, old_value, new_value, user_id, created_at)
        VALUES (
            'UPDATE',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(OLD),
            row_to_json(NEW),
            v_user_id,
            NOW()
        );
        RETURN NEW;
    ELSIF (TG_OP = 'INSERT') THEN
        INSERT INTO audit_logs (action, table_name, record_id, new_value, user_id, created_at)
        VALUES (
            'INSERT',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(NEW),
            v_user_id,
            NOW()
        );
        RETURN NEW;
    ELSIF (TG_OP = 'DELETE') THEN
        -- Hard delete - loguear
        INSERT INTO audit_logs (action, table_name, record_id, old_value, created_at)
        VALUES (
            'DELETE',
            TG_TABLE_NAME,
            v_record_id,
            row_to_json(OLD),
            NOW()
        );
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;


-- ---------------------------------------------------------------------------
-- 3. Los triggers
-- ---------------------------------------------------------------------------
-- `CREATE OR REPLACE TRIGGER` es de Postgres 14 en adelante. Se usa en vez de
-- `DROP TRIGGER` + `CREATE` porque es atomico: si la migración se corta a
-- mitad, no queda la tabla sin trigger, que es el estado en el que la
-- auditoría deja de registrar sin que nadie se entere.
CREATE OR REPLACE TRIGGER audit_devices
    AFTER INSERT OR DELETE OR UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_roles
    AFTER INSERT OR DELETE OR UPDATE ON roles
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_models
    AFTER INSERT OR DELETE OR UPDATE ON models
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_clients
    AFTER INSERT OR DELETE OR UPDATE ON clients
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_vendors
    AFTER INSERT OR DELETE OR UPDATE ON vendors
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_categories
    AFTER INSERT OR DELETE OR UPDATE ON categories
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_locations
    AFTER INSERT OR DELETE OR UPDATE ON locations
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_purchase_orders
    AFTER INSERT OR DELETE OR UPDATE ON purchase_orders
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();

CREATE OR REPLACE TRIGGER audit_users
    AFTER INSERT OR DELETE OR UPDATE ON users
    FOR EACH ROW EXECUTE FUNCTION audit_soft_delete();
