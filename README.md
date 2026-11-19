# Launcher del servidor

`launcher.py` + `modpack.zip` se compilan en un único `launcher.exe` (Windows).

## Publicar una versión nueva
1. Cambia `VERSION_LAUNCHER` en `launcher.py` (ej. `"1.2.0"`).
2. `git add . && git commit -m "v1.2.0" && git push`
3. `git tag v1.2.0 && git push origin v1.2.0`

GitHub Actions compila el `.exe` y lo sube a **Releases**. El tag debe coincidir con `VERSION_LAUNCHER`.
Los jugadores con una versión anterior reciben la actualización obligatoria al abrir el launcher.

## Compilar a mano en Windows
Instala Python 3.12 (con tkinter) y ejecuta `build_windows.bat`. El resultado queda en `dist\launcher.exe`.

## Datos del jugador (Windows)
`%APPDATA%\mi_servidor_mc` (juego, configuración y `launcher.log`).
