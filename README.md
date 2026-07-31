# Job Scanner multi-sitio / multi-país

Busca directamente en los sitios de empleo (no vía Google) ofertas remotas
que calcen con tu stack, filtra automáticamente las que ya están cerradas
o que no son remotas, y arma un CSV único con todo. Incluye un segundo
script para bajar el texto completo de una oferta puntual, listo para
pegar en tu Project de Claude que arma currículums.

## Instalación para no-programadores (Windows)

Si no usas herramientas de desarrollo normalmente, esta es la vía más
directa:

1. Instalá **Python** desde [python.org/downloads](https://www.python.org/downloads/)
   (botón amarillo grande "Download Python"). En la primera pantalla del
   instalador, tildá la casilla **"Add python.exe to PATH"** antes de
   darle a instalar — si te la saltás, el resto no va a funcionar.
2. Descargá este proyecto: en GitHub, botón verde **"Code" → "Download ZIP"**,
   y descomprimilo en cualquier carpeta.
3. Adentro de esa carpeta, doble-click en **`iniciar_gui.cmd`**. La
   primera vez va a tardar un poco instalando lo que necesita — es
   normal. Después se va a abrir solo en el navegador.
4. Elegí país y las palabras clave de tu búsqueda en el panel de la
   izquierda, y hacé click en **"Buscar ofertas"**. Puede tardar varios
   minutos — dejalo corriendo y volvé más tarde a revisar los
   resultados en la tabla de abajo.

Si Windows muestra una advertencia de seguridad al abrir el `.cmd`
("Windows protegió su PC" / SmartScreen), es porque no está firmado
digitalmente (cuesta dinero y no aplica para un proyecto personal) —
click en "Más información" y después "Ejecutar de todas formas".

## Requisitos

```bash
pip install -r requirements.txt
```

(o `pip install requests beautifulsoup4 --break-system-packages` si solo
vas a usar la línea de comandos, sin GUI)

## GUI (opcional)

```bash
streamlit run gui.py
```

En Windows, `iniciar_gui.cmd` hace lo mismo con doble-click: revisa si
faltan dependencias, las instala solo si hace falta, y después abre la
GUI.

Abre una página local con: selección de país/sitios, edición de keywords
(con botón para generar el prompt de Claude, subiendo el CV en PDF/DOCX/
TXT/MD o pegando el texto), botón "Buscar ofertas" con progreso detallado
(sitio, keyword, cuántas ofertas lleva encontradas, tabla en vivo de lo
que va encontrando) y botón "Detener" para cortarlo a mitad de camino, un
log expandible con los errores que hayan salido, tabla de resultados
filtrable/exportable, y el extractor de descripción completa de una
oferta puntual — todo sin usar la línea de comandos.

**El scan corre como proceso aparte del sistema operativo**, lanzado con
`subprocess.Popen` y coordinado por dos archivos en esta carpeta:
`scan_status.json` (progreso, log y resultados en vivo, que
`scan_getonbrd.py` escribe y `gui.py` solo lee) y `detener.flag` (crearlo
corta el scan; la GUI lo crea al apretar "Detener"). Esto es a propósito:
el scan sigue corriendo aunque cierres el navegador, reinicies Streamlit,
o el código se esté editando mientras tanto — nada depende de objetos en
memoria de la página, que se pierden en cualquiera de esos casos. Usa las
mismas funciones de `scan_getonbrd.py` y `extraer_oferta.py`, así que
cualquier sitio/país nuevo que se agregue ahí aparece automático en la
GUI.

## Uso (línea de comandos)

```bash
python scan_getonbrd.py                             # país por defecto (Chile), todos sus sitios
python scan_getonbrd.py --pais Chile
python scan_getonbrd.py --pais Chile --solo laborum_cl   # re-correr solo un sitio puntual
python scan_getonbrd.py --keywords-file keywords.txt     # usar keywords desde archivo
python scan_getonbrd.py --generar-prompt-keywords         # imprime un prompt para pegar en Claude
                                                            # y generar keywords.txt (no escanea nada)
```

Genera `ofertas_empleos.csv` con columnas:
- `sitio`, `titulo`, `empresa`
- `keywords_match` (qué keyword(s) la trajeron)
- `remota` (SI/NO/? si el sitio no lo expone sin filtrar)
- `activa` (SI/NO)
- `motivo`
- `link`
- `fecha_creacion` (fecha en que se vio por primera vez esa oferta — se
  preserva entre corridas, no se pisa con la fecha del último scan)

Correr sin `--solo` **no borra** resultados de sitios que no corrieron esa
vez: el script conserva las filas ya guardadas de esos sitios y solo
reemplaza las de los sitios que efectivamente corrieron.

Cada sitio deduplica internamente por link: si la misma oferta aparece
buscando varios keywords distintos, se guarda una sola vez con todos los
keywords que matchearon juntos en `keywords_match`.

### Sitios soportados hoy

| País | Sitio | Cómo busca | Descripción en CSV |
|---|---|---|---|
| Chile | GetOnBrd | No tiene buscador por texto — recorre categorías fijas y visita cada oferta | Completa (visita cada oferta igual, así que se guarda de paso) |
| Chile | Computrabajo | Búsqueda por texto (`/trabajo-de-<keyword>`), modalidad en la tarjeta | Solo con `--descripcion-completa` |
| Chile | Laborum | SPA — API interna `POST /api/avisos/searchV2` | Completa (el JSON de búsqueda ya trae el campo `detalle`) |
| Chile | Trabajando.cl | SPA — API interna `GET /api/searchjob` | Snippet corto (~50 caracteres alrededor del keyword, es lo único que expone el buscador — no soporta `--descripcion-completa` todavía) |
| Chile | ChileTrabajos | Búsqueda por texto (`/encuentra-un-empleo?2=<keyword>`), modalidad en ícono de beneficio de la tarjeta | Resumen truncado por defecto; completa con `--descripcion-completa` |
| Colombia | Computrabajo Colombia | Misma plataforma que Chile, solo cambia el subdominio (`co.computrabajo.com`) | Solo con `--descripcion-completa` |
| Colombia | ElEmpleo | Búsqueda por texto (`/co/ofertas-empleo/trabajo-<keyword>`); agregando `/modalidad-remoto` al final filtra por remoto en el mismo request | Completa (viene en un atributo `data-offer-description` oculto en la tarjeta) |
| Colombia | GetOnBrd Colombia | Mismo pool de ofertas que GetOnBrd Chile (ver nota abajo) filtrado por texto | Completa (mismo mecanismo que GetOnBrd Chile) |
| Colombia | Magneto | Búsqueda por texto (`/co/trabajos/buscar/remoto/<keyword>`); agregando `remoto/` al principio filtra por remoto en el mismo request | Solo con `--descripcion-completa` |
| Colombia | SENA (Agencia Pública de Empleo, gobierno) | Búsqueda por texto (`/spe-web/spe/public/buscadorVacante?solicitudId=<keyword>`), modalidad como texto plano "Teletrabajo"/"No teletrabajo" en la tarjeta. Sitio público y lento | No priorizado (bajo volumen de ofertas remotas, no soporta `--descripcion-completa`) |
| Chile | BNE (Bolsa Nacional de Empleo, gobierno) | API JSON pública sin login (`/data/ofertas/buscarListas?textoLibre=<keyword>`). Sin campo de modalidad — se detecta buscando "remoto"/"teletrabajo" en título+descripción, que ya vienen completos en la respuesta | Completa (mismo campo que ya se usa para detectar "remoto") |
| Cualquiera | LinkedIn | Endpoint público "jobs-guest" (sin login, no oficial), recibe `location` según el país | Solo con `--descripcion-completa` |

**Sobre la columna `descripcion`**: por defecto se llena solo con lo que
cada sitio ya entrega en su propia respuesta de búsqueda/listado (JSON de
una API, o texto/atributos ya presentes en la tarjeta HTML), sin agregar
ninguna request extra por oferta — salvo GetOnBrd, que de todas formas
visita cada oferta para chequear si está cerrada o es remota, así que ahí
la descripción completa sale gratis. Por eso el nivel de detalle por
defecto varía tanto entre sitios: descripción completa (GetOnBrd, Laborum,
ElEmpleo, BNE) vs. un snippet corto (Trabajando.cl, ChileTrabajos) vs.
vacío (Computrabajo, Magneto, LinkedIn, SENA).

**Modo `--descripcion-completa`** (flag en CLI, checkbox "Descripción
completa (más lento)" en la GUI): para Computrabajo, ChileTrabajos,
LinkedIn y Magneto, visita cada oferta que ya pasó el filtro de
keyword/remoto y saca su descripción real con los mismos extractores de
`extraer_oferta.py`, igual que ya hacía GetOnBrd. Alarga el scan de esos
sitios (una request extra por oferta aceptada), así que queda apagado por
defecto. Trabajando.cl y SENA no lo soportan todavía — no tienen un
extractor de página de detalle funcionando (ver limitaciones abajo).

GetOnBrd **no** tiene una variante "rápida" que se salte la visita por
oferta: a diferencia del resto de los sitios, no tiene buscador por
texto, así que la única forma de saber si una oferta matchea algún
keyword, es remota o sigue activa es visitando su página — no es algo
opcional como en los demás sitios, donde esa visita solo estaba de más
para sacar la descripción.

**Sitios descartados**: `bumeran.com.co` (redirige a `laborum.cl` incluso
sin sesión iniciada, no es un sitio real aparte para Colombia) y
Konzerta (es de Panamá — la ruta `/co` da 404 propio de la SPA, no tiene
versión Colombia).

**Nota sobre GetOnBrd por país**: confirmado que el sitio no filtra por país
en el servidor (`?country=<x>` no cambia los resultados — es un pool único
para toda Latinoamérica). "GetOnBrd Colombia" reutiliza la misma función
que "GetOnBrd" Chile pero, además del keyword y remoto, revisa el texto
completo de cada oferta buscando el nombre del país/sus ciudades
principales (`GETONBRD_CIUDADES_POR_PAIS`). Una oferta también pasa el
filtro si dice explícitamente que está abierta a toda Latinoamérica, o si
no menciona ningún país/ciudad conocido (ej. tarjetas que solo dicen
"Remoto" sin especificar dónde) — en esos casos se asume abierta en vez
de excluirla. Esto significa que correr "GetOnBrd Colombia" vuelve a
descargar y revisar las mismas ~700 ofertas que "GetOnBrd" Chile (mismos
~20-25 min), solo que filtra distinto al final — es trabajo redundante
pero no hay forma de evitarlo dado que el sitio no soporta filtrar por
país en el servidor.

Detalle técnico de cada endpoint/selector está documentado como docstring
al inicio de `scan_getonbrd.py`.

## Extraer el texto completo de una oferta puntual

```bash
python extraer_oferta.py "<link-de-la-oferta>"
```

Baja la página y saca título + descripción completa lista para copiar al
Project de Claude. Cubre GetOnBrd, Computrabajo (Chile y Colombia),
ChileTrabajos, ElEmpleo, Magneto, BNE y LinkedIn. Pendientes: Laborum y
Trabajando.cl (son SPAs sin la descripción completa en HTML estático —
para esos dos, copiar el texto directo desde el navegador) y SENA
(no priorizado, bajo volumen de ofertas remotas).

## Editar búsqueda

- **keywords.txt** (opcional, una keyword por línea, `#` comenta la
  línea): si existe, se usa en vez de la lista por defecto. Se puede
  generar su contenido con `--generar-prompt-keywords`, pegando el
  prompt resultante en Claude.
- Si no existe `keywords.txt`, se usa `DEFAULT_KEYWORDS` al inicio del
  script (pre-cargada con un stack de ejemplo: automatización, python,
  selenium, platform engineer, soporte técnico, backup, data protection,
  tier 3, powershell).

## Agregar un país nuevo

1. Escribir las funciones `escanear_<sitio>(keywords, require_remote)` de
   cada sitio de ese país (mismo contrato que las de Chile: devuelven
   `list[dict]` con las claves de `CSV_FIELDS` salvo `sitio`).
2. Sumarlas a la lista `SITIOS`. Si la plataforma ya existe en otro país
   (como Computrabajo), no hace falta reescribir la función — se le puede
   pasar un `base_url`/`nombre_sitio` distinto vía `kwargs_extra` en el
   registro (ver la entrada `computrabajo_co` como ejemplo).
3. Agregar la entrada correspondiente en el dict `PAISES` con sus ids.

LinkedIn ya es reutilizable para cualquier país (recibe `location` como
parámetro) — no hace falta reescribirlo.

**Pendiente**: Magneto (Colombia) y otros países además de Chile/Colombia
— mismo proceso de investigación que se usó para los sitios actuales.

## Limitaciones conocidas / mantenimiento

- **Selectores HTML frágiles**: cualquier sitio puede cambiar su
  estructura en cualquier momento. Si un sitio deja de traer resultados,
  hay que inspeccionar el HTML/API actual y ajustar el selector.
- **APIs no oficiales**: Laborum, Trabajando.cl y LinkedIn se acceden vía
  endpoints internos/no documentados (no hay alternativa pública). Pueden
  cambiar o dejar de funcionar sin aviso.
- **Rate limiting**: 1.5s de delay entre requests, y los sitios se
  escanean en **round robin** (un request por sitio por turno, no un
  sitio entero seguido antes de pasar al siguiente) — así ningún sitio
  individual recibe una ráfaga larga de requests seguidos, sin alargar
  el tiempo total del scan. Si algún sitio empieza a bloquear el
  user-agent o la IP igual, hay que subir el delay o rotar user-agents.
- **No hace login**: solo lee resultados públicos.

## Coordinación con el Project de Claude (armado de CVs)

No hay integración en vivo — es un puente manual en dos puntos:
1. `--generar-prompt-keywords` → pegas el prompt en Claude → guardas la
   respuesta en `keywords.txt`.
2. `extraer_oferta.py <link>` → texto limpio de la oferta → lo pegas en
   el Project de Claude que arma el CV.
