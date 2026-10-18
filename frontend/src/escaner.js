/**
 * Lector de codigos (QR y codigo de barras).
 *
 * POR QUE `BarcodeDetector` Y NO UNA BIBLIOTECA
 * --------------------------------------------
 * La version anterior usaba `html5-qrcode` desde unpkg.com: un CDN de terceros
 * con la aplicacion entera depende de que ese dominio este vivo y no
 * comprometido. Con `BarcodeDetector`, que es una API NATIVA del navegador, hay
 * cero dependencias: nada que auditar, nada que actualizar, nada que pueda
 * inyectar script.
 *
 * Ademas es mas rapido: la deteccion ocurre en el motor del navegador, sin
 * decodificar el frame en JavaScript, y con deteccion automatica de formatos.
 *
 * SOPORTE REAL, NO TEORICO
 * ------------------------
 * `BarcodeDetector` esta en Chrome/Edge (Android y escritorio) y en Safari 17+.
 * En Firefox de escritorio NO esta. Por eso hay tres niveles:
 *
 *   1. `BarcodeDetector` nativo, si existe.
 *   2. Ingreso MANUAL del codigo.
 *
 * El ingreso manual no es un parche: es lo que hace que la app funcione en
 * cualquier dispositivo. Un Firefox sin camara soportada tiene que poder
 * inventario igual.
 *
 * LA CAMARA NUNCA SALE DE ESTE DISPOSITIVO
 * ---------------------------------------
 * El fotograma se queda en el `<video>` local. A la API viaja SOLO el string
 * leido. Esto no es solo privacidad: mandar imagenes a un servidor central desde
 * un deposito con mala senal agrega ~300 ms por lectura y multiplica el ancho
 * de banda. Ademas una imagen es un archivo binario que hay que validar y
 * guardar en disco en algun lado; un string de 12 caracteres, no.
 */

/** Formatos que se piden. Se pide la lista y no se asume: un navegador puede
 *  no soportar todos, y pedir uno que no existe hace fallar el constructor. */
const FORMATOS_DESEADOS = [
  'qr_code',
  'code_128',
  'code_39',
  'code_93',
  'ean_13',
  'ean_8',
  'upc_a',
  'upc_e',
  'itf',
  'data_matrix',
  'pdf417',
];

/** Formatos que presque siempre existen y son un fallback razonable. */
const FORMATOS_MINIMOS = ['qr_code', 'code_128', 'ean_13'];

export class LectorQR {
  #stream = null;
  #detector = null;
  #loopActivo = false;
  #formats = [];

  #video = null;

  /**
   * @param {HTMLElement} contenedor  Donde se inserta el `<video>`.
   *
   * El lector crea su propio `<video>` en vez de recibirlo: asi el `<video>`
   * nunca puede quedar apuntando a otro lado si la vista se vuelve a montar,
   * y `detener()` siempre sabe exactamente que apagar.
   */
  constructor(contenedor) {
    this.#video = document.createElement('video');
    this.#video.setAttribute('playsinline', ''); // iOS: sin esto abre a pantalla completa
    this.#video.muted = true;
    this.#video.hidden = true;
    contenedor.append(this.#video);
  }

  /** El `<video>`, para poder mostrarlo u ocultarlo desde la vista. */
  get elementoVideo() {
    return this.#video;
  }

  /** Si el navegador puede decodificar codigos de forma nativa. */
  static haySoporteNativo() {
    return typeof window !== 'undefined' && 'BarcodeDetector' in window;
  }

  /** Si el navegador puede abrir la camara (pide permiso HTTPS o localhost). */
  static haySoporteDeCamara() {
    return Boolean(navigator.mediaDevices?.getUserMedia);
  }

  /**
   * Por que la camara no va a funcionar, en una frase que el usuario entienda.
   * Devuelve null si deberia funcionar.
   */
  static razonNoDisponible() {
    if (!window.isSecureContext) {
      return (
        'La camara solo funciona en HTTPS o en localhost. ' +
        'Abri la app por una direccion segura.'
      );
    }
    if (!LectorQR.haySoporteDeCamara()) {
      return 'Este navegador no permite abrir la camara. Ingresa el codigo a mano.';
    }
    if (!LectorQR.haySoporteNativo()) {
      return (
        'Este navegador no puede leer codigos automaticamente. ' +
        'Ingresa el codigo a mano, o probá con Chrome o Safari.'
      );
    }
    return null;
  }

  async #construirDetector() {
    const soportados = await window.BarcodeDetector.getSupportedFormats();
    this.#formats = FORMATOS_DESEADOS.filter((f) => soportados.includes(f));

    // Si el navegador no anuncia ninguno de los basicos, se prueban igual: hay
    // implementaciones que devuelven una lista incompleta pero funcionan.
    if (this.#formats.length === 0) this.#formats = FORMATOS_MINIMOS;

    this.#detector = new window.BarcodeDetector({ formats: this.#formats });
  }

  /**
   * Abre la camara y empieza a leer.
   *
   * @param {(codigo: string) => void} alLeer
   * @returns {Promise<void>}
   */
  async iniciar(alLeer) {
    if (this.#loopActivo) return;

    if (!LectorQR.haySoporteNativo()) {
      throw new Error('Este navegador no puede leer codigos automaticamente.');
    }

    if (!this.#detector) await this.#construirDetector();

    // `facingMode: environment` es la camara de atras: la de la que apunta al
    // mundo. En un celular es la que se quiere; en un escritorio sin camara
    // trasera, el navegador elige la que exista.
    try {
      this.#stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: { ideal: 'environment' },
          // Un codigo de barras se lee con nitidez; 1280 es suficiente y no
          // fuerza al celular a codificar en 4K, que consume bateria y calor.
          width: { ideal: 1280 },
          height: { ideal: 720 },
        },
        audio: false,
      });
    } catch (error) {
      throw new Error(mensajeDePermiso(error));
    }

    this.#video.srcObject = this.#stream;
    await this.#video.play();

    this.#loopActivo = true;
    this.#bucle(alLeer);
  }

  /**
   * El bucle de deteccion.
   *
   * Por que con `requestAnimationFrame` y no con un `setInterval`: el bucle tiene
   * que correr en el MISMO ritmo que los frames que dibuja la pantalla. Con un
   * intervalo se pide el frame al motor antes de que exista, y la deteccion va
   * a destiempo y consume CPU.
   *
   * `detectar(video)` es una promesa asincrona: si se espera su resultado, el
   * bucle se frena mientras la camara decodifica. Por eso se dispara sin
   * `await` y se protege con `#leyendo` para no apilar detecciones.
   */
  #bucle(alLeer) {
    let leyendo = false;

    const paso = async () => {
      if (!this.#loopActivo) return;

      // El video tiene que tener dimensiones antes de detectar: en el primer
      // frame todavia puede estar en 0x0, y `detect()` sobre eso lanza.
      if (!leyendo && this.#video.readyState >= 2 && this.#video.videoWidth > 0) {
        leyendo = true;
        try {
          const codigos = await this.#detector.detectar(this.#video);
          if (codigos.length > 0 && this.#loopActivo) {
            // Se toma el primero. Un frame con dos codigos es raro y adivinar
            // cual es el correcto seria peor que mostrar el primero y dejar que
            // el operador lo confirme.
            const valor = (codigos[0].rawValue ?? '').trim();
            if (valor) {
              // Se detiene ANTES de llamar al callback: seguir leyendo frames
              // mientras la app procesa el escaneo produce diez escaneos del
              // mismo codigo en un segundo.
              this.detener();
              alLeer(valor);
              return;
            }
          }
        } catch {
          // Un frame que falla al decodificar es normal (movimiento, foco).
          // Se ignora y se sigue con el siguiente.
        } finally {
          leyendo = false;
        }
      }

      if (this.#loopActivo) {
        requestAnimationFrame(paso);
      }
    };

    requestAnimationFrame(paso);
  }

  /** Cierra la camara. Se puede llamar aunque nunca haya arrancado. */
  detener() {
    this.#loopActivo = false;

    if (this.#stream) {
      for (const pista of this.#stream.getTracks()) pista.stop();
      this.#stream = null;
    }

    if (this.#video) {
      this.#video.srcObject = null;
    }
  }

  get activo() {
    return this.#loopActivo;
  }
}

/**
 * Traduce el error de `getUserMedia` a algo que el operador pueda hacer.
 *
 * Los codigos son del navegador y no dicen nada ("NotAllowedError"). El operador
 * necesita distinguir "no diste permiso" de "no hay camara" de "estas en otra
 * pagina", porque la accion es distinta en cada caso.
 */
function mensajeDePermiso(error) {
  switch (error?.name) {
    case 'NotAllowedError':
    case 'SecurityError':
      return (
        'No diste permiso para usar la camara. Habilitalo en el candado de la ' +
        'direccion, arriba a la izquierda, y volve a intentar.'
      );
    case 'NotFoundError':
    case 'OverconstrainedError':
      return 'No se encontro ninguna camara en este dispositivo.';
    case 'NotReadableError':
      return 'La camara esta ocupada por otra aplicacion. Cerrala e intenta de nuevo.';
    default:
      return `No se pudo abrir la camara: ${error?.message ?? 'error desconocido'}.`;
  }
}

/**
 * Formatea un codigo leido.
 *
 * Un lector puede devolver el codigo con espacios, guiones bajos o alatorios de
 * mayusculas alrededor. `device.serial_number` esta en MAYUSCULAS y sin espacios,
 * asi que sin normalizar un escaneo falla segun como suene el beep.
 */
export function normalizarCodigo(texto) {
  return String(texto ?? '').trim();
}
