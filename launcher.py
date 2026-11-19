#!/usr/bin/env python3

import os
import threading
import subprocess
import zipfile
import webbrowser
import uuid
import urllib.parse
import urllib.request
import io
import re
import sys
import time
import shutil
import hashlib
import json
import tkinter as tk
from tkinter import messagebox, ttk, simpledialog, filedialog
from PIL import Image, ImageTk
import minecraft_launcher_lib

ES_WINDOWS = sys.platform.startswith("win")
EMPAQUETADO = getattr(sys, "frozen", False)   # True cuando corre como .exe (PyInstaller)

VERSION_BASE = "1.20.1"
USAR_FORGE = True
RAM_GB_DEFAULT = 4

# Carpeta donde se guarda el juego, la config y el log
if ES_WINDOWS:
    RUTA_JUEGO = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "mi_servidor_mc")
else:
    RUTA_JUEGO = os.path.expanduser("~/.local/share/mi_servidor_mc")

CLIENT_ID = "00000000402b5328"
REDIRECT_URL = "https://login.live.com/oauth20_desktop.srf"

SERVER_IP = "Trimau.aternos.me"
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1572765164904652922/qGgvUd9EJ4YaUAZtKHeDj3Ka1CdMgP9Y5VDLvwxGWNON997eBVTLRCBlM74rHoeDFBoN"

FUENTE = "Segoe UI" if ES_WINDOWS else "Sans"

# ==========================================
# ACTUALIZACIONES (GitHub Releases)
# ==========================================
# Sube este número SIEMPRE antes de publicar y que coincida con el tag de la release (v1.1.0 -> "1.1.0")
VERSION_LAUNCHER = "1.2.1"
GITHUB_REPO = "Trimau/server-mc"   # el repo debe ser público
if EMPAQUETADO:
    ASSET_LAUNCHER = "launcher.exe" if ES_WINDOWS else "launcher"   # archivo que se sube a cada release
else:
    ASSET_LAUNCHER = "launcher.py"

CONFIG_PATH = os.path.join(RUTA_JUEGO, "launcher_config.json")
MODS_ORIGEN_DIR = os.path.join(RUTA_JUEGO, "mods", "mods_opcionales")
LOG_PATH = os.path.join(RUTA_JUEGO, "launcher.log")


def ruta_recurso(nombre):
    """Ruta de un archivo empaquetado dentro del .exe (PyInstaller) o junto al .py."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


def parse_version(texto):
    nums = re.findall(r"\d+", texto or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def log(mensaje):
    """Escribe en launcher.log (un .exe sin consola no tiene dónde mostrar errores)."""
    try:
        os.makedirs(RUTA_JUEGO, exist_ok=True)
        modo = "w" if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 500_000 else "a"
        with open(LOG_PATH, modo, encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {mensaje}\n")
    except Exception:
        pass


def enviar_apariencia_discord(nombre_jugador, ruta_skin="", ruta_capa=""):
    """Envía la skin y/o capa seleccionada al Webhook de Discord sin bloquear el inicio."""
    if not DISCORD_WEBHOOK_URL or "TU_ID" in DISCORD_WEBHOOK_URL:
        return

    adjuntos = []
    for etiqueta, ruta in [("skin", ruta_skin), ("capa", ruta_capa)]:
        if ruta and os.path.isfile(ruta):
            try:
                with open(ruta, "rb") as f:
                    adjuntos.append((etiqueta, f"{nombre_jugador}_{etiqueta}.png", f.read()))
            except Exception as e:
                log(f"No se pudo leer {etiqueta} para Discord: {e}")

    if not adjuntos:
        return

    boundary = f"----LauncherBoundary{uuid.uuid4().hex}"
    cuerpo = bytearray()

    def agregar_campo(nombre, valor):
        cuerpo.extend(f"--{boundary}\r\n".encode("utf-8"))
        cuerpo.extend(f'Content-Disposition: form-data; name="{nombre}"\r\n\r\n'.encode("utf-8"))
        cuerpo.extend(f"{valor}\r\n".encode("utf-8"))

    agregar_campo("content", f"Apariencia configurada por **{nombre_jugador}** en `{SERVER_IP}`")

    for idx, (_, nombre_archivo, contenido) in enumerate(adjuntos):
        cuerpo.extend(f"--{boundary}\r\n".encode("utf-8"))
        cuerpo.extend(
            f'Content-Disposition: form-data; name="file{idx}"; filename="{nombre_archivo}"\r\n'.encode("utf-8")
        )
        cuerpo.extend(b"Content-Type: image/png\r\n\r\n")
        cuerpo.extend(contenido)
        cuerpo.extend(b"\r\n")

    cuerpo.extend(f"--{boundary}--\r\n".encode("utf-8"))

    try:
        req = urllib.request.Request(
            DISCORD_WEBHOOK_URL,
            data=bytes(cuerpo),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "mc-launcher",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8):
            pass
    except Exception as e:
        log(f"Error enviando apariencia a Discord: {e}")


def mover_archivo(origen, destino):
    """os.replace con reintentos: el antivirus de Windows a veces bloquea el archivo unos instantes."""
    for intento in range(8):
        try:
            os.replace(origen, destino)
            return
        except PermissionError:
            if intento == 7:
                raise
            time.sleep(0.5)


def validar_descarga(ruta):
    """Comprueba que lo descargado es realmente un launcher antes de reemplazar el actual."""
    if EMPAQUETADO:
        magia = b"MZ" if ES_WINDOWS else b"\x7fELF"
        with open(ruta, "rb") as f:
            if not f.read(4).startswith(magia):
                raise RuntimeError("El archivo descargado no es un ejecutable válido.")
    else:
        with open(ruta, "rb") as f:
            datos = f.read()
        try:
            compile(datos, ruta, "exec")
        except SyntaxError:
            raise RuntimeError("El launcher descargado no es un script de Python válido.")


def ruta_minecraft_oficial():
    if ES_WINDOWS:
        return os.path.join(os.environ.get("APPDATA", ""), ".minecraft")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/minecraft")
    return os.path.expanduser("~/.minecraft")


MODS_OPCIONALES = [
    {
        "id": "embeddium",
        "nombre": "Embeddium",
        "desc": "Mod de rendimiento esencial para Forge que mejora drásticamente los FPS y optimiza el motor gráfico.",
        "archivo": "embeddium-1.20.1.jar",
        "icono": "embeddium.png",
        "url": "https://tuservidor.com/downloads/mods/embeddium-1.20.1.jar"
    },
    {
        "id": "jei",
        "nombre": "Just Enough Items (JEI)",
        "desc": "Muestra una lista interactiva de todos los ítems del juego y sus recetas de fabricación en el inventario.",
        "archivo": "jei-1.20.1-forge.jar",
        "icono": "jei.png",
        "url": "https://tuservidor.com/downloads/mods/jei-1.20.1-forge.jar"
    },
    {
        "id": "journeymap",
        "nombre": "JourneyMap",
        "desc": "Mapea tu mundo en tiempo real en el minimapa y en pantalla completa, registrando puntos de muerte y marcadores.",
        "archivo": "journeymap-1.20.1-forge.jar",
        "icono": "journeymap.png",
        "url": "https://tuservidor.com/downloads/mods/journeymap-1.20.1-forge.jar"
    },
    {
        "id": "appleskin",
        "nombre": "AppleSkin",
        "desc": "Añade información visual sobre la saturación, la restauración de comida y los valores ocultos directamente en la barra de hambre.",
        "archivo": "appleskin-forge-1.20.1.jar",
        "icono": "appleskin.png",
        "url": "https://tuservidor.com/downloads/mods/appleskin-forge-1.20.1.jar"
    }
]

GUIA_MODS_DATA = [
    {
        "titulo": "🚀 Embeddium — Optimización Gráfica y FPS",
        "imagen": "guia_embeddium.png",
        "desc": "Es la pieza central de rendimiento para este modpack. Reemplaza el motor de renderizado por defecto de Minecraft para eliminar tirones de lag y multiplicar los FPS, ideal para jugar fluido."
    },
    {
        "titulo": "📖 Just Enough Items (JEI) — Recetas y Crafteos",
        "imagen": "guia_jei.png",
        "desc": "Indispensable para consultar recetas dentro del juego. Presiona la tecla 'R' sobre cualquier objeto en tu inventario para ver cómo se fabrica, o 'U' para ver en qué recetas se utiliza."
    },
    {
        "titulo": "🗺️ JourneyMap — Minimapa y Puntos de Muerte",
        "imagen": "guia_journeymap.png",
        "desc": "Despliega un minimapa interactivo en tiempo real en la esquina superior derecha. Presiona 'J' para abrir el mapa completo, crear marcadores de tu base y localizar tu punto de muerte."
    }
]


class LauncherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Minecraft Launcher")

        self.root.geometry("980x740")
        self.root.minsize(880, 620)
        self.root.resizable(True, True)
        self.root.config(bg="#1e1e23")

        self.path_skin = ""
        self.path_capa = ""
        self.img_skin_preview = None
        self.img_capa_preview = None
        self.vars_mods = {}
        self.guia_images = []
        self.guia_desc_labels = []

        self.ms_session = None
        self.img_cabeza_premium = None

        self.config_guardada = self.cargar_config()
        self.instalar_modpack()
        self.inicializar_mods_opcionales()

        self.var_ram = tk.IntVar(value=self.config_guardada.get("ram", RAM_GB_DEFAULT))

        # --- NAV ---
        nav_frame = tk.Frame(root, bg="#141416", height=60)
        nav_frame.pack(side="top", fill="x")

        tk.Label(
            nav_frame,
            text="MINECRAFT LAUNCHER",
            fg="white",
            bg="#141416",
            font=(FUENTE, 15, "bold")
        ).pack(side="left", padx=20, pady=15)

        tk.Label(
            nav_frame,
            text=f"v{VERSION_LAUNCHER}",
            fg="#666666",
            bg="#141416",
            font=(FUENTE, 9)
        ).pack(side="right", padx=20)

        # --- FOOTER ---
        footer_frame = tk.Frame(root, bg="#141416", height=90)
        footer_frame.pack(side="bottom", fill="x")

        self.btn_jugar = tk.Button(
            footer_frame,
            text="JUGAR",
            bg="#2ecc71",
            fg="white",
            font=(FUENTE, 11, "bold"),
            width=18,
            height=2,
            bd=0,
            command=self.comenzar_hilo,
            cursor="hand2"
        )
        self.btn_jugar.pack(side="left", padx=20, pady=15)

        info_footer = tk.Frame(footer_frame, bg="#141416")
        info_footer.pack(side="right", fill="both", expand=True, padx=20, pady=15)

        self.progress = ttk.Progressbar(info_footer, orient="horizontal", mode="determinate")
        self.progress.pack(fill="x", pady=(5, 5))

        self.lbl_estado = tk.Label(
            info_footer,
            text="Listo para jugar",
            fg="#888888",
            bg="#141416",
            font=(FUENTE, 9)
        )
        self.lbl_estado.pack(anchor="w")

        # --- BODY ---
        body_frame = tk.Frame(root, bg="#1e1e23")
        body_frame.pack(side="top", fill="both", expand=True, padx=20, pady=20)

        # --- ASIDE ---
        aside_frame = tk.Frame(body_frame, bg="#25252b", width=200)
        aside_frame.pack(side="left", fill="y", padx=(0, 15), ipadx=8, ipady=10)
        aside_frame.pack_propagate(False)

        cuenta_top_frame = tk.Frame(aside_frame, bg="#25252b")
        cuenta_top_frame.pack(side="top", fill="x")

        tk.Label(
            cuenta_top_frame,
            text="Cuenta",
            fg="white",
            bg="#25252b",
            font=(FUENTE, 11, "bold")
        ).pack(anchor="center", pady=(0, 10))

        self.account_container = tk.Frame(cuenta_top_frame, bg="#25252b")
        self.account_container.pack(fill="x", anchor="center")

        self.frame_ms_info = tk.Frame(self.account_container, bg="#25252b")

        header_ms = tk.Frame(self.frame_ms_info, bg="#25252b")
        header_ms.pack(fill="x", anchor="center", pady=5)

        self.lbl_cabeza_premium = tk.Label(header_ms, bg="#25252b")
        self.lbl_cabeza_premium.pack(side="top", pady=(0, 5))

        self.lbl_ms_nombre = tk.Label(
            header_ms,
            text="",
            fg="#2ecc71",
            bg="#25252b",
            font=(FUENTE, 10, "bold"),
            wraplength=160,
            justify="center"
        )
        self.lbl_ms_nombre.pack(side="top")

        self.btn_cerrar_sesion = tk.Button(
            self.frame_ms_info,
            text="Cerrar Sesión",
            bg="#e74c3c",
            fg="white",
            font=(FUENTE, 8, "bold"),
            bd=0,
            cursor="hand2",
            command=self.cerrar_sesion_ms
        )
        self.btn_cerrar_sesion.pack(anchor="center", pady=(10, 0))

        self.frame_offline = tk.Frame(self.account_container, bg="#25252b")

        tk.Label(self.frame_offline, text="Nombre de Usuario:", fg="#cccccc", bg="#25252b", font=(FUENTE, 9)).pack(anchor="center")
        self.txt_usuario = tk.Entry(self.frame_offline, font=(FUENTE, 11), width=18, justify="center")
        self.txt_usuario.pack(pady=3, anchor="center")

        nombre_guardado = self.config_guardada.get("username", "")
        self.txt_usuario.insert(0, nombre_guardado if nombre_guardado else "TuNombre")

        self.var_premium = tk.BooleanVar(value=False)
        self.chk_premium = tk.Checkbutton(
            self.frame_offline, text="Cuenta Microsoft", variable=self.var_premium,
            fg="#cccccc", bg="#25252b", selectcolor="#2d2d35", command=self.toggle_premium, font=(FUENTE, 9)
        )
        self.chk_premium.pack(anchor="center", pady=5)

        self.frame_offline.pack(fill="x")

        threading.Thread(target=self._intentar_restaurar_sesion_ms, daemon=True).start()

        ip_frame = tk.Frame(aside_frame, bg="#25252b")
        ip_frame.pack(side="bottom", fill="x", pady=(0, 5))

        tk.Label(ip_frame, text="IP SERVER", fg="#aaaaaa", bg="#25252b", font=(FUENTE, 9, "bold")).pack(anchor="center")
        tk.Label(ip_frame, text=SERVER_IP, fg="#2ecc71", bg="#25252b", font=(FUENTE, 10, "bold")).pack(anchor="center")

        # Ajustes centrados verticalmente
        settings_container = tk.Frame(aside_frame, bg="#25252b")
        settings_container.pack(side="top", fill="both", expand=True, padx=5)

        settings_inner = tk.Frame(settings_container, bg="#25252b")
        settings_inner.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(
            settings_inner,
            text="Memoria RAM (GB):",
            fg="#cccccc",
            bg="#25252b",
            font=(FUENTE, 8, "bold")
        ).pack(anchor="center")

        self.lbl_ram_val = tk.Label(
            settings_inner,
            text=f"{self.var_ram.get()} GB",
            fg="#2ecc71",
            bg="#25252b",
            font=(FUENTE, 9, "bold")
        )
        self.lbl_ram_val.pack(anchor="center")

        self.slider_ram = tk.Scale(
            settings_inner,
            from_=2,
            to=16,
            orient="horizontal",
            variable=self.var_ram,
            bg="#25252b",
            fg="white",
            highlightbackground="#25252b",
            troughcolor="#1e1e23",
            sliderrelief="flat",
            bd=0,
            length=140,
            command=lambda val: self.lbl_ram_val.config(text=f"{int(float(val))} GB")
        )
        self.slider_ram.pack(anchor="center", pady=(0, 10))

        self.btn_eliminar_cache = tk.Button(
            settings_inner,
            text="Eliminar caché",
            bg="#e74c3c",
            fg="white",
            font=(FUENTE, 8, "bold"),
            bd=0,
            pady=6,
            cursor="hand2",
            command=self.eliminar_cache
        )
        self.btn_eliminar_cache.pack(fill="x", padx=10)

        # --- MAIN ---
        main_frame = tk.Frame(body_frame, bg="#1e1e23")
        main_frame.pack(side="left", fill="both", expand=True, padx=10)

        tabs_bar = tk.Frame(main_frame, bg="#1e1e23")
        tabs_bar.pack(anchor="w", fill="x", pady=(0, 10))

        self.btn_tab_apariencia = tk.Button(
            tabs_bar, text="Apariencia", bg="#2d2d35", fg="white", font=(FUENTE, 9, "bold"),
            bd=0, padx=14, pady=7, cursor="hand2", command=lambda: self.cambiar_pestana("apariencia")
        )
        self.btn_tab_apariencia.pack(side="left", padx=(0, 5))

        self.btn_tab_mods = tk.Button(
            tabs_bar, text="Mods Opcionales", bg="#141416", fg="#888888", font=(FUENTE, 9, "bold"),
            bd=0, padx=14, pady=7, cursor="hand2", command=lambda: self.cambiar_pestana("mods")
        )
        self.btn_tab_mods.pack(side="left", padx=(0, 5))

        self.btn_tab_guia = tk.Button(
            tabs_bar, text="Guía de Mods", bg="#141416", fg="#888888", font=(FUENTE, 9, "bold"),
            bd=0, padx=14, pady=7, cursor="hand2", command=lambda: self.cambiar_pestana("guia")
        )
        self.btn_tab_guia.pack(side="left")

        self.content_area = tk.Frame(main_frame, bg="#1e1e23")
        self.content_area.pack(fill="both", expand=True)

        # -----------------------------------------------------------------
        # PESTAÑA 1: APARIENCIA
        # -----------------------------------------------------------------
        self.view_apariencia = tk.Frame(self.content_area, bg="#1e1e23")

        tk.Label(
            self.view_apariencia,
            text="Personalización de Apariencia",
            fg="white",
            bg="#1e1e23",
            font=(FUENTE, 12, "bold")
        ).pack(anchor="w", pady=(0, 10))

        skins_wrapper = tk.Frame(self.view_apariencia, bg="#1e1e23")
        skins_wrapper.pack(fill="both", expand=True)
        skins_wrapper.grid_columnconfigure(0, weight=1)
        skins_wrapper.grid_columnconfigure(1, weight=1)
        skins_wrapper.grid_rowconfigure(0, weight=1)

        frame_skin_container = tk.Frame(skins_wrapper, bg="#25252b", padx=25, pady=20)
        frame_skin_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        tk.Label(frame_skin_container, text="Skin PNG", fg="white", bg="#25252b", font=(FUENTE, 11, "bold")).pack(anchor="center", pady=(0, 15))

        self.canvas_skin = tk.Canvas(frame_skin_container, width=180, height=180, bg="#2d2d35", highlightthickness=1, highlightbackground="#444")
        self.canvas_skin.pack(expand=True, pady=(0, 15))
        self.canvas_skin.create_text(90, 90, text="Sin Skin", fill="#888888", font=(FUENTE, 10))

        self.txt_ruta_skin = tk.Entry(frame_skin_container, font=(FUENTE, 9), justify="center", state="readonly")
        self.txt_ruta_skin.pack(fill="x", pady=(0, 10))

        self.btn_skin = tk.Button(
            frame_skin_container, text="Examinar Skin", bg="#3498db", fg="white",
            font=(FUENTE, 10, "bold"), command=self.seleccionar_skin, bd=0, pady=10, cursor="hand2"
        )
        self.btn_skin.pack(fill="x")

        frame_capa_container = tk.Frame(skins_wrapper, bg="#25252b", padx=25, pady=20)
        frame_capa_container.grid(row=0, column=1, sticky="nsew", padx=(10, 0))

        tk.Label(frame_capa_container, text="Capa PNG", fg="white", bg="#25252b", font=(FUENTE, 11, "bold")).pack(anchor="center", pady=(0, 15))

        self.canvas_capa = tk.Canvas(frame_capa_container, width=108, height=180, bg="#2d2d35", highlightthickness=1, highlightbackground="#444")
        self.canvas_capa.pack(expand=True, pady=(0, 15))
        self.canvas_capa.create_text(54, 90, text="Sin Capa", fill="#888888", font=(FUENTE, 10))

        self.txt_ruta_capa = tk.Entry(frame_capa_container, font=(FUENTE, 9), justify="center", state="readonly")
        self.txt_ruta_capa.pack(fill="x", pady=(0, 10))

        self.btn_capa = tk.Button(
            frame_capa_container, text="Examinar Capa", bg="#9b59b6", fg="white",
            font=(FUENTE, 10, "bold"), command=self.seleccionar_capa, bd=0, pady=10, cursor="hand2"
        )
        self.btn_capa.pack(fill="x")

        ruta_skin_guardada = self.config_guardada.get("skin", "")
        if ruta_skin_guardada and self._aplicar_skin(ruta_skin_guardada):
            self.path_skin = ruta_skin_guardada
            self.txt_ruta_skin.config(state="normal")
            self.txt_ruta_skin.delete(0, tk.END)
            self.txt_ruta_skin.insert(0, os.path.basename(ruta_skin_guardada))
            self.txt_ruta_skin.config(state="readonly")

        ruta_capa_guardada = self.config_guardada.get("capa", "")
        if ruta_capa_guardada and self._aplicar_capa(ruta_capa_guardada):
            self.path_capa = ruta_capa_guardada
            self.txt_ruta_capa.config(state="normal")
            self.txt_ruta_capa.delete(0, tk.END)
            self.txt_ruta_capa.insert(0, os.path.basename(ruta_capa_guardada))
            self.txt_ruta_capa.config(state="readonly")

        # -----------------------------------------------------------------
        # PESTAÑA 2: MODS OPCIONALES
        # -----------------------------------------------------------------
        self.view_mods = tk.Frame(self.content_area, bg="#1e1e23")

        tk.Label(self.view_mods, text="Mods Opcionales", fg="white", bg="#1e1e23", font=(FUENTE, 11, "bold")).pack(anchor="center", pady=(0, 5))

        container_mods = tk.Frame(self.view_mods, bg="#000000", bd=1, relief="solid")
        container_mods.pack(fill="both", expand=True)

        self.canvas_mods = tk.Canvas(container_mods, bg="#000000", highlightthickness=0)
        scrollbar = ttk.Scrollbar(container_mods, orient="vertical", command=self.canvas_mods.yview)
        self.scrollable_mods_frame = tk.Frame(self.canvas_mods, bg="#000000")

        self.scrollable_mods_frame.bind("<Configure>", lambda e: self.canvas_mods.configure(scrollregion=self.canvas_mods.bbox("all")))
        self.canvas_mods.create_window((0, 0), window=self.scrollable_mods_frame, anchor="nw")
        self.canvas_mods.configure(yscrollcommand=scrollbar.set)

        self.canvas_mods.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        mods_dir = os.path.join(RUTA_JUEGO, "mods")
        mods_guardados = self.config_guardada.get("mods", {})

        for mod in MODS_OPCIONALES:
            card = tk.Frame(self.scrollable_mods_frame, bg="#121215", padx=12, pady=10)
            card.pack(fill="x", pady=8, padx=8)

            card_content = tk.Frame(card, bg="#121215")
            card_content.pack(anchor="center", fill="x", expand=True)

            icono_path = os.path.join(MODS_ORIGEN_DIR, mod["icono"]) if mod.get("icono") else None
            if not icono_path or not os.path.exists(icono_path):
                icono_path = os.path.join(mods_dir, mod["icono"]) if mod.get("icono") else None

            lbl_icon = None
            if icono_path and os.path.exists(icono_path):
                try:
                    pil_img = Image.open(icono_path).resize((32, 32), Image.Resampling.NEAREST)
                    tk_img = ImageTk.PhotoImage(pil_img)
                    lbl_icon = tk.Label(card_content, image=tk_img, bg="#121215")
                    lbl_icon.image = tk_img
                except Exception:
                    pass

            if not lbl_icon:
                lbl_icon = tk.Label(card_content, text="📦", bg="#1a1a1e", fg="white", font=(FUENTE, 16), width=3, height=2)

            lbl_icon.pack(side="left", padx=(0, 12))

            info_mod = tk.Frame(card_content, bg="#121215")
            info_mod.pack(side="left", fill="x", expand=True)

            tk.Label(info_mod, text=mod["nombre"], fg="white", bg="#121215", font=(FUENTE, 11, "bold")).pack(anchor="w")
            tk.Label(info_mod, text=mod["desc"], fg="#bbbbbb", bg="#121215", font=(FUENTE, 9), wraplength=360, justify="left").pack(anchor="w", pady=(2, 0))

            ruta_jar = os.path.join(mods_dir, mod["archivo"])
            ruta_jar_disabled = ruta_jar + ".disabled"

            estado_fs = os.path.exists(ruta_jar) and not os.path.exists(ruta_jar_disabled)
            estado_guardado = mods_guardados.get(mod["id"])
            activo = estado_guardado if isinstance(estado_guardado, bool) else estado_fs

            var_mod = tk.BooleanVar(value=activo)
            self.vars_mods[mod["id"]] = {"var": var_mod, "archivo": mod["archivo"]}

            def toggle_chk(v=var_mod, l=None):
                v.set(not v.get())
                actualizar_aspecto_chk(v, l)

            def actualizar_aspecto_chk(v, l):
                if v.get():
                    l.config(text="☑", fg="#2ecc71")
                else:
                    l.config(text="☐", fg="#666666")

            lbl_chk = tk.Label(
                card_content,
                text="☑" if activo else "☐",
                fg="#2ecc71" if activo else "#666666",
                bg="#121215",
                font=(FUENTE, 26),
                cursor="hand2"
            )
            lbl_chk.pack(side="right", padx=15)
            lbl_chk.bind("<Button-1>", lambda e, v=var_mod, l=lbl_chk: toggle_chk(v, l))

        # -----------------------------------------------------------------
        # PESTAÑA 3: GUÍA DE MODS (RESPONSIVE EN ANCHO)
        # -----------------------------------------------------------------
        self.view_guia = tk.Frame(self.content_area, bg="#1e1e23")

        tk.Label(self.view_guia, text="Guía de Mods y Novedades", fg="white", bg="#1e1e23", font=(FUENTE, 11, "bold")).pack(anchor="center", pady=(0, 5))

        container_guia = tk.Frame(self.view_guia, bg="#000000", bd=1, relief="solid")
        container_guia.pack(fill="both", expand=True)

        self.canvas_guia = tk.Canvas(container_guia, bg="#000000", highlightthickness=0)
        scrollbar_guia = ttk.Scrollbar(container_guia, orient="vertical", command=self.canvas_guia.yview)
        self.scrollable_guia_frame = tk.Frame(self.canvas_guia, bg="#000000")

        self.canvas_window_guia = self.canvas_guia.create_window((0, 0), window=self.scrollable_guia_frame, anchor="nw")
        
        def _on_canvas_guia_configure(event):
            self.canvas_guia.itemconfig(self.canvas_window_guia, width=event.width)
            new_wraplength = event.width - 60
            if new_wraplength > 200:
                for lbl in self.guia_desc_labels:
                    lbl.config(wraplength=new_wraplength)

        self.canvas_guia.bind("<Configure>", _on_canvas_guia_configure)
        self.scrollable_guia_frame.bind("<Configure>", lambda e: self.canvas_guia.configure(scrollregion=self.canvas_guia.bbox("all")))
        self.canvas_guia.configure(yscrollcommand=scrollbar_guia.set)

        self.canvas_guia.pack(side="left", fill="both", expand=True)
        scrollbar_guia.pack(side="right", fill="y")

        guia_dir = os.path.join(RUTA_JUEGO, "guia")
        os.makedirs(guia_dir, exist_ok=True)

        for item in GUIA_MODS_DATA:
            post = tk.Frame(self.scrollable_guia_frame, bg="#121215", padx=16, pady=14)
            post.pack(fill="x", pady=8, padx=8)

            tk.Label(
                post,
                text=item["titulo"],
                fg="#2ecc71",
                bg="#121215",
                font=(FUENTE, 11, "bold")
            ).pack(anchor="w", pady=(0, 8))

            img_path = os.path.join(guia_dir, item.get("imagen", ""))
            lbl_img_guia = None
            if item.get("imagen") and os.path.exists(img_path):
                try:
                    pil_guia = Image.open(img_path)
                    tk_guia = ImageTk.PhotoImage(pil_guia)
                    self.guia_images.append(tk_guia)
                    lbl_img_guia = tk.Label(post, image=tk_guia, bg="#1a1a1e")
                except Exception:
                    pass

            if not lbl_img_guia:
                lbl_img_guia = tk.Label(
                    post,
                    text=f"🖼️  Espacio para captura / banner del mod\n(Coloca una imagen en {guia_dir})",
                    bg="#1a1a1e",
                    fg="#777777",
                    font=(FUENTE, 9),
                    height=5,
                    relief="solid",
                    bd=1
                )

            lbl_img_guia.pack(fill="x", pady=(0, 8))

            desc_label = tk.Label(
                post,
                text=item["desc"],
                fg="#cccccc",
                bg="#121215",
                font=(FUENTE, 9),
                wraplength=600,
                justify="left"
            )
            desc_label.pack(anchor="w", fill="x", expand=True)
            self.guia_desc_labels.append(desc_label)

        self.pestana_actual = "apariencia"
        self.root.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.root.bind_all("<Button-4>", self._on_mouse_wheel)
        self.root.bind_all("<Button-5>", self._on_mouse_wheel)

        self.cambiar_pestana("apariencia")
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        if EMPAQUETADO:
            try:
                os.remove(sys.executable + ".old")   # restos de la actualización anterior
            except OSError:
                pass

        self.btn_jugar.config(state="disabled")
        self.actualizar_estado("Buscando actualizaciones...")
        threading.Thread(target=self._buscar_actualizaciones, daemon=True).start()

    def _on_mouse_wheel(self, event):
        target_canvas = None
        if self.pestana_actual == "mods":
            target_canvas = self.canvas_mods
        elif self.pestana_actual == "guia":
            target_canvas = self.canvas_guia

        if not target_canvas:
            return

        if event.delta:
            target_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        elif event.num == 4:
            target_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            target_canvas.yview_scroll(1, "units")

    def cambiar_pestana(self, nombre):
        self.pestana_actual = nombre
        self.view_apariencia.pack_forget()
        self.view_mods.pack_forget()
        self.view_guia.pack_forget()

        self.btn_tab_apariencia.config(bg="#141416", fg="#888888")
        self.btn_tab_mods.config(bg="#141416", fg="#888888")
        self.btn_tab_guia.config(bg="#141416", fg="#888888")

        if nombre == "apariencia":
            self.view_apariencia.pack(fill="both", expand=True)
            self.btn_tab_apariencia.config(bg="#2d2d35", fg="white")
        elif nombre == "mods":
            self.view_mods.pack(fill="both", expand=True)
            self.btn_tab_mods.config(bg="#2d2d35", fg="white")
        elif nombre == "guia":
            self.view_guia.pack(fill="both", expand=True)
            self.btn_tab_guia.config(bg="#2d2d35", fg="white")

    def _descargar_cabeza(self, nombre_jugador):
        try:
            url = f"https://mc-heads.net/avatar/{nombre_jugador}/96"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                img_data = response.read()
            img = Image.open(io.BytesIO(img_data)).resize((48, 48), Image.Resampling.NEAREST)
            return ImageTk.PhotoImage(img)
        except Exception as e:
            log(f"No se pudo descargar la cabeza de {nombre_jugador}: {e}")
            return None

    def _intentar_restaurar_sesion_ms(self):
        refresh_token = self.config_guardada.get("ms_refresh_token", "")
        if not refresh_token:
            return

        try:
            session = minecraft_launcher_lib.microsoft_account.complete_refresh(
                CLIENT_ID, None, REDIRECT_URL, refresh_token
            )
            self.ms_session = session
            self.config_guardada["ms_refresh_token"] = session.get("refresh_token", refresh_token)
            self.root.after(0, lambda: self._mostrar_sesion_ms(session["name"]))
        except Exception:
            self.ms_session = None
            self.config_guardada.pop("ms_refresh_token", None)

    def _mostrar_sesion_ms(self, nombre):
        self.lbl_ms_nombre.config(text=f"Conectado como:\n{nombre}")
        self.frame_offline.pack_forget()
        self.frame_ms_info.pack(fill="x", anchor="w")

        def cargar_cabeza():
            img = self._descargar_cabeza(nombre)
            if img:
                self.root.after(0, lambda: self.lbl_cabeza_premium.config(image=img))
                self.img_cabeza_premium = img

        threading.Thread(target=cargar_cabeza, daemon=True).start()

    def _ocultar_sesion_ms(self):
        self.frame_ms_info.pack_forget()
        self.frame_offline.pack(fill="x", anchor="w")
        self.img_cabeza_premium = None
        self.lbl_cabeza_premium.config(image="")

    def cerrar_sesion_ms(self):
        self.ms_session = None
        self.config_guardada.pop("ms_refresh_token", None)
        self.config_guardada.pop("ms_username", None)
        self._ocultar_sesion_ms()
        self.guardar_config()
        messagebox.showinfo("Sesión cerrada", "Has cerrado la sesión de Microsoft.")

    def eliminar_cache(self):
        confirmar = messagebox.askyesno(
            "Advertencia",
            "¿Realmente quieres que se elimine la caché?\n\n"
            "Se borrarán la skin, la capa, el nombre de usuario y la cuenta de Microsoft guardados.",
            icon="warning",
            default="no"
        )
        if not confirmar:
            return

        # Cuenta de Microsoft
        self.ms_session = None
        self.config_guardada.pop("ms_refresh_token", None)
        self.config_guardada.pop("ms_username", None)
        self._ocultar_sesion_ms()
        self.var_premium.set(False)

        # Skin y capa (solo se olvidan las rutas; los PNG originales del usuario no se tocan)
        self.path_skin = ""
        self.path_capa = ""
        self.img_skin_preview = None
        self.img_capa_preview = None

        self.canvas_skin.delete("all")
        self.canvas_skin.create_text(90, 90, text="Sin Skin", fill="#888888", font=(FUENTE, 10))
        self.canvas_capa.delete("all")
        self.canvas_capa.create_text(54, 90, text="Sin Capa", fill="#888888", font=(FUENTE, 10))

        for entry in (self.txt_ruta_skin, self.txt_ruta_capa):
            entry.config(state="normal")
            entry.delete(0, tk.END)
            entry.config(state="readonly")

        # Copias que el launcher dejó en CustomSkinLoader
        shutil.rmtree(os.path.join(RUTA_JUEGO, "CustomSkinLoader", "LocalSkin"), ignore_errors=True)

        # Nombre de usuario
        self.txt_usuario.delete(0, tk.END)
        self.txt_usuario.insert(0, "TuNombre")

        self.guardar_config()
        self.actualizar_estado("Caché eliminada")
        messagebox.showinfo("Caché eliminada", "Se eliminó la caché correctamente.")

    # ------------------------------------------------------------------
    # ACTUALIZACIONES AUTOMÁTICAS (GitHub Releases)
    # ------------------------------------------------------------------
    def _mostrar_aviso(self, titulo, texto, error=False):
        """Muestra un aviso (solo botón Aceptar) desde un hilo secundario y espera a que se cierre."""
        evento = threading.Event()

        def mostrar():
            (messagebox.showerror if error else messagebox.showinfo)(titulo, texto, parent=self.root)
            evento.set()

        self.root.after(0, mostrar)
        evento.wait()

    def _ultima_release(self):
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(url, headers={
            "User-Agent": "mc-launcher-updater",
            "Accept": "application/vnd.github+json"
        })
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    def _descargar_archivo(self, url, destino, digest=None, etiqueta=""):
        req = urllib.request.Request(url, headers={"User-Agent": "mc-launcher-updater"})
        tmp = destino + ".part"
        sha = hashlib.sha256()
        leido = 0
        with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            self.root.after(0, lambda: self.progress.config(value=0, maximum=total or 100))
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                sha.update(chunk)
                leido += len(chunk)
                if total:
                    self.root.after(0, lambda v=leido: self.progress.config(value=v))
                    self.actualizar_estado(f"Descargando {etiqueta}... {leido * 100 // total}%")

        if digest and digest.startswith("sha256:") and sha.hexdigest() != digest.split(":", 1)[1]:
            os.remove(tmp)
            raise RuntimeError("La descarga está corrupta (el SHA-256 no coincide).")
        os.replace(tmp, destino)

    def _buscar_actualizaciones(self):
        bloqueado = False   # hay una actualización pendiente: no se puede jugar
        reiniciado = False
        try:
            release = self._ultima_release()
            tag = release.get("tag_name", "")
            if parse_version(tag) <= parse_version(VERSION_LAUNCHER):
                self.actualizar_estado("Listo para jugar")
                return

            asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_LAUNCHER), None)
            if not asset:
                log(f"La release {tag} no incluye el archivo {ASSET_LAUNCHER}")
                self.actualizar_estado("Listo para jugar")
                return

            bloqueado = True
            self._mostrar_aviso(
                "Actualización obligatoria",
                f"Hay una nueva versión del launcher ({tag}).\n"
                f"Tu versión: v{VERSION_LAUNCHER}\n\n"
                "Es obligatorio actualizar para poder jugar.\n"
                "El launcher se reiniciará solo al terminar."
            )
            self._actualizar_launcher(asset)
            reiniciado = True
        except Exception as e:
            log(f"Error en la actualización: {e}")
            if bloqueado:
                self.actualizar_estado("Actualización pendiente")
                self._mostrar_aviso(
                    "Error al actualizar",
                    f"No se pudo actualizar el launcher:\n{e}\n\n"
                    "No puedes jugar hasta actualizar. Cierra y vuelve a abrir el launcher para reintentar.",
                    error=True
                )
            else:
                self.actualizar_estado("Listo para jugar")
        finally:
            if not reiniciado:
                self.root.after(0, lambda: self.progress.config(value=0))
                if not bloqueado:
                    self.root.after(0, lambda: self.btn_jugar.config(state="normal"))

    def _actualizar_launcher(self, asset):
        """Reemplaza este mismo launcher (.exe o .py) por el de la release y lo vuelve a abrir."""
        ruta = os.path.abspath(sys.executable if EMPAQUETADO else __file__)
        nuevo, viejo = ruta + ".new", ruta + ".old"

        self._descargar_archivo(asset["browser_download_url"], nuevo, asset.get("digest"), "launcher")

        try:
            validar_descarga(nuevo)
        except Exception:
            try:
                os.remove(nuevo)
            except OSError:
                pass
            raise

        try:
            os.chmod(nuevo, os.stat(ruta).st_mode)   # conserva el permiso de ejecución
        except OSError:
            pass

        if EMPAQUETADO:
            # Un .exe en uso no se puede sobrescribir, pero sí renombrar
            try:
                os.remove(viejo)
            except OSError:
                pass
            mover_archivo(ruta, viejo)
            try:
                mover_archivo(nuevo, ruta)
            except Exception:
                mover_archivo(viejo, ruta)   # rollback
                raise
            comando = [ruta]
        else:
            mover_archivo(nuevo, ruta)
            comando = [sys.executable, ruta] + sys.argv[1:]

        entorno = dict(os.environ)
        entorno["PYINSTALLER_RESET_ENVIRONMENT"] = "1"   # el nuevo .exe no debe reutilizar la carpeta temporal de este
        opciones = {
            "env": entorno,
            "cwd": os.path.dirname(ruta),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if ES_WINDOWS:
            opciones["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(comando, **opciones)
        self.root.after(0, self.root.destroy)

    def _pedir_texto(self, titulo, texto):
        """askstring desde un hilo secundario: el diálogo se abre en el hilo principal y se espera la respuesta."""
        resultado = {}
        evento = threading.Event()

        def pedir():
            resultado["v"] = simpledialog.askstring(titulo, texto, parent=self.root)
            evento.set()

        self.root.after(0, pedir)
        evento.wait()
        return resultado.get("v")

    # ------------------------------------------------------------------
    # MODPACK (viene dentro del .exe)
    # ------------------------------------------------------------------
    def _guardar_clave_config(self, clave, valor):
        self.config_guardada[clave] = valor
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            datos = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    datos = json.load(f)
            datos[clave] = valor
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"No se pudo guardar {clave} en la config: {e}")

    def instalar_modpack(self):
        """Extrae modpack.zip en la carpeta del juego solo si es nuevo o si faltan archivos."""
        zip_path = ruta_recurso("modpack.zip")
        if not os.path.exists(zip_path):
            return
        try:
            with zipfile.ZipFile(zip_path, "r") as z:
                archivos = [i for i in z.infolist() if not i.is_dir()]
                firma = hashlib.sha1(
                    "|".join(f"{i.filename}:{i.CRC}:{i.file_size}" for i in archivos).encode("utf-8")
                ).hexdigest()
                faltan = any(
                    not os.path.exists(os.path.join(RUTA_JUEGO, *i.filename.split("/"))) for i in archivos
                )
                if firma == self.config_guardada.get("modpack_firma") and not faltan:
                    return
                os.makedirs(RUTA_JUEGO, exist_ok=True)
                z.extractall(RUTA_JUEGO)
            self._guardar_clave_config("modpack_firma", firma)
            log("Modpack instalado")
        except Exception as e:
            log(f"No se pudo instalar el modpack: {e}")

    def _java_runtime(self, version_id, callbacks=None):
        """Ruta al Java que instala Mojang junto con la versión (el jugador no necesita instalar Java)."""
        try:
            info = minecraft_launcher_lib.runtime.get_version_runtime_information(version_id, RUTA_JUEGO)
            nombre = info["name"] if info else "java-runtime-gamma"
        except Exception:
            nombre = "java-runtime-gamma"   # el que usa Minecraft 1.20.1 (Java 17)
        try:
            ruta = minecraft_launcher_lib.runtime.get_executable_path(nombre, RUTA_JUEGO)
            if not ruta:
                minecraft_launcher_lib.runtime.install_jvm_runtime(nombre, RUTA_JUEGO, callback=callbacks)
                ruta = minecraft_launcher_lib.runtime.get_executable_path(nombre, RUTA_JUEGO)
            return ruta
        except Exception as e:
            log(f"No se pudo preparar el Java de Mojang: {e}")
            return None

    def cargar_config(self):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def guardar_config(self):
        username = self.txt_usuario.get().strip()
        if username == "TuNombre":
            username = ""

        config = {
            "mods": {mid: datos["var"].get() for mid, datos in self.vars_mods.items()},
            "skin": self.path_skin,
            "capa": self.path_capa,
            "username": username,
            "premium": False,
            "ram": self.var_ram.get(),
        }
        if self.config_guardada.get("modpack_firma"):
            config["modpack_firma"] = self.config_guardada["modpack_firma"]
        if self.ms_session:
            config["ms_refresh_token"] = self.ms_session.get("refresh_token", "")
            config["ms_username"] = self.ms_session.get("name", "")
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"Error guardando config: {e}")

    def _aplicar_skin(self, ruta):
        if not ruta or not os.path.exists(ruta):
            return False
        try:
            img = Image.open(ruta).convert("RGBA")
            head = img.crop((8, 8, 16, 16))
            helmet = img.crop((40, 8, 48, 16))
            head.paste(helmet, (0, 0), helmet)
            img_resized = head.resize((180, 180), Image.Resampling.NEAREST)
            self.img_skin_preview = ImageTk.PhotoImage(img_resized)
            self.canvas_skin.delete("all")
            self.canvas_skin.create_image(0, 0, anchor="nw", image=self.img_skin_preview)
            return True
        except Exception as e:
            log(f"Error aplicando skin: {e}")
            return False

    def _aplicar_capa(self, ruta):
        if not ruta or not os.path.exists(ruta):
            return False
        try:
            img = Image.open(ruta).convert("RGBA")
            cape_front = img.crop((1, 1, 11, 17))
            img_resized = cape_front.resize((108, 180), Image.Resampling.NEAREST)
            self.img_capa_preview = ImageTk.PhotoImage(img_resized)
            self.canvas_capa.delete("all")
            self.canvas_capa.create_image(0, 0, anchor="nw", image=self.img_capa_preview)
            return True
        except Exception as e:
            log(f"Error aplicando capa: {e}")
            return False

    def on_closing(self):
        self.root.destroy()

    def inicializar_mods_opcionales(self):
        mods_destino = os.path.join(RUTA_JUEGO, "mods")
        os.makedirs(mods_destino, exist_ok=True)
        os.makedirs(MODS_ORIGEN_DIR, exist_ok=True)

        for mod in MODS_OPCIONALES:
            archivo = mod["archivo"]
            origen = os.path.join(MODS_ORIGEN_DIR, archivo)
            destino = os.path.join(mods_destino, archivo)
            destino_disabled = destino + ".disabled"

            if os.path.exists(origen) and not os.path.exists(destino) and not os.path.exists(destino_disabled):
                shutil.copy2(origen, destino)

    def seleccionar_skin(self):
        archivo = filedialog.askopenfilename(filetypes=[("Archivos PNG", "*.png")])
        if archivo and self._aplicar_skin(archivo):
            self.path_skin = archivo
            self.txt_ruta_skin.config(state="normal")
            self.txt_ruta_skin.delete(0, tk.END)
            self.txt_ruta_skin.insert(0, os.path.basename(archivo))
            self.txt_ruta_skin.config(state="readonly")

    def seleccionar_capa(self):
        archivo = filedialog.askopenfilename(filetypes=[("Archivos PNG", "*.png")])
        if archivo and self._aplicar_capa(archivo):
            self.path_capa = archivo
            self.txt_ruta_capa.config(state="normal")
            self.txt_ruta_capa.delete(0, tk.END)
            self.txt_ruta_capa.insert(0, os.path.basename(archivo))
            self.txt_ruta_capa.config(state="readonly")

    def toggle_premium(self):
        if self.var_premium.get():
            threading.Thread(target=self._login_ms_manual, daemon=True).start()

    def _login_ms_manual(self):
        try:
            self.actualizar_estado("Iniciando sesión en Microsoft...")
            login_url, state, code_verifier = minecraft_launcher_lib.microsoft_account.get_secure_login_data(CLIENT_ID, REDIRECT_URL)
            webbrowser.open(login_url)

            url_resp = self._pedir_texto("Microsoft", "Pega la URL final después de iniciar sesión:")

            if not url_resp:
                self.root.after(0, lambda: self.var_premium.set(False))
                self.actualizar_estado("Login cancelado")
                return

            self.actualizar_estado("Validando cuenta con Microsoft...")

            parsed = urllib.parse.urlparse(url_resp.strip())
            params = urllib.parse.parse_qs(parsed.query) or urllib.parse.parse_qs(parsed.fragment)

            session = minecraft_launcher_lib.microsoft_account.complete_login(
                CLIENT_ID, None, REDIRECT_URL, params['code'][0], code_verifier
            )

            self.ms_session = session

            self.root.after(0, lambda: self._mostrar_sesion_ms(session["name"]))
            self.root.after(0, lambda: self.var_premium.set(False))
            self.guardar_config()
            self.actualizar_estado("Sesión iniciada correctamente")

        except Exception as e:
            self.root.after(0, lambda m=f"No se pudo iniciar sesión: {e}": messagebox.showerror("Error", m))
            self.root.after(0, lambda: self.var_premium.set(False))
            self.actualizar_estado("Error de login")

    def actualizar_estado(self, texto):
        self.root.after(0, lambda: self.lbl_estado.config(text=texto))

    def comenzar_hilo(self):
        self.guardar_config()
        threading.Thread(target=self.proceso_iniciar, daemon=True).start()

    def gestionar_mods_opcionales(self):
        mods_destino = os.path.join(RUTA_JUEGO, "mods")
        os.makedirs(mods_destino, exist_ok=True)

        for mod_id, datos in self.vars_mods.items():
            archivo_jar = datos["archivo"]
            ruta_jar = os.path.join(mods_destino, archivo_jar)
            ruta_jar_disabled = ruta_jar + ".disabled"
            ruta_origen = os.path.join(MODS_ORIGEN_DIR, archivo_jar)

            if datos["var"].get():
                if os.path.exists(ruta_jar_disabled):
                    os.rename(ruta_jar_disabled, ruta_jar)
                elif not os.path.exists(ruta_jar) and os.path.exists(ruta_origen):
                    shutil.copy2(ruta_origen, ruta_jar)
            else:
                if os.path.exists(ruta_jar):
                    os.rename(ruta_jar, ruta_jar_disabled)

    def vincular_assets(self):
        oficial = ruta_minecraft_oficial()
        if not os.path.exists(oficial):
            return
        for carpeta in ["assets", "libraries"]:
            origen, destino = os.path.join(oficial, carpeta), os.path.join(RUTA_JUEGO, carpeta)
            if os.path.exists(origen) and not os.path.lexists(destino):
                try:
                    os.symlink(origen, destino, target_is_directory=True)
                except Exception:
                    if ES_WINDOWS:
                        # Sin permisos de administrador no hay symlinks, pero sí "junctions"
                        try:
                            subprocess.run(
                                ["cmd", "/c", "mklink", "/J", destino, origen],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False
                            )
                        except Exception as e:
                            log(f"No se pudo enlazar {carpeta}: {e}")

    def configurar_custom_skin_loader(self, nombre_jugador):
        csl_dir = os.path.join(RUTA_JUEGO, "CustomSkinLoader")
        skins_dir = os.path.join(csl_dir, "LocalSkin", "skins")
        capes_dir = os.path.join(csl_dir, "LocalSkin", "capes")

        if self.path_skin and os.path.exists(self.path_skin):
            os.makedirs(skins_dir, exist_ok=True)
            shutil.copy2(self.path_skin, os.path.join(skins_dir, f"{nombre_jugador}.png"))

        if self.path_capa and os.path.exists(self.path_capa):
            os.makedirs(capes_dir, exist_ok=True)
            shutil.copy2(self.path_capa, os.path.join(capes_dir, f"{nombre_jugador}.png"))

        json_path = os.path.join(csl_dir, "CustomSkinLoader.json")
        proveedor_local = {
            "name": "LocalSkin",
            "type": "Legacy",
            "checkPNG": False,
            "skin": "LocalSkin/skins/{USERNAME}.png",
            "model": "auto",
            "cape": "LocalSkin/capes/{USERNAME}.png"
        }

        configuracion = {"enable": True, "loadlist": []}
        if os.path.isfile(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    configuracion = json.load(f)
            except:
                pass

        configuracion.setdefault("enable", True)
        loadlist = configuracion.get("loadlist", [])
        if not isinstance(loadlist, list):
            loadlist = []

        loadlist = [p for p in loadlist if not (isinstance(p, dict) and p.get("name") == "LocalSkin")]
        loadlist.insert(0, proveedor_local)
        configuracion["loadlist"] = loadlist

        os.makedirs(csl_dir, exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(configuracion, f, ensure_ascii=False, indent=4)

    def proceso_iniciar(self):
        self.root.after(0, lambda: self.btn_jugar.config(state="disabled"))

        try:
            os.makedirs(RUTA_JUEGO, exist_ok=True)
            self.actualizar_estado("Vinculando archivos...")
            self.vincular_assets()

            if self.ms_session:
                session = self.ms_session
                try:
                    session = minecraft_launcher_lib.microsoft_account.complete_refresh(
                        CLIENT_ID, None, REDIRECT_URL, session["refresh_token"]
                    )
                    self.ms_session = session
                except Exception:
                    self.root.after(0, lambda: messagebox.showerror("Error", "La sesión de Microsoft ha caducado. Vuelve a iniciar sesión."))
                    self.root.after(0, lambda: self.btn_jugar.config(state="normal"))
                    self.root.after(0, lambda: self.cerrar_sesion_ms())
                    return
            else:
                usuario = self.txt_usuario.get().strip()
                if not usuario:
                    self.root.after(0, lambda: messagebox.showerror("Error", "Ingresa un nombre de usuario."))
                    self.root.after(0, lambda: self.btn_jugar.config(state="normal"))
                    return
                session = {"name": usuario, "id": str(uuid.uuid3(uuid.NAMESPACE_DNS, usuario)), "access_token": "offline"}
                self.actualizar_estado("Configurando skin y capa...")
                self.configurar_custom_skin_loader(usuario)

            if self.path_skin or self.path_capa:
                threading.Thread(
                    target=enviar_apariencia_discord,
                    args=(session["name"], self.path_skin, self.path_capa),
                    daemon=True,
                ).start()

            self.actualizar_estado("Instalando modpack...")
            self.instalar_modpack()

            self.actualizar_estado("Aplicando mods opcionales...")
            self.gestionar_mods_opcionales()

            callbacks = {
                "setStatus": self.actualizar_estado,
                "setProgress": lambda v: self.root.after(0, lambda: self.progress.config(value=v)),
                "setMax": lambda m: self.root.after(0, lambda: self.progress.config(maximum=m))
            }

            java_exe = None
            if USAR_FORGE:
                forge_ver = minecraft_launcher_lib.forge.find_forge_version(VERSION_BASE)
                if not forge_ver:
                    raise RuntimeError(f"No se encontró una versión de Forge para Minecraft {VERSION_BASE}.")
                version_lanzar = forge_ver.replace("-", "-forge-", 1)
                if not os.path.exists(os.path.join(RUTA_JUEGO, "versions", version_lanzar)):
                    # Forge necesita Java para instalarse: se usa el que descarga Mojang con la versión base
                    minecraft_launcher_lib.install.install_minecraft_version(VERSION_BASE, RUTA_JUEGO, callback=callbacks)
                    java_exe = self._java_runtime(VERSION_BASE, callbacks)
                    try:
                        minecraft_launcher_lib.forge.install_forge_version(forge_ver, RUTA_JUEGO, callback=callbacks, java=java_exe)
                    except TypeError:
                        minecraft_launcher_lib.forge.install_forge_version(forge_ver, RUTA_JUEGO, callback=callbacks)
            else:
                version_lanzar = VERSION_BASE
                minecraft_launcher_lib.install.install_minecraft_version(VERSION_BASE, RUTA_JUEGO, callback=callbacks)

            java_exe = java_exe or self._java_runtime(version_lanzar, callbacks)

            self.actualizar_estado("Abriendo Minecraft...")
            jvm_args = [f"-Xmx{self.var_ram.get()}G", "-Xms2G"]
            options = {
                "username": session["name"],
                "uuid": session["id"],
                "token": session["access_token"],
                "jvmArguments": jvm_args
            }
            if java_exe:
                if ES_WINDOWS and java_exe.lower().endswith("java.exe"):
                    javaw = java_exe[:-8] + "javaw.exe"   # javaw no abre ventana de consola
                    if os.path.exists(javaw):
                        java_exe = javaw
                options["executablePath"] = java_exe

            comando = minecraft_launcher_lib.command.get_minecraft_command(version_lanzar, RUTA_JUEGO, options)
            opciones = {
                "cwd": RUTA_JUEGO,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            }
            if ES_WINDOWS:
                opciones["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen(comando, **opciones)
            self.root.after(0, self.root.destroy)

        except Exception as e:
            self.root.after(0, lambda m=str(e): messagebox.showerror("Error", m))
            self.root.after(0, lambda: self.btn_jugar.config(state="normal"))
            self.actualizar_estado("Error")


if __name__ == "__main__":
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()