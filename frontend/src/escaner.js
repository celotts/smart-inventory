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

/**
 * Cuanto tiempo se ignora un codigo que ya se leyo, en modo continuo.
 *
 * Es el tiempo minimo entre dos escaneos del MISMO valor. Un barrido con la
 * camara sobre una etiqueta produce decenas de lecturas por segundo; sin este
 * filtro, un solo equipo se registraria dozens de veces.
 *
 * TRES SEGUNDOS
 * -------------
 * Es lo que tarda un operador en pasar de una etiqueta a otra. Un valor mas
 * corto deja pasar el doble escaneo de la misma etiqueta; uno mas largo hace
 * que un codigo repetido a proposito tarde en registrarse.
 */
const COOLDOWN_MS = 3000;

export class LectorQR {
  #stream = null;
  #detector = null;
  #loopActivo = false;
  #formats = [];

  #video = null;

  // Modo continuo: el lector NO se detiene al leer, y el mismo codigo se
  // ignora durante `COOLDOWN_MS`.
  #continuo = false;
  #ultimoValor = null;
  #ultimoMomento = 0;

  /**
   * @param {HTMLElement} contenedor  Donde se inserta el `<video>`.
   * @param {object} [opciones]
   * @param {boolean} [opciones.continuo]  Si es true, el lector sigue
   *   escaneando despues de leer, en vez de apagarse.
   *
   * El lector crea su propio `<video>` en vez de recibirlo: asi el `<video>`
   * nunca puede quedar apuntando a otro lado si la vista se vuelve a montar,
   * y `detener()` siempre sabe exactamente que apagar.
   */
  constructor(contenedor, { continuo = false } = {}) {
    this.#video = document.createElement('video');
    this.#video.setAttribute('playsinline', ''); // iOS: sin esto abre a pantalla completa
    this.#video.muted = true;
    this.#video.hidden = true;
    contenedor.append(this.#video);

    this.#continuo = continuo;
  }

  /** El `<video>`, para poder mostrarlo u ocultarlo desde la vista. */
  get elementoVideo() {
    return this.#video;
  }

  /**
   * Los formatos que el navegador negoció con el detector.
   *
   * No es lo mismo lo que la app pide y lo que el navegador concede: Chrome en
   * macOS no anuncia `upc_a`, por ejemplo. Cuando una etiqueta no se lee, esto
   * es lo primero que hay que ver: dice si el formato de la etiqueta está entre
   * los que el navegador puede decodificar en ESTE equipo.
   *
   * @returns {string[]}
   */
  get formatos() {
    return [...this.#formats];
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
          // RESOLUCION, Y POR QUE ES EL UNICO AJUSTE QUE IMPORTA PARA LAS
          // BARRAS
          //
          // Medido en este repo con un Code 128 real: el detector deja de leerlo
          // cuando el codigo ocupa menos de 240px de los 1280 del cuadro, y lo
          // lee sin problema a 320, 400, 480... O sea, el limite no es el formato:
          // es la cantidad de pixeles que caen sobre el codigo.
          //
          // Por eso se pide 1080p y no 720p. Duplicar la resolucion duplica los
          // pixeles por barra, y con eso una etiqueta que antes habia que poner a
          // 10cm del lente se lee a la distancia normal de trabajo. Es la misma
          // diferencia entre "hay que acercar mucho" y "se lee como una QR".
          //
          // `ideal`, no `exact`: se pide 1080p pero el navegador puede entregar
          // otra cosa segun el dispositivo. Forzar con `exact` hace fallar la
          // camara entera en equipos que no pueden.
          //
          // El costo es real y es el que se esta pagando: mas pixeles significan
          // mas decodificacion por frame, y en un celular eso es bateria y calor.
          // 4K dejaria de lado por eso.
          width: { ideal: 1920 },
          height: { ideal: 1080 },
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
    let fallosSeguidor = 0;
    let falloAvisado = false;

    const paso = async () => {
      if (!this.#loopActivo) return;

      // El video tiene que tener dimensiones antes de detectar: en el primer
      // frame todavia puede estar en 0x0, y `detect()` sobre eso lanza.
      if (!leyendo && this.#video.readyState >= 2 && this.#video.videoWidth > 0) {
        leyendo = true;
        try {
          // `detect`, no `detectar`: `BarcodeDetector` es una API del navegador
          // con un unico metodo, en ingles. Llamar a `detectar` tira un TypeError
          // ("is not a function") que caia en el `catch` de abajo y se
          // confundia con "este frame no se pudo decodificar": el bucle seguia
          // reintentando en silencio y el escaner no leia NINGUN codigo, con la
          // camara encendida y el encuadre perfecto.
          const codigos = await this.#detector.detect(this.#video);
          fallosSeguidor = 0;
          falloAvisado = false;

          if (codigos.length > 0 && this.#loopActivo) {
            // Se toma el primero. Un frame con dos codigos es raro y adivinar
            // cual es el correcto seria peor que mostrar el primero y dejar que
            // el operador lo confirme.
            const valor = (codigos[0].rawValue ?? '').trim();
            if (valor) {
              if (this.#continuo) {
                // MODO CONTINUO: la camara sigue encendida y el operador
                // sigue escaneando, que es como se trabaja en un deposito.
                //
                // El mismo codigo se ignora durante `COOLDOWN_MS`: la etiqueta
                // sigue en el encuadre y el detector la vuelve a leer unas
                // 30 veces por segundo. Sin este filtro, un solo barrido
                // registraria el mismo equipo treinta veces.
                //
                // El filtro es POR VALOR: una etiqueta nueva en el encuadre se
                // registra al instante.
                const ahora = Date.now();
                if (valor !== this.#ultimoValor || ahora - this.#ultimoMomento > COOLDOWN_MS) {
                  this.#ultimoValor = valor;
                  this.#ultimoMomento = ahora;
                  alLeer(valor);
                }
              } else {
                // Se detiene ANTES de llamar al callback: seguir leyendo frames
                // mientras la app procesa el escaneo produce diez escaneos del
                // mismo codigo en un segundo.
                this.detener();
                alLeer(valor);
                return;
              }
            }
          }
        } catch (error) {
          // Un frame que falla al decodificar es normal (movimiento, foco).
          // Se ignora y se sigue con el siguiente.
          //
          // PERO no todos los errores son eso: un `TypeError` aca significa que
          // el detector esta roto, y con el `catch` vacío eso se traduce en un
          // escaner que jamas lee un codigo y no deja rastro. Al tercer frame
          // seguido se avisa una vez por sesion, para que quede en la consola.
          fallosSeguidor++;
          if (fallosSeguidor === 3 && !falloAvisado) {
            falloAvisado = true;
            console.error('El detector de codigos esta fallando:', error);
          }
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

  /**
   * Forget the last value read, so the SAME code can be read again right away.
   *
   * It is used after a manual scan: the operator typed a code that the camera
   * had just read, and the app has to accept it even if it is the same as the
   * previous one.
   */
  olvidarUltimo() {
    this.#ultimoValor = null;
    this.#ultimoMomento = 0;
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
