import os
import zipfile
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import filedialog

def renumerar_postes_kml(kml_content):
    ns = {'kml': 'http://www.opengis.net/kml/2.2'}
    ET.register_namespace('', ns['kml'])
    
    root = ET.fromstring(kml_content)
    contador = 1
    
    for folder in root.findall('.//kml:Folder', ns):
        name_elem = folder.find('kml:name', ns)
        
        # Filtro exclusivo para carpetas de Postes
        if name_elem is not None and name_elem.text and name_elem.text.strip().lower() == 'postes':
            for placemark in folder.findall('kml:Placemark', ns):
                pm_name = placemark.find('kml:name', ns)
                if pm_name is None:
                    pm_name = ET.SubElement(placemark, '{http://www.opengis.net/kml/2.2}name')
                
                pm_name.text = str(contador)
                contador += 1
                
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)

def seleccionar_y_procesar():
    # Ocultar la ventana principal de tkinter
    root = tk.Tk()
    root.withdraw()
    
    # Abrir ventana de selección de archivo
    ruta_entrada = filedialog.askopenfilename(
        title="Selecciona el archivo KMZ o KML de origen",
        filetypes=[("Archivos KML / KMZ", "*.kmz *.kml"), ("Todos los archivos", "*.*")]
    )
    
    # Si el usuario cancela la selección
    if not ruta_entrada:
        print("Operación cancelada por el usuario.")
        return

    # Generar ruta de salida en la misma carpeta con prefijo 'renumerado_'
    directorio, nombre_archivo = os.path.split(ruta_entrada)
    ruta_salida = os.path.join(directorio, f"renumerado_{nombre_archivo}")

    es_kmz = ruta_entrada.lower().endswith('.kmz')
    
    try:
        if es_kmz:
            with zipfile.ZipFile(ruta_entrada, 'r') as kmz:
                kml_filename = [f for f in kmz.namelist() if f.endswith('.kml')][0]
                kml_content = kmz.read(kml_filename)
            
            kml_modificado = renumerar_postes_kml(kml_content)
            
            with zipfile.ZipFile(ruta_salida, 'w', zipfile.ZIP_DEFLATED) as kmz_out:
                kmz_out.writestr(kml_filename, kml_modificado)
        else:
            with open(ruta_entrada, 'rb') as f:
                kml_content = f.read()
                
            kml_modificado = renumerar_postes_kml(kml_content)
            
            with open(ruta_salida, 'wb') as f:
                f.write(kml_modificado)

        print(f"\n¡Éxito! Archivo guardado como:\n{ruta_salida}")
    except Exception as e:
        print(f"Ocurrió un error al procesar el archivo: {e}")

if __name__ == '__main__':
    seleccionar_y_procesar()