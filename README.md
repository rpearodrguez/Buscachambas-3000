# Job Scanner multi-sitio / multi-país

Busca directamente en los sitios de empleo (no vía Google) ofertas remotas
que calcen con tu stack, filtra automáticamente las que ya están cerradas
o que no son remotas, y arma un CSV único con todo. Incluye un segundo
script para bajar el texto completo de una oferta puntual, listo para
pegar en tu Project de Claude que arma currículums.

## Requisitos

```bash
pip install requests beautifulsoup4 --break-system-packages
```

## GUI (opcional)

```bash
pip install streamlit pandas pypdf python-docx pyperclip
streamlit run gui.py
```

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

Correr sin `--solo` **no borra** resultados de sitios que no corrieron esa
vez: el script conserva las filas ya guardadas de esos sitios y solo
reemplaza las de los sitios que efectivamente corrieron.

### Sitios soportados hoy (Chile)

| Sitio | Cómo busca |
|---|---|
| GetOnBrd | No tiene buscador por texto — recorre categorías fijas y visita cada oferta |
| Computrabajo | Búsqueda por texto (`/trabajo-de-<keyword>`), modalidad en la tarjeta |
| Laborum | SPA — API interna `POST /api/avisos/searchV2` |
| Trabajando.cl | SPA — API interna `GET /api/searchjob` |
| ChileTrabajos | Búsqueda por texto (`/encuentra-un-empleo?2=<keyword>`), modalidad en ícono de beneficio de la tarjeta |
| LinkedIn | Endpoint público "jobs-guest" (sin login, no oficial) |

Detalle técnico de cada endpoint/selector está documentado como docstring
al inicio de `scan_getonbrd.py`.

## Extraer el texto completo de una oferta puntual

```bash
python extraer_oferta.py "<link-de-la-oferta>"
```

Baja la página y saca título + descripción completa lista para copiar al
Project de Claude. Cubre GetOnBrd, Computrabajo y LinkedIn (Laborum y
Trabajando.cl son SPAs sin la descripción completa en HTML estático —
para esos dos, copiar el texto directo desde el navegador).

## Editar búsqueda

- **keywords.txt** (opcional, una keyword por línea, `#` comenta la
  línea): si existe, se usa en vez de la lista por defecto. Se puede
  generar su contenido con `--generar-prompt-keywords`, pegando el
  prompt resultante en Claude.
- Si no existe `keywords.txt`, se usa `DEFAULT_KEYWORDS` al inicio del
  script (pre-cargada con el stack de Richard: automatización, python,
  selenium, platform engineer, soporte técnico, backup, data protection,
  tier 3, powershell).

## Agregar un país nuevo

1. Escribir las funciones `escanear_<sitio>(keywords, require_remote)` de
   cada sitio de ese país (mismo contrato que las de Chile: devuelven
   `list[dict]` con las claves de `CSV_FIELDS` salvo `sitio`).
2. Sumarlas a la lista `SITIOS`.
3. Agregar la entrada correspondiente en el dict `PAISES` con sus ids.

LinkedIn ya es reutilizable para cualquier país (recibe `location` como
parámetro) — no hace falta reescribirlo.

**Pendiente**: Colombia — falta investigar sus sitios más usados
(Computrabajo Colombia probablemente funciona igual que la versión
chilena, ElEmpleo.com y Magneto hay que investigarlos desde cero, como
hicimos acá con Laborum/Trabajando.cl).

## Limitaciones conocidas / mantenimiento

- **Selectores HTML frágiles**: cualquier sitio puede cambiar su
  estructura en cualquier momento. Si un sitio deja de traer resultados,
  hay que inspeccionar el HTML/API actual y ajustar el selector.
- **APIs no oficiales**: Laborum, Trabajando.cl y LinkedIn se acceden vía
  endpoints internos/no documentados (no hay alternativa pública). Pueden
  cambiar o dejar de funcionar sin aviso.
- **Rate limiting**: 1.5s de delay entre requests por sitio. Si algún
  sitio empieza a bloquear el user-agent o la IP, hay que subir el delay
  o rotar user-agents.
- **No hace login**: solo lee resultados públicos.

## Coordinación con el Project de Claude (armado de CVs)

No hay integración en vivo — es un puente manual en dos puntos:
1. `--generar-prompt-keywords` → pegas el prompt en Claude → guardas la
   respuesta en `keywords.txt`.
2. `extraer_oferta.py <link>` → texto limpio de la oferta → lo pegas en
   el Project de Claude que arma el CV.
