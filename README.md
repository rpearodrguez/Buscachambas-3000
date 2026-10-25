# Job Scanner multi-sitio / multi-país

Busca directamente en los sitios de empleo (no vía Google) ofertas remotas
que calcen con tu stack, filtra automáticamente las que ya están cerradas
o que no son remotas, y arma un CSV único con todo. Incluye un segundo
script para bajar el texto completo de una oferta puntual, listo para
pegar en tu Project de Claude que arma currículums.

## Dos formas de usarlo

| | **Local** (tu PC) | **En la nube** (varias personas) |
|---|---|---|
| Quién lo usa | tú | tú y las personas que invites |
| Dónde corre el escaneo | en tu PC | en un job de Google Cloud Run |
| Dónde quedan los datos | archivos en la carpeta del proyecto | un bucket privado, una carpeta por persona |
| Acceso | sin login | login con Google + lista de usuarios |
| Cómo se abre | doble clic en `iniciar_gui.cmd` | la página publicada en Streamlit Cloud |

**La versión en la nube no reemplaza a la local:** la local sigue funcionando
igual, sin cuenta de Google ni nada de la nube (ver
[Seguir usándolo en local](#seguir-usándolo-en-local)). Una y otra no comparten
datos: lo que escaneas en tu PC queda en tu carpeta, y lo que se escanea en la nube
queda en el bucket.

---

# Uso en local

## Instalación para no-programadores (Windows)

Si no usas herramientas de desarrollo normalmente, esta es la vía más
directa:

1. Instala **Python** desde [python.org/downloads](https://www.python.org/downloads/)
   (botón amarillo grande "Download Python"). En la primera pantalla del
   instalador, marca la casilla **"Add python.exe to PATH"** antes de
   darle a instalar: si te la saltas, el resto no va a funcionar.
2. Descarga este proyecto: en GitHub, botón verde **"Code" → "Download ZIP"**,
   y descomprímelo en cualquier carpeta.
3. Dentro de esa carpeta, haz doble clic en **`iniciar_gui.cmd`**. La
   primera vez va a tardar un poco instalando lo que necesita; es
   normal. Después se abre solo en el navegador.
4. Elige país y las palabras clave de tu búsqueda en el panel de la
   izquierda, y haz clic en **"Buscar ofertas"**. Puede tardar varios
   minutos: déjalo corriendo y vuelve más tarde a revisar los
   resultados en la tabla de abajo.

Si Windows muestra una advertencia de seguridad al abrir el `.cmd`
("Windows protegió su PC" / SmartScreen), es porque no está firmado
digitalmente (cuesta dinero y no aplica para un proyecto personal):
haz clic en "Más información" y después en "Ejecutar de todas formas".

## Seguir usándolo en local

Todo lo de la nube es **opcional**. Para usar la herramienta solo en tu PC:

- **Lo más simple: `iniciar_gui.cmd`.** Fuerza el modo local (define
  `BUSCACHAMBAS_MODO=local`), así que ignora cualquier configuración de la
  nube que haya en `.streamlit/secrets.toml`: sin login, sin cuotas, sin Google
  Cloud. El escaneo corre en tu PC y los datos quedan en esta carpeta
  (`ofertas_empleos.csv`, `keywords.txt`, `scan_status.json`...).
- **A mano:** `streamlit run app/gui.py`. Arranca en modo local mientras no exista
  `.streamlit/secrets.toml` con la configuración de la nube. Si existe y quieres
  forzar el modo local, define antes la variable. En PowerShell:
  ```powershell
  $env:BUSCACHAMBAS_MODO = "local"
  streamlit run app/gui.py
  ```
- **Línea de comandos:** `python escaner/scan_getonbrd.py` es siempre local, salvo que
  le pases `--storage` (ver [Uso por línea de comandos](#uso-línea-de-comandos)).

El aviso por correo también funciona en local, con tu propia cuenta de correo
(ver más abajo). Lo único de la nube que no existe en local son el login, la
lista de usuarios, las cuotas y las alertas al administrador.

## Requisitos

```bash
pip install -r requirements.txt
```

(o `pip install requests beautifulsoup4 --break-system-packages` si solo
vas a usar la línea de comandos, sin GUI)

Hay otros archivos de dependencias, cada uno para un uso distinto:

| Archivo | Para qué |
|---|---|
| `requirements.txt` | usar la herramienta en local (GUI y línea de comandos) |
| `requirements-cloud.txt` | además, probar la versión en la nube desde tu PC |
| `requirements-admin.txt` | además, la herramienta de administración |
| `app/requirements.txt` | lo que instala Streamlit Cloud al publicar la GUI (no hace falta instalarlo a mano) |

## Estructura del proyecto

```
app/            gui.py                  GUI (Streamlit): streamlit run app/gui.py
                requirements.txt        dependencias que instala Streamlit Cloud
escaner/        scan_getonbrd.py        el scanner (línea de comandos y job de la nube)
                extraer_oferta.py       texto completo de una oferta puntual
administracion/ admin.py                herramienta de administración, solo en tu PC
                admin_nube.py           operaciones en Google Cloud (job, secretos, correo, gastos)
                chequeo_conexion.py     chequeos previos de la conexión con Google Cloud
                gasto.py, gasto_grafico.py            lógica y gráfico de los gastos
                actividad.py, actividad_grafico.py    escaneos por día/semana/mes/año (la lógica; falta la pantalla)
comun/          almacen.py              archivos en disco o en el bucket, con la misma interfaz
                lanzador.py             lanza el job y detecta un escaneo sin señales de vida
                acceso.py, usuarios.py  login, lista de usuarios, cuotas y candados
                limpieza.py             qué se puede borrar del bucket (y la guarda que impide borrar lo demás)
cloud/          Dockerfiles y cloudbuild del scanner y de la sonda de IPs
tests/          python -m unittest discover -s tests
administrar.bat menú de administración (ver más abajo)
iniciar_gui.cmd abre la GUI en modo local
```

Los puntos de entrada se ejecutan por su ruta (`streamlit run app/gui.py`,
`python escaner/scan_getonbrd.py`) desde cualquier carpeta: cada uno agrega la
raíz del repo al path para importar los demás paquetes. Tus datos locales
(`ofertas_empleos.csv`, `keywords.txt`, `email_config.json`, `admin_local.json`,
`.streamlit/secrets.toml`) viven en la **raíz** del repo; la GUI y el admin se
posicionan ahí al arrancar.

## La GUI en local

```bash
streamlit run app/gui.py
```

En Windows, `iniciar_gui.cmd` hace lo mismo con doble clic: revisa si
faltan dependencias, las instala solo si hace falta, y después abre la
GUI.

Abre una página local con: selección de país/sitios, edición de keywords
(con botón para generar el prompt de Claude, subiendo el CV en PDF/DOCX/
TXT/MD o pegando el texto), botón "Buscar ofertas" con progreso detallado
(sitio, keyword, cuántas ofertas lleva encontradas, tabla en vivo de lo
que va encontrando) y botón "Detener" para cortarlo a mitad de camino, un
log expandible con los errores que hayan salido, tabla de resultados
filtrable/exportable, y el extractor de descripción completa de una
oferta puntual, todo sin usar la línea de comandos.

**El escaneo corre como proceso aparte del sistema operativo**, lanzado con
`subprocess.Popen` y coordinado por dos archivos en esta carpeta:
`scan_status.json` (progreso, log y resultados en vivo, que
`escaner/scan_getonbrd.py` escribe y `app/gui.py` solo lee) y `detener.flag` (crearlo
corta el escaneo; la GUI lo crea al apretar "Detener"). Esto es a propósito:
el escaneo sigue corriendo aunque cierres el navegador, reinicies Streamlit,
o el código se esté editando mientras tanto: nada depende de objetos en
memoria de la página, que se pierden en cualquiera de esos casos. Usa las
mismas funciones de `escaner/scan_getonbrd.py` y `escaner/extraer_oferta.py`, así que
cualquier sitio/país nuevo que se agregue ahí aparece automático en la
GUI.

Mientras el escaneo corre, la GUI muestra el tiempo transcurrido; al
terminar deja "Duración del último escaneo" junto al resultado (también
queda en `scan_status.json` como `duracion_seg`, y la línea final de la
consola la imprime). Detener a mitad de camino **conserva** las ofertas que ya
se habían encontrado. Los resultados de cada sitio que corre **reemplazan** los
que ese sitio tenía guardados (los de sitios que no corrieron se conservan).

## Aviso por correo (local)

Para recibir un correo cuando termine (aunque hayas cerrado el
navegador): en la barra lateral, "Aviso por correo", completa el correo
que recibirá el aviso y los datos SMTP de la cuenta que envía (servidor,
puerto, usuario, contraseña) y aprieta "Guardar y probar", que manda un
correo de prueba. Se guarda en `email_config.json` (está en `.gitignore`,
tiene la contraseña; también se puede crear a mano a partir de
`email_config.example.json`). En Gmail hay que usar una **contraseña de
aplicación** (cuenta de Google → Seguridad → verificación en 2 pasos →
Contraseñas de aplicaciones), no la clave normal. Después marca "Enviar
correo al terminar el scan". Por consola:
`python escaner/scan_getonbrd.py --email-a tu@correo.com`.

El correo trae la duración, los totales y la lista de ofertas nuevas, y adjunta
`ofertas_empleos.csv` con todos los resultados guardados; si el escaneo se detiene
con el botón, también avisa. Si el envío falla, el escaneo igual queda
guardado y la GUI muestra el error. Si se te olvidó marcar el aviso, al terminar
la GUI ofrece el botón **«Enviar este resultado por correo»**, que lo manda
**una sola vez por escaneo** (una marca en el almacenamiento lo impide aunque
hagas doble clic; si el envío falla, se libera para reintentar).

---

# Versión en la nube

## Cómo está armada

```
 Navegador ──► Streamlit Cloud (app/gui.py) ──lanza──► Cloud Run Job (escaner/scan_getonbrd.py) ──► sitios de empleo
                        │                                        │
                        └──────────► Bucket privado ◄────────────┘
                                     estado, CSV, keywords y preferencias: una carpeta por persona

 Tu PC:  administracion/admin.py  (con TUS credenciales de Google)  ──►  job, bucket, Secret Manager, BigQuery
```

- **La página** (Streamlit Cloud) solo muestra el avance y lanza el job: no tiene
  la contraseña del correo ni permiso para cambiar la configuración.
- **El job** corre el escaneo en Google Cloud (hasta 2 horas), aunque cierres
  el navegador, y es el único que manda correos (la contraseña va en Secret Manager).
- **El bucket** es el punto de encuentro: la página y el job se coordinan por archivos en él.
- **La herramienta de administración** corre solo en tu PC, con tus credenciales.

Está pensado para caber en el free tier de Google (Cloud Run, Cloud Storage,
Secret Manager). Verifica los límites vigentes en las páginas de precios de Google.

## Almacenamiento y seguridad

Por defecto el estado del escaneo, el flag de detener, las keywords y el CSV son
archivos en la carpeta del proyecto. En la nube viven en un bucket de Cloud
Storage, con una carpeta por persona: `python escaner/scan_getonbrd.py --storage gs://<bucket>/<carpeta>`
(la GUI usa `BUSCACHAMBAS_STORAGE=gs://<bucket>`; requiere `pip install -r requirements-cloud.txt`).
El código vive en `comun/almacen.py`.

**Seguridad (el repo es público: nada de esto va aquí, solo en variables de
entorno o en los secrets de la plataforma):**
- El bucket tiene que ser privado, y el código lo exige: antes de usarlo
  verifica que tenga la *prevención de acceso público* en `enforced` y el
  *acceso uniforme a nivel de bucket*; si no es así, o no puede verificarlo,
  se niega a usarlo.
  ```
  gcloud storage buckets create gs://<bucket> --location=<región> \
    --uniform-bucket-level-access --public-access-prevention
  ```
  Para verificarlo: `gcloud storage buckets describe gs://<bucket> --format="value(public_access_prevention,uniform_bucket_level_access)"`
  debe dar `enforced` y `True`. La cuenta de servicio necesita
  `roles/storage.objectUser` sobre ese bucket y, para poder verificarlo,
  `roles/storage.legacyBucketReader`; nada más.
- La carpeta de cada persona sale de un hash de su correo, calculado en el
  servidor; la persona nunca elige la ruta.
- Los mensajes de error y los logs que ve la persona no incluyen nombres de
  bucket, rutas ni detalles de la infraestructura (van solo al log del servidor).
- Credenciales (`email_config.json`, llaves de cuentas de servicio,
  `.streamlit/secrets.toml`, `.env`, `admin_local.json`) están en `.gitignore` y `.gcloudignore`.

## Login, usuarios y cuotas

Con `BUSCACHAMBAS_JOB` y `BUSCACHAMBAS_STORAGE` definidos (variables de
entorno o secrets; plantilla en `.streamlit/secrets.example.toml`), la GUI
exige login con Google (`st.login`, necesita `Authlib` y `httpx`):

- Entran solo los correos de la lista de usuarios (`admin/usuarios.json` en el
  bucket, que se edita desde `administracion/admin.py`), con el correo verificado por Google.
  Sin lista, sin correo verificado o ante cualquier duda, no se entra.
- **Cuota:** escaneos por semana por persona (ventana móvil de 7 días; 1 por
  defecto; los administradores no tienen tope). **Un escaneo a la vez** por
  persona (candado atómico en el bucket) y un **tope global** de escaneos
  simultáneos (`BUSCACHAMBAS_MAX_SIMULTANEOS`, 2 por defecto).
- **Primera sesión vacía:** una persona nueva no ve ninguna keyword (las de ejemplo del código no
  se le muestran) y parte con todos los sitios marcados. Después se le recuerdan por persona el
  país, los sitios, "solo remotas" y el aviso por correo (`preferencias.json` en su carpeta).
  Las **keywords no se recuerdan** salvo que las guarde con el botón "Guardar keywords".
- **País por defecto, por persona:** el administrador lo asigna a cada persona en la pestaña *Usuarios* de
  `administracion/admin.py`; solo vale hasta que esa persona tenga elecciones guardadas.

## Correos

**A la persona que escanea** (siempre al correo con el que inició sesión; nadie puede elegir otro destinatario):
- El aviso al terminar, si marcó la casilla, y el botón **«Enviar este resultado por correo»** si se le olvidó
  (una sola vez por escaneo). Lo manda el job, con el mismo resumen y el CSV adjunto.

**Al administrador, cuando un escaneo se cae:**
- El job manda un correo con quién lo lanzó, el país y los sitios, la **traza del error** y el **log
  completo de la ejecución adjunto** (`log_escaneo.txt`). Si el almacenamiento no se puede usar, también avisa.
- La persona no ve la traza: solo "El escaneo terminó con un error inesperado. El administrador fue
  avisado por correo." (la traza y el log van solo a tu correo).
- El correo del administrador se configura en la pestaña *Correo (SMTP)* de `administracion/admin.py`
  (queda en la variable `BUSCACHAMBAS_ADMIN_EMAIL` del job); "Guardar y probar alerta" manda una alerta de
  ejemplo con un log de ejemplo. Vacío = nadie recibe avisos.
- **No cubre** el caso en que Google mata el job sin darle tiempo a avisar (se pasa de las 2 horas o se
  queda sin memoria). Para eso serviría una alerta de Cloud Monitoring sobre ejecuciones fallidas (pendiente).

## Publicar y mantener

**La página (Streamlit Community Cloud):**
- Repositorio de este proyecto, rama `master`, archivo principal `app/gui.py`. Instala las dependencias
  de `app/requirements.txt`.
- En *Secrets* se pega el contenido de la plantilla `.streamlit/secrets.example.toml` ya rellenada
  (bucket, job, sección `[auth]` con el cliente OAuth de Google y `[gcp_service_account]`).
  Ese contenido nunca va al repo.
- El cliente OAuth de Google necesita estas direcciones de retorno: `http://localhost:8501/oauth2callback`
  (para probar en tu PC) y `https://<tu-app>.streamlit.app/oauth2callback`. Con la app de OAuth en modo
  *Testing* hay que agregar a cada persona también como usuario de prueba en la consola de Google, además
  de la lista de usuarios de la herramienta (la lista de la herramienta es la que decide quién entra).
- Streamlit Cloud redespliega la página solo con cada `git push`.

**El job (Cloud Run): hay que actualizarlo a mano.** El job **no** se redespliega solo: cada vez que
cambien `escaner/` o `comun/` (por ejemplo, una opción nueva de la línea de comandos), hay que reconstruir la
imagen y actualizar el job justo después de subir, o la página nueva llamará a un job viejo y fallará:

```bash
gcloud builds submit --config cloud/scanner/cloudbuild.yaml \
  --substitutions=_REGION=<región-del-repositorio>,_REPO=<repositorio-de-imágenes> .
gcloud run jobs deploy <job> --image <región>-docker.pkg.dev/<proyecto>/<repositorio-de-imágenes>/scanner \
  --region <región-del-job> --service-account <cuenta-del-job> \
  --cpu 1 --memory 512Mi --max-retries 0 --task-timeout 2h
```

Las variables y el secreto del correo del job se conservan al actualizarlo. Variables que usa el job:
`BUSCACHAMBAS_SMTP_HOST`, `BUSCACHAMBAS_SMTP_PORT`, `BUSCACHAMBAS_SMTP_USER`, `BUSCACHAMBAS_SMTP_PASSWORD`
(esta viene de Secret Manager) y `BUSCACHAMBAS_ADMIN_EMAIL`; todas se cambian desde la herramienta de administración.

**Configuración de la página** (variables de entorno o secrets): `BUSCACHAMBAS_STORAGE`, `BUSCACHAMBAS_JOB`,
`BUSCACHAMBAS_MAX_SIMULTANEOS` (opcional), `BUSCACHAMBAS_GCP_KEYFILE` (solo en tu PC: ruta al archivo de la
llave; en Streamlit Cloud va la sección `[gcp_service_account]`) y `[auth]`. `BUSCACHAMBAS_MODO=local` fuerza el
modo local aunque estén las anteriores.

---

# Administración (solo en tu PC)

`administracion/admin.py` es una herramienta local para administrar la versión en la nube
sin escribir comandos. Corre con **tus** credenciales de Google, no con las de la GUI pública, que
no tiene ningún permiso para cambiar esta configuración.

**Lo más simple: doble clic en `administrar.bat`.** Abre un menú que hace todo lo de abajo por ti:
abrir la herramienta (antes revisa la conexión con Google Cloud), solo revisar la conexión, iniciar
sesión en Google Cloud, instalar las dependencias, correr las pruebas y ver qué hay en la carpeta.
No guarda nada sensible: la conexión vive en `admin_local.json` (en `.gitignore`) y el acceso a
Google en las credenciales de `gcloud`, ambos fuera del repositorio. La herramienta se abre en
`http://localhost:8510`, aparte de la GUI (8501), para que no se pisen.

Los chequeos previos (`administracion/chequeo_conexion.py`, solo lectura, se puede correr solo)
revisan: `gcloud` instalado, sesión, credenciales de aplicación, conexión guardada, bucket privado,
job, correo del administrador, secreto del correo, lista de usuarios y si hay algo que limpiar en el
bucket; y dicen qué hacer si algo falla.

A mano, sin el menú:

```
pip install -r requirements.txt -r requirements-admin.txt
gcloud auth application-default login      # una vez, con tu cuenta de Google
streamlit run administracion/admin.py --server.address localhost --server.port 8510
```

Pestañas:

- **Conexión:** proyecto, región, job, bucket, nombre del secreto y (opcional) la tabla de facturación.
  Se guardan en `admin_local.json` (en `.gitignore`; el repo es público).
- **Correo (SMTP):** servidor, puerto y usuario se escriben en las variables del job; el token va a Secret
  Manager sin mostrarse y no se puede volver a leer (se conservan la versión nueva y la anterior). Aquí
  también va el **correo del administrador** para las alertas de fallos. "Guardar y probar correo" y
  "Guardar y probar alerta" ejecutan el job, así se prueba el camino real.
- **Usuarios:** lista de correos autorizados, con rol, cuota de escaneos por semana (1 por defecto) y país
  por defecto de cada uno. Se edita eligiendo a la persona (sin perder el resto de su ficha) y no deja la
  lista sin ningún administrador. Vive en la parte `admin/` del bucket privado.
- **Gastos:** dos vistas del gasto por servicio (barras apiladas, bruto o neto, por proyecto): **Últimos días**,
  con la variación contra el período anterior de igual duración, y **Por mes**, con la tendencia mes a mes. Las
  variaciones de totales solo se calculan entre meses con datos de casi todos sus días (si no, darían números sin
  sentido) y se agrega el promedio por día, que sí es comparable con un mes a medias. Trae el detalle por concepto
  (SKU y región), con marcas de *egreso de datos* y *región de Europa*, y «qué explica el cambio» entre dos períodos.
  Lee la **exportación de facturación a BigQuery**, que se activa una sola vez en la consola de
  Facturación (*Billing export → BigQuery export → Standard usage cost*); el nombre de la tabla
  `gcp_billing_export_v1_…` se pega en la pestaña Conexión. La exportación tarda en ponerse al día (horas) y
  puede traer historial parcial. El neto es lo que realmente se paga: el free tier aparece como créditos o
  descuentos que compensan el uso. Lógica en `administracion/gasto.py`, gráfico en `administracion/gasto_grafico.py`.
- **Limpieza:** revisa qué hay en el bucket (cuánto pesa, de quién es cada carpeta) y propone borrar solo lo que
  sobra: carpetas de personas que ya no están en la lista de usuarios, candados y banderas temporales colgados,
  archivos de funciones que ya no existen y estados de escaneos que murieron sin cerrarse. **Nada se borra sin
  vista previa y confirmación** (casilla + escribir `BORRAR`), y la lista de usuarios y todo lo de las personas
  vigentes están protegidos por una guarda (`comun/limpieza.py`: si un solo nombre de la lista a borrar no pasa,
  no se borra ninguno). Puede activar una regla del bucket que borra sola los candados y banderas con más de un
  día; los datos de las personas nunca van en una regla automática. Google guarda lo borrado unos días (borrado
  suave), así que un error se puede recuperar desde su consola.

---

## Pruebas

```bash
python -m unittest discover -s tests
```

Cubren la lógica sin tocar la nube (almacenamiento con un cliente falso, correos con un SMTP falso, la guarda de
la limpieza con rutas maliciosas, las reglas de acceso y cuotas, los gastos y las alertas). `administrar.bat`
(opción 5) las corre con doble clic.

---

# Uso (línea de comandos)

```bash
python escaner/scan_getonbrd.py                             # país por defecto (Chile), todos sus sitios
python escaner/scan_getonbrd.py --pais Chile
python escaner/scan_getonbrd.py --pais Chile --solo laborum_cl   # re-correr solo un sitio puntual
python escaner/scan_getonbrd.py --keywords-file keywords.txt     # usar keywords desde archivo
python escaner/scan_getonbrd.py --generar-prompt-keywords         # imprime un prompt para pegar en Claude
                                                            # y generar keywords.txt (no escanea nada)
```

Genera `ofertas_empleos.csv` con columnas:
- `sitio`, `titulo`, `empresa`
- `keywords_match` (qué keyword(s) la trajeron)
- `remota` (SI/NO/? si el sitio no lo expone sin filtrar)
- `activa` (SI/NO)
- `motivo`
- `link`
- `fecha_creacion` (fecha en que se vio por primera vez esa oferta; se
  preserva entre escaneos, no se pisa con la fecha del último scan)

Correr sin `--solo` **no borra** resultados de sitios que no corrieron esa
vez: el script conserva las filas ya guardadas de esos sitios y solo
reemplaza las de los sitios que efectivamente corrieron.

Cada sitio deduplica internamente por link: si la misma oferta aparece
buscando varios keywords distintos, se guarda una sola vez con todos los
keywords que matchearon juntos en `keywords_match`.

Opciones que usan la GUI y la herramienta de administración (no hace falta usarlas a mano):
`--storage`, `--status-file`, `--detener-file`, `--usuario`, `--inicio-ts`, `--email-a`, `--enviar-resultado`,
`--probar-correo` y `--probar-alerta`. `python escaner/scan_getonbrd.py --help` las describe todas.

## Sitios soportados hoy

| País | Sitio | Cómo busca | Descripción en CSV |
|---|---|---|---|
| Chile | GetOnBrd | No tiene buscador por texto: recorre categorías fijas y visita cada oferta | Completa (visita cada oferta igual, así que se guarda de paso) |
| Chile | Computrabajo | Búsqueda por texto (`/trabajo-de-<keyword>`), modalidad en la tarjeta | Solo con `--descripcion-completa` |
| Chile | Laborum | SPA, API interna `POST /api/avisos/searchV2` | Completa (el JSON de búsqueda ya trae el campo `detalle`) |
| Chile | Trabajando.cl | SPA, API interna `GET /api/searchjob`; su API de detalle (`GET /api/ofertas/<id>`) sí trae la descripción completa | Snippet corto por defecto; completa con `--descripcion-completa` |
| Chile | ChileTrabajos | Búsqueda por texto (`/encuentra-un-empleo?2=<keyword>`), modalidad en ícono de beneficio de la tarjeta | Resumen truncado por defecto; completa con `--descripcion-completa` |
| Colombia | Computrabajo Colombia | Misma plataforma que Chile, solo cambia el subdominio (`co.computrabajo.com`) | Solo con `--descripcion-completa` |
| Colombia | ElEmpleo | Búsqueda por texto (`/co/ofertas-empleo/trabajo-<keyword>`); agregando `/modalidad-remoto` al final filtra por remoto en el mismo request | Completa (viene en un atributo `data-offer-description` oculto en la tarjeta) |
| Colombia | GetOnBrd Colombia | Mismo pool de ofertas que GetOnBrd Chile (ver nota abajo) filtrado por texto | Completa (mismo mecanismo que GetOnBrd Chile) |
| Colombia | Magneto | Búsqueda por texto (`/co/trabajos/buscar/remoto/<keyword>`); agregando `remoto/` al principio filtra por remoto en el mismo request | Solo con `--descripcion-completa` |
| Colombia | SENA (Agencia Pública de Empleo, gobierno) | Búsqueda por texto (`/spe-web/spe/public/buscadorVacante?solicitudId=<keyword>`), modalidad como texto plano "Teletrabajo"/"No teletrabajo" en la tarjeta. Sitio público y lento | No priorizado (bajo volumen de ofertas remotas): el único sitio que no soporta `--descripcion-completa` |
| Chile | BNE (Bolsa Nacional de Empleo, gobierno) | API JSON pública sin login (`/data/ofertas/buscarListas?textoLibre=<keyword>`). Sin campo de modalidad: se detecta buscando "remoto"/"teletrabajo" en título+descripción, que ya vienen completos en la respuesta | Completa (mismo campo que ya se usa para detectar "remoto") |
| Cualquiera | LinkedIn | Endpoint público "jobs-guest" (sin login, no oficial), recibe `location` según el país | Solo con `--descripcion-completa` |

**Sobre la columna `descripcion`**: por defecto se llena solo con lo que
cada sitio ya entrega en su propia respuesta de búsqueda/listado (JSON de
una API, o texto/atributos ya presentes en la tarjeta HTML), sin agregar
ninguna request extra por oferta, salvo GetOnBrd, que de todas formas
visita cada oferta para chequear si está cerrada o es remota, así que ahí
la descripción completa sale gratis. Por eso el nivel de detalle por
defecto varía tanto entre sitios: descripción completa (GetOnBrd, Laborum,
ElEmpleo, BNE) vs. un snippet corto (Trabajando.cl, ChileTrabajos) vs.
vacío (Computrabajo, Magneto, LinkedIn, SENA).

**Modo `--descripcion-completa`** (flag en CLI; en la GUI es el botón
"📄 Buscar con descripción completa" en vez de "🔍 Buscar ofertas"): para
Computrabajo, ChileTrabajos, LinkedIn, Magneto y Trabajando.cl, visita
cada oferta que ya pasó el filtro de keyword/remoto y saca su descripción
real, con los extractores de `escaner/extraer_oferta.py` en los primeros
cuatro, y con la API de detalle propia (`GET /api/ofertas/<id>`) en
Trabajando.cl. Alarga el escaneo de esos sitios (una request extra por
oferta aceptada), así que no es el botón por defecto. SENA es el único
sitio que no lo soporta: no tiene un extractor de detalle funcionando
(ver limitaciones abajo).

GetOnBrd **no** tiene una variante "rápida" que se salte la visita por
oferta: a diferencia del resto de los sitios, no tiene buscador por
texto, así que la única forma de saber si una oferta matchea algún
keyword, es remota o sigue activa es visitando su página; no es algo
opcional como en los demás sitios, donde esa visita solo estaba de más
para sacar la descripción.

**Sitios descartados**: `bumeran.com.co` (redirige a `laborum.cl` incluso
sin sesión iniciada, no es un sitio real aparte para Colombia) y
Konzerta (es de Panamá: la ruta `/co` da 404 propio de la SPA, no tiene
versión Colombia).

**Nota sobre GetOnBrd por país**: confirmado que el sitio no filtra por país
en el servidor (`?country=<x>` no cambia los resultados: es un pool único
para toda Latinoamérica). "GetOnBrd Colombia" reutiliza la misma función
que "GetOnBrd" Chile pero, además del keyword y remoto, revisa el texto
completo de cada oferta buscando el nombre del país/sus ciudades
principales (`GETONBRD_CIUDADES_POR_PAIS`). Una oferta también pasa el
filtro si dice explícitamente que está abierta a toda Latinoamérica, o si
no menciona ningún país/ciudad conocido (ej. tarjetas que solo dicen
"Remoto" sin especificar dónde): en esos casos se asume abierta en vez
de excluirla. Esto significa que correr "GetOnBrd Colombia" vuelve a
descargar y revisar las mismas ~700 ofertas que "GetOnBrd" Chile (mismos
~20-25 min), solo que filtra distinto al final: es trabajo redundante
pero no hay forma de evitarlo dado que el sitio no soporta filtrar por
país en el servidor.

Detalle técnico de cada endpoint/selector está documentado como docstring
al inicio de `escaner/scan_getonbrd.py`.

## Extraer el texto completo de una oferta puntual

```bash
python escaner/extraer_oferta.py "<link-de-la-oferta>"
```

Baja la página (o, en Trabajando.cl, llama a su API de detalle) y saca
título + descripción completa lista para copiar al Project de Claude.
Cubre GetOnBrd, Computrabajo (Chile y Colombia), ChileTrabajos, ElEmpleo,
Magneto, BNE, LinkedIn y Trabajando.cl. Laborum no lo necesita (su propia
búsqueda ya trae la descripción completa). Pendiente: SENA (no
priorizado, bajo volumen de ofertas remotas).

En la versión en la nube, esta función corre desde los servidores de
Streamlit (no desde el job), así que con sitios chilenos puede fallar
por la IP; el escaneo en sí no se ve afectado.

## Editar búsqueda

- **keywords.txt** (opcional, una keyword por línea, `#` comenta la
  línea): si existe, se usa en vez de la lista por defecto. Se puede
  generar su contenido con `--generar-prompt-keywords`, pegando el
  prompt resultante en Claude.
- Si no existe `keywords.txt`, se usa `DEFAULT_KEYWORDS` al inicio del
  script (pre-cargada con un stack de ejemplo: automatización, python,
  selenium, platform engineer, soporte técnico, backup, data protection,
  tier 3, powershell). En la versión en la nube, una persona nueva no las ve.

## Agregar un país nuevo

1. Escribir las funciones `escanear_<sitio>(keywords, require_remote)` de
   cada sitio de ese país (mismo contrato que las de Chile: devuelven
   `list[dict]` con las claves de `CSV_FIELDS` salvo `sitio`).
2. Sumarlas a la lista `SITIOS`. Si la plataforma ya existe en otro país
   (como Computrabajo), no hace falta reescribir la función: se le puede
   pasar un `base_url`/`nombre_sitio` distinto vía `kwargs_extra` en el
   registro (ver la entrada `computrabajo_co` como ejemplo).
3. Agregar la entrada correspondiente en el dict `PAISES` con sus ids.

LinkedIn ya es reutilizable para cualquier país (recibe `location` como
parámetro): no hace falta reescribirlo.

## Limitaciones conocidas / mantenimiento

- **Selectores HTML frágiles**: cualquier sitio puede cambiar su
  estructura en cualquier momento. Si un sitio deja de traer resultados,
  hay que inspeccionar el HTML/API actual y ajustar el selector.
- **APIs no oficiales**: Laborum, Trabajando.cl y LinkedIn se acceden vía
  endpoints internos/no documentados (no hay alternativa pública). Pueden
  cambiar o dejar de funcionar sin aviso.
- **Bloqueos por IP**: algunos sitios (BNE, por ejemplo) devuelven `403` de forma intermitente; el escaneo sigue
  con el resto y lo anota en la lista de errores. Desde la nube, el job corre en una región de Google cercana al
  país que se escanea: desde una región lejana, sitios como ChileTrabajos y LinkedIn pueden bloquear la IP.
- **Rate limiting**: 1.5s de delay entre requests, y los sitios se
  escanean en **round robin** (un request por sitio por turno, no un
  sitio entero seguido antes de pasar al siguiente): así ningún sitio
  individual recibe una ráfaga larga de requests seguidos, sin alargar
  el tiempo total del escaneo. Si algún sitio empieza a bloquear el
  user-agent o la IP igual, hay que subir el delay o rotar user-agents.
- **No hace login**: solo lee resultados públicos.

## Coordinación con el Project de Claude (armado de CVs)

No hay integración en vivo: es un puente manual en dos puntos:
1. `--generar-prompt-keywords` → pegas el prompt en Claude → guardas la
   respuesta en `keywords.txt`.
2. `escaner/extraer_oferta.py <link>` → texto limpio de la oferta → lo pegas en
   el Project de Claude que arma el CV.

## Pendientes

- **Panel de actividad** en la herramienta de administración: cuántos escaneos se han hecho por día, semana,
  mes y año, por persona. La lógica y el gráfico ya están (`administracion/actividad.py`,
  `administracion/actividad_grafico.py`, con tests); falta la pantalla. Cuenta los escaneos lanzados desde la
  versión en la nube (los de tu PC no pasan por ahí).
- **Ordenar los menús** de la herramienta de administración en grupos (por ejemplo Panel / Gestión / Sistema).
- **Alerta de Cloud Monitoring** para los jobs que Google mata sin dejar que avisen (tiempo límite o memoria).
- **Publicar la app de OAuth en producción** (hoy está en modo *Testing*): Google exige una página de inicio y
  una política de privacidad públicas; mientras tanto cada persona nueva se agrega como usuario de prueba.
- Probar la cuota semanal con una cuenta que no sea administradora (la lógica está probada con tests).
- Más países además de Chile y Colombia: mismo proceso de investigación que se usó para los sitios actuales.
