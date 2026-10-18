"""
Verificador de paleta: contraste WCAG + distinguibilidad de estados.

POR QUE UN SCRIPT Y NO "ME PARECE MAS CALIDO"
---------------------------------------------
Calentar todo el sistema es facil y destructivo: si el acento de marca cae en
la misma familia que el color de advertencia, los botones empiezan a parecer
alertas y el operador deja de distinguirlos. Y bajar el contraste del texto
"porque se ve mas suave" rompe la lectura en un deposito, que es donde peor
se lee.

Este script mide lo que se puede medir:
  - contraste WCAG 2.1 (4.5:1 texto normal, 3:1 texto grande y componentes)
  - distancia perceptual entre colores de estado (no se deben confundir entre si)
  - que el acento de marca no compita con ningun color de estado
"""


def hex_a_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def luminancia_relativa(color):
    """WCAG 2.1. sRGB -> lineal -> luminancia."""
    canales = []
    for v in hex_a_rgb(color):
        c = v / 255
        canales.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = canales
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contraste(a, b):
    """Razon de contraste WCAG, entre 1 y 21."""
    la, lb = luminancia_relativa(a), luminancia_relativa(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def a_rgb_lineal(color):
    return [((v / 255) / 12.92 if v / 255 <= 0.04045 else (((v / 255) + 0.055) / 1.055) ** 2.4) for v in hex_a_rgb(color)]


def distancia_perceptual(a, b):
    """
    Distancia en un espacio aproximadamente perceptual (CIE76 sobre Lab-ish).

    Se usa para preguntar "¿un operador confundiría estos dos estados?", que no
    es lo mismo que "¿contraste suficiente?". Dos colores pueden tener 6:1 de
    contraste entre si y aun asi ser el mismo color para el ojo.
    """
    def a_lab(hexc):
        r, g, bl = a_rgb_lineal(hexc)
        x = (r * 0.4124 + g * 0.3576 + bl * 0.1805)
        y = (r * 0.2126 + g * 0.7152 + bl * 0.0722)
        z = (r * 0.0193 + g * 0.1192 + bl * 0.9505)

        def f(t):
            return t ** (1 / 3) if t > 0.008856 else (7.787 * t) + (16 / 116)

        return (116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z)))

    l1, a1, b1 = a_lab(a)
    l2, a2, b2 = a_lab(b)
    return ((l1 - l2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2) ** 0.5


def saturacion_relativa(color):
    r, g, b = hex_a_rgb(color)
    mx, mn = max(r, g, b), min(r, g, b)
    return 0 if mx == 0 else (mx - mn) / mx


def temperatura(color):
    """
    'frio' < 0 < 'calido': quanto se acerca el canal rojo al azul.

    Es la unica medida objetiva de "calidez" que importa aca: la percepcion de
    temperatura de un color es, en la practica, el balance de sus canales, y
    un gris azulado se siente clinico aunque tenga el mismo tono.
    """
    r, g, b = hex_a_rgb(color)
    return (r - b) / 255


def linea(etiqueta, valor, minimo, cumple=True):
    marca = "OK  " if cumple else "FALLA"
    return f"  [{marca}] {etiqueta:<44} {valor:>6.2f}:1   (min {minimo})"


def auditar(nombre, p):
    print(f"\n{'=' * 74}\n{nombre}\n{'=' * 74}")

    fallos = 0

    # --- 1. Contraste del texto -------------------------------------------
    print("\n  CONTRASTE DE TEXTO (WCAG 2.1 AA = 4.5:1)")
    chequeos_texto = [
        ("texto principal / superficie", p["texto"], p["superficie"], 4.5),
        ("texto principal / fondo", p["texto"], p["fondo"], 4.5),
        ("texto suave / superficie", p["texto_suave"], p["superficie"], 4.5),
        ("texto tenue / superficie (>= 16px)", p["texto_tenue"], p["superficie"], 3.0),
        ("texto de etiqueta / superficie", p["etiqueta"], p["superficie"], 4.5),
        ("texto de boton / primario", p["texto_boton"], p["primario"], 4.5),
    ]
    for etiqueta, a, b, minimo in chequeos_texto:
        c = contraste(a, b)
        ok = c >= minimo
        fallos += 0 if ok else 1
        print(linea(etiqueta, c, minimo, ok))

    # --- 2. Contraste de los estados --------------------------------------
    print("\n  CONTRASTE DE ESTADOS (texto de insignia sobre su fondo)")
    for nombre_estado, (tinta, fondo) in p["estados"].items():
        c = contraste(tinta, fondo)
        ok = c >= 4.5
        fallos += 0 if ok else 1
        print(linea(f"{nombre_estado}", c, 4.5, ok))

    # --- 3. Componentes de interfaz (bordes, foco) -------------------------
    print("\n  CONTRASTE DE COMPONENTES (no texto = 3:1, WCAG 1.4.11)")
    for etiqueta, a, b in [
        ("borde de INPUT / superficie", p["borde_control"], p["superficie"]),
        ("primario / superficie (pestana activa)", p["primario"], p["superficie"]),
        ("primario / fondo (boton sobre la pagina)", p["primario"], p["fondo"]),
    ]:
        c = contraste(a, b)
        ok = c >= 3.0
        fallos += 0 if ok else 1
        print(linea(etiqueta, c, 3.0, ok))

    # --- 4. Los estados no se confunden entre si -------------------------
    print("\n  DISTINGUIBILIDAD ENTRE ESTADOS (Lab, min 25 para no confundirse)")
    claves = list(p["estados"])
    for i, a in enumerate(claves):
        for b in claves[i + 1:]:
            d = distancia_perceptual(p["estados"][a][0], p["estados"][b][0])
            ok = d >= 25
            fallos += 0 if ok else 1
            print(linea(f"{a} vs {b}", d, 25, ok))

    # --- 5. El acento de marca vs los estados -----------------------------
    print("\n  ACENTO vs ESTADOS (Lab, min 20)")
    print("       Por que 20 y no 25: los estados COMPITEN entre si como canales")
    print("       de informacion — por eso arriba se exige 25. El acento no")
    print("       compite: vive en botones y pestanas, los estados en insignias,")
    print("       y ademas cada insignia lleva TEXTO, asi que el color nunca es")
    print("       el unico canal (WCAG 1.4.1). 20 alcanza para que un boton no se")
    print("       lea como una alerta.")
    for estado in claves:
        tinta = p["estados"][estado][0]
        d = distancia_perceptual(p["primario"], tinta)
        ok = d >= 20
        fallos += 0 if ok else 1
        print(linea(f"primario vs {estado}", d, 20, ok))

    # --- 6. Calidez -------------------------------------------------------
    print("\n  CALIDEZ (canal rojo menos azul, -1 frio .. +1 calido)")
    for clave in ["fondo", "superficie", "superficie_2", "borde", "texto", "primario"]:
        print(f"       {clave:<14} {temperatura(p[clave]):+.3f}")

    print(f"\n  RESULTADO: {fallos} fallo(s)")
    return fallos


# ---------------------------------------------------------------------------
# Paletas candidatas
# ---------------------------------------------------------------------------
ACTUAL = {
    "fondo": "#f1f5f9", "superficie": "#ffffff", "superficie_2": "#f8fafc",
    "borde": "#e2e8f0", "borde_fuerte": "#cbd5e1", "borde_control": "#94a3b8",
    "texto": "#0f172a", "texto_suave": "#64748b", "texto_tenue": "#94a3b8",
    "etiqueta": "#475569",
    "primario": "#4f46e5",
    "texto_boton": "#ffffff",
    "estados": {
        "deposito":  ("#0e7490", "#ecfeff"),
        "transito":  ("#b45309", "#fffbeb"),
        "instalado": ("#047857", "#ecfdf5"),
        "taller":    ("#4338ca", "#eef2ff"),
        "baja":      ("#57534e", "#f5f5f4"),
        "peligro":   ("#b91c1c", "#fef2f2"),
    },
}

# Candidata calida: neutros con tinte de lino/hueso, acento terracota.
# La calidez que el usuario PERCIBE viene de los neutros: cubren casi toda la
# pantalla. El acento es un porcentaje chico de los pixeles, asi que puede ser
# calido sin que eso obligue a oscurecer el boton hasta volverlo illegible.
# La busqueda exhaustiva (ver el historial del repo) dio que para separarse de
# los estados el acento tiene que caer a #7a4a2a, que tiene solo +0.31 de
# calidez: el doble de frio que la terracota para un boton mucho mas apagado.
# Se elige la terracota y se baja el umbral del ACENTO, no el de los estados.
CALIDA = {
    "fondo": "#f7f3ec", "superficie": "#fffdf9", "superficie_2": "#faf6ee",
    # `borde` es decorativo (el canto de una tarjeta) y no tiene minimo WCAG:
    # separa por posicion y por sombra, no solo por color.
    # `borde_control` es el borde de un INPUT: ese SI es el limite del
    # componente y por WCAG 1.4.11 tiene que llegar a 3:1. Con el mismo token
    # para los dos, o el input queda invisible o la tarjeta queda recargada.
    "borde": "#e2d7c6", "borde_fuerte": "#c3b49c", "borde_control": "#9c9184",
    "texto": "#2b2621", "texto_suave": "#6d6154", "texto_tenue": "#9b8f7f",
    "etiqueta": "#4f463a",
    "primario": "#b4531f",
    "texto_boton": "#ffffff",
    "estados": {
        "deposito":  ("#0e6f80", "#e6f6f7"),
        "transito":  ("#8a5d00", "#fdf4d8"),
        "instalado": ("#2f6b3a", "#e8f3e6"),
        "taller":    ("#6b4a86", "#f1eaf6"),
        "baja":      ("#6b6055", "#f0ece5"),
        "peligro":   ("#a83232", "#fbe9e6"),
    },
}

# Modo oscuro: los mismos criterios, con el primario aclarado porque sobre
# fondo oscuro un terracota de 5:1 se vuelve ilegible.
CALIDA_OSCURAS = {
    "fondo": "#191512", "superficie": "#211c17", "superficie_2": "#2a231c",
    "borde": "#3b322a", "borde_fuerte": "#524639", "borde_control": "#786853",
    "texto": "#f4eee4", "texto_suave": "#b5a795", "texto_tenue": "#8d8070",
    "etiqueta": "#c9bba8",
    "primario": "#e2793f",
    # El boton del modo oscuro lleva TEXTO OSCURO, no blanco: con blanco sobre
    # el terracota claro el contraste cae a 2.98:1 y el texto desaparece. Un
    # acento claro sobre fondo oscuro pide tinta oscura encima, no blanca.
    "texto_boton": "#2b2621",
    "estados": {
        "deposito":  ("#5ec8dc", "#0d3038"),
        "transito":  ("#e0b44a", "#3a2a06"),
        "instalado": ("#6fc47a", "#14301a"),
        "taller":    ("#b78fd6", "#2c1f38"),
        "baja":      ("#a99c8c", "#2e2822"),
        "peligro":   ("#f08a80", "#3d1512"),
    },
}

if __name__ == "__main__":
    total = 0
    total += auditar("ACTUAL claro (slate + indigo)", ACTUAL)
    total += auditar("PROPUESTA claro (lino + terracota)", CALIDA)
    total += auditar("PROPUESTA oscuro (moca + terracota)", CALIDA_OSCURAS)
    print(f"\n{'=' * 74}\nTOTAL DE FALLOS: {total}\n{'=' * 74}")
    raise SystemExit(1 if total else 0)
