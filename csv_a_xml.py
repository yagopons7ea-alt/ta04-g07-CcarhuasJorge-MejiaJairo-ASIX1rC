import csv
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def obtener_ruta_archivo(nombre):
    """Busca los archivos relativos en la carpeta del programa."""

    ruta = Path(nombre).expanduser()

    # Si la ruta no es absoluta, buscar junto al programa
    if not ruta.is_absolute():
        ruta = Path(__file__).resolve().parent / ruta

    return ruta.resolve()


def limpiar_nombre(nombre, numero):
    """Convierte el encabezado en una etiqueta XML válida."""

    nombre = nombre.strip()

    # Sustituir espacios y caracteres especiales por _
    nombre = re.sub(r"[^a-zA-Z0-9_.-]", "_", nombre)

    # XML no permite comenzar una etiqueta por un número
    if not nombre or not re.match(r"[a-zA-Z_]", nombre):
        nombre = f"campo_{numero}"

    return nombre


def convertir_csv_xml(archivo_csv, archivo_xml):

    # 1. Comprobar las extensiones de los archivos
    if archivo_csv.suffix.lower() != ".csv":
        print("Error: el archivo de entrada debe tener extensión .csv.")
        return

    if archivo_xml.suffix.lower() != ".xml":
        print("Error: el archivo de salida debe tener extensión .xml.")
        return

    try:

        # 2. Abrir el archivo CSV
        with open(
            archivo_csv,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as archivo:

            # 3. Leer el CSV respetando comillas y saltos de línea
            lector = csv.reader(
                archivo,
                delimiter=",",
                quotechar='"',
                doublequote=True
            )

            # 4. Obtener los encabezados
            encabezados = next(lector, None)

            if not encabezados:
                print("Error: el archivo CSV está vacío.")
                return

            # 5. Limpiar los nombres de las columnas
            nombres = []
            usados = set()

            for i, encabezado in enumerate(encabezados, start=1):

                nombre = limpiar_nombre(encabezado, i)

                # Evitar nombres duplicados
                base = nombre
                contador = 2

                while nombre in usados:
                    nombre = f"{base}_{contador}"
                    contador += 1

                usados.add(nombre)
                nombres.append(nombre)

            # 6. Crear el elemento raíz XML
            raiz = ET.Element("datos")

            total_registros = 0

            # 7. Procesar cada fila del CSV
            for fila in lector:

                # Ignorar filas completamente vacías
                if not fila or all(valor.strip() == "" for valor in fila):
                    continue

                # Comprobar que todas las filas tienen las mismas columnas
                if len(fila) != len(nombres):
                    raise ValueError(
                        f"Fila terminada en línea {lector.line_num}: "
                        f"se esperaban {len(nombres)} columnas, "
                        f"pero se encontraron {len(fila)}."
                    )

                # Crear un nuevo registro
                registro = ET.SubElement(raiz, "registro")

                # Introducir los valores en las etiquetas
                for nombre, valor in zip(nombres, fila):

                    elemento = ET.SubElement(registro, nombre)

                    # Conservar el contenido original
                    elemento.text = valor if valor != "" else None

                total_registros += 1

        # 8. Crear el árbol XML
        arbol = ET.ElementTree(raiz)

        # Dar formato e indentación al documento
        ET.indent(arbol, space="    ")

        # 9. Guardar el archivo XML
        arbol.write(
            str(archivo_xml),
            encoding="utf-8",
            xml_declaration=True
        )

        # 10. Mostrar el resultado
        print("\n" + "=" * 45)
        print("       CONVERSIÓN CSV A XML")
        print("=" * 45)

        print(f"Archivo CSV:       {archivo_csv}")
        print(f"Archivo XML:       {archivo_xml}")
        print(f"Columnas:          {len(nombres)}")
        print(f"Registros:         {total_registros}")

        print("=" * 45)
        print("Conversión completada correctamente.")

    # CONTROL DE ERRORES

    except FileNotFoundError as error:
        archivo_afectado = error.filename or archivo_csv

        print(f"Error: no se encuentra el archivo o directorio '{archivo_afectado}'.")
        print("Revisa el nombre y la ubicación del archivo.")

    except UnicodeDecodeError:
        print("Error: no se ha podido interpretar la codificación del CSV.")
        print("Revisa que el archivo no contenga errores de codificación.")

    except PermissionError as error:
        archivo_afectado = error.filename or "archivo"

        print(f"Error: no tienes permisos para acceder a '{archivo_afectado}'.")
        print("Revisa el propietario, el grupo y los permisos del archivo.")
        print("Comprueba que tu usuario tenga permisos de lectura o escritura.")

    except (csv.Error, ValueError, OSError, ET.ParseError) as error:
        print(f"Error durante la conversión: {error}")


# 11. Ejecutar el programa
if __name__ == "__main__":

    print("\n" + "=" * 45)
    print("       CONVERSOR CSV A XML")
    print("=" * 45)

    # Nombres definidos por defecto
    nombre_csv = "incidencies.csv"
    nombre_xml = "incidencies.xml"

    # Obtener las rutas relativas a la carpeta del script
    archivo_csv = obtener_ruta_archivo(nombre_csv)
    archivo_xml = obtener_ruta_archivo(nombre_xml)

    # 1. Comprobar si existe el archivo CSV
    if not archivo_csv.is_file():
        print(f"\nError: no se ha encontrado el archivo '{nombre_csv}'.")
        print("Revisa si ha habido un cambio de nombre o si se ha movido a otra carpeta.")

    else:
        # 2. Si el archivo XML ya existe, solicitar confirmación de reemplazo
        proceder = True

        if archivo_xml.is_file():
            try:
                respuesta = input(
                    f"\nEl archivo '{nombre_xml}' ya existe en el directorio. ¿Deseas reemplazarlo? (s/n): "
                ).strip().lower()

                if respuesta not in ["s", "si", "sí"]:
                    proceder = False
                    print("\nOperación cancelada. Se ha conservado el archivo XML existente.")
            except (EOFError, KeyboardInterrupt):
                proceder = False
                print("\nOperación cancelada por el usuario.")

        # 3. Realizar la conversión si corresponde
        if proceder:
            convertir_csv_xml(archivo_csv, archivo_xml)
