#!/usr/bin/env python3

import hashlib
import json
import os
import tempfile
import unicodedata
import xml.etree.ElementTree as ET

from pathlib import Path
from typing import Any


# Correspondencia de las columnas del XML
MAPEO_COLUMNAS = {
    "Marca_de_temps": "Fecha_Registro",
    "Adre_a_electr_nica": "Usuario_Email",
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

PUNTUACION_PERMITIDA = set(
    ".,;:!?¡¿'’\"“”‘()[]-_/@+%&·…"
)


# =====================================================
# 1. VALIDACIÓN DE TEXTO
# =====================================================

def es_texto_valido(texto: str) -> bool:

    if not texto:
        return True

    texto = unicodedata.normalize("NFC", str(texto))

    for caracter in texto:

        if caracter.isalpha():

            nombre = unicodedata.name(caracter, "")

            if "LATIN" not in nombre:
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


# =====================================================
# 2. GENERAR IDENTIFICADORES
# =====================================================

def crear_id(incidencia: dict[str, Any]) -> str:

    # Comprobar si ya existe un identificador
    for clave in ("id", "ID", "Id"):

        valor = incidencia.get(clave)

        if valor is not None and str(valor).strip():
            return str(valor).strip()

    fecha = str(
        incidencia.get("Fecha_Registro")
        or incidencia.get("Marca_de_temps")
        or ""
    ).strip()

    email = str(
        incidencia.get("Usuario_Email")
        or incidencia.get("Adre_a_electr_nica")
        or ""
    ).strip().lower()

    if fecha and email:

        contenido = f"{fecha}|{email}"

    else:

        contenido = json.dumps(
            incidencia,
            ensure_ascii=False,
            sort_keys=True,
            default=str
        )

    return hashlib.sha256(
        contenido.encode("utf-8")
    ).hexdigest()


# =====================================================
# 3. LEER EL ARCHIVO XML
# =====================================================

def leer_xml(ruta_xml: Path) -> list[dict[str, Any]]:

    if not ruta_xml.exists():
        raise FileNotFoundError(
            f"No se ha encontrado el archivo '{ruta_xml.name}'. "
            "Revisa si ha habido un cambio de nombre o si se ha movido a otra carpeta."
        )

    try:

        arbol = ET.parse(ruta_xml)

    except PermissionError as error:

        raise PermissionError(
            f"No tienes permisos de lectura para el XML: {ruta_xml}"
        ) from error

    except ET.ParseError as error:

        raise ValueError(
            f"El XML no tiene un formato válido: {error}"
        ) from error

    except UnicodeDecodeError as error:

        raise ValueError(
            f"No se puede interpretar la codificación del XML: {error}"
        ) from error

    except OSError as error:

        raise OSError(
            f"No se puede abrir el XML: {error}"
        ) from error

    raiz = arbol.getroot()

    elementos = raiz.findall(".//registro")

    if not elementos:
        raise ValueError(
            "No se han encontrado elementos <registro> en el XML."
        )

    incidencias = []

    for numero, registro in enumerate(elementos, start=1):

        datos = {
            "Numero_Registro": numero
        }

        for elemento in registro:

            etiqueta = elemento.tag

            nombre_campo = MAPEO_COLUMNAS.get(
                etiqueta,
                etiqueta
            )

            valor = (
                elemento.text
                if elemento.text is not None
                else ""
            )

            # Ignorar columnas auxiliares vacías
            if etiqueta.startswith("Columna_") and not valor.strip():
                continue

            datos[nombre_campo] = valor

        # Comprobar todos los campos
        campos_invalidos = []

        for campo, valor in datos.items():

            if campo == "Numero_Registro":
                continue

            if str(valor).strip() and not es_texto_valido(str(valor)):
                campos_invalidos.append(campo)

        datos["Estado_validacion"] = (
            "Descartada" if campos_invalidos else "Válida"
        )

        if campos_invalidos:
            datos["Campos_invalidos"] = campos_invalidos

        datos["id"] = crear_id(datos)

        incidencias.append(datos)

    return incidencias


# =====================================================
# 4. CARGAR EL JSON EXISTENTE
# =====================================================

def cargar_json(ruta_json: Path) -> list[dict[str, Any]]:

    try:

        with ruta_json.open("r", encoding="utf-8") as archivo:

            datos = json.load(archivo)

    except json.JSONDecodeError as error:

        raise ValueError(
            f"El archivo {ruta_json} contiene JSON incorrecto. "
            "No se ha modificado."
        ) from error

    except PermissionError as error:

        raise PermissionError(
            f"No tienes permisos de lectura para el JSON: {ruta_json}"
        ) from error

    except OSError as error:

        raise OSError(
            f"No se puede leer {ruta_json}: {error}"
        ) from error

    if not isinstance(datos, list) or not all(
        isinstance(x, dict) for x in datos
    ):

        raise ValueError(
            "El contenido JSON debe ser una lista de objetos. "
            "No se ha modificado."
        )

    return datos


# =====================================================
# 5. ELIMINAR DUPLICADOS
# =====================================================

def asignar_ids_y_eliminar_duplicados(registros):

    resultado = []

    ids_vistos = set()

    duplicados = 0

    for original in registros:

        registro = dict(original)

        identificador = crear_id(registro)

        registro["id"] = identificador

        if identificador in ids_vistos:

            duplicados += 1
            continue

        ids_vistos.add(identificador)

        resultado.append(registro)

    return resultado, duplicados


# =====================================================
# 6. GUARDAR JSON DE FORMA SEGURA
# =====================================================

def escribir_json_seguro(ruta_json: Path, registros):

    try:

        ruta_json.parent.mkdir(
            parents=True,
            exist_ok=True
        )

    except PermissionError as error:

        raise PermissionError(
            f"No tienes permisos para acceder a la carpeta: "
            f"{ruta_json.parent}"
        ) from error

    temporal = None

    try:

        descriptor, temporal = tempfile.mkstemp(
            prefix=f".{ruta_json.name}.",
            suffix=".tmp",
            dir=str(ruta_json.parent)
        )

        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8"
        ) as archivo:

            json.dump(
                registros,
                archivo,
                ensure_ascii=False,
                indent=4
            )

            archivo.write("\n")

        os.replace(temporal, ruta_json)

        temporal = None

    except PermissionError as error:

        raise PermissionError(
            f"No tienes permisos para escribir el archivo: {ruta_json}"
        ) from error

    finally:

        if temporal and os.path.exists(temporal):
            os.remove(temporal)


# =====================================================
# 7. ALMACENAR LAS INCIDENCIAS
# =====================================================

def guardar_incidencias(ruta_xml: Path, ruta_json: Path):

    nuevas, duplicados_entrada = (
        asignar_ids_y_eliminar_duplicados(
            leer_xml(ruta_xml)
        )
    )

    print("\n" + "=" * 58)
    print("             ALMACENAMIENTO DE INCIDENCIAS JSON")
    print("=" * 58)

    print(f"XML de entrada:       {ruta_xml}")
    print(f"Incidencias leídas:   {len(nuevas) + duplicados_entrada}")
    print(f"IDs repetidos en XML: {duplicados_entrada}")
    print(f"Incidencias únicas:   {len(nuevas)}")

    # Si el JSON no existe, crearlo
    if not ruta_json.exists():

        finales = nuevas

        operacion = "creación"

        añadidas = len(finales)

        repetidas = duplicados_entrada

    else:

        print(f"\nEl archivo {ruta_json} ya existe.")

        print("\n¿Qué deseas hacer?")

        print("[1] Sobreescribir el archivo")
        print("[2] Añadir nuevas incidencias")
        print("[3] Cancelar")

        while True:

            try:

                opcion = input("\nSelecciona una opción (1/2/3): ").strip()

            except (EOFError, KeyboardInterrupt):

                print("\nOperación cancelada. No se ha modificado el JSON.")
                return

            if opcion in {"1", "2", "3"}:
                break

            print("Opción no válida. Introduce 1, 2 o 3.")

        if opcion == "3":

            print("Operación cancelada. No se ha modificado el JSON.")
            return

        # SOBRESCRIBIR
        if opcion == "1":

            finales, duplicados_internos = (
                asignar_ids_y_eliminar_duplicados(nuevas)
            )

            operacion = "sobreescritura"

            añadidas = len(finales)

            repetidas = duplicados_internos

        # AÑADIR
        else:

            antiguas = cargar_json(ruta_json)

            historial, duplicados_historial = (
                asignar_ids_y_eliminar_duplicados(antiguas)
            )

            ids_existentes = {
                registro["id"]
                for registro in historial
            }

            añadidas = 0

            repetidas = duplicados_historial

            for incidencia in nuevas:

                identificador = crear_id(incidencia)

                if identificador in ids_existentes:

                    repetidas += 1
                    continue

                incidencia["id"] = identificador

                historial.append(incidencia)

                ids_existentes.add(identificador)

                añadidas += 1

            finales = historial

            operacion = "adición"

    # Guardar el resultado
    escribir_json_seguro(ruta_json, finales)

    validas = sum(
        1 for x in finales
        if x.get("Estado_validacion") == "Válida"
    )

    descartadas = sum(
        1 for x in finales
        if x.get("Estado_validacion") == "Descartada"
    )

    print("\n" + "=" * 58)
    print("                 RESULTADO DEL ALMACENAMIENTO")
    print("=" * 58)

    print(f"Operación realizada:       {operacion.capitalize()}")
    print(f"Incidencias añadidas:      {añadidas}")
    print(f"Duplicados no añadidos:    {repetidas}")
    print(f"Total almacenado:          {len(finales)}")

    print(f"  ├─ Válidas:               {validas}")
    print(f"  └─ Descartadas:           {descartadas}")

    print(f"Archivo generado:          {ruta_json.resolve()}")

    print("=" * 58)
    print("JSON guardado correctamente.")


# =====================================================
# 8. EJECUCIÓN DEL PROGRAMA
# =====================================================

def main():

    print("\n" + "=" * 58)
    print("          IMPORTADOR DE INCIDENCIAS XML A JSON")
    print("=" * 58)

    # Definición directa de los nombres de los archivos
    ruta_xml = Path("incidencies.xml")
    ruta_json = Path("incidencies.json")

    # Ejecutar almacenamiento
    try:

        guardar_incidencias(ruta_xml, ruta_json)

    except FileNotFoundError as error:

        print(f"\nERROR: {error}")

    except PermissionError as error:

        print(f"\nERROR DE PERMISOS: {error}")
        print("Revisa el propietario, el grupo y los permisos del archivo.")
        print("Comprueba que tu usuario tenga permisos de lectura y escritura.")

    except (ValueError, OSError) as error:

        print(f"\nERROR: {error}")


if __name__ == "__main__":

    main()
