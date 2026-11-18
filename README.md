# Órbita Local

Órbita Local es una aplicación web local para revisar la lista de cuentas que sigue la persona que inicia sesión con X. Cada persona ejecuta su propia copia, registra su propia aplicación en X y paga las consultas que genere con sus propias credenciales. No es una web pública multiusuario ni una instancia central que pague por sus visitantes.

## Arranque rápido

Requisitos: Python 3.10 o posterior y una app propia de X Developer Platform con acceso de lectura a la API.

1. Copia `.env.example` como `.env`.
2. En X Developer Console, configura OAuth 2.0, una app de tipo **Web App** o **Native App**, y añade esta URL de retorno exactamente: `http://127.0.0.1:8765/oauth/callback`.
3. Activa los permisos de lectura para la cuenta: `users.read`, `follows.read` y `tweet.read`. No se piden permisos para seguir, publicar, borrar o enviar mensajes.
4. Pega el `Client ID` en `X_CLIENT_ID`. Para una app confidencial de tipo Web App, pega también su `Client Secret` en `X_CLIENT_SECRET`. No compartas `.env` ni lo subas a GitHub.
5. Ejecuta `start.bat` en Windows. Abre `http://127.0.0.1:8765` si el navegador no se abre automáticamente.
6. Escribe el @ de tu propia cuenta y pulsa **Conectar con X**. OAuth confirma que el @ pertenece a la cuenta que autorizas; escribir un @ por sí solo no da acceso.
7. Elige cuántos perfiles analizar. Antes de cada análisis, Órbita muestra una estimación y pide confirmación.

No se instala ningún paquete de Python. El servidor escucha solo en `127.0.0.1`, es decir, en el propio ordenador. El token OAuth permanece en memoria mientras `app.py` esté en marcha; no se guarda en disco. La app no solicita `offline.access`, así que X indica que el token de acceso expira normalmente en dos horas. Detén el servidor con Ctrl+C para borrar el token de memoria.

## Costes de X

Las lecturas de la API son de pago por uso. Como orientación inicial, el estimador de la interfaz usa `$0.001` por cuenta devuelta al leer la lista propia de seguidos (Owned Reads, si la cuenta y la app cumplen las condiciones de X) más hasta cinco publicaciones por perfil a `$0.005` cada una: máximo estimado `$0.026` por perfil. Para 50 perfiles son aproximadamente `$1.30`; para 250, `$6.50`. El coste real puede cambiar según la tarifa vigente, el acceso de la cuenta y los recursos que devuelva X. Consulta las tarifas actuales y fija un límite de gasto en Developer Console antes de escanear. El estimador es orientativo, no un límite de facturación garantizado.

La API puede denegar la lectura de publicaciones o limitar el número de cuentas. La app limita cada análisis a 250 perfiles, pide confirmación justo antes de las consultas y comunica los errores parciales.

## Qué analiza

- Lee las cuentas seguidas por el usuario autenticado mediante `GET /2/users/{id}/following`.
- Para cada cuenta consulta hasta cinco publicaciones recientes con `GET /2/users/{id}/tweets`; la más reciente disponible sirve para estimar inactividad.
- Busca señales sencillas de automatización en la proporción entre seguidos y seguidores y el ritmo acumulado de publicaciones. “Posibles señales” es solo un indicio heurístico, no confirma que una cuenta sea un bot.
- Las publicaciones, likes, respuestas, datos privados no expuestos por la API y cuentas sin acceso no se pueden inferir a partir de esta consulta. La fecha mostrada significa “última publicación disponible para la API”, no actividad total en X.

## Arquitectura y privacidad

`index.html` es la interfaz; `app.py` sirve la web, hace el intercambio OAuth PKCE y envía solicitudes a X desde el equipo local. Las credenciales de aplicación y las claves secretas nunca se insertan en HTML o JavaScript. `.env` está excluido de Git por `.gitignore`. Los análisis no se guardan en disco ni se envían a otro servidor. Las solicitudes de autorización/API sí se envían a X, como parte de la conexión y del análisis solicitados.

Si compartes el código en un repositorio, distribuye `.env.example`, nunca `.env`. Quien clone el proyecto tendrá que registrar y mantener su propia app de desarrollador, configurar sus propias credenciales y pagar el uso correspondiente. No se puede desplegar esta configuración como una página estática de GitHub Pages; OAuth y las llamadas deben pasar por un servidor seguro que opere cada propietario.

## Documentación oficial

- [OAuth 2.0 PKCE de X](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
- [Endpoint de cuentas seguidas](https://docs.x.com/x-api/users/get-following)
- [Precios y créditos de X API](https://docs.x.com/x-api/getting-started/pricing)
