#!/bin/bash
# Script de verificación de procesos operativos
# Uso: ./verificar-procesos.sh
# Devuelve código de salida 0 si todo OK, 1 si hay problemas

set -e

echo "========================================"
echo "  Verificación de Procesos Operativos"
echo "========================================"
echo ""

PASSED=0
FAILED=0

# 1. Verificar migraciones
echo "--- 1. Migraciones versionadas ---"
MIGRATIONS=$(ls database/migrations/*.sql 2>/dev/null | wc -l)
if [ "$MIGRATIONS" -ge 10 ]; then
    echo "  ✅ $MIGRATIONS migraciones encontradas"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ Solo $MIGRATIONS migraciones (mínimo 10)"
    FAILED=$((FAILED + 1))
fi

# 2. Verificar tests backend
echo "--- 2. Tests backend ---"
cd backend
TESTS_RESULT=$(python3 -m pytest tests/ -q --tb=line 2>&1 | tail -1)
if echo "$TESTS_RESULT" | grep -q "passed"; then
    PASSED=$((PASSED + 1))
    echo "  ✅ Tests backend passing"
else
    FAILED=$((FAILED + 1))
    echo "  ❌ Tests backend con fallos"
fi
cd ..

# 3. Verificar CI workflow
echo "--- 3. Workflow CI ---"
if [ -f .github/workflows/ci.yml ]; then
    echo "  ✅ .github/workflows/ci.yml existe"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ .github/workflows/ci.yml no encontrado"
    FAILED=$((FAILED + 1))
fi

# 4. Verificar scripts de backup
echo "--- 4. Scripts de backup ---"
if [ -f scripts/backup.sh ] && [ -f scripts/restore.sh ]; then
    echo "  ✅ scripts/backup.sh y scripts/restore.sh existen"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ Faltan scripts de backup"
    FAILED=$((FAILED + 1))
fi

# 5. Probar restore rápido
echo "--- 5. Restore rápido ---"
if bash scripts/restore.sh > /dev/null 2>&1; then
    echo "  ✅ Restore exitoso"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ Restore falló"
    FAILED=$((FAILED + 1))
fi

# 6. Verificar frontend tests sintaxis
echo "--- 6. Sintaxis front ---"
cd frontend
SYNTAX_ERRORS=0
find . -name "*.mjs" -o -name "*.js" | head -20 | while read f; do
    node --check "$f" 2>/dev/null || SYNTAX_ERRORS=$((SYNTAX_ERRORS + 1))
done
if [ "$SYNTAX_ERRORS" -eq 0 ]; then
    echo "  ✅ Sin errores de sintaxis frontend"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ $SYNTAX_ERRORS errores de sintaxis"
    FAILED=$((FAILED + 1))
fi
cd ..

# 7. Verificar compose levanta
echo "--- 7. Stack levanta ---"
if docker compose ps 2>/dev/null | grep -q "Up"; then
    echo "  ✅ Stack Docker levantado"
    PASSED=$((PASSED + 1))
else
    echo "  ❌ Stack Docker no levantado"
    FAILED=$((FAILED + 1))
fi

# Resultado final
echo ""
echo "========================================"
echo "  Resultado: $PASSED proceso(s) passed, $FAILED fallido(s)"
echo "========================================"

if [ "$FAILED" -eq 0 ]; then
    echo "  🎢 Todos los procesos operativos!"
    exit 0
else
    echo "  ⚠️  Algunos procesos necesitan atención"
    exit 1
fi
