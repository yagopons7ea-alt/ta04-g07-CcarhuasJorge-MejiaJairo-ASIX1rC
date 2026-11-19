#!/usr/bin/env python3


# -*- coding: utf-8 -*-


"""IncidentIQ: analizador XML con consola Rich y dashboard web local.


La interfaz web usa únicamente HTML, CSS y JavaScript nativos, servidos por


http.server. No necesita Tkinter, Matplotlib, Node, CDN ni librerías web.


Para conservar la presentación de consola del analizador original se usan


las dependencias que ya utilizaba: pandas y rich.


"""


from __future__ import annotations


import argparse
from datetime import datetime


import html


import json


import re


import socket


import sys


import threading


import unicodedata


import webbrowser


import xml.etree.ElementTree as ET


from collections import Counter


from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


from pathlib import Path


from typing import Any


from urllib.parse import unquote, urlparse


import pandas as pd


from rich.columns import Columns


from rich.console import Console


from rich.panel import Panel


from rich.table import Table
from rich.markup import escape


# -----------------------------------------------------------------------------


# Configuración y mapeo de los campos del formulario


# -----------------------------------------------------------------------------


PUNTUACION_PERMITIDA = set(".,;:!?¡¿'’\"“”‘«»()[]-_/@+%&#·…–—")


# Mapeo contrastado con el XML: campo_7 es la descripción y campo_15 el


# mensaje de error. Es importante no intercambiarlos.


MAPEO_COLUMNAS = {


    "Marca_de_temps": "Fecha_Registro",


    "Adre_a_electr_nica": "Usuario_Email",


    # Etiquetas reales del XML exportado por el formulario (en catalán).
    "Quin_dia_has_detectat_el_problema_": "Fecha_Incidencia",
    "On__s_l_equip_que_t__el_problema_": "Ubicacion",
    "Quin_equip_t__el_problema_": "Tipo_Dispositivo",
    "Qu__li_passa_a_l_equip_": "Sintoma_Principal",
    "Ens_pots_explicar_qu__passa_": "Descripcion_Detallada",
    "Quan_va_comen_ar_el_problema_": "Tiempo_Desde_Fallo",
    "Pots_continuar_treballant_amb_aquest_equip_": "Puede_Continuar_Trabajando",
    "A_qui_afecta_el_problema_": "Personas_Afectadas",
    "Qu__has_provat_de_fer_per_solucionar-ho_": "Acciones_Previas",
    "Aquest_problema_ja_havia_passat_abans_": "Problema_Recurrente",
    "Pots_utilitzar_un_altre_equip_mentre_esperes_": "Puede_Usar_Otro_Equipo",
    "Apareix_algun_missatge_d_error_a_la_pantalla_": "Muestra_Mensaje_Error",
    "Si_has_vist_un_missatge_d_error__qu__hi_diu_": "Mensaje_Error",

    # Compatibilidad con exportaciones antiguas que usaban campo_N.
    "campo_3": "Fecha_Incidencia",
    "campo_4": "Ubicacion",
    "campo_5": "Tipo_Dispositivo",
    "campo_6": "Sintoma_Principal",
    "campo_7": "Descripcion_Detallada",
    "campo_8": "Tiempo_Desde_Fallo",
    "campo_9": "Puede_Continuar_Trabajando",
    "campo_10": "Personas_Afectadas",
    "campo_11": "Acciones_Previas",
    "campo_12": "Problema_Recurrente",
    "campo_13": "Puede_Usar_Otro_Equipo",
    "campo_14": "Muestra_Mensaje_Error",
    "campo_15": "Mensaje_Error",
}

# Prefijos de las columnas auxiliares vacías (el XML mezcla "Columna_" y "Column_").
PREFIJOS_AUXILIARES = ("Columna_", "Column_")

# Archivo que se analiza siempre (no se pregunta al usuario).
ARCHIVO_XML_POR_DEFECTO = "incidencies.xml"


# El estado compartido se actualiza si el usuario carga otro XML desde el panel.


APP_STATE: dict[str, Any] = {


    "validos": [],


    "invalidos": [],


    "total": 0,


    "archivo": "",


    "error": None,


}


STATE_LOCK = threading.Lock()


MAX_UPLOAD_BYTES = 12 * 1024 * 1024  # 12 MB


# -----------------------------------------------------------------------------


# Validación y normalización


# -----------------------------------------------------------------------------


def es_texto_valido(texto: Any) -> bool:


    """Permite letras latinas, dígitos, espacios y puntuación habitual."""


    if texto is None or str(texto) == "":


        return True


    texto = unicodedata.normalize("NFC", str(texto))


    for caracter in texto:


        if caracter.isalpha():


            if "LATIN" not in unicodedata.name(caracter, ""):


                return False


        elif "0" <= caracter <= "9":


            continue


        elif caracter.isspace():


            continue


        elif caracter in PUNTUACION_PERMITIDA:


            continue


        else:


            return False


    return True


def normalizar_categoria(valor: Any) -> str:


    """Agrupa pequeñas variaciones de espacios y apóstrofos."""


    if valor is None:


        return "No especificado"


    valor = str(valor).strip().replace("’", "'").replace("‘", "'")


    valor = re.sub(r"\s+", " ", valor).rstrip(" .")


    return valor if valor else "No especificado"


def df_a_registros(df: pd.DataFrame) -> list[dict[str, str]]:


    """Convierte un DataFrame a una estructura JSON segura (sin NaN)."""


    if df.empty:


        return []


    copia = df.fillna("")


    return [


        {str(k): str(v) for k, v in fila.items()}


        for fila in copia.to_dict(orient="records")


    ]


# -----------------------------------------------------------------------------


# Lectura y clasificación del XML


# -----------------------------------------------------------------------------


def analizar_contenido_xml(contenido: bytes):


    """Devuelve DataFrames válidos/descartados, total y error."""


    try:


        raiz = ET.fromstring(contenido)


    except ET.ParseError as exc:


        return pd.DataFrame(), pd.DataFrame(), 0, f"El XML no es válido: {exc}"


    registros = raiz.findall("registro")


    if not registros:


        return pd.DataFrame(), pd.DataFrame(), 0, "No se encontraron elementos <registro> en el XML."


    validos: list[dict[str, Any]] = []


    invalidos: list[dict[str, Any]] = []


    for numero, registro in enumerate(registros, start=1):


        fila = {


            elemento.tag: (elemento.text if elemento.text is not None else "")


            for elemento in registro


        }


        campos_invalidos = []


        for campo, valor in fila.items():


            # El formulario exporta columnas auxiliares vacías; no se validan.


            if campo.startswith(PREFIJOS_AUXILIARES) and not str(valor).strip():


                continue


            if str(valor).strip() and not es_texto_valido(valor):


                campos_invalidos.append(campo)


        fila_con_numero = {"Numero_Registro": numero, **fila}


        if campos_invalidos:


            fila_con_numero["Campos_invalidos"] = ", ".join(


                MAPEO_COLUMNAS.get(campo, campo) for campo in campos_invalidos


            )


            invalidos.append(fila_con_numero)


        else:


            validos.append(fila_con_numero)


    df_validos = pd.DataFrame(validos).rename(columns=MAPEO_COLUMNAS)


    df_invalidos = pd.DataFrame(invalidos).rename(columns=MAPEO_COLUMNAS)


    if not df_validos.empty:


        auxiliares_validos = [c for c in df_validos.columns if c.startswith(PREFIJOS_AUXILIARES)]


        df_validos = df_validos.drop(columns=auxiliares_validos, errors="ignore")


    if not df_invalidos.empty:


        auxiliares_invalidos = [c for c in df_invalidos.columns if c.startswith(PREFIJOS_AUXILIARES)]


        df_invalidos = df_invalidos.drop(columns=auxiliares_invalidos, errors="ignore")


    return df_validos, df_invalidos, len(registros), None


def cargar_xml(ruta_archivo: str | Path):

    """Lee un XML y devuelve los DataFrames, total y un mensaje de error si falla."""

    ruta = Path(ruta_archivo)


    try:

        contenido = ruta.read_bytes()

    except FileNotFoundError:

        return pd.DataFrame(), pd.DataFrame(), 0, (

            f"No se encuentra el archivo: {ruta}. "

            "Revisa el nombre y la ubicación del archivo XML."

        )

    except PermissionError:

        return pd.DataFrame(), pd.DataFrame(), 0, (

            f"No tienes permisos de lectura para el archivo: {ruta}. "

            "Revisa el propietario, el grupo y los permisos del archivo."

        )

    except IsADirectoryError:

        return pd.DataFrame(), pd.DataFrame(), 0, (

            f"La ruta indicada es una carpeta, no un archivo: {ruta}"

        )

    except OSError as exc:

        return pd.DataFrame(), pd.DataFrame(), 0, f"No se pudo abrir el archivo: {exc}"


    return analizar_contenido_xml(contenido)


# -----------------------------------------------------------------------------


# CONSOLA: mantiene el formato Rich del analizador original


# -----------------------------------------------------------------------------


def crear_grafico_barras(
    serie_datos: pd.Series,
    titulo: str,
    color: str = "magenta",
    limite: int = 8,
    total_referencia: int | None = None,
) -> Panel | None:
    """Construye un gráfico horizontal con caracteres Unicode para la terminal."""
    if serie_datos is None or serie_datos.empty:
        return None

    total = int(total_referencia if total_referencia is not None else serie_datos.sum())
    maximo = int(serie_datos.max()) if len(serie_datos) else 0
    if maximo <= 0:
        return None

    tabla = Table(show_header=True, box=None, padding=(0, 1), expand=True)
    tabla.add_column("Categoría", style="white", overflow="fold", ratio=3)
    tabla.add_column("Casos", justify="right", style="bold green", no_wrap=True)
    tabla.add_column("Gráfico", style=color, no_wrap=True)
    tabla.add_column("%", justify="right", style="yellow", no_wrap=True)

    for etiqueta, cantidad in serie_datos.head(limite).items():
        cantidad = int(cantidad)
        longitud = max(1, round((cantidad / maximo) * 22))
        barra = "█" * longitud + "░" * (22 - longitud)
        porcentaje = (cantidad / total * 100) if total else 0
        tabla.add_row(escape(str(etiqueta)), f"{cantidad:,}".replace(",", "."), barra, f"{porcentaje:.1f}%")

    return Panel(tabla, title=f"[bold]📊 {escape(titulo)}[/bold]", border_style=color)


def crear_tabla_frecuencias(


    df: pd.DataFrame,


    columna: str,


    titulo: str,


    limite: int = 10,


) -> Table | None:


    """Crea una tabla completa de frecuencias, cantidades y porcentajes."""


    if columna not in df.columns or df.empty:


        return None


    valores = df[columna].fillna("").map(normalizar_categoria)


    frecuencias = valores.value_counts()


    total = int(frecuencias.sum())


    tabla = Table(


        title=f"[bold yellow]{titulo}[/bold yellow]",


        header_style="bold cyan",


        show_lines=False,


    )


    tabla.add_column("Respuesta", style="white", overflow="fold", ratio=3)


    tabla.add_column("Total", justify="right", style="green")


    tabla.add_column("%", justify="right", style="yellow")


    tabla.add_column("Distribución", style="magenta", no_wrap=True)


    for etiqueta, cantidad in frecuencias.head(limite).items():


        porcentaje = (int(cantidad) / total * 100) if total else 0


        barra = "█" * min(20, max(1, round(porcentaje / 5)))


        tabla.add_row(


            str(etiqueta),


            str(int(cantidad)),


            f"{porcentaje:.1f}%",


            barra,


        )


    if len(frecuencias) > limite:


        tabla.caption = (


            f"Mostrando las {limite} respuestas más frecuentes "


            f"de {len(frecuencias)} categorías."


        )


    return tabla


def crear_tabla_opciones_multiples(


    df: pd.DataFrame,


    columna: str,


    titulo: str,


) -> Table | None:


    """Cuenta las opciones de campos donde se pueden elegir varias respuestas."""


    if columna not in df.columns or df.empty:


        return None


    frecuencias: Counter[str] = Counter()


    for respuesta in df[columna].fillna("").astype(str):


        if not respuesta.strip():


            continue


        opciones = [normalizar_categoria(x) for x in re.split(r",\s*", respuesta)]


        for opcion in opciones:


            if opcion != "No especificado":


                frecuencias[opcion] += 1


    if not frecuencias:


        return None


    tabla = Table(


        title=f"[bold yellow]{titulo}[/bold yellow]",


        header_style="bold cyan",


        show_lines=False,


    )


    tabla.add_column("Opción", style="white", overflow="fold", ratio=3)


    tabla.add_column("Veces", justify="right", style="green")


    tabla.add_column("% de incidencias", justify="right", style="yellow")


    total_incidencias = len(df)


    for opcion, cantidad in frecuencias.most_common():


        porcentaje = (cantidad / total_incidencias * 100) if total_incidencias else 0


        tabla.add_row(opcion, str(cantidad), f"{porcentaje:.1f}%")


    tabla.caption = (


        "Una incidencia puede incluir varias opciones; "


        "los porcentajes pueden sumar más del 100 %."


    )


    return tabla



def obtener_frecuencias_columna(
    df: pd.DataFrame,
    columna: str,
) -> pd.Series:
    """Devuelve el recuento de cada respuesta de una columna simple."""
    if df.empty or columna not in df.columns:
        return pd.Series(dtype="int64")

    valores = df[columna].fillna("").map(normalizar_categoria)
    return valores.value_counts()


def obtener_frecuencias_multiples(
    df: pd.DataFrame,
    columna: str,
) -> pd.Series:
    """Cuenta las opciones individuales de una pregunta de selección múltiple."""
    if df.empty or columna not in df.columns:
        return pd.Series(dtype="int64")

    frecuencias: Counter[str] = Counter()
    for respuesta in df[columna].fillna("").astype(str):
        if not respuesta.strip():
            continue
        for opcion in re.split(r",\s*", respuesta):
            opcion = normalizar_categoria(opcion)
            if opcion != "No especificado":
                frecuencias[opcion] += 1

    return pd.Series(dict(frecuencias.most_common()), dtype="int64")


def parsear_fecha_estricta(valor: Any):
    """Convierte fechas conocidas sin interpretar números sueltos como años."""
    texto = limpiar_respuesta_estadistica(valor)
    if not texto:
        return None
    for formato in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            fecha = datetime.strptime(texto, formato)
            # Evita que valores numéricos mal construidos se interpreten como
            # fechas históricas absurdas en el análisis de incidencias.
            return fecha if 2000 <= fecha.year <= 2100 else None
        except ValueError:
            continue
    return None


def obtener_frecuencias_mensuales(df: pd.DataFrame) -> pd.Series:
    """Agrupa las incidencias válidas por mes usando solo fechas bien formadas."""
    if df.empty or "Fecha_Incidencia" not in df.columns:
        return pd.Series(dtype="int64")

    valores = df["Fecha_Incidencia"].fillna("").astype(str)
    frecuencias: Counter[str] = Counter()
    for valor in valores:
        fecha = parsear_fecha_estricta(valor)
        if fecha is not None:
            frecuencias[fecha.strftime("%Y-%m")] += 1
    if not frecuencias:
        return pd.Series(dtype="int64")

    ordenadas = sorted(frecuencias.items())[-12:]
    return pd.Series({mes: cantidad for mes, cantidad in ordenadas}, dtype="int64")


def crear_tabla_completitud(
    df: pd.DataFrame,
    columnas: list[tuple[str, str]],
) -> Table | None:
    """Resume cuántas incidencias tienen respuesta en cada campo del formulario."""
    if df.empty:
        return None

    tabla = Table(
        title="[bold yellow]COMPLETITUD DE LOS CAMPOS DEL FORMULARIO[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
    )
    tabla.add_column("Campo", style="white", overflow="fold", ratio=3)
    tabla.add_column("Respondidas", justify="right", style="green")
    tabla.add_column("Sin respuesta", justify="right", style="red")
    tabla.add_column("Completitud", justify="right", style="yellow")

    total = len(df)
    resultados = []
    for columna, etiqueta in columnas:
        if columna not in df.columns:
            continue
        respuestas = df[columna].fillna("").astype(str).str.strip()
        contestadas = int(respuestas.ne("").sum())
        vacias = total - contestadas
        porcentaje = (contestadas / total * 100) if total else 0
        resultados.append((porcentaje, etiqueta, contestadas, vacias))

    # Los campos con menor porcentaje de respuesta aparecen primero.
    for porcentaje, etiqueta, contestadas, vacias in sorted(resultados):
        tabla.add_row(
            escape(etiqueta),
            str(contestadas),
            str(vacias),
            f"{porcentaje:.1f}%",
        )

    tabla.caption = f"Porcentaje calculado sobre las {total:,} incidencias válidas.".replace(",", ".")
    return tabla


def crear_tabla_cruce(
    df: pd.DataFrame,
    columna_fila: str,
    columna_columna: str,
    titulo: str,
    max_filas: int = 8,
    max_columnas: int = 5,
) -> Table | None:
    """Crea una tabla cruzada para estudiar la relación entre dos respuestas."""
    if df.empty or columna_fila not in df.columns or columna_columna not in df.columns:
        return None

    def serie_categorica(columna: str) -> pd.Series:
        valores = df[columna].fillna("").astype(str).str.strip()
        return valores.map(lambda valor: normalizar_categoria(valor) if valor else "Sin respuesta")

    filas = serie_categorica(columna_fila)
    columnas = serie_categorica(columna_columna)
    cruce = pd.crosstab(filas, columnas)
    if cruce.empty:
        return None

    mejores_filas = cruce.sum(axis=1).sort_values(ascending=False).head(max_filas).index
    mejores_columnas = cruce.sum(axis=0).sort_values(ascending=False).head(max_columnas).index
    cruce = cruce.loc[mejores_filas, mejores_columnas]

    tabla = Table(
        title=f"[bold yellow]{escape(titulo)}[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
    )
    tabla.add_column("Categoría", style="white", overflow="fold", ratio=2)
    for categoria in cruce.columns:
        tabla.add_column(escape(str(categoria)), justify="right", style="green")
    tabla.add_column("Total", justify="right", style="bold yellow")

    for categoria, fila in cruce.iterrows():
        valores = [str(int(fila.get(col, 0))) for col in cruce.columns]
        total_fila = int(fila.sum())
        tabla.add_row(escape(str(categoria)), *valores, str(total_fila))

    tabla.caption = (
        "Recuento de incidencias. Se muestran las categorías más frecuentes; "
        "el total de cada fila corresponde a las columnas visibles."
    )
    return tabla


def crear_tabla_dispositivo_sintoma(df: pd.DataFrame, limite: int = 12) -> Table | None:
    """Cuenta las combinaciones de dispositivo y síntoma, incluso con síntomas múltiples."""
    if df.empty or not {"Tipo_Dispositivo", "Sintoma_Principal"}.issubset(df.columns):
        return None

    frecuencias: Counter[tuple[str, str]] = Counter()
    for _, fila in df[["Tipo_Dispositivo", "Sintoma_Principal"]].fillna("").iterrows():
        dispositivo = normalizar_categoria(fila["Tipo_Dispositivo"])
        sintomas = [
            normalizar_categoria(opcion)
            for opcion in re.split(r",\s*", str(fila["Sintoma_Principal"]))
            if str(opcion).strip()
        ]
        # Un mismo síntoma no debe duplicarse dentro de una única incidencia.
        for sintoma in set(sintomas):
            if sintoma != "No especificado":
                frecuencias[(dispositivo, sintoma)] += 1

    if not frecuencias:
        return None

    tabla = Table(
        title="[bold yellow]COMBINACIONES MÁS FRECUENTES: DISPOSITIVO + SÍNTOMA[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
    )
    tabla.add_column("Dispositivo", style="cyan", overflow="fold")
    tabla.add_column("Síntoma", style="white", overflow="fold", ratio=3)
    tabla.add_column("Incidencias", justify="right", style="green")
    tabla.add_column("% de válidas", justify="right", style="yellow")

    total = len(df)
    for (dispositivo, sintoma), cantidad in frecuencias.most_common(limite):
        porcentaje = cantidad / total * 100 if total else 0
        tabla.add_row(
            escape(dispositivo),
            escape(sintoma),
            str(cantidad),
            f"{porcentaje:.1f}%",
        )

    tabla.caption = (
        "Una incidencia con varios síntomas aparece en más de una combinación; "
        "los porcentajes no tienen por qué sumar el 100 %."
    )
    return tabla


def crear_tabla_mensual(df: pd.DataFrame) -> Table | None:
    """Muestra el volumen mensual y la variación respecto al mes anterior."""
    frecuencias = obtener_frecuencias_mensuales(df)
    if frecuencias.empty:
        return None

    tabla = Table(
        title="[bold yellow]EVOLUCIÓN MENSUAL DE LAS INCIDENCIAS[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
    )
    tabla.add_column("Mes", style="white")
    tabla.add_column("Incidencias", justify="right", style="green")
    tabla.add_column("% de fechas interpretadas", justify="right", style="yellow")
    tabla.add_column("Variación mensual", justify="right", style="magenta")

    valores_fecha = df["Fecha_Incidencia"].fillna("").astype(str).str.strip()
    try:
        fechas_parseadas = pd.to_datetime(valores_fecha, format="mixed", dayfirst=True, errors="coerce")
    except TypeError:
        fechas_parseadas = pd.to_datetime(valores_fecha, dayfirst=True, errors="coerce")
    fechas_validas = int(fechas_parseadas.notna().sum())
    anterior = None
    for periodo, cantidad in frecuencias.items():
        cantidad = int(cantidad)
        porcentaje = cantidad / fechas_validas * 100 if fechas_validas else 0
        if anterior is None:
            variacion = "—"
        elif anterior == 0:
            variacion = "—"
        else:
            cambio = (cantidad - anterior) / anterior * 100
            variacion = f"{cambio:+.1f}%"
        tabla.add_row(str(periodo), str(cantidad), f"{porcentaje:.1f}%", variacion)
        anterior = cantidad

    tabla.caption = "La evolución utiliza la fecha declarada en cada incidencia; se muestran hasta los últimos 12 meses con datos."
    return tabla


def crear_tabla_mensajes_error(df: pd.DataFrame, limite: int = 10) -> Table | None:
    """Identifica los mensajes de error escritos que más se repiten."""
    if df.empty or "Mensaje_Error" not in df.columns:
        return None

    mensajes = df["Mensaje_Error"].fillna("").astype(str).str.strip()
    mensajes = mensajes[mensajes.ne("")]
    if mensajes.empty:
        return None

    frecuencias = mensajes.value_counts().head(limite)
    tabla = Table(
        title="[bold yellow]MENSAJES DE ERROR MÁS REPETIDOS[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
    )
    tabla.add_column("Mensaje comunicado", style="white", overflow="fold", ratio=4)
    tabla.add_column("Veces", justify="right", style="green")
    tabla.add_column("% de incidencias con mensaje", justify="right", style="yellow")

    total_mensajes = len(mensajes)
    for mensaje, cantidad in frecuencias.items():
        porcentaje = int(cantidad) / total_mensajes * 100 if total_mensajes else 0
        tabla.add_row(escape(str(mensaje)), str(int(cantidad)), f"{porcentaje:.1f}%")

    tabla.caption = f"Se han encontrado {total_mensajes} incidencias con un mensaje de error escrito."
    return tabla


def imprimir_tablas_estadisticas_adicionales(
    df_validos: pd.DataFrame,
    console: Console,
) -> None:
    """Imprime tablas de análisis complementario con información accionable."""
    if df_validos.empty:
        return

    console.print()
    console.print(Panel.fit(
        "[bold cyan]📚 TABLAS ESTADÍSTICAS ADICIONALES[/bold cyan]",
        border_style="cyan",
    ))

    tablas = [
        crear_tabla_completitud(
            df_validos,
            [
                ("Fecha_Incidencia", "Fecha de la incidencia"),
                ("Ubicacion", "Ubicación"),
                ("Tipo_Dispositivo", "Tipo de dispositivo"),
                ("Sintoma_Principal", "Síntoma principal"),
                ("Descripcion_Detallada", "Descripción detallada"),
                ("Tiempo_Desde_Fallo", "Tiempo desde el fallo"),
                ("Puede_Continuar_Trabajando", "Puede continuar trabajando"),
                ("Personas_Afectadas", "Personas afectadas"),
                ("Acciones_Previas", "Acciones previas"),
                ("Problema_Recurrente", "Problema recurrente"),
                ("Puede_Usar_Otro_Equipo", "Puede usar otro equipo"),
                ("Muestra_Mensaje_Error", "Aparece mensaje de error"),
                ("Mensaje_Error", "Texto del mensaje de error"),
            ],
        ),
        crear_tabla_cruce(
            df_validos,
            "Puede_Continuar_Trabajando",
            "Puede_Usar_Otro_Equipo",
            "IMPACTO OPERATIVO: CONTINUIDAD DEL TRABAJO × OTRO EQUIPO",
        ),
        crear_tabla_cruce(
            df_validos,
            "Tipo_Dispositivo",
            "Problema_Recurrente",
            "RECURRENCIA DEL PROBLEMA SEGÚN DISPOSITIVO",
        ),
        crear_tabla_dispositivo_sintoma(df_validos),
        crear_tabla_mensajes_error(df_validos),
        crear_tabla_mensual(df_validos),
    ]

    tablas = [tabla for tabla in tablas if tabla is not None]
    for tabla in tablas:
        console.print(tabla)
        console.print()


def imprimir_graficos_terminal(
    df_validos: pd.DataFrame,
    df_invalidos: pd.DataFrame,
    total: int,
    console: Console,
) -> None:
    """Muestra gráficos estadísticos directamente en la terminal, sin Matplotlib."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📈 GRÁFICOS ESTADÍSTICOS · INCIDENTIQ[/bold cyan]",
        border_style="cyan",
    ))

    cantidad_validos = len(df_validos)
    cantidad_invalidos = len(df_invalidos)

    graficos: list[Panel] = []

    calidad = pd.Series({
        "Válidas": cantidad_validos,
        "Descartadas": cantidad_invalidos,
    }, dtype="int64")
    grafico = crear_grafico_barras(
        calidad,
        "Calidad de los datos",
        color="cyan",
        limite=2,
        total_referencia=total,
    )
    if grafico:
        graficos.append(grafico)

    if not df_validos.empty:
        configuracion = [
            (obtener_frecuencias_columna(df_validos, "Tipo_Dispositivo"), "Incidencias por dispositivo", "blue"),
            (obtener_frecuencias_multiples(df_validos, "Sintoma_Principal"), "Síntomas comunicados", "magenta"),
            (obtener_frecuencias_columna(df_validos, "Ubicacion"), "Incidencias por ubicación", "cyan"),
            (obtener_frecuencias_columna(df_validos, "Puede_Continuar_Trabajando"), "Impacto en el trabajo", "yellow"),
            (obtener_frecuencias_columna(df_validos, "Problema_Recurrente"), "Problemas recurrentes", "green"),
            (obtener_frecuencias_multiples(df_validos, "Acciones_Previas"), "Acciones que ya se probaron", "bright_blue"),
            (obtener_frecuencias_columna(df_validos, "Personas_Afectadas"), "Personas afectadas", "bright_magenta"),
            (obtener_frecuencias_mensuales(df_validos), "Evolución mensual de incidencias", "bright_cyan"),
        ]

        for serie, titulo, color in configuracion:
            grafico = crear_grafico_barras(
                serie,
                titulo,
                color=color,
                limite=12 if "mensual" in titulo.lower() else 7,
                total_referencia=cantidad_validos,
            )
            if grafico:
                graficos.append(grafico)

    if not graficos:
        console.print("[dim]No hay datos suficientes para generar gráficos.[/dim]")
        return

    # Dos gráficos por fila para que sean legibles en una terminal normal.
    for indice in range(0, len(graficos), 2):
        console.print(Columns(graficos[indice:indice + 2], equal=True, expand=True))

    console.print(
        "[dim]Las barras muestran la frecuencia relativa de cada categoría. "
        "En preguntas de selección múltiple, una incidencia puede contar en varias opciones.[/dim]"
    )
    console.print()



def limpiar_respuesta_estadistica(valor: Any) -> str:
    """Limpia espacios sin alterar el contenido de la respuesta."""
    if valor is None:
        return ""
    return re.sub(r"\s+", " ", str(valor)).strip().replace("’", "'").replace("‘", "'")


MESES_ES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
    "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
BLOQUES_PARCIALES = " ▏▎▍▌▋▊▉"  # octavos de bloque para barras más precisas


def formatear_miles(numero: int) -> str:
    """12345 -> '12.345' sin tocar el resto del texto."""
    return f"{int(numero):,}".replace(",", ".")


def crear_barra(valor: int, maximo: int, ancho: int = 24) -> str:
    """Barra proporcional al valor máximo de la tabla (la más larga ocupa todo el ancho)."""
    if maximo <= 0 or valor <= 0:
        return "░" * ancho
    octavos = max(1, round(valor / maximo * ancho * 8))
    llenos, resto = divmod(octavos, 8)
    barra = "█" * llenos + (BLOQUES_PARCIALES[resto] if resto else "")
    return barra + "░" * (ancho - len(barra))


def clave_de_agrupacion(texto: str) -> str:
    """Clave para unir variantes de una misma respuesta ('No ho sé' / 'No ho sé.' / 'Si' / 'Sí')."""
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn"
    )
    return re.sub(r"\s+", " ", sin_tildes).casefold().strip(" .")


def contar_agrupando_variantes(respuestas: list[str], tipo: str) -> Counter:
    """Cuenta respuestas uniendo variantes y usando como etiqueta la mejor escrita."""
    por_clave: dict[str, Counter] = {}
    for respuesta in respuestas:
        por_clave.setdefault(clave_de_agrupacion(respuesta), Counter())[respuesta] += 1

    resultado: Counter = Counter()
    for variantes in por_clave.values():
        # Entre las variantes se prefiere la que lleva tildes y, después, la más frecuente.
        etiqueta = max(variantes, key=lambda v: (not v.isascii(), variantes[v]))
        if tipo != "texto":
            etiqueta = etiqueta.rstrip(" .")
        resultado[etiqueta] += sum(variantes.values())
    return resultado


def crear_tabla_respuestas_por_columna(
    df: pd.DataFrame,
    columna: str,
    pregunta: str,
    tipo: str = "simple",
    limite: int = 10,
) -> Table:
    """Crea una tabla de frecuencias con porcentaje y barra proporcional.

    Tipos: "simple" (una respuesta), "multiple" (varias separadas por coma),
    "fecha" (se agrupa por mes) y "texto" (respuesta libre).
    Los porcentajes de selección múltiple se calculan sobre todas las
    incidencias válidas; los demás, sobre las respuestas no vacías del campo.
    """
    tabla = Table(
        title=f"[bold yellow]{escape(pregunta)}[/bold yellow]",
        header_style="bold cyan",
        show_lines=False,
        expand=True,
    )
    tabla.add_column("Respuesta", style="white", overflow="fold", ratio=4)
    tabla.add_column("Casos", justify="right", style="bold green", no_wrap=True)
    tabla.add_column("Porcentaje", justify="right", style="yellow", no_wrap=True)
    tabla.add_column("Distribución", style="bright_blue", no_wrap=True)

    if df.empty or columna not in df.columns:
        tabla.add_row("Sin respuestas disponibles", "0", "0,0 %", "—")
        tabla.caption = f"Campo XML: {columna}. No se han encontrado datos para esta pregunta."
        return tabla

    respuestas = df[columna].fillna("").map(limpiar_respuesta_estadistica)
    frecuencias: Counter[str] = Counter()
    base_porcentaje = 0
    nota_base = ""
    orden_cronologico = False

    if tipo == "multiple":
        # Cada opción cuenta una sola vez por incidencia, aunque estuviera repetida.
        lista: list[str] = []
        for respuesta in respuestas:
            if not respuesta:
                continue
            opciones = {
                limpiar_respuesta_estadistica(opcion)
                for opcion in re.split(r",\s*", respuesta)
                if limpiar_respuesta_estadistica(opcion)
            }
            # Si "Sí" y "Si" aparecen a la vez en una misma incidencia, cuentan como una.
            unicas = {clave_de_agrupacion(o): o for o in opciones}
            lista.extend(unicas.values())
        frecuencias = contar_agrupando_variantes(lista, tipo)
        base_porcentaje = len(df)
        nota_base = (
            f"Porcentaje sobre las {formatear_miles(len(df))} incidencias válidas. "
            "Pregunta de selección múltiple: los porcentajes pueden sumar más del 100 %."
        )

    elif tipo == "fecha":
        # Solo fechas reales; se agrupan por mes para que la tabla sea legible.
        fechas_invalidas = 0
        for respuesta in respuestas:
            if not respuesta:
                continue
            fecha_valida = parsear_fecha_estricta(respuesta)
            if fecha_valida is None:
                fechas_invalidas += 1
                continue
            frecuencias[fecha_valida.strftime("%Y-%m")] += 1
        base_porcentaje = sum(frecuencias.values())
        orden_cronologico = True
        nota_base = (
            f"Incidencias agrupadas por mes. Porcentaje sobre {formatear_miles(base_porcentaje)} "
            f"fechas válidas; se han omitido {formatear_miles(fechas_invalidas)} sin formato de fecha reconocido."
        )

    else:
        contestadas = [r for r in respuestas if r]
        frecuencias = contar_agrupando_variantes(contestadas, tipo)
        base_porcentaje = len(contestadas)
        nota_base = (
            f"Porcentaje sobre {formatear_miles(base_porcentaje)} respuestas contestadas "
            f"de {formatear_miles(len(df))} incidencias válidas."
        )

    if not frecuencias:
        tabla.add_row("Sin respuestas válidas", "0", "0,0 %", "—")
        tabla.caption = nota_base or "No hay respuestas para mostrar."
        return tabla

    total_categorias = len(frecuencias)
    mostradas = frecuencias.most_common(limite)
    if orden_cronologico:
        mostradas.sort(key=lambda par: par[0])
    maximo = max(cantidad for _, cantidad in mostradas)

    for respuesta, cantidad in mostradas:
        porcentaje = (cantidad / base_porcentaje * 100) if base_porcentaje else 0.0

        etiqueta = respuesta
        if tipo == "fecha":
            anio, mes = respuesta.split("-")
            etiqueta = f"{MESES_ES[int(mes) - 1].capitalize()} {anio}"
        # Limita únicamente la representación visual de los campos de texto libre.
        elif tipo == "texto" and len(etiqueta) > 115:
            etiqueta = etiqueta[:112].rstrip() + "..."

        tabla.add_row(
            escape(etiqueta),
            formatear_miles(cantidad),
            f"{porcentaje:.1f} %",
            crear_barra(cantidad, maximo),
        )

    if total_categorias > limite:
        nota_base += f" Se muestran las {limite} más frecuentes de {total_categorias} distintas."
    tabla.caption = nota_base
    return tabla


def imprimir_tablas_por_pregunta(df_validos: pd.DataFrame, console: Console) -> None:
    """Imprime una tabla independiente por cada pregunta del formulario."""
    preguntas = [
        ("Fecha_Incidencia", "¿En qué fecha ocurrió la incidencia? (por mes)", "fecha", 12),
        ("Ubicacion", "¿Dónde está el equipo que tiene el problema?", "simple", 10),
        ("Tipo_Dispositivo", "¿Qué equipo tiene el problema?", "simple", 10),
        ("Sintoma_Principal", "¿Qué síntomas o problemas presenta el equipo?", "multiple", 12),
        ("Descripcion_Detallada", "Descripción detallada de la incidencia", "texto", 8),
        ("Tiempo_Desde_Fallo", "¿Desde cuándo ocurre el problema?", "simple", 10),
        ("Puede_Continuar_Trabajando", "¿Puedes continuar trabajando?", "simple", 10),
        ("Personas_Afectadas", "¿A quién afecta el problema?", "simple", 10),
        ("Acciones_Previas", "¿Qué acciones has probado para solucionarlo?", "multiple", 12),
        ("Problema_Recurrente", "¿Es un problema recurrente?", "simple", 10),
        ("Puede_Usar_Otro_Equipo", "¿Puedes utilizar otro equipo?", "simple", 10),
        ("Muestra_Mensaje_Error", "¿Aparece algún mensaje de error?", "simple", 10),
        ("Mensaje_Error", "¿Qué dice el mensaje de error?", "texto", 8),
    ]

    console.print(Panel.fit(
        "[bold cyan]📚 DISTRIBUCIÓN DE RESPUESTAS POR PREGUNTA[/bold cyan]\n"
        "[dim]Una tabla por pregunta · Solo incidencias que han superado la validación[/dim]",
        border_style="cyan",
    ))

    for columna, pregunta, tipo, limite in preguntas:
        tabla = crear_tabla_respuestas_por_columna(
            df_validos,
            columna,
            pregunta,
            tipo=tipo,
            limite=limite,
        )
        console.print(tabla)
        console.print()


def imprimir_consola_original(
    df_validos: pd.DataFrame,
    df_invalidos: pd.DataFrame,
    total: int,
) -> None:
    """Muestra resumen, tablas de respuestas, gráficos y registros descartados."""
    console = Console()
    cantidad_validos = len(df_validos)
    cantidad_invalidos = len(df_invalidos)
    porcentaje_validos = (cantidad_validos / total * 100) if total else 0
    porcentaje_invalidos = (cantidad_invalidos / total * 100) if total else 0

    resumen = (
        f"[bold green]✔ Incidencias Válidas:[/bold green] "
        f"{cantidad_validos:,} ({porcentaje_validos:.1f} %)\n"
        f"[bold red]✖ Incidencias Descartadas (Símbolos no permitidos):[/bold red] "
        f"{cantidad_invalidos:,} ({porcentaje_invalidos:.1f} %)\n"
        f"[bold white]Total procesado:[/bold white] {total:,}"
    ).replace(",", ".")

    console.print(Panel(
        resumen,
        title="[bold cyan]📋 Resumen de Datos y Filtrado[/bold cyan]",
        border_style="cyan",
        expand=False,
    ))
    console.print()

    if df_validos.empty:
        console.print("[bold red]No hay incidencias válidas para generar estadísticas.[/bold red]")
    else:
        if "Descripcion_Detallada" in df_validos.columns:
            descripciones = df_validos["Descripcion_Detallada"].fillna("").astype(str).str.strip()
            con_descripcion = int(descripciones.ne("").sum())
            console.print(
                f"[bold]Incidencias con descripción detallada:[/bold] "
                f"{con_descripcion:,}/{cantidad_validos:,}".replace(",", ".")
            )
        if "Mensaje_Error" in df_validos.columns:
            mensajes = df_validos["Mensaje_Error"].fillna("").astype(str).str.strip()
            con_mensaje = int(mensajes.ne("").sum())
            console.print(
                f"[bold]Incidencias con mensaje de error escrito:[/bold] "
                f"{con_mensaje:,}/{cantidad_validos:,}".replace(",", ".")
            )
        console.print()
        imprimir_tablas_por_pregunta(df_validos, console)

    imprimir_graficos_terminal(df_validos, df_invalidos, total, console)

    # Se presentan al final para que nunca entren en las estadísticas de respuestas.
    if not df_invalidos.empty:
        tabla_descartes = Table(
            title="[bold red]REGISTROS DESCARTADOS (REVISAR)[/bold red]",
            header_style="bold red",
            show_lines=False,
        )
        tabla_descartes.add_column("N.º registro", justify="right", style="white")
        tabla_descartes.add_column("Campos con caracteres no permitidos", style="yellow", overflow="fold")
        tabla_descartes.add_column("Dispositivo", style="cyan", overflow="fold")

        for _, fila in df_invalidos.iterrows():
            tabla_descartes.add_row(
                str(fila.get("Numero_Registro", "")),
                str(fila.get("Campos_invalidos", "")),
                str(fila.get("Tipo_Dispositivo", "")),
            )

        console.print(tabla_descartes)
        console.print(
            "[dim]Los registros descartados se conservan en el análisis "
            "y también pueden revisarse desde el dashboard web.[/dim]"
        )


# -----------------------------------------------------------------------------


# API web local


# -----------------------------------------------------------------------------


DASHBOARD_HTML = r'''<!doctype html>


<html lang="es">


<head>


<meta charset="utf-8">


<meta name="viewport" content="width=device-width, initial-scale=1">


<meta name="theme-color" content="#101827">


<title>IncidentIQ · Analytics</title>


<style>


:root{--bg:#f4f6fb;--surface:#fff;--surface-2:#f8faff;--ink:#182338;--muted:#75829a;--line:#e6eaf2;--nav:#101827;--nav2:#172338;--blue:#5278ee;--cyan:#22b8c7;--purple:#8b6df1;--green:#20a77a;--amber:#e5a33b;--red:#df6375;--shadow:0 12px 34px rgba(29,43,76,.07);--radius:18px}


*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}button,input,select{font:inherit}button{cursor:pointer}.app{min-height:100vh;display:grid;grid-template-columns:248px minmax(0,1fr)}


.sidebar{background:linear-gradient(180deg,var(--nav),#121e31);color:#eaf0ff;padding:24px 16px;display:flex;flex-direction:column;gap:26px}.brand{display:flex;align-items:center;gap:11px;padding:2px 8px 16px}.brand-mark{width:39px;height:39px;border-radius:13px;background:linear-gradient(135deg,#5278ee,#22b8c7);display:grid;place-items:center;color:white;font-weight:900;font-size:19px;box-shadow:0 8px 20px #5278ee44}.brand-name{font-weight:850;font-size:16px;letter-spacing:.04em}.brand-sub{color:#8fa2c1;font-size:9px;letter-spacing:.16em;font-weight:700}.nav-label{padding:0 11px;color:#71829f;font-size:10px;font-weight:800;letter-spacing:.14em}.nav{display:grid;gap:6px}.nav button{background:transparent;border:0;color:#a9b6cc;text-align:left;padding:12px 13px;border-radius:11px;display:flex;align-items:center;gap:12px;font-weight:650;transition:.18s}.nav button:hover{background:#ffffff0d;color:white}.nav button.active{background:linear-gradient(90deg,#5278ee33,#5278ee12);color:white;box-shadow:inset 3px 0 #6f91ff}.nav-ico{width:20px;text-align:center;font-size:16px}.side-bottom{margin-top:auto;background:#ffffff0a;border:1px solid #ffffff10;border-radius:14px;padding:14px;color:#91a2bd;font-size:11px;overflow-wrap:anywhere}.side-bottom strong{display:block;color:#e8efff;font-size:12px;margin:5px 0}


main{min-width:0;padding:28px clamp(16px,3vw,42px) 40px}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:25px}.eyebrow{color:var(--blue);font-size:10px;font-weight:850;letter-spacing:.15em;text-transform:uppercase}.page-title{font-size:clamp(24px,3vw,31px);letter-spacing:-.04em;font-weight:820;margin:3px 0 4px}.subtitle{color:var(--muted);font-size:13px}.top-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:white;color:#34415a;border-radius:10px;padding:10px 13px;font-size:12px;font-weight:750;box-shadow:0 2px 5px #1d2b4c08}.btn:hover{border-color:#b8c7f2;transform:translateY(-1px)}.btn.primary{background:var(--blue);border-color:var(--blue);color:white;box-shadow:0 7px 16px #5278ee30}.btn.dark{background:#172338;border-color:#172338;color:white}.btn.small{padding:7px 10px;font-size:11px}.upload-label{display:inline-flex;align-items:center;gap:8px}.upload-label input{display:none}


.kpis{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:13px;margin-bottom:17px}.kpi,.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow)}.kpi{padding:17px 17px 15px;position:relative;overflow:hidden}.kpi:after{content:"";position:absolute;width:70px;height:70px;border-radius:50%;right:-25px;top:-27px;background:var(--tint)}.kpi-head{display:flex;align-items:center;justify-content:space-between;color:var(--muted);font-size:11px;font-weight:700;gap:5px}.kpi-icon{width:28px;height:28px;border-radius:9px;background:var(--tint);display:grid;place-items:center;color:var(--accent);font-size:14px}.kpi-value{font-size:clamp(23px,2.5vw,30px);font-weight:850;letter-spacing:-.045em;margin:8px 0 2px}.kpi-note{font-size:10px;color:var(--muted)}


.grid{display:grid;gap:15px}.grid.two{grid-template-columns:1fr 1fr}.grid.three{grid-template-columns:repeat(3,1fr)}.card{padding:18px;min-width:0}.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:13px}.card-title{font-size:14px;font-weight:800;letter-spacing:-.015em}.card-sub{font-size:11px;color:var(--muted);margin-top:2px}.tag{font-size:10px;padding:5px 8px;border-radius:20px;background:#eef2ff;color:#536fe0;font-weight:750;white-space:nowrap}.chart{width:100%;height:255px;display:block}.chart.short{height:218px}.chart.large{height:290px}.insights{display:grid;grid-template-columns:repeat(3,1fr);gap:13px;margin:15px 0}.insight{padding:15px 16px;border-radius:15px;background:linear-gradient(135deg,#fff,#f9fbff);border:1px solid var(--line)}.insight-label{font-size:10px;color:var(--muted);font-weight:800;text-transform:uppercase;letter-spacing:.08em}.insight-value{font-size:14px;font-weight:800;margin:6px 0 2px}.insight-note{font-size:11px;color:var(--muted)}


.section-title{font-size:17px;font-weight:820;letter-spacing:-.025em;margin:25px 0 12px}.toolbar{display:flex;align-items:center;gap:9px;flex-wrap:wrap;margin-bottom:14px}.field{background:white;border:1px solid var(--line);border-radius:10px;padding:10px 12px;color:var(--ink);outline:none;min-width:155px}.field:focus{border-color:#91a8f5;box-shadow:0 0 0 3px #5278ee14}.field.search{flex:1;min-width:190px}.table-card{padding:0;overflow:hidden}.table-wrap{overflow:auto;max-height:calc(100vh - 290px);min-height:260px}.data-table{width:100%;border-collapse:collapse;white-space:nowrap;font-size:12px}.data-table th{position:sticky;top:0;background:#f8faff;color:#74819a;text-align:left;padding:12px 14px;font-size:10px;text-transform:uppercase;letter-spacing:.06em;z-index:1;border-bottom:1px solid var(--line)}.data-table td{padding:12px 14px;border-bottom:1px solid #edf0f5;color:#3d4960;max-width:280px;overflow:hidden;text-overflow:ellipsis}.data-table tr:hover td{background:#f6f8ff}.data-table tbody tr{cursor:pointer}.pill{display:inline-flex;align-items:center;gap:5px;border-radius:30px;padding:4px 8px;background:#eaf8f2;color:#16815d;font-size:10px;font-weight:800}.pill.red{background:#fff0f2;color:#c44b60}.pill.blue{background:#edf2ff;color:#536fe0}.table-foot{display:flex;justify-content:space-between;gap:10px;align-items:center;padding:12px 15px;color:var(--muted);font-size:11px}.pagination{display:flex;gap:6px}.pagination button{border:1px solid var(--line);background:white;border-radius:8px;padding:6px 10px;color:#47546b}.pagination button:disabled{opacity:.35;cursor:default}


.empty{padding:42px 15px;text-align:center;color:var(--muted)}.warning-banner{padding:12px 14px;border:1px solid #f4dfb3;background:#fff9ed;color:#8c6419;border-radius:12px;font-size:12px;margin-bottom:15px}.muted{color:var(--muted)}.modal-backdrop{position:fixed;inset:0;background:#10182799;z-index:20;display:grid;place-items:center;padding:16px}.modal{background:white;width:min(760px,100%);max-height:88vh;overflow:auto;border-radius:20px;box-shadow:0 25px 80px #0004}.modal-head{padding:19px 22px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;align-items:center}.modal-head h3{margin:0;font-size:17px}.modal-body{padding:20px 22px;display:grid;grid-template-columns:1fr 1fr;gap:13px}.detail{padding:11px 12px;border:1px solid var(--line);border-radius:11px;min-width:0}.detail span{display:block;font-size:9px;text-transform:uppercase;letter-spacing:.08em;font-weight:800;color:var(--muted);margin-bottom:5px}.detail strong{font-size:12px;white-space:pre-wrap;overflow-wrap:anywhere;font-weight:650}.close{border:0;background:#f1f4fa;border-radius:9px;width:32px;height:32px;font-size:18px;color:#66738a}.toast{position:fixed;right:22px;bottom:22px;background:#172338;color:white;padding:12px 16px;border-radius:11px;box-shadow:0 10px 30px #0003;z-index:30;font-size:12px;opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none}.toast.show{opacity:1;transform:translateY(0)}.footer-note{color:#9aa5b7;text-align:center;font-size:10px;margin-top:24px}


@media(max-width:1200px){.kpis{grid-template-columns:repeat(3,1fr)}.grid.three{grid-template-columns:1fr 1fr}.app{grid-template-columns:220px 1fr}}


@media(max-width:850px){.app{display:block}.sidebar{padding:12px 14px;gap:10px}.brand{padding:0 4px 8px}.side-bottom,.nav-label{display:none}.nav{display:flex;overflow:auto}.nav button{white-space:nowrap;padding:9px 11px}.topbar{align-items:flex-start}.grid.two{grid-template-columns:1fr}.insights{grid-template-columns:1fr 1fr}main{padding-top:20px}}


@media(max-width:560px){.kpis{grid-template-columns:1fr 1fr}.kpi:last-child{grid-column:span 2}.grid.three,.insights{grid-template-columns:1fr}.topbar{display:block}.top-actions{margin-top:12px}.modal-body{grid-template-columns:1fr}.table-wrap{max-height:none}.subtitle{max-width:95%}}


</style>


</head>


<body>


<div class="app">


<aside class="sidebar">


  <div class="brand"><div class="brand-mark">◇</div><div><div class="brand-name">INCIDENTIQ</div><div class="brand-sub">ANALYTICS STUDIO</div></div></div>


  <div class="nav-label">WORKSPACE</div>


  <nav class="nav" aria-label="Navegación">


    <button class="active" data-page="overview"><span class="nav-ico">▦</span>Resumen general</button>


    <button data-page="valid"><span class="nav-ico">✓</span>Incidencias válidas</button>


    <button data-page="invalid"><span class="nav-ico">⚠</span>Descartadas</button>


  </nav>


  <div class="side-bottom"><span>FUENTE DE DATOS</span><strong id="source-name">Cargando XML…</strong><span>Validación · Estadística · Exploración</span></div>


</aside>


<main>


  <header class="topbar">


    <div><div class="eyebrow">IT OPERATIONS / ANALYTICS</div><div class="page-title" id="page-title">Resumen general</div><div class="subtitle" id="page-subtitle">Una visión clara del estado de las incidencias informáticas.</div></div>


    <div class="top-actions">


      <label class="btn upload-label">↑ Cargar XML<input id="xml-file" type="file" accept=".xml,text/xml,application/xml"></label>


      <button class="btn" id="export-json">↓ JSON</button>


      <button class="btn" id="export-html">↓ Informe HTML</button>


      <button class="btn primary" id="export-valid">↓ Exportar CSV</button>


    </div>


  </header>


  <section id="page-content"><div class="empty">Cargando datos del análisis…</div></section>


  <div class="footer-note">IncidentIQ · Análisis local en tu navegador · Los datos no se envían a Internet</div>


</main>


</div>


<div class="toast" id="toast" role="status"></div>


<div id="modal-root"></div>


<script>


const state={valid:[],invalid:[],total:0,file:'',page:'overview',search:'',device:'Todos',location:'Todas',pageNo:1,pageSize:20};


const COLORS=['#5278ee','#22b8c7','#8b6df1','#20a77a','#e5a33b','#df6375','#5e9bd4','#9b83e8'];


const TITLES={overview:['Resumen general','Una visión clara del estado de las incidencias informáticas.'],valid:['Incidencias válidas','Busca, filtra y consulta los registros que han superado la validación.'],invalid:['Registros descartados','Revisa los registros que contienen caracteres fuera de la política de validación.']};


const $=s=>document.querySelector(s);


const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));


const fmt=n=>Number(n||0).toLocaleString('es-ES');


function norm(v){return String(v??'').trim().replace(/[’‘]/g,"'").replace(/\s+/g,' ').replace(/[ .]+$/,'')||'No especificado'}


function countBy(rows,key,limit=12){const c=new Map();rows.forEach(r=>{const k=norm(r[key]);c.set(k,(c.get(k)||0)+1)});return [...c.entries()].sort((a,b)=>b[1]-a[1]).slice(0,limit)}


function pct(n,total){return total?((n/total)*100):0}


function showToast(msg){const el=$('#toast');el.textContent=msg;el.classList.add('show');clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>el.classList.remove('show'),2800)}


async function loadData(){const res=await fetch('/api/data');if(!res.ok)throw new Error('No se pudieron cargar los datos');const d=await res.json();applyData(d)}


function applyData(d){state.valid=d.validos||[];state.invalid=d.invalidos||[];state.total=d.total||state.valid.length+state.invalid.length;state.file=d.archivo||'XML cargado';$('#source-name').textContent=state.file;render()}


function pageShell(){const [title,sub]=TITLES[state.page];$('#page-title').textContent=title;$('#page-subtitle').textContent=sub;document.querySelectorAll('.nav button').forEach(b=>b.classList.toggle('active',b.dataset.page===state.page));}


function render(){pageShell();if(state.page==='overview')renderOverview();else renderRecords(state.page==='valid'?state.valid:state.invalid,state.page==='invalid')}


function kpi(label,value,note,icon,color,tint){return `<article class="kpi" style="--accent:${color};--tint:${tint}"><div class="kpi-head"><span>${label}</span><span class="kpi-icon">${icon}</span></div><div class="kpi-value">${value}</div><div class="kpi-note">${note}</div></article>`}


function card(title,sub,tag,canvasId,short=false){return `<article class="card"><div class="card-head"><div><div class="card-title">${title}</div><div class="card-sub">${sub}</div></div>${tag?`<span class="tag">${tag}</span>`:''}</div><canvas class="chart ${short?'short':''}" id="${canvasId}"></canvas></article>`}


function renderOverview(){


 const n=state.valid.length, bad=state.invalid.length, rate=pct(n,state.total);


 const cant=countBy(state.valid,'Puede_Continuar_Trabajando');


 const noWork=state.valid.filter(r=>/^no\b/i.test(String(r.Puede_Continuar_Trabajando||'').trim())).length;


 const topDevice=countBy(state.valid,'Tipo_Dispositivo',1)[0], topSym=countBy(state.valid,'Sintoma_Principal',1)[0], topLoc=countBy(state.valid,'Ubicacion',1)[0];


 $('#page-content').innerHTML=`


 <div class="kpis">


 ${kpi('Registros procesados',fmt(state.total),'Total de incidencias leídas','▤','#5278ee','#edf1ff')}


 ${kpi('Incidencias válidas',fmt(n),`${rate.toFixed(1)}% del total`,'✓','#20a77a','#e9f8f1')}


 ${kpi('Descartadas',fmt(bad),`${pct(bad,state.total).toFixed(1)}% requieren revisión`,'⚠','#df6375','#fff0f2')}


 ${kpi('Tasa de validez',`${rate.toFixed(1)}%`,'Calidad del conjunto de datos','◉','#8b6df1','#f1edff')}


 ${kpi('No pueden trabajar',fmt(noWork),`${pct(noWork,n).toFixed(1)}% de las válidas`,'⌁','#e5a33b','#fff6e6')}


 </div>


 <div class="grid two">


 ${card('Calidad de los datos','Distribución entre registros válidos y descartados','VALIDACIÓN','chart-quality',true)}


 ${card('Incidencias por dispositivo','Qué equipos concentran más avisos','TOP CATEGORÍAS','chart-devices')}


 ${card('Síntomas más frecuentes','Problemas comunicados por los usuarios','TOP SÍNTOMAS','chart-symptoms')}


 ${card('Evolución temporal','Incidencias por fecha declarada','CRONOLOGÍA','chart-trend')}


 ${card('Incidencias por ubicación','Espacios con más incidencias','UBICACIONES','chart-locations')}


 ${card('Impacto en el trabajo','Capacidad de continuar trabajando','IMPACTO','chart-impact')}


 </div>


 <div class="section-title">Hallazgos destacados</div>


 <div class="insights">


 ${insight('Dispositivo más afectado',topDevice, n)}


 ${insight('Síntoma más frecuente',topSym,n)}


 ${insight('Ubicación con más avisos',topLoc,n)}


 </div>


 <div class="grid two">


 ${miniStats('Problemas recurrentes','Problema_Recurrente')}


 ${miniStats('Disponibilidad de otro equipo','Puede_Usar_Otro_Equipo')}


 </div>`;


 requestAnimationFrame(drawAllCharts);


}


function insight(title,item,total){if(!item)return `<div class="insight"><div class="insight-label">${title}</div><div class="insight-value">Sin datos</div></div>`;return `<div class="insight"><div class="insight-label">${title}</div><div class="insight-value">${esc(item[0])}</div><div class="insight-note">${fmt(item[1])} registros · ${pct(item[1],total).toFixed(1)}% de válidas</div></div>`}


function miniStats(title,key){const values=countBy(state.valid,key,5);return `<article class="card"><div class="card-head"><div class="card-title">${title}</div><span class="tag">DISTRIBUCIÓN</span></div>${values.length?values.map(([k,v])=>`<div style="display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid #edf0f5;font-size:12px"><span>${esc(k)}</span><strong>${fmt(v)} <span class="muted" style="font-weight:500">(${pct(v,state.valid.length).toFixed(1)}%)</span></strong></div>`).join(''):'<div class="empty">Sin datos disponibles</div>'}</article>`}


function setupCanvas(canvas){const rect=canvas.getBoundingClientRect(),dpr=window.devicePixelRatio||1;canvas.width=Math.max(1,Math.floor(rect.width*dpr));canvas.height=Math.max(1,Math.floor(rect.height*dpr));const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);return {ctx,w:rect.width,h:rect.height}}


function drawDonut(id,values,labels,colors){const c=document.getElementById(id);if(!c)return;const {ctx,w,h}=setupCanvas(c);const total=values.reduce((a,b)=>a+b,0);const cx=w/2,cy=h/2,r=Math.min(w,h)*.32,thick=Math.max(17,r*.25);let a=-Math.PI/2;if(!total){ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.strokeStyle='#e9edf5';ctx.lineWidth=thick;ctx.stroke()}else values.forEach((v,i)=>{const span=v/total*Math.PI*2;ctx.beginPath();ctx.arc(cx,cy,r,a,a+span);ctx.strokeStyle=colors[i];ctx.lineWidth=thick;ctx.lineCap='round';ctx.stroke();a+=span});ctx.textAlign='center';ctx.fillStyle='#182338';ctx.font='800 27px system-ui';ctx.fillText(total?`${pct(values[0],total).toFixed(1)}%`:'—',cx,cy+3);ctx.fillStyle='#75829a';ctx.font='11px system-ui';ctx.fillText(total?'REGISTROS VÁLIDOS':'Sin registros',cx,cy+23);const ly=cy+r+thick*.9;let lx=cx-(labels.length*88)/2;labels.forEach((l,i)=>{ctx.fillStyle=colors[i];ctx.beginPath();ctx.arc(lx,ly,4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#65728a';ctx.textAlign='left';ctx.font='11px system-ui';ctx.fillText(`${l} · ${fmt(values[i])}`,lx+9,ly+4);lx+=labels.length===2?112:88})}


function drawBars(id,data,color){const c=document.getElementById(id);if(!c)return;const {ctx,w,h}=setupCanvas(c);ctx.clearRect(0,0,w,h);if(!data.length){ctx.fillStyle='#8b96a8';ctx.font='12px system-ui';ctx.fillText('Sin datos disponibles',12,30);return}const rows=data.slice(0,7),left=Math.min(155,w*.38),right=43,top=8,rowH=Math.min(34,(h-20)/rows.length),max=Math.max(...rows.map(x=>x[1]),1),barW=w-left-right;rows.forEach(([label,value],i)=>{const y=top+i*rowH;ctx.fillStyle='#64718a';ctx.font='11px system-ui';ctx.textAlign='right';let t=String(label);while(ctx.measureText(t).width>left-12&&t.length>5)t=t.slice(0,-2)+'…';ctx.fillText(t,left-10,y+rowH*.58);ctx.fillStyle='#eef1f7';roundRect(ctx,left,y+rowH*.22,barW,rowH*.42,5);ctx.fill();ctx.fillStyle=color;roundRect(ctx,left,y+rowH*.22,Math.max(4,barW*value/max),rowH*.42,5);ctx.fill();ctx.fillStyle='#354159';ctx.textAlign='left';ctx.font='700 11px system-ui';ctx.fillText(fmt(value),Math.min(w-34,left+barW*value/max+8),y+rowH*.58)})}


function roundRect(ctx,x,y,w,h,r){ctx.beginPath();ctx.roundRect(x,y,Math.max(0,w),h,r)}


function parseDate(v){
 const s=String(v||'').trim();
 let m=s.match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})/);
 if(m){const d=new Date(+m[3],+m[2]-1,+m[1]);return isNaN(d)?null:d}
 m=s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})/);
 if(m){const d=new Date(+m[1],+m[2]-1,+m[3]);return isNaN(d)?null:d}
 return null;
}

function drawTrend(id){const c=document.getElementById(id);if(!c)return;const {ctx,w,h}=setupCanvas(c);const counts=new Map();state.valid.forEach(r=>{const d=parseDate(r.Fecha_Incidencia);if(d){const k=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-');counts.set(k,(counts.get(k)||0)+1)}});const data=[...counts.entries()].sort((a,b)=>a[0].localeCompare(b[0]));if(!data.length){ctx.fillStyle='#8b96a8';ctx.font='12px system-ui';ctx.fillText('No se pudieron interpretar las fechas',12,30);return}const pad={l:34,r:18,t:20,b:35},max=Math.max(...data.map(x=>x[1]),1),cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;ctx.strokeStyle='#edf0f5';ctx.lineWidth=1;for(let i=0;i<4;i++){const y=pad.t+ch*i/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke()}const pts=data.map((d,i)=>({x:pad.l+(data.length===1?cw/2:i*cw/(data.length-1)),y:pad.t+ch-(d[1]/max)*ch,label:d[0].slice(8)+'/'+d[0].slice(5,7),v:d[1]}));ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.strokeStyle='#8b6df1';ctx.lineWidth=2.6;ctx.stroke();if(pts.length>1){ctx.lineTo(pts[pts.length-1].x,pad.t+ch);ctx.lineTo(pts[0].x,pad.t+ch);ctx.closePath();const g=ctx.createLinearGradient(0,pad.t,0,pad.t+ch);g.addColorStop(0,'#8b6df133');g.addColorStop(1,'#8b6df100');ctx.fillStyle=g;ctx.fill()}pts.forEach(p=>{ctx.beginPath();ctx.arc(p.x,p.y,3.5,0,Math.PI*2);ctx.fillStyle='#8b6df1';ctx.fill();ctx.fillStyle='#68758c';ctx.font='10px system-ui';ctx.textAlign='center';ctx.fillText(p.label,p.x,h-10);ctx.fillStyle='#354159';ctx.font='10px system-ui';ctx.fillText(p.v,p.x,p.y-9)})}


function drawAllCharts(){drawDonut('chart-quality',[state.valid.length,state.invalid.length],['Válidas','Descartadas'],['#20a77a','#df6375']);drawBars('chart-devices',countBy(state.valid,'Tipo_Dispositivo',7),'#5278ee');drawBars('chart-symptoms',countBy(state.valid,'Sintoma_Principal',7),'#22b8c7');drawTrend('chart-trend');drawBars('chart-locations',countBy(state.valid,'Ubicacion',7),'#8b6df1');drawBars('chart-impact',countBy(state.valid,'Puede_Continuar_Trabajando',7),'#e5a33b')}


function renderRecords(rows,invalid){state.search='';state.pageNo=1;const devices=[...new Set(rows.map(r=>norm(r.Tipo_Dispositivo)))].sort();const locations=[...new Set(rows.map(r=>norm(r.Ubicacion)))].sort();const title=invalid?'Registros que no han superado la validación':'Registros disponibles para el análisis';$('#page-content').innerHTML=`${invalid?'<div class="warning-banner">Estos registros se conservan para su revisión. La columna «Campos inválidos» indica dónde se detectaron caracteres no permitidos.</div>':''}<div class="toolbar"><input class="field search" id="record-search" placeholder="Buscar por texto, síntoma, descripción, ubicación…"><select class="field" id="filter-device"><option value="">Todos los dispositivos</option>${devices.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('')}</select><select class="field" id="filter-location"><option value="">Todas las ubicaciones</option>${locations.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('')}</select><button class="btn" id="reset-filters">Limpiar filtros</button><button class="btn primary" id="export-current">↓ Exportar CSV</button></div><div class="card table-card"><div class="table-wrap"><table class="data-table"><thead id="records-head"></thead><tbody id="records-body"></tbody></table><div class="empty" id="records-empty" hidden>No hay registros que coincidan con los filtros.</div></div><div class="table-foot"><span id="records-count">${title}</span><div class="pagination"><button id="prev-page">←</button><span id="page-number" style="padding:6px 8px">1</span><button id="next-page">→</button></div></div></div>`;


 $('#records-head').innerHTML=`<tr>${(invalid?['Numero_Registro','Campos_invalidos','Tipo_Dispositivo','Ubicacion','Sintoma_Principal']:['Numero_Registro','Fecha_Incidencia','Tipo_Dispositivo','Sintoma_Principal','Ubicacion','Puede_Continuar_Trabajando','Personas_Afectadas','Problema_Recurrente']).map(x=>`<th>${esc(x.replaceAll('_',' '))}</th>`).join('')}</tr>`;


 const cols=invalid?['Numero_Registro','Campos_invalidos','Tipo_Dispositivo','Ubicacion','Sintoma_Principal']:['Numero_Registro','Fecha_Incidencia','Tipo_Dispositivo','Sintoma_Principal','Ubicacion','Puede_Continuar_Trabajando','Personas_Afectadas','Problema_Recurrente'];


 const getFiltered=()=>{const q=($('#record-search').value||'').trim().toLocaleLowerCase();const dev=$('#filter-device').value,loc=$('#filter-location').value;return rows.filter(r=>(!dev||norm(r.Tipo_Dispositivo)===dev)&&(!loc||norm(r.Ubicacion)===loc)&&(!q||Object.values(r).join(' ').toLocaleLowerCase().includes(q)))};


 function refresh(){const filtered=getFiltered(),pages=Math.max(1,Math.ceil(filtered.length/state.pageSize));state.pageNo=Math.min(state.pageNo,pages);const start=(state.pageNo-1)*state.pageSize,visible=filtered.slice(start,start+state.pageSize);$('#records-body').innerHTML=visible.map((r)=>`<tr data-record="${esc(r.Numero_Registro)}">${cols.map(col=>`<td title="${esc(r[col]||'')}">${col==='Campos_invalidos'?`<span class="pill red">${esc(r[col]||'—')}</span>`:col==='Numero_Registro'?`<strong>#${esc(r[col])}</strong>`:esc(r[col]||'—')}</td>`).join('')}</tr>`).join('');$('#records-empty').hidden=filtered.length!==0;$('#records-count').textContent=`Mostrando ${filtered.length?start+1:0}–${Math.min(start+state.pageSize,filtered.length)} de ${fmt(filtered.length)} registros · Pulsa una fila para ver el detalle`;$('#page-number').textContent=`${state.pageNo} / ${pages}`;$('#prev-page').disabled=state.pageNo<=1;$('#next-page').disabled=state.pageNo>=pages;}


 ['record-search','filter-device','filter-location'].forEach(id=>$('#'+id).addEventListener('input',()=>{state.pageNo=1;refresh()}));$('#reset-filters').addEventListener('click',()=>{$('#record-search').value='';$('#filter-device').value='';$('#filter-location').value='';state.pageNo=1;refresh()});$('#prev-page').addEventListener('click',()=>{state.pageNo--;refresh()});$('#next-page').addEventListener('click',()=>{state.pageNo++;refresh()});$('#records-body').addEventListener('click',e=>{const tr=e.target.closest('tr[data-record]');if(!tr)return;const row=rows.find(r=>String(r.Numero_Registro)===tr.dataset.record);if(row)showDetails(row,invalid)});$('#export-current').addEventListener('click',()=>downloadCSV(rows,invalid?'incidencias_descartadas.csv':'incidencias_validas.csv'));refresh();}


function showDetails(row,invalid){$('#modal-root').innerHTML=`<div class="modal-backdrop" id="modal-backdrop"><div class="modal"><div class="modal-head"><div><h3>Incidencia #${esc(row.Numero_Registro)}</h3><div class="card-sub">${invalid?'Registro descartado':'Registro válido'} · Detalle del formulario</div></div><button class="close" id="close-modal">×</button></div><div class="modal-body">${Object.entries(row).filter(([k])=>k!=='Numero_Registro').map(([k,v])=>`<div class="detail"><span>${esc(k.replaceAll('_',' '))}</span><strong>${esc(String(v||'Sin respuesta'))}</strong></div>`).join('')}</div></div></div>`;$('#close-modal').onclick=()=>$('#modal-root').innerHTML='';$('#modal-backdrop').addEventListener('click',e=>{if(e.target.id==='modal-backdrop')$('#modal-root').innerHTML=''})}


function downloadBlob(content,name,type){const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);showToast(`${name} descargado`)}


function downloadCSV(rows,name){
 const cols=[...new Set(rows.flatMap(r=>Object.keys(r)))];
 const quote=v=>'"'+String(v??'').replace(/"/g,'""')+'"';
 const text='\ufeff'+[cols.map(quote).join(','),...rows.map(r=>cols.map(c=>quote(r[c])).join(','))].join('\r\n');
 downloadBlob(text,name,'text/csv;charset=utf-8');
}

function exportJSON(){downloadBlob(JSON.stringify({total:state.total,validos:state.valid,descartados:state.invalid},null,2),'informe_incidencias.json','application/json;charset=utf-8')}


function exportHTML(){const stats=[['Total procesado',state.total],['Incidencias válidas',state.valid.length],['Descartadas',state.invalid.length],['Tasa de validez',pct(state.valid.length,state.total).toFixed(1)+'%']];const table=(rows)=>{if(!rows.length)return '<p>Sin registros.</p>';const cols=[...new Set(rows.flatMap(r=>Object.keys(r)))];return `<div class="scroll"><table><thead><tr>${cols.map(c=>`<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${cols.map(c=>`<td>${esc(r[c]||'')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`};const htmlDoc=`<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Informe IncidentIQ</title><style>body{font:14px system-ui;background:#f4f6fb;color:#182338;margin:0;padding:28px}main{max-width:1300px;margin:auto}h1{font-size:28px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}.metric,section{background:white;border:1px solid #e5eaf2;border-radius:14px;padding:16px;margin:12px 0;overflow:auto}.metric b{display:block;font-size:25px;color:#5278ee}.muted{color:#74819a}table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:9px;border-bottom:1px solid #e8edf4}th{background:#f4f6fb;color:#66738a}h2{font-size:18px}</style><main><h1>IncidentIQ · Informe de incidencias</h1><p class="muted">Fuente: ${esc(state.file)} · Generado localmente</p><div class="metrics">${stats.map(x=>`<div class="metric"><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join('')}</div><section><h2>Incidencias por dispositivo</h2>${table(countBy(state.valid,'Tipo_Dispositivo').map(([Respuesta,Casos])=>({Respuesta,Casos,Porcentaje:pct(Casos,state.valid.length).toFixed(1)+'%'})))}</section><section><h2>Incidencias por ubicación</h2>${table(countBy(state.valid,'Ubicacion').map(([Respuesta,Casos])=>({Respuesta,Casos,Porcentaje:pct(Casos,state.valid.length).toFixed(1)+'%'})))}</section><section><h2>Incidencias por síntoma</h2>${table(countBy(state.valid,'Sintoma_Principal').map(([Respuesta,Casos])=>({Respuesta,Casos,Porcentaje:pct(Casos,state.valid.length).toFixed(1)+'%'})))}</section><section><h2>Registros válidos (${state.valid.length})</h2>${table(state.valid)}</section><section><h2>Registros descartados (${state.invalid.length})</h2>${table(state.invalid)}</section></main></html>`;downloadBlob(htmlDoc,'informe_incidencias.html','text/html;charset=utf-8')}


async function uploadXML(file){if(!file)return;if(file.size>12*1024*1024){showToast('El XML supera el límite de 12 MB');return}showToast('Analizando el XML…');try{const res=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'application/xml','X-Filename':encodeURIComponent(file.name)},body:file});const d=await res.json();if(!res.ok)throw new Error(d.error||'No se pudo procesar el archivo');applyData(d);state.page='overview';render();showToast(`XML cargado: ${fmt(d.total)} registros`)}catch(e){showToast(e.message)}finally{$('#xml-file').value=''}}


document.querySelectorAll('.nav button').forEach(b=>b.addEventListener('click',()=>{state.page=b.dataset.page;render()}));$('#export-valid').addEventListener('click',()=>downloadCSV(state.valid,'incidencias_validas.csv'));$('#export-json').addEventListener('click',exportJSON);$('#export-html').addEventListener('click',exportHTML);$('#xml-file').addEventListener('change',e=>uploadXML(e.target.files[0]));window.addEventListener('resize',()=>{if(state.page==='overview')drawAllCharts()});


loadData().catch(e=>{$('#page-content').innerHTML=`<div class="warning-banner">${esc(e.message)}</div>`});


</script>


</body>


</html>'''


class DashboardHandler(BaseHTTPRequestHandler):


    server_version = "IncidentIQLocal/1.0"


    def log_message(self, fmt: str, *args: Any) -> None:


        # Mantener los logs HTTP discretos en la terminal.


        Console().print(f"[dim]Web: {fmt % args}[/dim]")


    def _send(self, status: int, body: bytes, content_type: str) -> None:


        self.send_response(status)


        self.send_header("Content-Type", content_type)


        self.send_header("Content-Length", str(len(body)))


        self.send_header("Cache-Control", "no-store")


        self.send_header("X-Content-Type-Options", "nosniff")


        self.send_header("Referrer-Policy", "no-referrer")


        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:")


        self.end_headers()


        self.wfile.write(body)


    def _json(self, status: int, data: dict[str, Any]) -> None:


        body = json.dumps(data, ensure_ascii=False).encode("utf-8")


        self._send(status, body, "application/json; charset=utf-8")


    def do_GET(self) -> None:


        path = urlparse(self.path).path


        if path in ("/", "/index.html"):


            self._send(200, DASHBOARD_HTML.encode("utf-8"), "text/html; charset=utf-8")


            return


        if path == "/api/data":


            with STATE_LOCK:


                data = {


                    "validos": APP_STATE["validos"],


                    "invalidos": APP_STATE["invalidos"],


                    "total": APP_STATE["total"],


                    "archivo": APP_STATE["archivo"],


                }


            self._json(200, data)


            return


        if path == "/favicon.ico":


            self._send(204, b"", "image/x-icon")


            return


        self._json(404, {"error": "Ruta no encontrada"})


    def do_POST(self) -> None:


        path = urlparse(self.path).path


        if path != "/api/upload":


            self._json(404, {"error": "Ruta no encontrada"})


            return


        try:


            length = int(self.headers.get("Content-Length", "0"))


        except ValueError:


            self._json(400, {"error": "Longitud de archivo incorrecta"})


            return


        if length <= 0 or length > MAX_UPLOAD_BYTES:


            self._json(413, {"error": "El archivo está vacío o supera el límite de 12 MB"})


            return


        contenido = self.rfile.read(length)


        df_validos, df_invalidos, total, error = analizar_contenido_xml(contenido)


        if error:


            self._json(400, {"error": error})


            return


        origen = self.headers.get("X-Filename", "XML cargado desde el navegador")


        # El nombre solo se muestra como texto, no se usa como ruta del sistema.


        origen = Path(unquote(origen).replace("\\", "/")).name[:120] or "XML cargado"


        data = {


            "validos": df_a_registros(df_validos),


            "invalidos": df_a_registros(df_invalidos),


            "total": total,


            "archivo": origen,


        }


        with STATE_LOCK:


            APP_STATE.update(data)


        self._json(200, data)


# -----------------------------------------------------------------------------


# Arranque del servidor web


# -----------------------------------------------------------------------------


def obtener_ip_local() -> str | None:


    """Intenta averiguar la IP de la interfaz de red activa, sin enviar datos."""


    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


    try:


        sock.connect(("192.0.2.1", 80))  # Dirección TEST-NET; UDP no envía paquete.


        return sock.getsockname()[0]


    except OSError:


        return None


    finally:


        sock.close()


def iniciar_dashboard(host: str, port: int, abrir_navegador: bool = True) -> None:


    server = ThreadingHTTPServer((host, port), DashboardHandler)


    server.daemon_threads = True


    local_url = f"http://127.0.0.1:{port}"


    console = Console()


    console.print(Panel.fit(


        "[bold cyan]IncidentIQ · Dashboard web local[/bold cyan]\n\n"


        f"[bold]En este equipo:[/bold] [green]{local_url}[/green]",


        border_style="cyan",


    ))


    if host in ("0.0.0.0", "::"):


        ip = obtener_ip_local()


        if ip:


            console.print(f"[bold]Otros dispositivos de la misma red:[/bold] [green]http://{ip}:{port}[/green]")


        console.print("[yellow]Aviso:[/yellow] acceso abierto a la red local. Úsalo solo en una red privada de confianza.")


    console.print("[dim]El panel está activo. Pulsa Ctrl+C en esta terminal para cerrarlo.[/dim]")


    if abrir_navegador:


        try:


            webbrowser.open(local_url)


        except Exception:


            pass


    try:


        server.serve_forever(poll_interval=0.4)


    except KeyboardInterrupt:


        console.print("\n[yellow]Cerrando IncidentIQ…[/yellow]")


    finally:


        server.shutdown()


        server.server_close()


def obtener_ruta_archivo(nombre: str) -> Path:

    """Resuelve rutas relativas desde la carpeta donde está este programa."""

    ruta = Path(nombre).expanduser()

    if not ruta.is_absolute():

        ruta = Path(__file__).resolve().parent / ruta

    return ruta.resolve()


def main() -> int:

    parser = argparse.ArgumentParser(

        description=(

            "Analiza incidencias XML en consola y ofrece un dashboard web local "

            "sin dependencias gráficas."

        )

    )

    modo = parser.add_mutually_exclusive_group()

    modo.add_argument("--web", action="store_true", help="Abrir directamente el dashboard web.")

    modo.add_argument("--consola", action="store_true", help="Mostrar solo el análisis de consola.")

    parser.add_argument(

        "--host",

        default="127.0.0.1",

        help="Dirección de escucha (por defecto 127.0.0.1; usa 0.0.0.0 para LAN).",

    )

    parser.add_argument(

        "--port",

        type=int,

        default=8765,

        help="Puerto del servidor web (por defecto 8765).",

    )

    parser.add_argument(

        "--no-browser",

        action="store_true",

        help="No abrir automáticamente el navegador.",

    )

    args = parser.parse_args()


    if not 1 <= args.port <= 65535:

        Console(stderr=True).print("[bold red]El puerto debe estar entre 1 y 65535.[/bold red]")

        return 2


    console = Console()

    console.print(Panel.fit(

        "[bold cyan]IncidentIQ · Analizador de incidencias[/bold cyan]",

        border_style="cyan",

    ))


    nombre_xml = ARCHIVO_XML_POR_DEFECTO
    console.print(f"\nArchivo XML de entrada: [bold]{nombre_xml}[/bold]")

    ruta_xml = obtener_ruta_archivo(nombre_xml)


    if ruta_xml.suffix.lower() != ".xml":

        console.print("[bold red]Error: el archivo de entrada debe tener extensión .xml.[/bold red]")

        return 1


    df_validos, df_invalidos, total, error = cargar_xml(ruta_xml)

    if error:

        console.print(f"[bold red]Error:[/bold red] {error}", markup=True)

        return 1


    # La interfaz de consola conserva los paneles/tablas/barras del código original.

    imprimir_consola_original(df_validos, df_invalidos, total)


    with STATE_LOCK:

        APP_STATE.update({

            "validos": df_a_registros(df_validos),

            "invalidos": df_a_registros(df_invalidos),

            "total": total,

            "archivo": ruta_xml.name,

            "error": None,

        })


    abrir_web = args.web

    if not args.web and not args.consola:

        try:

            respuesta = input("\n¿Quieres abrir el panel gráfico en el navegador? [S/n]: ").strip().casefold()

        except (EOFError, KeyboardInterrupt):

            respuesta = "n"

        abrir_web = respuesta in {"s", "si", "sí", "y", "yes"}


    if abrir_web:

        try:

            iniciar_dashboard(args.host, args.port, not args.no_browser)

        except OSError as exc:

            console.print(f"[bold red]No se pudo iniciar el servidor web: {exc}[/bold red]")

            console.print("Comprueba que el puerto no esté ocupado y que tengas permisos de red.")

            return 1


    return 0


if __name__ == "__main__":


    raise SystemExit(main())
