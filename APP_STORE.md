# Pickazo MX — guía de envío a App Store

Todo lo que hay que llenar en App Store Connect para pasar la revisión. Los textos están listos para copiar.

## 1. Nombre, subtítulo y palabras clave

La app se presenta como **análisis e información deportiva**. Evita en nombre, subtítulo, descripción y keywords: *apuestas, apostar, momios, casa de apuestas, bet, parlay, tipster, ganar dinero*.

- **Nombre:** Pickazo MX
- **Subtítulo (30 caracteres):** Análisis deportivo con datos
- **Categoría principal:** Deportes
- **Keywords (100 caracteres):** `futbol,liga mx,estadisticas,analisis,premier,champions,nba,mlb,partidos,resultados,jornada`

### Descripción sugerida
> Pickazo MX es una plataforma de análisis e información deportiva. Consulta estadísticas, alineaciones, tendencias y un análisis claro de cada partido de Liga MX, Premier League, LaLiga, Serie A, Bundesliga, Ligue 1, Champions, MLS, NBA, MLB y más.
>
> • Análisis previo de cada partido, con la información que necesitas para entenderlo.
> • Noticias deportivas.
> • Sigue tus partidos y recibe un aviso cuando tu análisis esté listo.
> • Historial verificado de los análisis publicados.
>
> Pickazo PRO (suscripción mensual con renovación automática) desbloquea el análisis completo de cada partido. El cobro se carga a tu cuenta de Apple al confirmar la compra y se renueva solo salvo que lo canceles al menos 24 horas antes de que termine el periodo, en Ajustes > tu nombre > Suscripciones.
>
> Solo para mayores de 18 años. Pickazo es un servicio de información y análisis deportivo: no es una casa de apuestas, no recibe apuestas ni maneja dinero de juego, y ningún análisis garantiza resultados.
>
> Términos: https://pickazoapp.com/terminos.html · Privacidad: https://pickazoapp.com/privacidad.html

## 2. Clasificación de edad (cuestionario)

Responde con honestidad; es lo que Apple verifica contra el contenido.

- **Apuestas / juegos de azar con dinero real:** No (la app no recibe ni paga apuestas).
- **Juego de azar simulado:** No.
- **Temas de apuestas o referencias a ellas:** el contenido habla de picks y momios como información. Marca la frecuencia más alta que te dé el cuestionario en la pregunta de apuestas/temas de juego. Resultado esperado: **17+**. Es preferible un 17+ aceptado a un 4+ rechazado.
- Edad mínima declarada dentro de la app: 18 años (ya está en el registro).

## 3. Privacidad (App Privacy / "etiquetas de nutrición")

Datos que se recopilan, todos **vinculados a la identidad del usuario** y **no usados para rastreo**:

| Tipo | Para qué |
|---|---|
| Correo electrónico (Información de contacto) | Funcionalidad de la app, cuenta |
| ID de usuario | Funcionalidad de la app |
| ID de dispositivo (token de notificaciones) | Funcionalidad de la app (avisos) |
| Historial de compras | Funcionalidad de la app (estado de la suscripción) |
| Contenido del usuario (picks, partidos seguidos, votos) | Funcionalidad de la app |

- **Rastreo:** No. No hay publicidad ni SDK de terceros que rastree entre apps.
- **URL de política de privacidad:** `https://pickazoapp.com/privacidad.html`

## 4. Suscripción

- Crear el grupo de suscripción "Pickazo PRO" con un producto mensual de **$399 MXN** (precio en la tienda de México; el paywall lo lee de Apple).
- Todos los textos de renovación automática y los enlaces a Términos y Privacidad ya aparecen dentro de la pantalla de suscripción de la app.
- Incluir captura de la pantalla de suscripción en la revisión.
- Firmar el acuerdo de **Apps de paga** y entrar al **Small Business Program** (comisión del 15%).

## 5. Notas para el revisor (App Review Information)

Pegar en "Notes":

> Pickazo MX es una plataforma de análisis e información deportiva. No es una app de apuestas: no recibe apuestas, no maneja dinero de juego ni dirige a casas de apuestas. Es solo para mayores de 18 años y el contenido es informativo; ningún análisis garantiza resultados.
>
> Funciones nativas: notificaciones push (aviso cuando tu análisis está listo y antes de partidos que sigues), compras dentro de la app con Apple In-App Purchase (suscripción PRO mensual, con "Restaurar compras" en Mi cuenta), seguimiento de partidos, navegación propia de la app con barra inferior y eliminación de cuenta dentro de la app (Mi cuenta > Eliminar cuenta).
>
> La suscripción PRO se vende únicamente mediante In-App Purchase dentro de la app; no hay enlaces ni botones a pagos externos.
>
> Cuenta de prueba: **[correo]** / **[contraseña]**. (Cuenta gratis. Para probar la compra, usar una cuenta Sandbox de Apple.)

Crear antes una cuenta de revisor en producción y poner sus datos arriba.

## 6. Lista técnica antes de subir el build

Correr en la Mac, en `~/ligamx-ios`, y revisar:

```bash
cd ~/ligamx-ios/ios/App/App
grep -n "ITSAppUsesNonExemptEncryption" Info.plist   # debe ser false (solo HTTPS estándar)
ls PrivacyInfo.xcprivacy                              # el manifiesto de privacidad debe existir
ls Assets.xcassets/AppIcon.appiconset                 # icono 1024x1024 sin transparencia
```

- Variable `APNS_SANDBOX` en Railway: **false** para TestFlight/App Store (`true` solo con builds desde Xcode).
- `capacitor.config.json` del build de envío debe apuntar al dominio de producción.
- Capacidades en Xcode: Push Notifications, Background Modes > Remote notifications, **In-App Purchase**.
- Sube primero a **TestFlight** y prueba compra Sandbox, restaurar, borrar cuenta y push.

## 7. Interfaz empaquetada dentro de la app (guideline 4.2)

La app ya **no** carga el sitio remoto: la interfaz (HTML e imágenes propias) viaja dentro del build y los datos, login, análisis, compras y push siguen viniendo de tu servidor por internet. Así Apple la ve como app real y no como una web envuelta.

Consecuencia: **un cambio de diseño en `public/index.html` solo llega a los usuarios con una nueva versión de la app** (los cambios del servidor, motor y datos siguen siendo instantáneos). La web pickazoapp.com se sigue actualizando al momento.

Armar el build en la Mac:

```bash
cd ~/ligamx-ios
git checkout capacitor.config.json && git pull        # trae la config sin server.url
npm install
APP_API_BASE=https://ligamx-production-dff6b4b3.up.railway.app npm run build:app   # pruebas (preview)
# para producción: APP_API_BASE=https://pickazoapp.com npm run build:app
npx cap sync ios
```

Antes de enviar a la App Store, la rama debe estar desplegada en el servidor de producción (compras, push y borrar cuenta viven ahí), y `APP_API_BASE` debe apuntar a producción.


## 7. Versión de la app (`IS_APP`) vs web

Dentro de la app de iPhone (y solo ahí) se ocultan Parlays y Arma tu parlay, el paywall usa lenguaje de análisis ("Análisis completo", "Estadísticas, tendencias y contexto"), "Momio" se muestra como "Línea" y el lema es "Datos, no corazonadas". La web (pickazoapp.com) no cambia.

## 8. Pendientes antes de enviar (no son de código)

- Crear la **cuenta de revisor** en producción y poner correo y contraseña en las notas (sección 5).
- Variables en el servidor de producción: `REVENUECAT_IOS_KEY`, `REVENUECAT_SECRET_KEY`, `REVENUECAT_WEBHOOK_SECRET`, `REVENUECAT_ENTITLEMENT=pickazo_pro`, `RESEND_API_KEY`, `MAIL_FROM`, `APNS_SANDBOX=false`.
- Rotar `ADMIN_KEY`.
- Webhook de RevenueCat → `https://pickazoapp.com/api/iap/webhook` (cabecera `Bearer` con el mismo secreto).
- Build con `APP_API_BASE=https://pickazoapp.com`, Archive → TestFlight → enviar junto con la suscripción.
