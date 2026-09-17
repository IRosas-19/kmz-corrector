from flask import Flask, render_template, request, send_file, jsonify
import zipfile
import os
import uuid
import xml.etree.ElementTree as ET
from shapely.geometry import Point, LineString
from scipy.spatial import KDTree

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024  # 10 MB
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
OUTPUT_FOLDER = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

data_store = {}

NS = {'kml': 'http://www.opengis.net/kml/2.2'}


# ----------- LÓGICA DE RENUMERACIÓN DE POSTES -----------
def renumerar_postes_kml_bytes(kml_content):
    ET.register_namespace('', NS['kml'])
    root = ET.fromstring(kml_content)
    contador = 1
    total_modificados = 0
    
    for folder in root.findall('.//kml:Folder', NS):
        name_elem = folder.find('kml:name', NS)
        
        # Filtro exclusivo para carpetas de Postes (igual que en Numeracion.py)
        if name_elem is not None and name_elem.text and name_elem.text.strip().lower() == 'postes':
            for placemark in folder.findall('.//kml:Placemark', NS):
                pm_name = placemark.find('kml:name', NS)
                if pm_name is None:
                    pm_name = ET.SubElement(placemark, '{http://www.opengis.net/kml/2.2}name')
                
                pm_name.text = str(contador)
                contador += 1
                total_modificados += 1
                
    kml_output = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    return kml_output, total_modificados


# ----------- LEER KMZ CON XML -----------
def extraer_kmz(ruta_kmz):
    puntos = []

    with zipfile.ZipFile(ruta_kmz, 'r') as z:
        for f in z.namelist():
            if f.endswith('.kml'):
                kml_data = z.read(f)
                break

    root = ET.fromstring(kml_data)

    for pm in root.findall('.//kml:Placemark', NS):
        point = pm.find('.//kml:Point/kml:coordinates', NS)
        if point is not None and point.text:
            coords = point.text.strip().split(',')
            lon = float(coords[0])
            lat = float(coords[1])
            puntos.append((pm, (lon, lat)))

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
    return '.' in nombre and nombre.lower().endswith('.kmz')


# ----------- RUTAS -----------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/upload", methods=["POST"])
def upload():
    if "bueno" not in request.files or "malo" not in request.files:
        return "❌ Faltan archivos", 400
    
    bueno = request.files["bueno"]
    malo = request.files["malo"]

    if bueno.filename == "" or malo.filename == "":
        return "❌ Archivos vacíos", 400

    if not archivo_valido(bueno.filename) or not archivo_valido(malo.filename):
        return "❌ Solo se permiten archivos KMZ", 400
    
    id_sesion = str(uuid.uuid4())
    path_bueno = os.path.join(UPLOAD_FOLDER, id_sesion + "_b.kmz")
    path_malo = os.path.join(UPLOAD_FOLDER, id_sesion + "_m.kmz")

    bueno.save(path_bueno)
    malo.save(path_malo)

    puntos_buenos, _ = extraer_kmz(path_bueno)
    puntos_malos, root_malo = extraer_kmz(path_malo)

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
    data = data_store[id_sesion]
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


# ⚡ NUEVA RUTA INDEPENDIENTE PARA RENUMERAR
@app.route("/renumerar_solo", methods=["POST"])
def renumerar_solo():
    if "archivo_kmz" not in request.files:
        return jsonify({"error": "No se envió archivo"}), 400
    
    file = request.files["archivo_kmz"]
    if file.filename == "":
        return jsonify({"error": "Archivo no seleccionado"}), 400

    id_sesion = str(uuid.uuid4())
    ruta_salida = os.path.join(OUTPUT_FOLDER, f"renumerado_{id_sesion}.kmz")

    try:
        # Si es KMZ
        if file.filename.lower().endswith('.kmz'):
            with zipfile.ZipFile(file, 'r') as kmz:
                kml_filename = [f for f in kmz.namelist() if f.endswith('.kml')][0]
                kml_content = kmz.read(kml_filename)
            
            kml_modificado, total_postes = renumerar_postes_kml_bytes(kml_content)
            
            with zipfile.ZipFile(ruta_salida, 'w', zipfile.ZIP_DEFLATED) as kmz_out:
                kmz_out.writestr(kml_filename, kml_modificado)
        else:
            kml_content = file.read()
            kml_modificado, total_postes = renumerar_postes_kml_bytes(kml_content)
            
            with zipfile.ZipFile(ruta_salida, 'w', zipfile.ZIP_DEFLATED) as kmz_out:
                kmz_out.writestr("doc.kml", kml_modificado)

        # Guardar en data_store por si quiere ser descargado
        data_store[id_sesion] = {"salida": ruta_salida}

        return jsonify({
            "status": "ok",
            "id": id_sesion,
            "postes": total_postes
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/descargar/<id_sesion>")
def descargar(id_sesion):
    path = data_store[id_sesion].get("salida")
    nombre = request.args.get("nombre", "corregido") + ".kmz"

    if not path or not os.path.exists(path):
        return "Error: archivo no encontrado", 400

    return send_file(path, as_attachment=True, download_name=nombre)


@app.route("/guardarCambios/<id_sesion>", methods=["POST"])
def guardarCambios(id_sesion):
    data = request.json
    root = data_store[id_sesion]["root"]
    puntos_malos = data_store[id_sesion]["puntos_malos"]

    for item in data:
        idx = item["index"]
        lon, lat = item["coord"]

        pm = puntos_malos[idx][0]
        nodo = pm.find('.//{http://www.opengis.net/kml/2.2}coordinates')
        nodo.text = f"{lon},{lat},0"
        
    salida = os.path.join(OUTPUT_FOLDER, id_sesion + ".kmz")
    guardar_kmz(root, salida)
    data_store[id_sesion]["salida"] = salida

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port)