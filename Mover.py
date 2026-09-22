from flask import Flask, render_template, request, send_file, jsonify
import zipfile
import os
import uuid
import re
import xml.etree.ElementTree as ET
from scipy.spatial import KDTree

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

data_store = {}

# Namespace por defecto (KML 2.2). Se sobreescribe dinámicamente según
# el namespace real detectado en cada archivo (ver detectar_namespace).
NS_DEFAULT = {'kml': 'http://www.opengis.net/kml/2.2'}


# ----------- EXCEPCIONES PROPIAS -----------
class ArchivoInvalidoError(Exception):
    """Se lanza cuando el archivo no es un KMZ/KML válido o legible."""
    pass


# ----------- PARSER SEGURO Y TOLERANTE DE XML SUCIO -----------
def parsear_xml_seguro(kml_bytes_o_str):
    """
    Parsea el contenido de un KML tolerando etiquetas XML 'sucias' o mal formadas
    (ej. etiquetas inyectadas por Word/Excel con prefijos de namespace no declarados).

    Intenta primero usar `lxml` con `recover=True`. Si `lxml` no está disponible,
    limpia las etiquetas problemáticas mediante regex antes de pasar el XML a ElementTree.
    """
    if isinstance(kml_bytes_o_str, bytes):
        content_str = kml_bytes_o_str.decode('utf-8', errors='ignore')
    else:
        content_str = kml_bytes_o_str

    # 1. Intento primario: lxml en modo de recuperación (recover=True)
    try:
        from lxml import etree
        parser = etree.XMLParser(recover=True, encoding='utf-8')
        # lxml devuelve un ElementTree / Element compatible
        root_lxml = etree.fromstring(content_str.encode('utf-8'), parser=parser)
        # Convertimos la salida de lxml a una estructura limpia que xml.etree.ElementTree pueda manejar sin problemas
        kml_clean = etree.tostring(root_lxml, encoding='utf-8')
        return ET.fromstring(kml_clean)
    except ImportError:
        pass  # lxml no está instalado, se usa fallback
    except Exception:
        pass  # Si lxml falla por otra razón, se procede al fallback

    # 2. Fallback: Sanitización mediante Regex + ElementTree estándar
    # Elimina etiquetas con namespaces no declarados (ej: <o:p>, </o:p>, <v:shape ...>)
    cleaned_str = re.sub(r'</?[a-zA-Z_][a-zA-Z0-9_\-]*:[a-zA-Z0-9_\-]+[^>]*>', '', content_str)
    
    try:
        return ET.fromstring(cleaned_str)
    except ET.ParseError as e:
        raise ArchivoInvalidoError(f"El XML del KML está severamente dañado o mal formado: {e}")


# ----------- DETECCIÓN DE NAMESPACE REAL -----------
def detectar_namespace(root):
    """
    Detecta el namespace real del elemento raíz del KML.
    Distintos generadores (Google Earth, QGIS, etc.) pueden usar
    versiones distintas (2.0, 2.1, 2.2, 2.3) o incluso no declarar
    namespace. Si asumimos siempre 2.2, los findall() con ese
    namespace fijo no encuentran nada y el resultado queda vacío
    sin lanzar ningún error (falla silenciosa).
    """
    if root.tag.startswith('{'):
        uri = root.tag.split('}')[0].strip('{')
        return {'kml': uri}
    # Sin namespace declarado: se usan las etiquetas planas
    return {'kml': ''}


def _tag(ns, nombre):
    """Construye un nombre de etiqueta calificado o plano según el namespace."""
    uri = ns.get('kml', '')
    return f'{{{uri}}}{nombre}' if uri else nombre


# ----------- LEER CONTENIDO KML DESDE KMZ O KML PLANO -----------
def leer_kml_bytes(ruta_o_fileobj, nombre_archivo):
    """
    Acepta tanto .kmz (zip comprimido) como .kml plano.
    También cubre el caso de un .kmz que en realidad es un .kml
    sin comprimir (renombrado a mano), detectando si el archivo
    es realmente un ZIP antes de intentar abrirlo como tal.

    Devuelve: (kml_bytes, nombre_kml_interno_o_None)
    """
    es_kmz_por_nombre = nombre_archivo.lower().endswith('.kmz')

    # Verificamos la firma real del archivo (los ZIP empiezan con "PK")
    if hasattr(ruta_o_fileobj, 'read'):
        pos = ruta_o_fileobj.tell()
        firma = ruta_o_fileobj.read(2)
        ruta_o_fileobj.seek(pos)
        es_zip_real = firma == b'PK'
    else:
        with open(ruta_o_fileobj, 'rb') as f:
            es_zip_real = f.read(2) == b'PK'

    if es_zip_real:
        try:
            with zipfile.ZipFile(ruta_o_fileobj, 'r') as z:
                kml_files = [f for f in z.namelist() if f.lower().endswith('.kml')]
                if not kml_files:
                    raise ArchivoInvalidoError(
                        f"'{nombre_archivo}' es un ZIP válido pero no contiene ningún .kml adentro."
                    )
                if len(kml_files) > 1:
                    # Preferimos "doc.kml" si existe (convención estándar), si no, el primero.
                    preferido = next((f for f in kml_files if os.path.basename(f).lower() == 'doc.kml'), kml_files[0])
                else:
                    preferido = kml_files[0]
                return z.read(preferido), preferido
        except zipfile.BadZipFile:
            raise ArchivoInvalidoError(
                f"'{nombre_archivo}' no se pudo abrir como ZIP (KMZ corrupto o incompleto)."
            )
    else:
        # No es un ZIP real: lo tratamos como KML plano,
        # aunque venga con extensión .kmz por error.
        if hasattr(ruta_o_fileobj, 'read'):
            contenido = ruta_o_fileobj.read()
        else:
            with open(ruta_o_fileobj, 'rb') as f:
                contenido = f.read()

        if es_kmz_por_nombre:
            # Aviso implícito vía excepción específica más abajo si el parseo falla también
            pass

        return contenido, None


# ----------- LÓGICA DE RENUMERACIÓN DE POSTES -----------
def renumerar_postes_kml_bytes(kml_content):
    root = parsear_xml_seguro(kml_content)

    ns = detectar_namespace(root)
    if ns.get('kml'):
        ET.register_namespace('', ns['kml'])

    contador = 1
    total_modificados = 0

    for folder in root.findall(f'.//{_tag(ns, "Folder")}'):
        name_elem = folder.find(_tag(ns, "name"))

        if name_elem is not None and name_elem.text and name_elem.text.strip().lower() == 'postes':
            for placemark in folder.findall(f'.//{_tag(ns, "Placemark")}'):
                pm_name = placemark.find(_tag(ns, "name"))
                if pm_name is None:
                    pm_name = ET.SubElement(placemark, _tag(ns, "name"))

                pm_name.text = str(contador)
                contador += 1
                total_modificados += 1

    kml_output = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    return kml_output, total_modificados


# ----------- LEER KMZ/KML Y EXTRAER PUNTOS -----------
def extraer_kmz(ruta_kmz):
    nombre_archivo = os.path.basename(ruta_kmz)
    kml_data, _ = leer_kml_bytes(ruta_kmz, nombre_archivo)

    root = parsear_xml_seguro(kml_data)

    ns = detectar_namespace(root)
    puntos = []

    for pm in root.findall(f'.//{_tag(ns, "Placemark")}'):
        point = pm.find(f'.//{_tag(ns, "Point")}/{_tag(ns, "coordinates")}')
        if point is not None and point.text:
            coords = point.text.strip().split(',')
            try:
                lon = float(coords[0])
                lat = float(coords[1])
            except (ValueError, IndexError):
                continue  # coordenada mal formada, se ignora en vez de tronar
            puntos.append((pm, (lon, lat)))

    if not puntos:
        raise ArchivoInvalidoError(
            f"No se encontraron Placemarks con coordenadas válidas en '{nombre_archivo}' "
            f"(namespace detectado: '{ns.get('kml') or 'ninguno'}')."
        )

    return puntos, root


# ----------- CORREGIR PUNTOS -----------
def corregir_puntos(puntos_malos, puntos_buenos):
    coords_buenos = [p[1] for p in puntos_buenos]

    if not coords_buenos:
        return {
            "resultado": [],
            "corregidos": 0,
            "ignorados": len(puntos_malos)
        }

    tree = KDTree(coords_buenos)
    MAX_DIST = 0.00008
    resultado = []
    corregidos = 0
    ignorados = 0

    for pm, coord in puntos_malos:
        dist, idx = tree.query(coord)

        if dist <= MAX_DIST:
            lon, lat = coords_buenos[idx]
            nodo = pm.find('.//{http://www.opengis.net/kml/2.2}coordinates')
            if nodo is None:
                # Namespace distinto al 2.2: buscamos sin importar el namespace
                nodo = next((child for child in pm.iter() if child.tag.endswith('coordinates')), None)
            if nodo is not None:
                nodo.text = f"{lon},{lat},0"
            resultado.append({
                "coord": (lon, lat),
                "status": "corregido"
            })
            corregidos += 1
        else:
            resultado.append({
                "coord": coord,
                "status": "igual"
            })
            ignorados += 1

    return {
        "resultado": resultado,
        "corregidos": corregidos,
        "ignorados": ignorados
    }


# ----------- GUARDAR KMZ -----------
def guardar_kmz(root, salida):
    kml_str = ET.tostring(root, encoding='utf-8', method='xml')
    os.makedirs(os.path.dirname(salida), exist_ok=True)

    with zipfile.ZipFile(salida, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr("doc.kml", kml_str)


def archivo_valido(nombre):
    """Acepta .kmz y .kml — la validación real del contenido ocurre al leerlo."""
    return '.' in nombre and nombre.lower().endswith(('.kmz', '.kml'))


# ----------- RUTAS -----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "bueno" not in request.files or "malo" not in request.files:
        return jsonify({"error": "Faltan archivos"}), 400

    bueno = request.files["bueno"]
    malo = request.files["malo"]

    if bueno.filename == "" or malo.filename == "":
        return jsonify({"error": "Archivos vacíos"}), 400

    if not archivo_valido(bueno.filename) or not archivo_valido(malo.filename):
        return jsonify({"error": "Solo se permiten archivos KMZ o KML"}), 400

    id_sesion = str(uuid.uuid4())
    ext_bueno = os.path.splitext(bueno.filename)[1].lower()
    ext_malo = os.path.splitext(malo.filename)[1].lower()
    path_bueno = os.path.join(UPLOAD_FOLDER, id_sesion + "_b" + ext_bueno)
    path_malo = os.path.join(UPLOAD_FOLDER, id_sesion + "_m" + ext_malo)

    bueno.save(path_bueno)
    malo.save(path_malo)

    try:
        puntos_buenos, _ = extraer_kmz(path_bueno)
        puntos_malos, root_malo = extraer_kmz(path_malo)
    except ArchivoInvalidoError as e:
        return jsonify({"error": f"Archivo 'bueno' o 'malo' inválido: {e}"}), 400
    except Exception as e:
        return jsonify({"error": f"Error inesperado al leer los archivos: {e}"}), 400

    data_store[id_sesion] = {
        "root": root_malo,
        "puntos_buenos": puntos_buenos,
        "puntos_malos": puntos_malos
    }

    return jsonify({
        "id": id_sesion,
        "buenos": [p[1] for p in puntos_buenos],
        "malos": [p[1] for p in puntos_malos]
    })


@app.route("/corregir/<id_sesion>")
def corregir(id_sesion):
    data = data_store.get(id_sesion)
    if data is None:
        return jsonify({"error": "Sesión no encontrada o expirada. Vuelve a cargar los archivos."}), 404

    stats = corregir_puntos(data["puntos_malos"], data["puntos_buenos"])
    salida = os.path.join(OUTPUT_FOLDER, id_sesion + ".kmz")

    guardar_kmz(data["root"], salida)
    data_store[id_sesion]["salida"] = salida

    return jsonify({
        "resultado": stats["resultado"],
        "corregidos": stats["corregidos"],
        "ignorados": stats["ignorados"],
        "total_malos": len(data["puntos_malos"])
    })


# ⚡ RUTA INDEPENDIENTE PARA RENUMERAR
@app.route("/renumerar_solo", methods=["POST"])
def renumerar_solo():
    if "archivo_kmz" not in request.files:
        return jsonify({"error": "No se envió archivo"}), 400

    file = request.files["archivo_kmz"]
    if file.filename == "":
        return jsonify({"error": "Archivo no seleccionado"}), 400

    if not archivo_valido(file.filename):
        return jsonify({"error": "Solo se permiten archivos KMZ o KML"}), 400

    id_sesion = str(uuid.uuid4())
    ruta_salida = os.path.join(OUTPUT_FOLDER, f"renumerado_{id_sesion}.kmz")

    try:
        kml_content, kml_filename_interno = leer_kml_bytes(file, file.filename)
        kml_modificado, total_postes = renumerar_postes_kml_bytes(kml_content)

        nombre_dentro_zip = kml_filename_interno or "doc.kml"
        with zipfile.ZipFile(ruta_salida, 'w', zipfile.ZIP_DEFLATED) as kmz_out:
            kmz_out.writestr(nombre_dentro_zip, kml_modificado)

        data_store[id_sesion] = {"salida": ruta_salida}

        return jsonify({
            "status": "ok",
            "id": id_sesion,
            "postes": total_postes
        })
    except ArchivoInvalidoError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Error inesperado: {e}"}), 500


@app.route("/descargar/<id_sesion>")
def descargar(id_sesion):
    sesion = data_store.get(id_sesion)
    if sesion is None:
        return jsonify({"error": "Sesión no encontrada o expirada"}), 404

    path = sesion.get("salida")
    nombre = request.args.get("nombre", "corregido") + ".kmz"

    if not path or not os.path.exists(path):
        return jsonify({"error": "Archivo no encontrado"}), 404

    return send_file(path, as_attachment=True, download_name=nombre)


@app.route("/guardarCambios/<id_sesion>", methods=["POST"])
def guardarCambios(id_sesion):
    sesion = data_store.get(id_sesion)
    if sesion is None:
        return jsonify({"error": "Sesión no encontrada o expirada"}), 404

    data = request.json
    if not isinstance(data, list):
        return jsonify({"error": "Formato de datos inválido"}), 400

    root = sesion["root"]
    puntos_malos = sesion["puntos_malos"]

    for item in data:
        try:
            idx = item["index"]
            lon, lat = item["coord"]
            pm = puntos_malos[idx][0]
        except (KeyError, IndexError, TypeError, ValueError):
            continue  # ítem malformado, se ignora en vez de tronar toda la petición

        nodo = pm.find('.//{http://www.opengis.net/kml/2.2}coordinates')
        if nodo is None:
            nodo = next((child for child in pm.iter() if child.tag.endswith('coordinates')), None)
        if nodo is not None:
            nodo.text = f"{lon},{lat},0"

    salida = os.path.join(OUTPUT_FOLDER, id_sesion + ".kmz")
    guardar_kmz(root, salida)
    sesion["salida"] = salida

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)