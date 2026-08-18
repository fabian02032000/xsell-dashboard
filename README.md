# Dashboard de leads Xsell (en tiempo real, gratis)

Este proyecto muestra tus leads de HubSpot en un dashboard que se actualiza
solo, cada hora, y vive en una página web gratuita de GitHub. No necesitas
saber programar — solo sigue estos pasos una vez. Toma unos 15-20 minutos.

## Paso 1 — Crear el token de HubSpot (5 min)

1. Entra a tu cuenta de HubSpot.
2. Ve a **Configuración** (ícono de engranaje, arriba a la derecha) → **Integraciones** → **Apps privadas**.
3. Clic en **Crear una app privada**.
4. Ponle un nombre, por ejemplo "Dashboard Xsell".
5. Ve a la pestaña **Scopes** (alcances) y activa, como mínimo:
   - `crm.objects.contacts.read`
   - `crm.objects.deals.read` (opcional, solo si quieres el conteo de clientes cerrados)
6. Clic en **Crear app**, luego **Continuar creando**.
7. Copia el **token de acceso** que te muestra (empieza con `pat-...`). Guárdalo,
   lo vas a necesitar en el Paso 3. No lo compartas ni lo pegues en ningún
   lado público.

## Paso 2 — Crear el repositorio en GitHub (5 min)

1. Si no tienes cuenta, créala gratis en [github.com](https://github.com).
2. Clic en el botón verde **New** (o el ícono "+" arriba a la derecha → **New repository**).
3. Nombre del repositorio: `xsell-dashboard` (o el que prefieras).
4. Déjalo como **Public** (necesario para que GitHub Pages sea gratis).
5. Clic en **Create repository**.
6. En la página del repo recién creado, busca el botón **Add file → Upload files**.
7. Arrastra **todos los archivos y carpetas** de esta carpeta que te envié
   (incluyendo la carpeta `.github` y `scripts` completas) y confirma con
   **Commit changes**.

## Paso 3 — Guardar el token de forma segura (2 min)

1. Dentro del repositorio, ve a **Settings** (pestaña del repo, no la de tu cuenta).
2. En el menú izquierdo: **Secrets and variables → Actions**.
3. Clic en **New repository secret**.
4. Name: `HUBSPOT_TOKEN`
5. Value: pega el token que copiaste en el Paso 1.
6. Clic en **Add secret**.

## Paso 4 — Activar GitHub Pages (2 min)

1. Sigue en **Settings** → busca **Pages** en el menú izquierdo.
2. En "Build and deployment" → **Source**: elige **Deploy from a branch**.
3. **Branch**: `main`, carpeta `/ (root)`. Clic en **Save**.
4. GitHub te dará un link tipo `https://tu-usuario.github.io/xsell-dashboard/`
   — esa es la URL de tu dashboard. Puede tardar 1-2 minutos en activarse la
   primera vez.

## Paso 5 — Ejecutar la primera actualización manualmente

Por defecto, la actualización automática corre cada hora, pero para no
esperar la primera vez:

1. Ve a la pestaña **Actions** del repositorio.
2. Clic en **Actualizar dashboard** (a la izquierda).
3. Clic en el botón **Run workflow** (a la derecha) → **Run workflow** de nuevo para confirmar.
4. Espera 1-2 minutos y refresca la página — debería mostrar un check verde ✓.
5. Abre tu link de GitHub Pages del Paso 4 — ya deberías ver tus datos reales.

## Después de esto

No tienes que hacer nada más. El dashboard se actualiza solo cada hora
mientras el repositorio exista y el token siga siendo válido. Si algún día
regeneras el token en HubSpot, solo repite el Paso 3 con el nuevo valor.

## Ajustes opcionales

Si quieres cambiar la meta mensual (74), la fecha de inicio de campaña, o el
nombre de la etapa de "cerrado ganado" de tu pipeline de Negocios, edita estas
líneas en `.github/workflows/update-dashboard.yml`, dentro del paso
"Traer datos de HubSpot", agregando variables de entorno como:

```yaml
env:
  HUBSPOT_TOKEN: ${{ secrets.HUBSPOT_TOKEN }}
  MONTHLY_GOAL: "74"
  CAMPAIGN_START_DATE: "2026-07-25"
  CLOSEDWON_STAGE: "closedwon"
```

Si algo no funciona, entra a la pestaña **Actions** del repo, abre la última
ejecución en rojo (❌) y copia el mensaje de error — con eso puedo ayudarte a
diagnosticarlo.
